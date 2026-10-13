"""Storage contract for the Shared Brain. The rest of the app depends only on this."""
from __future__ import annotations

from abc import ABC, abstractmethod

from mynaksh.models import LifeAreaFocus, Memory, MemoryStatus, UserProfile


class BrainError(RuntimeError):
    """Raised when the underlying store is unreachable or a write fails."""


class SharedBrain(ABC):
    @abstractmethod
    def ping(self) -> None: ...

    # profile -----------------------------------------------------------------
    @abstractmethod
    def get_profile(self, user_id: str) -> UserProfile | None: ...

    @abstractmethod
    def save_profile(self, profile: UserProfile) -> None: ...

    # memories ----------------------------------------------------------------
    @abstractmethod
    def get_active_memory_by_key(self, user_id: str, key: str) -> Memory | None: ...

    @abstractmethod
    def list_memories(
        self, user_id: str, status: MemoryStatus | None = MemoryStatus.ACTIVE, limit: int = 200
    ) -> list[Memory]: ...

    @abstractmethod
    def find_candidate_memories(
        self, user_id: str, life_areas: set[str] | None, limit: int = 50
    ) -> list[Memory]:
        """Active memories in the given life areas, plus all preferences.

        `life_areas=None` means every active memory (used for "what do you remember" queries).
        Ordered most recently updated first.
        """

    @abstractmethod
    def save_memory(self, memory: Memory) -> None:
        """Insert or replace a memory by `memory_id`."""

    @abstractmethod
    def supersede_memory(self, old_memory_id: str, new_memory: Memory) -> None:
        """Atomically mark the old memory superseded and store its replacement."""

    # life areas ----------------------------------------------------------------
    @abstractmethod
    def record_life_area_interest(self, user_id: str, areas: set[str]) -> None:
        """Count that the user asked about these life areas."""

    @abstractmethod
    def get_life_area_focus(self, user_id: str, limit: int = 5) -> list[LifeAreaFocus]:
        """Life areas ranked by importance: questions asked plus active memories held."""

    def close(self) -> None:  # noqa: B027 - optional hook, default no-op
        return None
