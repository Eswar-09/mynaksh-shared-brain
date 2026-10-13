"""Thread-safe in-process Shared Brain. Used for tests and keyless local development."""
from __future__ import annotations

import threading

from mynaksh.brain.base import BrainError, SharedBrain
from mynaksh.models import LifeAreaFocus, Memory, MemoryStatus, MemoryType, UserProfile, utcnow


class InMemoryBrain(SharedBrain):
    def __init__(self) -> None:
        self._profiles: dict[str, UserProfile] = {}
        self._memories: dict[str, Memory] = {}
        self._area_asks: dict[str, dict[str, LifeAreaFocus]] = {}
        self._lock = threading.RLock()
        self.fail = False  # toggled by tests to simulate an outage

    def _check(self) -> None:
        if self.fail:
            raise BrainError("simulated shared brain outage")

    def ping(self) -> None:
        self._check()

    def get_profile(self, user_id: str) -> UserProfile | None:
        self._check()
        with self._lock:
            p = self._profiles.get(user_id)
            return p.model_copy(deep=True) if p else None

    def save_profile(self, profile: UserProfile) -> None:
        self._check()
        with self._lock:
            self._profiles[profile.user_id] = profile.model_copy(deep=True)

    def _user_memories(self, user_id: str) -> list[Memory]:
        return [m for m in self._memories.values() if m.user_id == user_id]

    def get_active_memory_by_key(self, user_id: str, key: str) -> Memory | None:
        self._check()
        with self._lock:
            for m in self._user_memories(user_id):
                if m.key == key and m.status == MemoryStatus.ACTIVE:
                    return m.model_copy(deep=True)
        return None

    def list_memories(self, user_id, status=MemoryStatus.ACTIVE, limit=200):
        self._check()
        with self._lock:
            rows = [m for m in self._user_memories(user_id) if status is None or m.status == status]
        rows.sort(key=lambda m: m.updated_at, reverse=True)
        return [m.model_copy(deep=True) for m in rows[:limit]]

    def find_candidate_memories(self, user_id, life_areas, limit=50):
        rows = self.list_memories(user_id, MemoryStatus.ACTIVE, limit=10_000)
        if life_areas is not None:
            rows = [
                m for m in rows if m.life_area in life_areas or m.type == MemoryType.PREFERENCE
            ]
        return rows[:limit]

    def save_memory(self, memory: Memory) -> None:
        self._check()
        with self._lock:
            self._memories[memory.memory_id] = memory.model_copy(deep=True)

    def supersede_memory(self, old_memory_id: str, new_memory: Memory) -> None:
        self._check()
        with self._lock:
            old = self._memories.get(old_memory_id)
            if old is None:
                raise BrainError(f"memory {old_memory_id} not found")
            old.status = MemoryStatus.SUPERSEDED
            old.updated_at = utcnow()
            new_memory.supersedes = old_memory_id
            self._memories[new_memory.memory_id] = new_memory.model_copy(deep=True)

    def record_life_area_interest(self, user_id: str, areas: set[str]) -> None:
        self._check()
        with self._lock:
            user = self._area_asks.setdefault(user_id, {})
            for area in areas:
                f = user.setdefault(area, LifeAreaFocus(area=area))
                f.asked_count += 1
                f.last_asked = utcnow()

    def get_life_area_focus(self, user_id: str, limit: int = 5) -> list[LifeAreaFocus]:
        self._check()
        with self._lock:
            focus = {a: f.model_copy() for a, f in self._area_asks.get(user_id, {}).items()}
            for m in self._user_memories(user_id):
                if m.status == MemoryStatus.ACTIVE:
                    focus.setdefault(m.life_area, LifeAreaFocus(area=m.life_area)).memory_count += 1
        ranked = sorted(focus.values(), key=lambda f: (f.importance, f.asked_count), reverse=True)
        return ranked[:limit]
