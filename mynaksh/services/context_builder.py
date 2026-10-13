"""Context selection: pick only what this message needs before calling the LLM.

1. Understand the query with cheap rules: life areas, follow-up, recall, astrology.
2. Fetch candidate memories from the graph, filtered by life area (graph traversal).
3. Score each candidate:  S = 0.6*R + 0.25*D + 0.15*F
     R  lexical relevance between the query (expanded with area keywords) and the memory
     D  direct topic match (memory life area is one the query is about)
     F  freshness x confidence (half-life 180 days)
4. Keep the top-k above a threshold, under a character budget.
5. Follow-ups reuse the memories that backed the previous answer.
6. Add the user's most important life areas and a summary of older turns, when present.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from mynaksh.astrology import describe_sign
from mynaksh.brain.base import BrainError, SharedBrain
from mynaksh.models import ContextItem, LifeAreaFocus, Memory, MemoryType, Turn, UserProfile
from mynaksh.services.prompts import CHAT_SYSTEM

AREA_KEYWORDS: dict[str, tuple[str, ...]] = {
    "career": ("career", "job", "jobs", "work", "office", "promotion", "promoted", "boss", "interview",
               "switch", "business", "startup", "entrepreneurship", "entrepreneur", "venture", "founder",
               "freelance", "freelancing", "resign", "resignation", "layoff", "laid", "hiring", "offer",
               "internship", "client", "clients", "profession", "manager", "product", "company", "role",
               "hike", "appraisal", "naukri", "kaam", "vyapar", "dhandha"),
    "relationships": ("love", "relationship", "marriage", "married", "marry", "partner", "wife",
                      "husband", "girlfriend", "boyfriend", "dating", "wedding", "breakup", "compatibility",
                      "shaadi", "pyaar", "rishta"),
    "health": ("health", "fitness", "sleep", "stress", "anxiety", "diet", "exercise", "illness", "yoga", "energy",
               "sehat", "swasthya"),
    "finance": ("money", "finance", "finances", "invest", "investment", "salary", "savings", "loan",
                "debt", "wealth", "property", "paisa", "paise", "dhan"),
    "education": ("study", "studies", "exam", "exams", "college", "university", "course", "learn",
                  "degree", "school", "admission", "padhai", "pariksha"),
    "family": ("family", "mother", "father", "parents", "son", "daughter", "brother", "sister", "kids", "children"),
    "travel": ("travel", "trip", "abroad", "relocate", "relocation", "visa", "vacation"),
    "spirituality": ("spiritual", "meditation", "temple", "karma", "puja", "mantra", "remedy", "remedies"),
}
# Devanagari keywords are matched as substrings (Python's \b is unreliable for Devanagari).
AREA_KEYWORDS_HI: dict[str, tuple[str, ...]] = {
    "career": ("करियर", "नौकरी", "काम", "व्यापार", "व्यवसाय", "प्रमोशन", "इंटरव्यू"),
    "relationships": ("शादी", "प्यार", "रिश्ता", "विवाह", "पति", "पत्नी"),
    "health": ("सेहत", "स्वास्थ्य", "बीमारी", "योग"),
    "finance": ("पैसा", "पैसे", "धन", "निवेश", "कर्ज"),
    "education": ("पढ़ाई", "परीक्षा", "कॉलेज"),
    "family": ("परिवार", "माता", "पिता", "बच्चे"),
    "travel": ("यात्रा", "विदेश"),
    "spirituality": ("मंदिर", "पूजा", "मंत्र", "ध्यान"),
}
ASTRO_WORDS = re.compile(
    r"\b(zodiac|sign|horoscope|kundli|chart|planet|planets|birth|stars|astrology|rashi|nakshatra|"
    r"lucky|saturn|jupiter|mars|venus|mercury|moon|sun|transit|dasha)\b", re.I)
RECALL = re.compile(r"\b(remember(?!\s+that\b)|recall|know about me|what do you know|told you|did i (say|mention)|yaad hai)\b"
                    r"|याद", re.I)
FOLLOWUP = re.compile(
    r"^(why|how come|what do you mean|explain|elaborate|tell me more|go on|expand|really|are you sure|"
    r"and\b|so\b|but\b|what about)|\b(that|this|it|those)\W*$", re.I)
STOPWORDS = set("""a an the and or but if of to in on for with about at by from is are was were be been am
i me my mine you your we our it this that these those what why how when where which who should could
would can will do does did have has had so not no yes please tell any some more much very just also""".split())

W_REL, W_DIRECT, W_FRESH = 0.6, 0.25, 0.15
HALF_LIFE_DAYS = 180


def _words(text: str) -> list[str]:
    return re.findall(r"[^\s.,!?;:'\"()\[\]{}।]+", text.lower())


def _tokens(text: str) -> set[str]:
    out = set()
    for t in _words(text):
        if t in STOPWORDS or len(t) < 3:
            continue
        out.add(t[:-1] if t.endswith("s") and len(t) > 4 else t)  # crude plural stem
    return out


@dataclass
class QueryAnalysis:
    areas: set[str]
    is_followup: bool
    is_recall: bool
    is_astro: bool
    tokens: set[str]


def analyze(message: str) -> QueryAnalysis:
    low = message.lower()
    words = set(_words(low))
    areas = {a for a, kws in AREA_KEYWORDS.items() if words & set(kws)}
    areas |= {a for a, kws in AREA_KEYWORDS_HI.items() if any(k in message for k in kws)}
    is_recall = bool(RECALL.search(message))
    is_followup = (not areas and not is_recall and len(message.split()) <= 10 and bool(FOLLOWUP.search(message.strip())))
    tokens = _tokens(message)
    for a in areas:
        tokens |= {k for k in AREA_KEYWORDS[a]} | {a}
    return QueryAnalysis(areas, is_followup, is_recall, bool(ASTRO_WORDS.search(message)), tokens)


def score_memory(m: Memory, q: QueryAnalysis, now: datetime) -> tuple[float, dict[str, float]]:
    mem_tokens = _tokens(f"{m.value} {m.key.replace('_', ' ').replace(':', ' ')} {m.life_area} "
                         + " ".join(str(v) for v in m.attributes.values()))
    rel = len(mem_tokens & q.tokens) / max(1, min(len(mem_tokens), 4))
    rel = min(1.0, rel)
    direct = 1.0 if m.life_area in q.areas else (0.5 if m.type == MemoryType.PREFERENCE else 0.0)
    updated = m.updated_at if m.updated_at.tzinfo else m.updated_at.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - updated).total_seconds() / 86400)
    fresh = m.confidence * math.pow(0.5, age_days / HALF_LIFE_DAYS)
    total = W_REL * rel + W_DIRECT * direct + W_FRESH * fresh
    return round(total, 3), {"relevance": rel, "direct": direct, "fresh": round(fresh, 3)}


@dataclass
class BuiltContext:
    system: str
    messages: list[dict[str, str]]
    items: list[ContextItem]
    memory_ids: list[str]
    analysis: QueryAnalysis
    warnings: list[str] = field(default_factory=list)

    @property
    def tags(self) -> list[str]:
        seen: list[str] = []
        for item in self.items:
            if item.tag not in seen:
                seen.append(item.tag)
        return seen


class ContextBuilder:
    def __init__(self, max_memories: int = 5, min_score: float = 0.3, memory_char_budget: int = 1200,
                 recent_turns: int = 6, followup_turns: int = 10, turn_char_limit: int = 600,
                 summary_char_limit: int = 800) -> None:
        self.summary_char_limit = summary_char_limit
        self.max_memories = max_memories
        self.min_score = min_score
        self.memory_char_budget = memory_char_budget
        self.recent_turns = recent_turns
        self.followup_turns = followup_turns
        self.turn_char_limit = turn_char_limit

    # ---------------------------------------------------------------- memories
    def select_memories(self, brain: SharedBrain, user_id: str, q: QueryAnalysis,
                        history: list[Turn]) -> list[tuple[Memory, float]]:
        now = datetime.now(timezone.utc)
        if q.is_recall:
            candidates = brain.find_candidate_memories(user_id, q.areas or None, limit=50)
            scored = [(m, score_memory(m, q, now)[0]) for m in candidates]
            # recall questions want breadth: everything in scope, ranked, no threshold
            scored.sort(key=lambda x: x[1], reverse=True)
            return self._budget(scored, limit=8)

        if q.is_followup:
            prev_ids = next((t.context_ids for t in reversed(history) if t.role == "assistant"), [])
            if not prev_ids:
                return []
            candidates = brain.find_candidate_memories(user_id, None, limit=100)
            by_id = {m.memory_id: m for m in candidates}
            return [(by_id[i], 1.0) for i in prev_ids if i in by_id]

        if not q.areas and not q.tokens:
            return []
        candidates = brain.find_candidate_memories(user_id, q.areas or None, limit=50)
        scored = [(m, score_memory(m, q, now)[0]) for m in candidates]
        scored = [s for s in scored if s[1] >= self.min_score]
        scored.sort(key=lambda x: x[1], reverse=True)
        return self._budget(scored, limit=self.max_memories)

    def _budget(self, scored: list[tuple[Memory, float]], limit: int) -> list[tuple[Memory, float]]:
        out, used = [], 0
        for m, s in scored[:limit]:
            size = len(m.render()) + 24
            if used + size > self.memory_char_budget:
                break
            out.append((m, s))
            used += size
        return out

    # ---------------------------------------------------------------- profile
    @staticmethod
    def _profile_block(p: UserProfile, q: QueryAnalysis, items: list[ContextItem]) -> str:
        lines = []
        basics = [f"Name: {p.name or 'unknown'}", f"Language: {p.preferred_language}"]
        lines.append(" | ".join(basics))
        items.append(ContextItem(kind="profile", ref="basics", tag="user_profile", text=lines[0]))
        if p.zodiac_sign:
            sign = f"Sun sign: {describe_sign(p.zodiac_sign)}"
            lines.append(sign)
            items.append(ContextItem(kind="profile", ref="zodiac_sign", tag="astrology_profile", text=sign))
        if q.is_astro or q.is_recall:
            birth = (f"Birth details: date {p.date_of_birth or 'unknown'}, time {p.time_of_birth or 'unknown'}, "
                     f"place {p.birth_place or 'unknown'}")
            lines.append(birth)
            items.append(ContextItem(kind="profile", ref="birth_details", tag="birth_details", text=birth))
        missing = p.missing_fields()
        if missing:
            lines.append("Missing profile fields: " + ", ".join(missing))
        return "\n".join(lines)

    # ---------------------------------------------------------------- build
    def build(self, message: str, profile: UserProfile, brain: SharedBrain | None,
              history_source: list[Turn], today: date, summary: str = "") -> BuiltContext:
        q = analyze(message)
        items: list[ContextItem] = []
        warnings: list[str] = []

        profile_text = self._profile_block(profile, q, items)

        selected: list[tuple[Memory, float]] = []
        focus: list[LifeAreaFocus] = []
        if brain is not None:
            try:
                selected = self.select_memories(brain, profile.user_id, q, history_source)
                focus = [f for f in brain.get_life_area_focus(profile.user_id, limit=3) if f.importance >= 2]
            except BrainError:
                warnings.append("shared_brain_unavailable")
        if focus:
            line = "Life areas this user cares about most: " + ", ".join(
                f"{f.area} (asked {f.asked_count}x, {f.memory_count} memories)" for f in focus)
            profile_text += "\n" + line
            items.append(ContextItem(kind="life_area", ref=",".join(f.area for f in focus),
                                     tag="life_area_focus", text=line))
        for m, s in selected:
            items.append(ContextItem(kind="memory", ref=m.memory_id, tag=m.tag, text=m.render(), score=s))
        memories_text = "\n".join(f"- [{m.life_area} {m.type.value}] {m.render()}" for m, _ in selected) or "None relevant."

        n_turns = self.followup_turns if q.is_followup else self.recent_turns
        history = history_source[-n_turns:]
        messages = [{"role": t.role, "content": t.content[: self.turn_char_limit]} for t in history]
        messages.append({"role": "user", "content": message})
        summary_text = ""
        if summary:
            summary_text = "\n## Earlier in this conversation\n" + summary[: self.summary_char_limit] + "\n"
            items.append(ContextItem(kind="conversation", ref="summary", tag="conversation_summary",
                                     text=summary[:200]))
        if history:
            items.append(ContextItem(kind="conversation", ref=f"last_{len(history)}_turns",
                                     tag="recent_conversation", text=f"{len(history)} recent turns"))

        system = CHAT_SYSTEM.format(language=profile.preferred_language, today=today.isoformat(),
                                    profile=profile_text, memories=memories_text, summary=summary_text)
        return BuiltContext(system=system, messages=messages, items=items,
                            memory_ids=[m.memory_id for m, _ in selected], analysis=q, warnings=warnings)
