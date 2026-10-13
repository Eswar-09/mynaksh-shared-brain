"""Short-term conversation context.

Kept separate from the Shared Brain on purpose: raw turns are high-volume and
short-lived, while the graph holds only distilled long-term facts. In production
this interface would be backed by Redis with a TTL.
"""
from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from collections import OrderedDict, deque

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


class InMemorySessionStore(SessionStore):
    def __init__(self, max_turns: int = 40, ttl_seconds: int = 6 * 3600, max_sessions: int = 10_000):
        self._data: OrderedDict[str, tuple[str, deque[Turn], float]] = OrderedDict()
        self._max_turns = max_turns
        self._ttl = ttl_seconds
        self._max_sessions = max_sessions
        self._lock = threading.Lock()

    def _get(self, session_id: str):
        entry = self._data.get(session_id)
        if entry and time.monotonic() - entry[2] > self._ttl:
            del self._data[session_id]
            return None
        return entry

    def owner(self, session_id: str) -> str | None:
        with self._lock:
            entry = self._get(session_id)
            return entry[0] if entry else None

    def append(self, session_id: str, user_id: str, turn: Turn) -> None:
        with self._lock:
            entry = self._get(session_id)
            if entry and entry[0] != user_id:
                raise SessionConflict(session_id)
            turns = entry[1] if entry else deque(maxlen=self._max_turns)
            turns.append(turn)
            self._data[session_id] = (user_id, turns, time.monotonic())
            self._data.move_to_end(session_id)
            while len(self._data) > self._max_sessions:
                self._data.popitem(last=False)

    def recent(self, session_id: str, limit: int) -> list[Turn]:
        with self._lock:
            entry = self._get(session_id)
            return list(entry[1])[-limit:] if entry and limit > 0 else []
