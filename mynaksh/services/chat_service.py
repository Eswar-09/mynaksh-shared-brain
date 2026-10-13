"""Chat -> Context Selection -> Shared Brain -> LLM -> Response -> Memory Update."""
from __future__ import annotations

import logging
from datetime import date
from typing import Callable

from mynaksh.brain.base import BrainError, SharedBrain
from mynaksh.llm.base import LLMClient, LLMError
from mynaksh.models import ChatRequest, ChatResponse, MemoryUpdate, Turn, UserProfile
from mynaksh.services.context_builder import ContextBuilder
from mynaksh.services.memory_manager import MemoryManager, worth_extracting
from mynaksh.services.profile_extractor import apply_profile_facts, extract_profile_facts
from mynaksh.sessions import SessionConflict, SessionStore

log = logging.getLogger(__name__)


class ChatService:
    def __init__(self, brain: SharedBrain, sessions: SessionStore, llm: LLMClient,
                 extraction_llm: LLMClient | None = None, context_builder: ContextBuilder | None = None,
                 today: Callable[[], date] = date.today) -> None:
        self.brain = brain
        self.sessions = sessions
        self.llm = llm
        self.memory = MemoryManager(brain, extraction_llm or llm)
        self.context = context_builder or ContextBuilder()
        self.today = today

    def chat(self, req: ChatRequest) -> ChatResponse:
        warnings: list[str] = []
        owner = self.sessions.owner(req.session_id)
        if owner and owner != req.user_id:
            raise SessionConflict(req.session_id)

        # 1. Load the profile. A first message auto-creates an empty profile so
        #    users can introduce themselves in chat, as in the brief's example.
        brain_ok = True
        is_new = False
        try:
            profile = self.brain.get_profile(req.user_id)
            if profile is None:
                profile, is_new = UserProfile(user_id=req.user_id), True
        except BrainError:
            brain_ok = False
            profile = UserProfile(user_id=req.user_id)
            warnings.append("shared_brain_unavailable")

        # 2. Stage-1 memory: explicit profile facts and corrections, applied before
        #    answering so "I was born on ..." is used in this very reply.
        profile, profile_updates = apply_profile_facts(profile, extract_profile_facts(req.message))
        profile_saved = True
        if brain_ok and (is_new or profile_updates):
            try:
                self.brain.save_profile(profile)
            except BrainError:
                brain_ok, profile_saved = False, False
                warnings.append("shared_brain_unavailable")

        # 3. Context selection.
        history = self.sessions.recent(req.session_id, limit=self.context.followup_turns)
        ctx = self.context.build(req.message, profile, self.brain if brain_ok else None, history, self.today())
        warnings += [w for w in ctx.warnings if w not in warnings]

        # 4. Generate. LLMError propagates to the API layer (503); the turn is not
        #    recorded so a retry does not duplicate history.
        reply = self.llm.generate(ctx.system, ctx.messages)

        # 5. Short-term memory.
        self.sessions.append(req.session_id, req.user_id, Turn(role="user", content=req.message))
        self.sessions.append(req.session_id, req.user_id,
                             Turn(role="assistant", content=reply, context_ids=ctx.memory_ids))

        # 6. Long-term memory update. Failures never block the reply, but are reported.
        updates: list[MemoryUpdate] = list(profile_updates) if profile_saved and brain_ok else []
        status = "saved" if updates else "nothing_to_save"
        if not brain_ok:
            status = "failed" if (profile_updates or worth_extracting(req.message)) else "skipped"
        elif worth_extracting(req.message):
            try:
                existing = self.brain.list_memories(req.user_id, limit=50)
                extracted = self.memory.extract(req.message, existing, self.today())
                mem_updates = self.memory.apply(req.user_id, req.session_id, extracted)
                updates += mem_updates
                status = "saved" if updates else "nothing_to_save"
            except LLMError as exc:
                log.warning("memory extraction failed: %s", exc)
                warnings.append("memory_extraction_failed")
                status = "saved" if updates else "failed"
            except BrainError as exc:
                log.warning("memory write failed: %s", exc)
                warnings.append("memory_write_failed")
                status = "failed"

        return ChatResponse(
            response=reply,
            user_id=req.user_id,
            session_id=req.session_id,
            context_used=ctx.tags,
            context_details=ctx.items,
            memory_updates=updates,
            memory_status=status,
            missing_profile_fields=profile.missing_fields(),
            warnings=warnings,
        )
