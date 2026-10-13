"""Same contract tests against every Shared Brain backend.

Neo4j runs only when NEO4J_TEST_URI is set, e.g.
    NEO4J_TEST_URI=bolt://localhost:7687 pytest tests/test_brain_contract.py
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest

from mynaksh.brain.in_memory import InMemoryBrain
from mynaksh.models import Memory, MemoryStatus, MemoryType, UserProfile
from tests.conftest import neo4j_settings


def _backends():
    yield pytest.param("memory", id="memory")
    yield pytest.param("neo4j", id="neo4j",
                       marks=pytest.mark.skipif(neo4j_settings() is None, reason="NEO4J_TEST_URI not set"))


@pytest.fixture(params=list(_backends()))
def brain(request):
    if request.param == "memory":
        yield InMemoryBrain()
        return
    from mynaksh.brain.neo4j_store import Neo4jBrain
    b = Neo4jBrain(**neo4j_settings())
    b.init_schema()
    yield b
    b.close()


@pytest.fixture
def uid():
    return f"test-{uuid.uuid4().hex[:8]}"


def test_profile_round_trip(brain, uid):
    p = UserProfile(user_id=uid, name="Rahul", date_of_birth=date(1995, 8, 15), birth_place="Delhi",
                    zodiac_sign="Leo", preferred_language="Hindi")
    brain.save_profile(p)
    got = brain.get_profile(uid)
    assert got.name == "Rahul" and got.date_of_birth == date(1995, 8, 15) and got.zodiac_sign == "Leo"
    brain.save_profile(got.model_copy(update={"birth_place": "Mumbai"}))
    assert brain.get_profile(uid).birth_place == "Mumbai"
    assert brain.get_profile(f"{uid}-missing") is None


def test_memory_lifecycle(brain, uid):
    brain.save_profile(UserProfile(user_id=uid))
    goal = Memory(user_id=uid, type=MemoryType.GOAL, key="goal:career_change", value="Career change",
                  life_area="career", attributes={"target_year": 2027})
    hobby = Memory(user_id=uid, type=MemoryType.INTEREST, key="interest:photography", value="Photography")
    pref = Memory(user_id=uid, type=MemoryType.PREFERENCE, key="preference:answer_length", value="Short answers")
    for m in (goal, hobby, pref):
        brain.save_memory(m)

    got = brain.get_active_memory_by_key(uid, "goal:career_change")
    assert got.memory_id == goal.memory_id and got.attributes == {"target_year": 2027}

    career = {m.key for m in brain.find_candidate_memories(uid, {"career"})}
    assert career == {"goal:career_change", "preference:answer_length"}
    assert len(brain.find_candidate_memories(uid, None)) == 3

    new = Memory(user_id=uid, type=MemoryType.GOAL, key="goal:career_change", value="Career change",
                 life_area="career", attributes={"target_year": 2028})
    brain.supersede_memory(goal.memory_id, new)
    active = brain.get_active_memory_by_key(uid, "goal:career_change")
    assert active.memory_id == new.memory_id and active.supersedes == goal.memory_id
    statuses = {m.memory_id: m.status for m in brain.list_memories(uid, status=None)}
    assert statuses[goal.memory_id] == MemoryStatus.SUPERSEDED
    assert len(brain.list_memories(uid)) == 3


def test_life_area_focus(brain, uid):
    brain.save_profile(UserProfile(user_id=uid))
    assert brain.get_life_area_focus(uid) == []
    brain.record_life_area_interest(uid, {"career"})
    brain.record_life_area_interest(uid, {"career", "health"})
    brain.save_memory(Memory(user_id=uid, type=MemoryType.LIFE_EVENT, key="life_event:marriage",
                             value="Upcoming marriage", life_area="relationships"))
    focus = {f.area: f for f in brain.get_life_area_focus(uid)}
    assert focus["career"].asked_count == 2 and focus["health"].asked_count == 1
    assert focus["relationships"].memory_count == 1 and focus["relationships"].asked_count == 0
    assert [f.area for f in brain.get_life_area_focus(uid)][0] == "career"


def test_neo4j_stores_astrology_attributes(brain, uid):
    if isinstance(brain, InMemoryBrain):
        pytest.skip("graph-only check")
    brain.save_profile(UserProfile(user_id=uid, date_of_birth=date(1990, 11, 1), zodiac_sign="Scorpio"))
    rows = brain._query(
        "MATCH (:User {user_id: $uid})-[:HAS_ZODIAC]->(z)-[:HAS_ELEMENT]->(e) "
        "MATCH (z)-[:RULED_BY]->(p) RETURN z.name AS z, e.name AS e, collect(p.name) AS planets",
        uid=uid)
    assert rows[0]["z"] == "Scorpio" and rows[0]["e"] == "Water"
    assert sorted(rows[0]["planets"]) == ["Mars", "Pluto"]
