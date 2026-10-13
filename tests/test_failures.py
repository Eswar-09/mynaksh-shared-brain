"""Error handling and fallbacks."""
from __future__ import annotations

INTRO = "My name is Rahul. I was born on 15 August 1995 in Delhi. I'm planning to switch jobs next year."


def test_invalid_input_is_422(hm):
    for bad in [{"user_id": "u", "session_id": "s", "message": "   "},
                {"user_id": "bad id!", "session_id": "s", "message": "hi"},
                {"user_id": "u", "session_id": "s"},
                {"user_id": "u", "session_id": "s", "message": "x" * 5000}]:
        assert hm.client.post("/chat", json=bad).status_code == 422


def test_future_birth_date_is_rejected(hm):
    r = hm.client.post("/users", json={"user_id": "u", "date_of_birth": "2999-01-01"})
    assert r.status_code == 422


def test_llm_outage_returns_503_with_fallback_and_records_nothing(hm):
    hm.llm.fail = True
    r = hm.client.post("/chat", json={"user_id": "rahul", "session_id": "s1", "message": "hello there"})
    assert r.status_code == 503
    assert r.json()["error"] == "llm_unavailable" and r.json()["fallback_response"]
    hm.llm.fail = False
    hm.chat("hello again")
    assert len(hm.llm.chat_calls[-1]["messages"]) == 1  # failed turn was not added to history


def test_extraction_failure_still_answers(hm):
    hm.llm.fail_extraction = True
    body = hm.chat(INTRO)
    assert body["response"]
    assert "memory_extraction_failed" in body["warnings"]
    # rule-based profile facts were still saved
    assert body["memory_status"] == "saved"
    assert hm.client.get("/users/rahul").json()["name"] == "Rahul"


def test_brain_outage_degrades_and_does_not_claim_persistence(hm):
    hm.chat(INTRO)
    hm.brain.fail = True
    body = hm.chat("I'm interested in entrepreneurship.", session="s2")
    assert body["response"]
    assert "shared_brain_unavailable" in body["warnings"]
    assert body["memory_status"] == "failed"
    assert body["memory_updates"] == []
    assert hm.client.get("/health").status_code == 503
    hm.brain.fail = False
    assert all(m["key"] != "interest:entrepreneurship" for m in hm.memories())


def test_session_cannot_be_hijacked_by_another_user(hm):
    hm.chat("hello", user="alice", session="shared")
    r = hm.client.post("/chat", json={"user_id": "bob", "session_id": "shared", "message": "hi"})
    assert r.status_code == 409


def test_profile_endpoint_round_trip(hm):
    r = hm.client.post("/users", json={"user_id": "priya", "name": "Priya", "date_of_birth": "1998-03-25",
                                      "preferred_language": "Hindi"})
    assert r.status_code == 201 and r.json()["zodiac_sign"] == "Aries"
    hm.chat("hello", user="priya")
    assert "Reply in Hindi" in hm.last_prompt
