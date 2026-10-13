"""Hindi/Hinglish input, smarter profile rules, life-area focus, astrology attributes, summaries."""
from __future__ import annotations

from datetime import time

import pytest

from mynaksh.services.context_builder import analyze
from mynaksh.services.memory_manager import worth_extracting
from mynaksh.services.profile_extractor import extract_profile_facts

INTRO = "My name is Rahul. I was born on 15 August 1995 in Delhi. I'm planning to switch jobs next year."


# Profile rules ---------------------------------------------------------------------
@pytest.mark.parametrize("msg,expected", [
    ("I'm Rahul, born 15th Aug 1995 in Delhi", {"name": "Rahul", "birth_place": "Delhi"}),
    ("Hi, I am Priya and my birthday is 3 March 1998", {"name": "Priya"}),
    ("I'm Indian and I live in Pune", {}),
    ("I'm Worried about my exams", {}),
    ("I prefer English songs", {}),
    ("I prefer Hindi.", {"preferred_language": "Hindi"}),
    ("Can you talk to me in Tamil?", {"preferred_language": "Tamil"}),
    ("Hindi mein baat karo", {"preferred_language": "Hindi"}),
])
def test_profile_rules(msg, expected):
    facts = extract_profile_facts(msg)
    for k, v in expected.items():
        assert facts.get(k) == v
    if not expected:
        assert "name" not in facts and "preferred_language" not in facts


def test_birth_time_after_place():
    facts = extract_profile_facts("I was born in Delhi on 15 August 1995 at 10:30 am")
    assert facts["time_of_birth"] == time(10, 30) and facts["birth_place"] == "Delhi"


@pytest.mark.parametrize("msg,expected", [
    ("Can you remember that I want to start a business?", True),
    ("Please note that I prefer short answers", True),
    ("What do you remember about my career goals?", False),
    ("Do you remember my goals?", False),
    ("मैं अगले साल नौकरी बदलना चाहता हूँ।", True),
    ("मेरा करियर कैसा रहेगा?", False),
    ("mera naam Rahul hai", True),
    ("shukriya", False),
])
def test_extraction_gate_multilingual(msg, expected):
    assert worth_extracting(msg) is expected


@pytest.mark.parametrize("msg,area", [
    ("Should I start my own venture?", "career"),
    ("Will I get promoted this year?", "career"),
    ("मेरा करियर कैसा रहेगा?", "career"),
    ("meri shaadi kab hogi?", "relationships"),
    ("paisa kab aayega?", "finance"),
])
def test_topic_detection_multilingual(msg, area):
    assert area in analyze(msg).areas


# End to end ------------------------------------------------------------------------
def test_explicit_remember_request_creates_memory(h):
    assert not analyze("Can you remember that I want to start a business?").is_recall
    body = h.chat("Can you remember that I want to start a business?")
    assert "birth_details" not in body["context_used"]
    assert ("created", "goal:start_business") in {(u["action"], u["key"]) for u in body["memory_updates"]}
    h.chat("Should I start my own venture?", session="s2")
    assert "Start own business" in h.last_prompt


def test_hinglish_intro_fills_profile_via_llm_extractor(h):
    body = h.chat("Namaste, mera naam Rahul hai aur main Delhi mein paida hua tha.")
    keys = {u["key"]: u["value"] for u in body["memory_updates"]}
    assert keys["name"] == "Rahul" and keys["birth_place"] == "Delhi"
    profile = h.client.get("/users/rahul").json()
    assert profile["name"] == "Rahul" and profile["birth_place"] == "Delhi"


def test_hindi_goal_is_stored_and_retrieved(h):
    h.chat("मैं अगले साल नौकरी बदलना चाहता हूँ।")
    [goal] = h.memories()
    assert goal["key"] == "goal:career_change" and goal["attributes"] == {"target_year": 2027}
    body = h.chat("मेरा करियर कैसा रहेगा?", session="s2")
    assert "career_goal" in body["context_used"]
    assert "reply the\n  same way they wrote" in h.last_prompt


def test_rules_win_over_llm_for_same_field(h):
    # The regex rule reads "Rahul"; the fake LLM extractor reads "Raju" from the Hinglish part.
    body = h.chat("My name is Rahul, ghar pe mera naam Raju hai.")
    assert [u["value"] for u in body["memory_updates"] if u["key"] == "name"] == ["Rahul"]
    assert h.client.get("/users/rahul").json()["name"] == "Rahul"
    assert len(h.llm.extraction_calls) == 1


def test_language_preference_changes_reply_language(h):
    h.chat(INTRO)
    h.chat("Please reply in Hindi from now on.")
    h.chat("What should I focus on for my career?", session="s2")
    assert "Reply in Hindi" in h.last_prompt


def test_life_area_focus_is_tracked_and_used(h):
    h.chat(INTRO)
    h.chat("What should I focus on for my career?")
    h.chat("Will I get a promotion soon?")
    brain = h.client.get("/users/rahul/brain").json()
    career = next(f for f in brain["life_area_focus"] if f["area"] == "career")
    assert career["asked_count"] >= 3 and career["memory_count"] == 1
    body = h.chat("Any advice for this week?", session="s2")
    assert "life_area_focus" in body["context_used"]
    assert "Life areas this user cares about most: career" in h.last_prompt


def test_brain_endpoint_exposes_astrology_attributes(h):
    h.chat(INTRO)
    brain = h.client.get("/users/rahul/brain").json()
    assert brain["astrology"] == {"sun_sign": "Leo", "element": "Fire", "ruling_planets": ["Sun"],
                                  "traits": "confident, generous, wants recognition"}
    assert brain["memories"]["goal"][0]["key"] == "goal:career_change"
    assert h.client.get("/users/ghost/brain").status_code == 404


# Conversation summary --------------------------------------------------------------
def test_long_sessions_are_summarized(h):
    for i in range(9):
        h.chat(f"Question number {i} about my career path?")
    assert len(h.llm.summary_calls) == 2
    body = h.chat("And one more thing about my career?")
    assert "conversation_summary" in body["context_used"]
    assert "## Earlier in this conversation" in h.last_prompt
    assert "Question number 0" in h.last_prompt  # old turn survives only via the summary
    assert all("Question number 0" not in m["content"] for m in h.llm.chat_calls[-1]["messages"])


def test_summary_failure_does_not_break_chat(hm):
    hm.llm.fail_summary = True
    for i in range(7):
        body = hm.chat(f"Question number {i} about my career path?")
    assert "summary_failed" in body["warnings"]
    assert body["response"]
