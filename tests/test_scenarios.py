"""Behavioural tests for the scenarios listed in the assignment brief."""
from __future__ import annotations

INTRO = "My name is Rahul. I was born on 15 August 1995 in Delhi. I'm planning to switch jobs next year."


# 1. New user ---------------------------------------------------------------------
def test_new_user_can_chat_with_empty_brain(h):
    body = h.chat("Hello, what can you do?", user="newbie")
    assert body["response"]
    assert body["context_used"] == ["user_profile"]
    assert body["memory_status"] == "nothing_to_save"
    assert set(body["missing_profile_fields"]) == {"name", "date_of_birth", "time_of_birth", "birth_place"}
    assert "None relevant." in h.last_prompt


# 2. Creating a long-term memory ----------------------------------------------------
def test_intro_creates_profile_and_career_goal(h):
    body = h.chat(INTRO)
    actions = {(u["action"], u["key"]) for u in body["memory_updates"]}
    assert ("profile_updated", "name") in actions
    assert ("profile_updated", "zodiac_sign") in actions
    assert ("created", "goal:career_change") in actions

    profile = h.client.get("/users/rahul").json()
    assert profile["zodiac_sign"] == "Leo" and profile["birth_place"] == "Delhi"
    [goal] = h.memories()
    assert goal["key"] == "goal:career_change"
    assert goal["attributes"] == {"target_year": 2027}
    assert goal["life_area"] == "career"


# 3. Retrieving a memory ------------------------------------------------------------
def test_career_question_retrieves_career_goal(h):
    h.chat(INTRO)
    body = h.chat("What should I focus on for my career?")
    assert "career_goal" in body["context_used"]
    assert "Career change (target_year: 2027)" in h.last_prompt
    assert body["memory_updates"] == []  # questions are not stored


# 4. Follow-up question -------------------------------------------------------------
def test_followup_uses_recent_conversation_and_previous_context(h):
    h.chat(INTRO)
    h.chat("What should I focus on for my career?")
    body = h.chat("Why do you say that?")
    assert "recent_conversation" in body["context_used"]
    assert "career_goal" in body["context_used"]  # memories that backed the last answer
    sent = h.llm.chat_calls[-1]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user", "assistant", "user"]
    assert sent[-3]["content"] == "What should I focus on for my career?"
    assert len(h.llm.extraction_calls) == 1  # only the intro triggered extraction


# 5. New session uses previous information -------------------------------------------
def test_new_session_recalls_long_term_memory(h):
    h.chat(INTRO, session="s1")
    body = h.chat("What do you remember about my career goals?", session="s2")
    assert "career_goal" in body["context_used"]
    assert "recent_conversation" not in body["context_used"]
    assert "Career change" in h.last_prompt
    assert len(h.llm.chat_calls[-1]["messages"]) == 1  # no turns leaked from session s1


# 6. Irrelevant memory is not sent ---------------------------------------------------
def test_unrelated_memories_are_excluded(h):
    h.chat(INTRO)
    h.chat("I'm getting married in 2027 and I love photography.")
    assert {m["key"] for m in h.memories()} >= {"life_event:marriage", "interest:photography"}

    body = h.chat("What should I focus on for my career?", session="s9")
    assert "career_goal" in body["context_used"]
    assert "relationships_life_event" not in body["context_used"]
    assert "Photography" not in h.last_prompt and "marriage" not in h.last_prompt.lower()


def test_question_without_matching_memory_gets_no_memories(h):
    h.chat(INTRO)
    body = h.chat("What is my lucky colour this week?", session="s3")
    assert [t for t in body["context_used"] if t.endswith(("_goal", "_interest"))] == []
    assert "None relevant." in h.last_prompt


# 7. User correcting existing information --------------------------------------------
def test_correcting_birth_place_overwrites_profile(h):
    h.chat(INTRO)
    body = h.chat("Sorry, I was actually born in Mumbai, not Delhi.")
    [upd] = [u for u in body["memory_updates"] if u["key"] == "birth_place"]
    assert upd["previous_value"] == "Delhi" and upd["value"] == "Mumbai"
    assert h.client.get("/users/rahul").json()["birth_place"] == "Mumbai"


def test_correcting_goal_supersedes_instead_of_duplicating(h):
    h.chat(INTRO)
    body = h.chat("Actually I now plan to switch jobs in 2028.")
    [upd] = [u for u in body["memory_updates"] if u["key"] == "goal:career_change"]
    assert upd["action"] == "updated"
    active = h.memories()
    assert len(active) == 1 and active[0]["attributes"]["target_year"] == 2028
    history = h.memories(history=True)
    assert {m["status"] for m in history} == {"active", "superseded"}
    assert active[0]["supersedes"] in {m["memory_id"] for m in history}


def test_repeating_a_fact_reinforces_without_duplicating(h):
    h.chat(INTRO)
    body = h.chat("Like I said, I want to switch jobs next year.", session="s2")
    assert body["memory_updates"][0]["action"] == "reinforced"
    [goal] = h.memories()
    assert goal["mention_count"] == 2


def test_retiring_a_goal(h):
    h.chat(INTRO)
    body = h.chat("I no longer want to switch jobs, I got promoted!")
    assert body["memory_updates"][0]["action"] == "retired"
    assert h.memories() == []


# 8. Missing user information --------------------------------------------------------
def test_missing_birth_details_are_flagged_to_llm(h):
    body = h.chat("What does my horoscope say about this month?", user="anon")
    assert "date_of_birth" in body["missing_profile_fields"]
    assert "Missing profile fields: name, date_of_birth" in h.last_prompt


def test_unknown_user_profile_is_404(h):
    assert h.client.get("/users/ghost").status_code == 404


# Not stored ------------------------------------------------------------------------
def test_small_talk_and_questions_do_not_trigger_extraction(h):
    for msg in ["thanks!", "ok", "What should I focus on for my career?", "Why do you say that?"]:
        h.chat(msg)
    assert h.llm.extraction_calls == []
    assert h.memories() == []
