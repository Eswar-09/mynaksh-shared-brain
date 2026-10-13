from __future__ import annotations

import os
from datetime import date

import pytest
from fastapi.testclient import TestClient

from mynaksh.brain.in_memory import InMemoryBrain
from mynaksh.config import Settings
from mynaksh.llm.fake import FakeLLM
from mynaksh.main import create_app
from mynaksh.sessions import InMemorySessionStore


def neo4j_settings() -> dict | None:
    uri = os.getenv("NEO4J_TEST_URI")
    if not uri:
        return None
    return {"uri": uri, "user": os.getenv("NEO4J_USER", "neo4j"),
            "password": os.getenv("NEO4J_PASSWORD", "mynaksh-dev")}


class Harness:
    def __init__(self, brain=None) -> None:
        self.brain = brain or InMemoryBrain()
        self.llm = FakeLLM()
        self.app = create_app(brain=self.brain, sessions=InMemorySessionStore(), llm=self.llm,
                              settings=Settings(llm_provider="fake"))
        self.app.state.chat_service.today = lambda: date(2026, 10, 13)
        self.client = TestClient(self.app)

    def chat(self, message: str, user: str = "rahul", session: str = "s1", expect: int = 200) -> dict:
        r = self.client.post("/chat", json={"user_id": user, "session_id": session, "message": message})
        assert r.status_code == expect, r.text
        return r.json()

    def memories(self, user: str = "rahul", history: bool = False) -> list[dict]:
        return self.client.get(f"/users/{user}/memories", params={"include_history": history}).json()

    @property
    def last_prompt(self) -> str:
        return self.llm.chat_calls[-1]["system"]


def _backends():
    yield pytest.param("memory", id="memory")
    yield pytest.param("neo4j", id="neo4j",
                       marks=pytest.mark.skipif(neo4j_settings() is None, reason="NEO4J_TEST_URI not set"))


@pytest.fixture(params=list(_backends()))
def h(request) -> Harness:
    """Scenario harness, run once per Shared Brain backend."""
    if request.param == "memory":
        return Harness()
    from mynaksh.brain.neo4j_store import Neo4jBrain
    brain = Neo4jBrain(**neo4j_settings())
    brain.init_schema()
    brain._query("MATCH (n) WHERE n:User OR n:Memory DETACH DELETE n", write=True)
    return Harness(brain)


@pytest.fixture
def hm() -> Harness:
    """In-memory harness for failure injection tests."""
    return Harness()
