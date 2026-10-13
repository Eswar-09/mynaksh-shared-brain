from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from mynaksh.astrology import sun_sign
from mynaksh.models import ExtractedMemory, Memory, MemoryType
from mynaksh.services.context_builder import analyze, score_memory
from mynaksh.services.memory_manager import worth_extracting
from mynaksh.services.profile_extractor import extract_profile_facts


@pytest.mark.parametrize("d,sign", [
    (date(1995, 8, 15), "Leo"), (date(2000, 1, 5), "Capricorn"), (date(2000, 12, 25), "Capricorn"),
    (date(2000, 3, 21), "Aries"), (date(2000, 3, 20), "Pisces"), (date(2000, 2, 19), "Pisces"),
])
def test_sun_sign(d, sign):
    assert sun_sign(d) == sign


@pytest.mark.parametrize("msg,expected", [
    ("I was born on 15 August 1995 in Delhi", {"date_of_birth": date(1995, 8, 15), "birth_place": "Delhi"}),
    ("born March 3rd, 1990 at 6:45 pm in New Delhi",
     {"date_of_birth": date(1990, 3, 3), "time_of_birth": time(18, 45), "birth_place": "New Delhi"}),
    ("my dob is 05/11/1992", {"date_of_birth": date(1992, 11, 5)}),
    ("My name is Rahul Sharma", {"name": "Rahul Sharma"}),
    ("please reply in Hindi from now", {"preferred_language": "Hindi"}),
    ("I was born in 1995", {}),
    ("I want to switch jobs", {}),
])
def test_profile_extraction(msg, expected):
    assert extract_profile_facts(msg) == expected


@pytest.mark.parametrize("msg,expected", [
    ("I'm planning to switch jobs next year.", True),
    ("I'm preparing for a product management interview next month, any tips?", True),
    ("What should I focus on for my career?", False),
    ("Why do you say that?", False),
    ("thanks!", False),
    ("Tell me about career opportunities.", False),
])
def test_extraction_gate(msg, expected):
    assert worth_extracting(msg) is expected


def test_query_analysis():
    assert analyze("Why do you say that?").is_followup
    assert not analyze("What should I focus on for my career?").is_followup
    assert analyze("What should I focus on for my career?").areas == {"career"}
    assert analyze("What do you remember about my career goals?").is_recall
    assert analyze("Will Saturn transit affect me?").is_astro


def test_scoring_prefers_on_topic_and_fresh_memories():
    now = datetime.now(timezone.utc)
    q = analyze("What should I focus on for my career?")
    career = Memory(user_id="u", type=MemoryType.GOAL, key="goal:career_change", value="Career change",
                    life_area="career")
    hobby = Memory(user_id="u", type=MemoryType.INTEREST, key="interest:photography", value="Photography")
    stale = career.model_copy(update={"updated_at": now - timedelta(days=720)})
    s_career, s_hobby, s_stale = (score_memory(m, q, now)[0] for m in (career, hobby, stale))
    assert s_career > s_stale > s_hobby
    assert s_hobby < 0.3  # below the selection threshold


def test_extracted_memory_key_normalisation():
    e = ExtractedMemory(type="goal", key="Career Change!!", value="Career change", life_area="Career")
    assert e.normalized_key() == "goal:career_change" and e.life_area == "career"
    assert ExtractedMemory(type="interest", key="goal:yoga", value="Yoga").normalized_key() == "interest:yoga"
    assert ExtractedMemory(type="goal", value="x", life_area="mars").life_area == "personal"
