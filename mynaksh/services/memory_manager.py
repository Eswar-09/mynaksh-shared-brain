"""Stages 2 and 3 of memory: LLM extraction of durable facts, then validated reconciliation.

Reconciliation rules, keyed on (user_id, key):
  * no active memory with the key        -> create
  * same value, no conflicting attribute -> reinforce (bump mention_count / confidence)
  * value or an attribute differs        -> supersede (old kept as history, linked)
  * action == retire                     -> mark the active memory retired
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date

from pydantic import ValidationError

from mynaksh.brain.base import SharedBrain
from mynaksh.llm.base import LLMClient
from mynaksh.models import ExtractedMemory, ExtractedProfile, Memory, MemoryStatus, MemoryUpdate, utcnow
from mynaksh.services.prompts import EXTRACTION_SYSTEM

log = logging.getLogger(__name__)

_TRIVIAL = re.compile(
    r"^(hi|hello|hey|namaste|thanks?|thank you|ok(ay)?|cool|great|nice|bye|good (morning|night)|yes|no|sure|"
    r"shukriya|dhanyavaad|theek hai|accha)\b[\s!.]*$",
    re.I,
)
# English and romanised Hindi first-person markers. Devanagari is matched as substrings because
# Python's \b does not treat Devanagari vowel signs as word characters.
_FIRST_PERSON = re.compile(
    r"\b(i|i'm|im|i've|i'll|i'd|my|me|mine|we|our|mera|meri|mere|main|mujhe|maine|hum|humara)\b", re.I)
_FIRST_PERSON_HI = ("मेरा", "मेरी", "मेरे", "मैं", "मुझे", "मैंने", "हम", "हमारा")
_QUESTION_START = re.compile(
    r"^(what|why|how|when|where|who|which|should|could|would|can|will|do|does|did|is|are|am|tell me|explain|"
    r"kya|kyun|kaise|kab|kahan)\b",
    re.I,
)
_HINDI_QUESTION = re.compile(r"(क्या|क्यों|कैसे|कैसा|कैसी|कब|कहाँ|\bkya\b|\bkyun\b|\bkaise\b|\bkaisa\b)", re.I)
# Explicit requests to remember are always worth extracting, even when phrased as a question.
_REMEMBER_REQUEST = re.compile(r"\b(remember|note( down)?|keep in mind|don'?t forget|yaad rakh)\w*\b", re.I)


def _is_first_person(sentence: str) -> bool:
    return bool(_FIRST_PERSON.search(sentence)) or any(w in sentence for w in _FIRST_PERSON_HI)


def _is_question(sentence: str) -> bool:
    if _QUESTION_START.match(sentence):
        return True
    return sentence.endswith("?") and bool(_HINDI_QUESTION.search(sentence))


def worth_extracting(message: str) -> bool:
    """Cheap gate so questions and small talk never cost an extraction call."""
    text = message.strip()
    if len(text.split()) < 2 or _TRIVIAL.match(text):
        return False
    if _REMEMBER_REQUEST.search(text) and _is_first_person(text) and not re.search(
        r"\bwhat (do|did) you remember\b|\bdo you remember\b", text, re.I
    ):
        return True
    for sentence in re.split(r"(?<=[.!?।])\s+", text):
        s = sentence.strip()
        if s and _is_first_person(s) and not _is_question(s):
            return True
    return False


class MemoryManager:
    def __init__(self, brain: SharedBrain, llm: LLMClient, min_confidence: float = 0.6,
                 max_per_turn: int = 5) -> None:
        self.brain = brain
        self.llm = llm
        self.min_confidence = min_confidence
        self.max_per_turn = max_per_turn

    def extract(self, message: str, existing: list[Memory], today: date) -> tuple[list[ExtractedMemory], dict]:
        """Return (validated memories, validated profile facts) from one LLM call."""
        payload = {
            "today": today.isoformat(),
            "message": message,
            "existing_memories": [
                {"key": m.key, "value": m.value, "attributes": m.attributes} for m in existing[:30]
            ],
        }
        raw = self.llm.generate_json(EXTRACTION_SYSTEM, json.dumps(payload))
        items = raw.get("memories") or []
        valid: list[ExtractedMemory] = []
        for item in items if isinstance(items, list) else []:
            try:
                valid.append(ExtractedMemory.model_validate(item))
            except ValidationError as exc:
                log.info("dropping invalid extracted memory %s: %s", item, exc.errors()[:1])
        memories = [e for e in valid if e.confidence >= self.min_confidence][: self.max_per_turn]
        return memories, ExtractedProfile.lenient(raw.get("profile"))

    def apply(self, user_id: str, session_id: str, extracted: list[ExtractedMemory]) -> list[MemoryUpdate]:
        updates: list[MemoryUpdate] = []
        seen: set[str] = set()
        for e in extracted:
            key = e.normalized_key()
            if key in seen:
                continue
            seen.add(key)
            current = self.brain.get_active_memory_by_key(user_id, key)

            if e.action == "retire":
                if current:
                    current.status = MemoryStatus.RETIRED
                    current.updated_at = utcnow()
                    self.brain.save_memory(current)
                    updates.append(MemoryUpdate(action="retired", key=key, value=current.render(),
                                                memory_id=current.memory_id))
                continue

            if current is None:
                mem = Memory(user_id=user_id, type=e.type, key=key, value=e.value, life_area=e.life_area,
                             attributes=e.attributes, confidence=e.confidence, source_session=session_id)
                self.brain.save_memory(mem)
                updates.append(MemoryUpdate(action="created", key=key, value=mem.render(), memory_id=mem.memory_id))
                continue

            same_value = current.value.strip().lower() == e.value.strip().lower()
            conflicts = {k for k, v in e.attributes.items() if k in current.attributes and current.attributes[k] != v}
            merged_attrs = {**current.attributes, **e.attributes}

            if same_value and not conflicts:
                current.attributes = merged_attrs
                current.mention_count += 1
                current.confidence = max(current.confidence, e.confidence)
                current.updated_at = utcnow()
                self.brain.save_memory(current)
                updates.append(MemoryUpdate(action="reinforced", key=key, value=current.render(),
                                            memory_id=current.memory_id))
            else:
                new = Memory(user_id=user_id, type=e.type, key=key, value=e.value, life_area=e.life_area,
                             attributes=merged_attrs, confidence=e.confidence, source_session=session_id,
                             mention_count=current.mention_count + 1, created_at=current.created_at)
                self.brain.supersede_memory(current.memory_id, new)
                updates.append(MemoryUpdate(action="updated", key=key, value=new.render(),
                                            memory_id=new.memory_id, previous_value=current.render()))
        return updates
