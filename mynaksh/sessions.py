"""Short-term conversation context.

Kept separate from the Shared Brain on purpose: raw turns are high-volume and
short-lived, while the graph holds only distilled long-term facts. Turns that
scroll out of the prompt window are folded into a rolling summary. In production
this interface would be backed by Redis with a TTL.
"""
from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from collections import OrderedDict, deque
from dataclasses import dataclass

from mynaksh.models import Turn


class SessionConflict(Exception):
    """A session_id is already bound to a different user."""


class SessionStore(ABC):
    @abstractmethod
    def owner(self, session_id: str) -> str | None: ...

    @abstractmethod
    def append(self, session_id: str, user_id: str, turn: Turn) -> None: ...

    @abstractmethod
    def recent(self, session_id: str, limit: int) -> list[Turn]: ...

    @abstractmethod
    def total(self, session_id: str) -> int:
        """Number of turns ever appended to the session."""

    @abstractmethod
    def turns_between(self, session_id: str, start: int, end: int) -> list[Turn]:
        """Turns with absolute index in [start, end) that are still retained."""

    @abstractmethod
    def get_summary(self, session_id: str) -> tuple[str, int]:
        """(summary text, number of leading turns it covers)."""

    @abstractmethod
    def set_summary(self, session_id: str, text: str, covered: int) -> None: ...


@dataclass
class _Session:
    user_id: str
    turns: deque
    last_seen: float
    total: int = 0
    summary: str = ""
    summarized: int = 0


class InMemorySessionStore(SessionStore):
    def __init__(self, max_turns: int = 40, ttl_seconds: int = 6 * 3600, max_sessions: int = 10_000):
        self._data: OrderedDict[str, _Session] = OrderedDict()
        self._max_turns = max_turns
        self._ttl = ttl_seconds
        self._max_sessions = max_sessions
        self._lock = threading.Lock()

    def _get(self, session_id: str) -> _Session | None:
        entry = self._data.get(session_id)
        if entry and time.monotonic() - entry.last_seen > self._ttl:
            del self._data[session_id]
            return None
        return entry

    def owner(self, session_id: str) -> str | None:
        with self._lock:
            entry = self._get(session_id)
            return entry.user_id if entry else None

    def append(self, session_id: str, user_id: str, turn: Turn) -> None:
        with self._lock:
            entry = self._get(session_id)
            if entry and entry.user_id != user_id:
                raise SessionConflict(session_id)
            if entry is None:
                entry = _Session(user_id, deque(maxlen=self._max_turns), time.monotonic())
            entry.turns.append((entry.total, turn))
            entry.total += 1
            entry.last_seen = time.monotonic()
            self._data[session_id] = entry
            self._data.move_to_end(session_id)
            while len(self._data) > self._max_sessions:
                self._data.popitem(last=False)

    def recent(self, session_id: str, limit: int) -> list[Turn]:
        with self._lock:
            entry = self._get(session_id)
            if not entry or limit <= 0:
                return []
            return [t for _, t in list(entry.turns)[-limit:]]

    def total(self, session_id: str) -> int:
        with self._lock:
            entry = self._get(session_id)
            return entry.total if entry else 0

    def turns_between(self, session_id: str, start: int, end: int) -> list[Turn]:
        with self._lock:
            entry = self._get(session_id)
            return [t for i, t in entry.turns if start <= i < end] if entry else []

    def get_summary(self, session_id: str) -> tuple[str, int]:
        with self._lock:
            entry = self._get(session_id)
            return (entry.summary, entry.summarized) if entry else ("", 0)

    def set_summary(self, session_id: str, text: str, covered: int) -> None:
        with self._lock:
            entry = self._get(session_id)
            if entry:
                entry.summary, entry.summarized = text, covered
