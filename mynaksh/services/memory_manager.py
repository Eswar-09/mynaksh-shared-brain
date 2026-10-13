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
from mynaksh.models import ExtractedMemory, Memory, MemoryStatus, MemoryUpdate, utcnow
from mynaksh.services.prompts import EXTRACTION_SYSTEM

log = logging.getLogger(__name__)

_TRIVIAL = re.compile(
    r"^(hi|hello|hey|namaste|thanks?|thank you|ok(ay)?|cool|great|nice|bye|good (morning|night)|yes|no|sure)\b[\s!.]*$",
    re.I,
)
_FIRST_PERSON = re.compile(r"\b(i|i'm|im|i've|i'll|i'd|my|me|mine|we|our)\b", re.I)
_QUESTION_START = re.compile(
    r"^(what|why|how|when|where|who|which|should|could|would|can|will|do|does|did|is|are|am|tell me|explain)\b",
    re.I,
)


def worth_extracting(message: str) -> bool:
    """Cheap gate so questions and small talk never cost an extraction call."""
    text = message.strip()
    if len(text.split()) < 3 or _TRIVIAL.match(text):
        return False
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        s = sentence.strip()
        if s and _FIRST_PERSON.search(s) and not _QUESTION_START.match(s):
            return True
    return False


class MemoryManager:
    def __init__(self, brain: SharedBrain, llm: LLMClient, min_confidence: float = 0.6,
                 max_per_turn: int = 5) -> None:
        self.brain = brain
        self.llm = llm
        self.min_confidence = min_confidence
        self.max_per_turn = max_per_turn

    def extract(self, message: str, existing: list[Memory], today: date) -> list[ExtractedMemory]:
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
        return [e for e in valid if e.confidence >= self.min_confidence][: self.max_per_turn]

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
