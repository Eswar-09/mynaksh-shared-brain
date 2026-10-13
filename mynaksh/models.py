"""Pydantic models shared across the API, services and storage layers."""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time, timezone
from enum import Enum
from typing import Literal, Union

from pydantic import BaseModel, Field, field_validator

ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,64}$"

LIFE_AREAS = (
    "career",
    "relationships",
    "health",
    "finance",
    "education",
    "family",
    "travel",
    "spirituality",
    "personal",
)

Scalar = Union[str, int, float, bool]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:48] or "item"


# --------------------------------------------------------------------------- profile


class UserProfileIn(BaseModel):
    """Payload for creating or updating a profile. Omitted fields are left unchanged."""

    user_id: str = Field(pattern=ID_PATTERN)
    name: str | None = Field(None, min_length=1, max_length=100)
    date_of_birth: date | None = None
    time_of_birth: time | None = None
    birth_place: str | None = Field(None, min_length=1, max_length=100)
    preferred_language: str | None = Field(None, min_length=2, max_length=30)

    @field_validator("date_of_birth")
    @classmethod
    def _not_in_future(cls, v: date | None) -> date | None:
        if v is not None and v > date.today():
            raise ValueError("date_of_birth cannot be in the future")
        return v


class UserProfile(BaseModel):
    user_id: str
    name: str | None = None
    date_of_birth: date | None = None
    time_of_birth: time | None = None
    birth_place: str | None = None
    preferred_language: str = "English"
    zodiac_sign: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def missing_fields(self) -> list[str]:
        fields = ("name", "date_of_birth", "time_of_birth", "birth_place")
        return [f for f in fields if getattr(self, f) is None]


# --------------------------------------------------------------------------- memory


class MemoryType(str, Enum):
    GOAL = "goal"
    INTEREST = "interest"
    PREFERENCE = "preference"
    LIFE_EVENT = "life_event"
    CONCERN = "concern"
    FACT = "fact"


class MemoryStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETIRED = "retired"


class Memory(BaseModel):
    """One durable fact about a user, stored as a node in the Shared Brain."""

    memory_id: str = Field(default_factory=lambda: f"mem_{uuid.uuid4().hex[:12]}")
    user_id: str
    type: MemoryType
    key: str  # stable identity, e.g. "goal:career_change"
    value: str
    life_area: str = "personal"
    attributes: dict[str, Scalar] = Field(default_factory=dict)
    confidence: float = Field(0.8, ge=0.0, le=1.0)
    status: MemoryStatus = MemoryStatus.ACTIVE
    source_session: str | None = None
    mention_count: int = 1
    supersedes: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def tag(self) -> str:
        """Human-readable category used in `context_used`, e.g. `career_goal`."""
        return f"{self.life_area}_{self.type.value}"

    def render(self) -> str:
        if not self.attributes:
            return self.value
        attrs = ", ".join(f"{k}: {v}" for k, v in self.attributes.items())
        return f"{self.value} ({attrs})"


class ExtractedMemory(BaseModel):
    """Validated output of the memory extractor, before it is reconciled with the graph."""

    action: Literal["upsert", "retire"] = "upsert"
    type: MemoryType
    key: str = ""
    value: str = Field(min_length=1, max_length=200)
    life_area: str = "personal"
    attributes: dict[str, Scalar] = Field(default_factory=dict)
    confidence: float = Field(0.7, ge=0.0, le=1.0)

    @field_validator("life_area")
    @classmethod
    def _known_area(cls, v: str) -> str:
        v = (v or "").strip().lower()
        return v if v in LIFE_AREAS else "personal"

    @field_validator("attributes")
    @classmethod
    def _small_attrs(cls, v: dict) -> dict:
        return {slugify(k): val for k, val in list(v.items())[:6] if val not in (None, "")}

    def normalized_key(self) -> str:
        key = (self.key or "").strip().lower()
        if re.fullmatch(r"[a-z_]+:[a-z0-9_]+", key):
            prefix, _, topic = key.partition(":")
            return f"{self.type.value}:{topic}" if prefix != self.type.value else key
        return f"{self.type.value}:{slugify(self.value)}"


# --------------------------------------------------------------------------- conversation


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime = Field(default_factory=utcnow)
    context_ids: list[str] = Field(default_factory=list)  # memories used to produce this turn


class ChatRequest(BaseModel):
    user_id: str = Field(pattern=ID_PATTERN)
    session_id: str = Field(pattern=ID_PATTERN)
    message: str = Field(min_length=1, max_length=4000)

    @field_validator("message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message must not be blank")
        return v


class ContextItem(BaseModel):
    kind: Literal["profile", "memory", "conversation"]
    ref: str  # memory_id, profile field group, or turn range
    tag: str
    text: str
    score: float | None = None


class MemoryUpdate(BaseModel):
    action: Literal["created", "reinforced", "updated", "retired", "profile_updated"]
    key: str
    value: str
    memory_id: str | None = None
    previous_value: str | None = None


class ChatResponse(BaseModel):
    response: str
    user_id: str
    session_id: str
    context_used: list[str]
    context_details: list[ContextItem] = Field(default_factory=list)
    memory_updates: list[MemoryUpdate] = Field(default_factory=list)
    memory_status: Literal["saved", "nothing_to_save", "skipped", "failed"] = "nothing_to_save"
    missing_profile_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
