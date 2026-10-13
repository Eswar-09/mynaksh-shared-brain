"""App factory. Run with: uvicorn mynaksh.main:create_app --factory"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from mynaksh.api.routes import router
from mynaksh.brain.base import BrainError, SharedBrain
from mynaksh.brain.in_memory import InMemoryBrain
from mynaksh.config import Settings
from mynaksh.llm.base import LLMClient, LLMError
from mynaksh.llm.factory import build_llms
from mynaksh.services.chat_service import ChatService
from mynaksh.sessions import InMemorySessionStore, SessionConflict, SessionStore

log = logging.getLogger("mynaksh")

LLM_FALLBACK_MESSAGE = (
    "I'm having trouble reaching my guidance engine right now. "
    "Your message was not lost; please try again in a moment."
)


def _build_brain(s: Settings) -> SharedBrain:
    if s.brain_backend == "neo4j":
        from mynaksh.brain.neo4j_store import Neo4jBrain
        return Neo4jBrain(s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database)
    if s.brain_backend == "memory":
        return InMemoryBrain()
    raise ValueError(f"unknown BRAIN_BACKEND {s.brain_backend!r}")


def create_app(brain: SharedBrain | None = None, sessions: SessionStore | None = None,
               llm: LLMClient | None = None, extraction_llm: LLMClient | None = None,
               settings: Settings | None = None) -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = settings or Settings.from_env()
    if llm is None:
        llm, default_extract = build_llms(s)
        extraction_llm = extraction_llm or default_extract
    brain = brain or _build_brain(s)
    service = ChatService(brain, sessions or InMemorySessionStore(), llm, extraction_llm)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if hasattr(brain, "init_schema"):
            try:
                brain.init_schema()
            except BrainError as exc:  # keep serving; /health reports degraded
                log.error("could not initialise graph schema: %s", exc)
        yield
        brain.close()

    app = FastAPI(title="MyNaksh Shared Brain Chat", version="1.0.0", lifespan=lifespan)
    app.state.chat_service = service
    app.include_router(router)

    @app.exception_handler(LLMError)
    async def _llm_down(_: Request, exc: LLMError):
        log.error("LLM failure: %s", exc)
        return JSONResponse(status_code=503, content={
            "error": "llm_unavailable", "fallback_response": LLM_FALLBACK_MESSAGE})

    @app.exception_handler(SessionConflict)
    async def _session_conflict(_: Request, exc: SessionConflict):
        return JSONResponse(status_code=409, content={
            "error": "session_conflict", "detail": f"session {exc} belongs to another user"})

    return app
