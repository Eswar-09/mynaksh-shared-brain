"""Neo4j implementation of the Shared Brain.

Graph shape:
    (:User)-[:HAS_MEMORY]->(:Memory)-[:ABOUT]->(:LifeArea)
    (:Memory)-[:SUPERSEDES]->(:Memory)          correction history
    (:User)-[:FOCUSES_ON {asked_count, last_asked}]->(:LifeArea)
    (:User)-[:HAS_ZODIAC]->(:ZodiacSign)-[:HAS_ELEMENT]->(:Element)
                                        -[:RULED_BY]->(:Planet)
    (:User)-[:PREFERS_LANGUAGE]->(:Language)

LifeArea, ZodiacSign and Language are shared nodes, so cross-user questions
("which Leo users have career goals?") are a single traversal.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, time
from typing import Any

from neo4j import GraphDatabase, RoutingControl
from neo4j.exceptions import DriverError, Neo4jError

from mynaksh.brain.base import BrainError, SharedBrain
from mynaksh.astrology import astrology_attributes
from mynaksh.models import LifeAreaFocus, Memory, MemoryStatus, UserProfile, utcnow

log = logging.getLogger(__name__)

SCHEMA = [
    "CREATE CONSTRAINT user_id IF NOT EXISTS FOR (u:User) REQUIRE u.user_id IS UNIQUE",
    "CREATE CONSTRAINT memory_id IF NOT EXISTS FOR (m:Memory) REQUIRE m.memory_id IS UNIQUE",
    "CREATE CONSTRAINT life_area IF NOT EXISTS FOR (a:LifeArea) REQUIRE a.name IS UNIQUE",
    "CREATE CONSTRAINT zodiac IF NOT EXISTS FOR (z:ZodiacSign) REQUIRE z.name IS UNIQUE",
    "CREATE CONSTRAINT language IF NOT EXISTS FOR (l:Language) REQUIRE l.name IS UNIQUE",
    "CREATE CONSTRAINT element IF NOT EXISTS FOR (e:Element) REQUIRE e.name IS UNIQUE",
    "CREATE CONSTRAINT planet IF NOT EXISTS FOR (p:Planet) REQUIRE p.name IS UNIQUE",
    "CREATE INDEX memory_user_key IF NOT EXISTS FOR (m:Memory) ON (m.user_id, m.key, m.status)",
]

_SAVE_PROFILE = """
MERGE (u:User {user_id: $user_id})
SET u += $props
WITH u
OPTIONAL MATCH (u)-[r:HAS_ZODIAC|PREFERS_LANGUAGE]->()
DELETE r
WITH DISTINCT u
MERGE (l:Language {name: $language})
MERGE (u)-[:PREFERS_LANGUAGE]->(l)
FOREACH (_ IN CASE WHEN $zodiac IS NULL THEN [] ELSE [1] END |
  MERGE (z:ZodiacSign {name: $zodiac})
  SET z.element = $element, z.ruling_planets = $planets, z.traits = $traits
  MERGE (u)-[:HAS_ZODIAC]->(z)
  MERGE (e:Element {name: $element})
  MERGE (z)-[:HAS_ELEMENT]->(e)
  FOREACH (planet IN $planets |
    MERGE (p:Planet {name: planet})
    MERGE (z)-[:RULED_BY]->(p))
)
"""

_SAVE_MEMORY = """
MERGE (u:User {user_id: $user_id})
  ON CREATE SET u.created_at = $now, u.updated_at = $now, u.preferred_language = 'English'
MERGE (m:Memory {memory_id: $memory_id})
SET m += $props
MERGE (u)-[:HAS_MEMORY]->(m)
MERGE (a:LifeArea {name: $life_area})
WITH m, a
OPTIONAL MATCH (m)-[stale:ABOUT]->(other:LifeArea) WHERE other <> a
DELETE stale
WITH DISTINCT m, a
MERGE (m)-[:ABOUT]->(a)
"""

_CANDIDATES = """
MATCH (:User {user_id: $user_id})-[:HAS_MEMORY]->(m:Memory {status: 'active'})-[:ABOUT]->(a:LifeArea)
WHERE $areas IS NULL OR a.name IN $areas OR m.type = 'preference'
RETURN m {.*} AS m
ORDER BY m.updated_at DESC
LIMIT $limit
"""


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, (date, datetime, time)) else v


def _memory_props(m: Memory) -> dict[str, Any]:
    data = m.model_dump(mode="json")
    data["attributes"] = json.dumps(m.attributes)
    return data


def _memory_from_record(row: dict[str, Any]) -> Memory:
    row = dict(row)
    row["attributes"] = json.loads(row.get("attributes") or "{}")
    return Memory.model_validate(row)


class Neo4jBrain(SharedBrain):
    def __init__(self, uri: str, user: str, password: str, database: str | None = None) -> None:
        self._driver = GraphDatabase.driver(
            uri, auth=(user, password), connection_timeout=5.0, max_transaction_retry_time=5.0
        )
        self._db = database

    # helpers -----------------------------------------------------------------
    def _query(self, cypher: str, write: bool = False, **params: Any) -> list[dict[str, Any]]:
        try:
            records, _, _ = self._driver.execute_query(
                cypher,
                params,
                database_=self._db,
                routing_=RoutingControl.WRITE if write else RoutingControl.READ,
            )
        except (Neo4jError, DriverError, OSError) as exc:
            log.warning("neo4j query failed: %s", exc)
            raise BrainError(str(exc)) from exc
        return [r.data() for r in records]

    def init_schema(self) -> None:
        for stmt in SCHEMA:
            self._query(stmt, write=True)

    def close(self) -> None:
        self._driver.close()

    # contract ----------------------------------------------------------------
    def ping(self) -> None:
        self._query("RETURN 1 AS ok")

    def get_profile(self, user_id: str) -> UserProfile | None:
        rows = self._query("MATCH (u:User {user_id: $uid}) RETURN u {.*} AS u", uid=user_id)
        return UserProfile.model_validate(rows[0]["u"]) if rows else None

    def save_profile(self, profile: UserProfile) -> None:
        props = {k: _iso(v) for k, v in profile.model_dump().items() if k != "user_id"}
        astro = astrology_attributes(profile.zodiac_sign)
        self._query(
            _SAVE_PROFILE,
            write=True,
            user_id=profile.user_id,
            props=props,
            language=profile.preferred_language,
            zodiac=profile.zodiac_sign,
            element=astro.get("element"),
            planets=astro.get("ruling_planets", []),
            traits=astro.get("traits"),
        )

    def get_active_memory_by_key(self, user_id: str, key: str) -> Memory | None:
        rows = self._query(
            "MATCH (m:Memory {user_id: $uid, key: $key, status: 'active'}) "
            "RETURN m {.*} AS m ORDER BY m.updated_at DESC LIMIT 1",
            uid=user_id,
            key=key,
        )
        return _memory_from_record(rows[0]["m"]) if rows else None

    def list_memories(self, user_id, status=MemoryStatus.ACTIVE, limit=200):
        rows = self._query(
            "MATCH (:User {user_id: $uid})-[:HAS_MEMORY]->(m:Memory) "
            "WHERE $status IS NULL OR m.status = $status "
            "RETURN m {.*} AS m ORDER BY m.updated_at DESC LIMIT $limit",
            uid=user_id,
            status=status.value if status else None,
            limit=limit,
        )
        return [_memory_from_record(r["m"]) for r in rows]

    def find_candidate_memories(self, user_id, life_areas, limit=50):
        rows = self._query(
            _CANDIDATES,
            user_id=user_id,
            areas=sorted(life_areas) if life_areas is not None else None,
            limit=limit,
        )
        return [_memory_from_record(r["m"]) for r in rows]

    def save_memory(self, memory: Memory) -> None:
        self._query(
            _SAVE_MEMORY,
            write=True,
            user_id=memory.user_id,
            memory_id=memory.memory_id,
            life_area=memory.life_area,
            props=_memory_props(memory),
            now=utcnow().isoformat(),
        )

    def record_life_area_interest(self, user_id: str, areas: set[str]) -> None:
        if not areas:
            return
        self._query(
            "MATCH (u:User {user_id: $uid}) "
            "UNWIND $areas AS area "
            "MERGE (a:LifeArea {name: area}) "
            "MERGE (u)-[f:FOCUSES_ON]->(a) ON CREATE SET f.asked_count = 0 "
            "SET f.asked_count = f.asked_count + 1, f.last_asked = $now",
            write=True,
            uid=user_id,
            areas=sorted(areas),
            now=utcnow().isoformat(),
        )

    def get_life_area_focus(self, user_id: str, limit: int = 5) -> list[LifeAreaFocus]:
        asked = self._query(
            "MATCH (:User {user_id: $uid})-[f:FOCUSES_ON]->(a:LifeArea) "
            "RETURN a.name AS area, f.asked_count AS asked, f.last_asked AS last",
            uid=user_id,
        )
        held = self._query(
            "MATCH (:User {user_id: $uid})-[:HAS_MEMORY]->(:Memory {status: 'active'})-[:ABOUT]->(a:LifeArea) "
            "RETURN a.name AS area, count(*) AS n",
            uid=user_id,
        )
        focus = {r["area"]: LifeAreaFocus(area=r["area"], asked_count=r["asked"], last_asked=r["last"])
                 for r in asked}
        for r in held:
            focus.setdefault(r["area"], LifeAreaFocus(area=r["area"])).memory_count = r["n"]
        ranked = sorted(focus.values(), key=lambda f: (f.importance, f.asked_count), reverse=True)
        return ranked[:limit]

    def supersede_memory(self, old_memory_id: str, new_memory: Memory) -> None:
        new_memory.supersedes = old_memory_id
        now = utcnow().isoformat()

        def work(tx):
            found = tx.run(
                "MATCH (old:Memory {memory_id: $old}) "
                "SET old.status = 'superseded', old.updated_at = $now RETURN old.memory_id AS id",
                old=old_memory_id,
                now=now,
            ).single()
            if found is None:
                raise BrainError(f"memory {old_memory_id} not found")
            tx.run(
                _SAVE_MEMORY,
                user_id=new_memory.user_id,
                memory_id=new_memory.memory_id,
                life_area=new_memory.life_area,
                props=_memory_props(new_memory),
                now=now,
            )
            tx.run(
                "MATCH (new:Memory {memory_id: $new}), (old:Memory {memory_id: $old}) "
                "MERGE (new)-[:SUPERSEDES]->(old)",
                new=new_memory.memory_id,
                old=old_memory_id,
            )

        try:
            with self._driver.session(database=self._db) as session:
                session.execute_write(work)
        except (Neo4jError, DriverError, OSError) as exc:
            raise BrainError(str(exc)) from exc
