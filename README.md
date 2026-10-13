# MyNaksh: Personalized Astrology Chat with a Shared Brain

[![tests](https://github.com/Eswar-09/mynaksh-shared-brain/actions/workflows/ci.yml/badge.svg)](https://github.com/Eswar-09/mynaksh-shared-brain/actions/workflows/ci.yml)

A FastAPI service that chats with users about astrology, remembers what matters about them in a
Neo4j graph, and selects only the relevant parts of that memory for each answer.

The core loop the brief asks for is implemented end to end:

```
POST /chat -> profile facts -> context selection -> Shared Brain -> LLM -> response -> memory update
```

## Quick start

**Offline, no keys, no Docker.** Uses the in-memory graph and a deterministic fake LLM.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
BRAIN_BACKEND=memory LLM_PROVIDER=fake uvicorn mynaksh.main:create_app --factory --port 8000
python scripts/demo.py            # replays the brief's example conversation
```

**With Neo4j and a real LLM.**

```bash
cp .env.example .env              # set LLM_PROVIDER=anthropic and ANTHROPIC_API_KEY (or openai)
docker compose up --build         # API on :8000, Neo4j browser on :7474 (neo4j / mynaksh-dev)
```

**Tests.**

```bash
pytest                                            # in-memory backend; Neo4j cases skip
NEO4J_TEST_URI=bolt://localhost:7687 pytest       # scenario and contract tests also run on Neo4j
```

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/chat` | Main endpoint. Body: `user_id`, `session_id`, `message` |
| POST | `/users` | Create or partially update a profile. Zodiac is derived from date of birth |
| GET | `/users/{user_id}` | Read a profile |
| GET | `/users/{user_id}/memories?include_history=true` | Raw memories, including superseded facts |
| GET | `/users/{user_id}/brain` | Shared Brain overview: profile, astrology attributes, life-area focus, memories by type |
| GET | `/health` | Brain connectivity and active LLM provider |

A profile is optional. A user's first chat creates an empty profile, and details such as
"I was born on 15 August 1995 in Delhi" fill it in, exactly as in the brief's example.

The `/chat` response extends the required format:

```json
{
  "response": "...",
  "user_id": "user-123",
  "session_id": "session-456",
  "context_used": ["user_profile", "astrology_profile", "career_goal", "recent_conversation"],
  "context_details": [{"kind": "memory", "ref": "mem_6c1d...", "tag": "career_goal",
                       "text": "Career change (target_year: 2027)", "score": 0.585}],
  "memory_updates": [{"action": "created", "key": "goal:career_change", "value": "..."}],
  "memory_status": "saved | nothing_to_save | skipped | failed",
  "missing_profile_fields": ["time_of_birth"],
  "warnings": []
}
```

`context_details` makes context selection auditable: it names the exact memory ids and scores
sent to the LLM. `memory_status` never claims a save that did not happen.

Full request/response transcripts are in [samples/sample_requests.md](samples/sample_requests.md).

## Architecture

```
mynaksh/
  api/routes.py                 HTTP layer, validation, status codes
  services/chat_service.py      orchestrates one turn
  services/profile_extractor.py stage 1 memory: rule-based profile facts and corrections
  services/memory_manager.py    stages 2-3 memory: LLM extraction, validation, reconciliation
  services/context_builder.py   query analysis, memory scoring, prompt assembly
  services/prompts.py           all prompt templates in one place
  brain/base.py                 SharedBrain interface
  brain/neo4j_store.py          Neo4j implementation
  brain/in_memory.py            in-process implementation for tests and offline runs
  sessions.py                   short-term conversation store with rolling summaries
  llm/base.py                   LLMClient interface, JSON parsing, provider fallback
  llm/anthropic_llm.py          Anthropic
  llm/openai_compat.py          OpenAI and any compatible endpoint (Gemini, Mistral, Ollama, vLLM)
  llm/fake.py                   deterministic offline LLM
  astrology.py                  sun sign and sign traits (deliberately simple)
```

One `/chat` turn:

1. **Load profile.** Create an empty one for a new user.
2. **Apply explicit profile facts.** Regex rules catch name, birth date, time, place and language,
   including corrections. They run before the answer so they are used immediately.
3. **Select context.** Analyse the query, fetch candidate memories from the graph, score them, keep
   the top few under a budget. Add the last few session turns.
4. **Generate.** One LLM call with a bounded system prompt plus recent turns.
5. **Record the turn** in the session store, tagged with the memory ids that backed the answer.
   Turns that scroll out of the prompt window are folded into a rolling summary.
6. **Update long-term memory.** Count the life areas asked about. Then gate, extract,
   validate, and reconcile with the graph.

## Shared Brain schema

```
(:User {user_id, name, date_of_birth, time_of_birth, birth_place, preferred_language, zodiac_sign})
   -[:HAS_ZODIAC]->        (:ZodiacSign {name, element, ruling_planets, traits})
                              -[:HAS_ELEMENT]-> (:Element {name})
                              -[:RULED_BY]->    (:Planet {name})
   -[:PREFERS_LANGUAGE]->  (:Language {name})
   -[:FOCUSES_ON {asked_count, last_asked}]-> (:LifeArea {name})
   -[:HAS_MEMORY]->        (:Memory {memory_id, type, key, value, attributes, confidence, status,
                                     mention_count, source_session, created_at, updated_at, supersedes})
                              -[:ABOUT]->      (:LifeArea {name})
                              -[:SUPERSEDES]-> (:Memory)      previous version of a corrected fact
```

**Entities.** `User` holds the structured profile. `Memory` holds every other durable fact. Its
`type` is one of goal, interest, preference, life_event, concern, fact. `LifeArea`, `ZodiacSign`,
`Element`, `Planet` and `Language` are shared nodes.

**Important life areas.** A user's life-area importance comes from two signals. One is how often
they ask about an area, counted on the `FOCUSES_ON` edge. The other is how many active memories
they hold in it, via `ABOUT`. Importance is questions asked plus twice the memories held. The top
areas go into every prompt, so general questions like "any advice for this week?" lean toward
what this user cares about.

**Astrology attributes.** The sun sign links to its element and ruling planets as shared nodes. That
makes cross-user astrology queries a single traversal, such as all Fire-sign users with a
relocation goal.

**Why one `Memory` label with a `type` property** rather than separate `Goal`, `Interest` and
`Preference` labels. Every memory needs the same lifecycle: identity, confidence, status,
versioning. One label keeps reconciliation and retrieval to a single code path and query. The brief's
`HAS_GOAL -> Career Change` view is the query
`(u)-[:HAS_MEMORY]->(m:Memory {type:'goal'})`. Dedicated labels become worthwhile when facts
link to each other, for example a goal that depends on an exam.

**Why shared `LifeArea` nodes.** Retrieval traverses `Memory-[:ABOUT]->LifeArea`, so selecting
"career" memories is an index-backed graph hop rather than a scan. Shared zodiac and life area
nodes also make cohort questions a single traversal, such as which Leo users have career goals.

**Constraints and indexes.** Unique `User.user_id`, `Memory.memory_id`, `LifeArea.name`,
`ZodiacSign.name`, `Element.name`, `Planet.name`, `Language.name`, plus a composite index on `Memory(user_id, key, status)` for
the reconciliation lookup.

**Retrieval query.**

```cypher
MATCH (:User {user_id: $user_id})-[:HAS_MEMORY]->(m:Memory {status: 'active'})-[:ABOUT]->(a:LifeArea)
WHERE $areas IS NULL OR a.name IN $areas OR m.type = 'preference'
RETURN m ORDER BY m.updated_at DESC LIMIT $limit
```

**Why the graph sits behind an interface.** `SharedBrain` has two implementations that pass the
same contract tests. The in-memory one lets reviewers and CI run everything with no
infrastructure. It is chosen by configuration only. The app never silently falls back to it when
Neo4j fails, since that would look healthy while losing data.

## Memory strategy

**Short-term vs long-term.** Raw turns live in a session store bounded to 40 turns with a 6 hour
TTL. Distilled facts live in the graph. Raw conversation never enters the graph. In production the
session store would be Redis.

**Rolling summary.** Once a session outgrows the 10-turn window, every 4 turns the older turns are
folded into a summary of at most 80 words, using the cheaper extraction model. The summary goes
into the prompt under "Earlier in this conversation". Summaries are session-scoped and never
written to the graph. If summarizing fails, the chat continues and the response carries a
`summary_failed` warning.

**What gets remembered.**

| Remember | Do not remember |
|---|---|
| Profile: name, birth date, time, place, language | Questions ("What should I focus on?") |
| Goals and plans, with resolved dates ("next year" becomes target_year 2027) | Greetings, thanks, small talk |
| Lasting interests | One-off moods ("I'm tired today") |
| Preferences about how to be advised | Anything the assistant said |
| Significant life events and ongoing concerns | Secrets and identifiers |
| Anything the user explicitly asks to be remembered | |

**Three stages.**

1. **Rules for profile facts.** Structured, high-value, easy to parse. No LLM call needed.
   They run before the answer, so details are used in the same reply. Rules avoid known false
   positives: "I'm Indian" is not a name, and "I prefer English songs" is not a language choice.
2. **Gate, then LLM extraction.** A cheap gate skips messages with no first-person statement, as
   well as pure questions and small talk. It understands English, romanised Hindi and
   Devanagari markers. "Remember that..." always passes, even when phrased as a question. In
   tests, questions and follow-ups trigger zero extraction calls. Messages that pass go to one
   extraction call. That call receives the user's existing memory keys so it can reuse them
   for updates. It also returns profile fields as a backup to the rules, which covers Hinglish
   and Hindi introductions like "mera naam Rahul hai". Where both find the same field, the rule wins.
3. **Validate and reconcile.** Output is parsed with Pydantic. Unknown life areas are coerced,
   keys are normalised to `type:topic`, confidence below 0.6 is dropped, at most 5 facts per turn.

**How existing memories are updated.** Reconciliation is keyed on `(user_id, key)`:

| Situation | Action |
|---|---|
| No active memory with that key | **create** |
| Same value, no conflicting attribute | **reinforce**: bump `mention_count`, keep max confidence, merge new attributes |
| Value or an attribute differs ("switch jobs in 2028" after "next year") | **supersede**: new node, old node marked `superseded`, linked with `SUPERSEDES` |
| User says it is no longer true | **retire** |

Superseding instead of overwriting keeps an audit trail and lets the assistant say "you earlier
planned 2027". Profile corrections overwrite the field, and the old value is returned in
`memory_updates.previous_value`.

## Context selection

1. **Understand the query** with rules, not an extra LLM call. Rules detect the life areas
   mentioned, in English, Hinglish ("shaadi", "naukri") and Devanagari ("करियर"). They also set
   three flags: follow-up ("Why do you say that?"), recall ("What do you remember..."), and
   astrology ("my chart", "Saturn").
2. **Fetch candidates** from the graph, filtered to those life areas plus preferences.
3. **Score** each candidate:

   `S = 0.6 * relevance + 0.25 * direct_topic_match + 0.15 * freshness`

   *Relevance* is token overlap between the memory and the query, expanded with life-area
   keywords. *Direct match* is 1 if the memory's life area is one the query is about. *Freshness* is
   confidence with a 180-day half-life. The weights were set by hand against the test scenarios
   and are not tuned on real data.
4. **Select** at most 5 memories with score of 0.3 or more, under a 1200-character budget.
5. **Special cases.** Follow-ups reuse the memories that backed the previous answer, read from
   the session turn, and get 10 turns of history instead of 6. Recall questions take
   everything in scope, ranked, with no threshold.
6. **Profile.** Name, language and sun sign are always included. Full birth details are included
   only for astrology or recall questions. Missing fields are listed so the LLM can ask for them.
7. **Life-area focus and summary.** The user's top life areas and the session summary are added
   when they exist. Both appear in `context_used`, as `life_area_focus` and `conversation_summary`.
8. **Language.** The LLM replies in the stored preferred language, or mirrors the user's language
   when they write in Hindi or Hinglish.

The LLM never receives the whole graph or the whole conversation.

## Error handling

| Situation | Behaviour |
|---|---|
| Invalid input: blank message, bad ids, oversized message, future birth date | `422` |
| Unknown user on `GET /users/{id}` | `404`. On `/chat` a profile is created |
| Session id already owned by another user | `409`, prevents cross-user context leaks |
| Missing profile fields | Listed in the response and the prompt. The LLM answers generally and asks |
| Empty memory or no relevant context | Prompt says "None relevant." and the LLM is told not to invent facts |
| LLM down | `503` with `fallback_response`. The turn is not recorded, so a retry does not duplicate history. An optional `LLM_FALLBACK_PROVIDER` tries a second provider first |
| Extraction LLM fails | Reply is still returned. Warning `memory_extraction_failed` |
| Graph down | Reply still returned from session context only. `memory_status: failed`, warning `shared_brain_unavailable`, `/health` returns 503 |
| Session summary fails | Chat continues with recent turns only. Warning `summary_failed` |

## Testing and evaluation

104 automated tests. The scenario and storage contract tests run against both backends when
Neo4j is available. GitHub Actions runs the full suite, plus lint, against a Neo4j service
container on every push.

| Brief scenario | Test |
|---|---|
| New user | `test_new_user_can_chat_with_empty_brain` |
| Creating a long-term memory | `test_intro_creates_profile_and_career_goal` |
| Retrieving a memory | `test_career_question_retrieves_career_goal` |
| Follow-up question | `test_followup_uses_recent_conversation_and_previous_context` |
| New session using previous information | `test_new_session_recalls_long_term_memory` |
| Irrelevant memory | `test_unrelated_memories_are_excluded`, `test_question_without_matching_memory_gets_no_memories` |
| User correcting information | `test_correcting_birth_place_overwrites_profile`, `test_correcting_goal_supersedes_instead_of_duplicating` |
| Missing user information | `test_missing_birth_details_are_flagged_to_llm`, `test_unknown_user_profile_is_404` |

Also covered: duplicate facts reinforce instead of duplicating, retiring a goal, small talk not
triggering extraction, explicit "remember that" requests, Hinglish and Hindi introductions and
goals, rules winning over the LLM for the same field, language preference, life-area focus,
astrology nodes in Neo4j, rolling summaries, LLM outage, extraction outage, summary outage,
graph outage, and session hijack. Unit tests cover zodiac, profile parsing and its false
positives, the extraction gate, query analysis and scoring.

The tests assert on **what the LLM was given**, not on HTTP status alone. The fake LLM records
every prompt, so a test can check that the photography memory was absent from a career prompt.

**How I would evaluate whether the Shared Brain improves responses.**

- **Memory accuracy.** A labelled set of around 200 messages with the facts that should be extracted.
  Measure precision and recall of extracted `(key, value)` pairs, and the false-save rate on small talk.
- **Context relevance.** Label which memories are relevant for each question. Measure precision@k
  and recall of `context_details`. The scoring weights get tuned here.
- **Personalization and consistency.** Run the same multi-session scripts with the brain on and off.
  An LLM judge with a rubric does pairwise comparison, spot-checked by humans. Count contradictions
  of stored facts.
- **Irrelevant context.** Rate of answers that mention an unrelated stored fact.
- **Persistence.** Cross-session recall rate in scripted conversations like `scripts/demo.py`.
- **Online.** Thumbs up or down, follow-up rate, and how often users correct the assistant
  ("no, I said...").

## Key decisions and trade-offs

- **Rules first, LLM where judgement is needed.** Profile parsing, query analysis and the
  extraction gate are rules. They are cheap, deterministic and testable. Open-ended fact
  extraction uses the LLM, which also backs up the profile rules for phrasings they miss, such as
  Hinglish or unusual date formats. The rule-based query analysis still uses keyword lists, so
  a topic phrased with words outside the lists gets no life-area match.
- **A separate extraction call** instead of asking the chat model for JSON alongside its answer.
  That keeps the chat prompt clean and lets extraction use a cheaper model through
  `EXTRACTION_MODEL`. It costs one extra call on statement-like turns only.
- **Lexical scoring, not embeddings.** It is enough for a handful of memories per user and needs no
  vector store. With hundreds of memories per user, I would add an embedding property on `Memory`
  and use Neo4j's vector index for the relevance term.
- **Synchronous memory update.** Simple and consistent: the next message always sees the update.
  It adds extraction latency to the response. See below.
- **Fake LLM as a first-class provider.** It makes the whole system runnable and testable without
  keys, and makes tests deterministic.

## Production considerations

- Move memory extraction off the request path to a queue worker, with retries and idempotency keyed
  on the turn id. Store the user turn before generation.
- Redis for sessions, shared by all API replicas.
- Per-user rate limits and token budgets. Log prompt size and token counts per turn.
- Prompt and extraction versioning, so evaluations compare like with like. `EXTRACTION_MARKER` is
  the start of that.
- Privacy: a delete-my-data endpoint (`DETACH DELETE` the user subgraph), encryption at rest, and a
  rule against storing sensitive categories. Show users what is remembered and let them edit it.
- Memory decay is already in scoring. A periodic job could retire stale low-confidence memories.
- Observability: trace each turn across selection, LLM and extraction. Alert on extraction failure
  rate and the `failed` memory status.
- Authentication: `user_id` must come from an auth token, not the request body.

## Bonus items from the brief

| Bonus | Status |
|---|---|
| Memory importance or confidence score | Done. Confidence per memory, mention counts, and life-area importance |
| Memory conflict resolution | Done. Reinforce, supersede with history, or retire |
| Memory expiration or decay | Partly. Freshness decays in scoring, with no automatic retirement |
| Conversation summarization | Done. Rolling per-session summary |
| Model fallback or routing | Done. `LLM_FALLBACK_PROVIDER`, plus a separate cheaper extraction model |
| Context or token optimization | Done. Top-k under a character budget, truncated turns, gated extraction |
| Hindi or multilingual responses | Done. Hindi and Hinglish input, preferred language, reply mirroring |
| Advanced graph traversal | Partly. Life-area traversal for retrieval and astrology nodes for cohort queries |

## What is simplified

- Astrology is a sun sign from the birth date with a short trait table. There is no ephemeris,
  ascendant or dasha. Interpretations are LLM-generated guidance.
- Tests run against the fake LLM. The Anthropic and OpenAI-compatible adapters are implemented
  but were not exercised against a live key in this submission.
- The fake LLM's extraction is a small set of regexes, including a few Hinglish and Hindi
  patterns. It demonstrates the pipeline, not the quality of real extraction. Real Hindi
  understanding depends on the configured LLM.
