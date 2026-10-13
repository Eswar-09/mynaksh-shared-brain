"""Deterministic offline LLM.

Used by the test-suite and when no API key is configured. Chat replies echo the
context they were given (so behaviour is inspectable), and extraction uses small
regex heuristics that mirror what the real extraction prompt asks for.
"""
from __future__ import annotations

import json
import re
from datetime import date

from mynaksh.llm.base import LLMClient, LLMError
from mynaksh.models import slugify
from mynaksh.services.prompts import EXTRACTION_MARKER

_INTEREST_AREAS = {
    "career": ("entrepreneur", "startup", "business", "product", "coding", "leadership"),
    "spirituality": ("meditation", "spiritual", "astrology", "vedic", "mantra"),
    "health": ("yoga", "fitness", "running", "gym"),
    "finance": ("investing", "stock", "crypto", "trading"),
}


def _area_for(text: str) -> str:
    low = text.lower()
    for area, words in _INTEREST_AREAS.items():
        if any(w in low for w in words):
            return area
    return "personal"


def _time_attrs(text: str, today: date) -> dict:
    low = text.lower()
    if m := re.search(r"\b(?:in|by)\s+(20\d\d)\b", low):
        return {"target_year": int(m.group(1))}
    if "next year" in low:
        return {"target_year": today.year + 1}
    if "next month" in low:
        month = today.month % 12 + 1
        year = today.year + (1 if today.month == 12 else 0)
        return {"timeframe": f"{year}-{month:02d}"}
    if "this year" in low:
        return {"target_year": today.year}
    return {}


def heuristic_extract(message: str, today: date) -> list[dict]:
    low = message.lower()
    out: list[dict] = []

    if re.search(r"\b(no longer|not)\b[^.]*\b(switch|chang|leav|quit)\w*\b[^.]*\b(job|career)", low) or re.search(
        r"\bdecided to stay\b", low
    ):
        out.append({"action": "retire", "type": "goal", "key": "goal:career_change",
                    "value": "Career change", "life_area": "career", "confidence": 0.9})
    elif re.search(r"\b(switch|chang|leav|quit)\w*\s+(my\s+|a\s+)?(job|jobs|career|company)", low):
        out.append({"type": "goal", "key": "goal:career_change", "value": "Career change",
                    "life_area": "career", "attributes": _time_attrs(message, today), "confidence": 0.9})

    if m := re.search(r"preparing for (?:an?|my)\s+(.+?)\s+interview", low):
        topic = m.group(1).strip()
        out.append({"type": "goal", "key": f"goal:{slugify(topic)}_interview",
                    "value": f"{topic.title()} interview", "life_area": "career",
                    "attributes": _time_attrs(message, today), "confidence": 0.9})

    if m := re.search(r"\b(?:interested in|passionate about|i love|my hobby is)\s+([a-z][a-z\s-]{2,40}?)(?:[.,!?]|\band\b|$)", low):
        topic = m.group(1).strip()
        out.append({"type": "interest", "key": f"interest:{slugify(topic)}", "value": topic.title(),
                    "life_area": _area_for(topic), "confidence": 0.85})

    if m := re.search(r"\b(getting married|wedding)\b", low):
        out.append({"type": "life_event", "key": "life_event:marriage", "value": "Upcoming marriage",
                    "life_area": "relationships", "attributes": _time_attrs(message, today),
                    "confidence": 0.85})

    if re.search(r"\b(keep (it|answers) short|short answers|be brief)\b", low):
        out.append({"type": "preference", "key": "preference:answer_length",
                    "value": "Prefers short answers", "life_area": "personal", "confidence": 0.9})

    if re.search(r"\b(worried|anxious|stressed) about (my )?(health|money|finances|job)", low):
        topic = re.search(r"about (?:my )?(health|money|finances|job)", low).group(1)
        area = {"health": "health", "money": "finance", "finances": "finance", "job": "career"}[topic]
        out.append({"type": "concern", "key": f"concern:{area}", "value": f"Worried about {topic}",
                    "life_area": area, "confidence": 0.75})
    return out


class FakeLLM(LLMClient):
    name = "fake"

    def __init__(self, fail: bool = False, fail_extraction: bool = False) -> None:
        self.fail = fail
        self.fail_extraction = fail_extraction
        self.calls: list[dict] = []

    @property
    def chat_calls(self) -> list[dict]:
        return [c for c in self.calls if EXTRACTION_MARKER not in c["system"]]

    @property
    def extraction_calls(self) -> list[dict]:
        return [c for c in self.calls if EXTRACTION_MARKER in c["system"]]

    def generate(self, system, messages, *, max_tokens=600):
        self.calls.append({"system": system, "messages": messages})
        is_extraction = EXTRACTION_MARKER in system
        if self.fail or (is_extraction and self.fail_extraction):
            raise LLMError("simulated provider outage")
        if is_extraction:
            payload = json.loads(messages[-1]["content"])
            today = date.fromisoformat(payload["today"])
            return json.dumps({"memories": heuristic_extract(payload["message"], today)})
        return self._reply(system, messages)

    @staticmethod
    def _reply(system: str, messages: list[dict]) -> str:
        known = system.split("## Known about the user (selected for this question)")[-1]
        facts = [ln[2:].strip() for ln in known.splitlines() if ln.startswith("- ")]
        question = messages[-1]["content"]
        parts = [f"[offline demo reply] You asked: \"{question}\"."]
        if len(messages) > 1:
            parts.append("Building on what we discussed earlier in this conversation.")
        if facts:
            parts.append("Keeping in mind: " + "; ".join(facts) + ".")
        return " ".join(parts)
