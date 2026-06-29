# CareerPilot — Pipeline Guide

This document explains every file in the project, what it does, and exactly how data flows from the moment the frontend hits `POST /interview/start` to the moment a scored report lands in MongoDB and is available to the frontend.

---

## Big Picture — Two Separate Phases

```
PHASE 1 — LIVE INTERVIEW  (real-time, voice)
   User browser mic ──► LiveKit cloud ──► Bot (Pipecat) ──► Gemini Live API
                                                                   │
                                             Gemini does STT + LLM + TTS
                                                                   │
   User browser speaker ◄── LiveKit cloud ◄── Bot (Pipecat) ◄──────┘

   Everything said is captured live in TranscriptCollector
   Audio is recorded via LiveKit Egress → uploaded directly to Cloudflare R2

PHASE 2 — SCORING  (runs after user disconnects, text-based)
   TranscriptCollector ──► Groq API (llama-3.3-70b) ──► Score report ──► MongoDB
```

These two phases use **different AI services**:
- Phase 1 uses **Gemini Live** (voice-to-voice, WebSocket, real-time, Google)
- Phase 2 uses **Groq** (text-in text-out, REST, fast inference, llama-3.3-70b-versatile)

---

## Complete File Map

```
api/
├── session.py          FastAPI app — the entry point for all HTTP requests
├── upload.py           Document upload endpoint — extracts text from PDF/DOCX (pdfplumber + python-docx)
├── token_helper.py     Generates signed LiveKit JWTs for user + bot
├── db.py               MongoDB read/write helpers (transcripts, scoring_reports, recordings)
├── r2.py               Cloudflare R2 helper — generates presigned URLs for audio playback
└── pdf_report.py       PDF generator (optional — generates downloadable PDF from report)

bot/
├── config.py           All constants: model names, VAD thresholds, agent voices, supported types
├── main.py             Bot entry point — builds pipeline, runs live session, triggers scoring
├── transcript.py       TranscriptCollector — stores every word said during the interview
│
├── agents/
│   ├── base_agent.py          Shared prompt builder (fills {{PLACEHOLDERS}} in templates)
│   ├── hr_agent.py            Amna — HR interviewer (female voice: Kore)
│   ├── technical_agent.py     Ahmed — Technical interviewer (male voice: Charon)
│   ├── cultural_agent.py      Hassan — Culture-fit interviewer (male voice: Orus)
│   └── negotiation_agent.py   Ayan — Salary negotiation roleplay (male voice: Fenrir)
│
└── scoring/
    ├── schemas.py      Pydantic models — defines the exact shape of all LLM outputs + report
    ├── pipeline.py     Scoring orchestrator — runs after session ends, assembles final report
    ├── per_question.py Scores each Q&A pair individually (one Groq call per question)
    └── holistic.py     Scores the full session as a whole (one Groq call for everything)

logs/
├── api_server.log      uvicorn stdout (written while server runs)
└── api_server_err.log  uvicorn stderr
```

---

## The Full Flow — Step by Step

### Step 0 — Frontend uploads documents (optional)  →  `api/upload.py`

Before starting a session the frontend can upload the resume and/or job description as a file instead of sending raw text.

```
POST /upload/document   multipart/form-data   file = resume.pdf or resume.docx
```

`api/upload.py` does the following:

1. **Validates file size** — rejects anything over 5 MB (HTTP 413)
2. **Validates file type** — accepts only `.pdf` (MIME `application/pdf`) and `.docx` (MIME `application/vnd.openxmlformats-officedocument.wordprocessingml.document`). Rejects everything else (HTTP 415)
3. **Extracts text:**
   - PDF → `pdfplumber` reads each page and joins the text
   - DOCX → `python-docx` reads each paragraph and joins them
4. **Validates extracted text** — rejects if fewer than 10 characters were extracted (catches scanned image PDFs with no selectable text, HTTP 422)
5. **Returns** the extracted plain text along with `char_count` and `word_count`

The frontend passes the returned `text` field as the `resume` or `job_description` field in the next step.

> **Scanned PDFs are not supported.** The PDF must contain selectable/copyable text (i.e. generated digitally from Word, LaTeX, or a PDF exporter). Image-only scans return a 422.

---

### Step 1 — Frontend hits `POST /interview/start`  →  `api/session.py`

The React frontend sends a POST request with:
```json
{
  "round_type": "hr",
  "resume": "Software engineer with 3 years...",
  "job_description": "We are hiring a backend engineer...",
  "num_questions": 3,
  "language": "english",
  "candidate_name": "Ahmed"
}
```

`api/session.py` does these things in order:

1. **Validates** `round_type` and `language` against allowed values from `bot/config.py`
2. **Generates a `session_id`** (UUID4) and a `room_name` (`interview-{session_id}`)
3. **Calls `api/token_helper.py`** twice — one JWT for the user (goes to React), one JWT for the bot (passed to subprocess)
4. **Pre-creates the LiveKit room** via LiveKit Room API — required before starting egress
5. **Starts LiveKit Egress** — begins audio-only recording (`OGG` format) that streams directly to Cloudflare R2 at key `recordings/{session_id}.ogg`. Returns an `egress_id`.
6. **Spawns `bot/main.py` as a subprocess** using `subprocess.Popen`:
   ```
   python -u -m bot.main
     --session_id      <uuid>
     --room_name       interview-<uuid>
     --bot_token       <jwt>
     --round_type      hr
     --resume          "..."
     --job_description "..."
     --num_questions   3
     --language_mode   english
     --egress_id       EG_xxxx         ← only if egress started successfully
     --r2_key          recordings/<uuid>.ogg
   ```
   The `-u` flag forces unbuffered output so bot logs appear immediately.
   Bot stdout+stderr are written to `logs/bot_{session_id}.log`.
7. **Returns** to the frontend:
   ```json
   {
     "session_id": "abc123",
     "room_name": "interview-abc123",
     "livekit_url": "wss://...",
     "user_token": "<jwt for React>"
   }
   ```

The bot subprocess is now running in the background and the room is being recorded. The API response is instant.

---

### Step 2 — Generating Tokens  →  `api/token_helper.py`

Called by `api/session.py` during Step 1. Creates two LiveKit JWTs:

- `identity="user"` → given to the React frontend so the user can join the room
- `identity="bot"` → given to the bot subprocess so Pipecat can join the same room

Both tokens grant publish + subscribe permissions on the specific room. They are signed using `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` from `.env`.

---

### Step 3 — Bot Starts Up  →  `bot/main.py`

The subprocess launched in Step 1 runs `bot/main.py`. This is the heart of the live interview.

**What main.py does on startup:**

1. **Parses args** — reads all the `--flags` passed by `api/session.py`
2. **Builds the system prompt** — calls the right agent function (e.g. `get_hr_prompt(...)`) with the resume, JD, num_questions, and language mode
3. **Creates `LiveKitTransport`** — Pipecat's connection to the LiveKit room. Audio flows through here.
4. **Creates `GeminiLiveLLMService`** — opens a persistent WebSocket to Google's Gemini Live API. This single service handles STT (speech-to-text), LLM (language model), and TTS (text-to-speech) all in one. The system prompt and **per-round voice** are loaded here.
5. **Creates `LLMContextAggregatorPair`** — two processors that sit around the LLM and manage conversation turn state
6. **Wires the pipeline:**
   ```
   LiveKit Input → UserAggregator → GeminiLiveLLM → LiveKit Output → AssistantAggregator
   ```
7. **Registers event handlers** (see Steps 4–5 below)
8. **Starts the pipeline** — `await runner.run()` — blocks until the session ends

---

### Step 4 — Building the Bot's Persona  →  `bot/agents/`

Before the pipeline starts, `main.py` calls an agent function to build the system prompt string that Gemini receives.

**`bot/agents/base_agent.py`** — shared utility called by all four agents. Takes a template string with `{{PLACEHOLDERS}}` and replaces them:

| Placeholder | Replaced with |
|---|---|
| `{{AGENT_NAME}}` | The interviewer's fixed name (Amna / Ahmed / Hassan / Ayan) |
| `{{CANDIDATE_RESUME}}` | The resume text passed from the API |
| `{{JOB_DESCRIPTION}}` | The job description text |
| `{{NUM_QUESTIONS}}` | How many questions to ask (e.g. `3`) |
| `{{LANGUAGE_INSTRUCTION}}` | Instruction like "Conduct in English only" |

It also validates that `round_type` and `language_mode` are supported values before building.

**The four agents — names and voices are fixed and not configurable by the frontend:**

| File | Persona | Voice | What it tests |
|---|---|---|---|
| `hr_agent.py` | **Amna**, Senior HR Manager | Kore (female) | Motivation, STAR-method behavioural questions, work history, culture fit |
| `technical_agent.py` | **Ahmed**, Senior Engineer | Charon (male) | Accuracy of resume claims, depth of technical knowledge, problem decomposition |
| `cultural_agent.py` | **Hassan**, Culture Lead | Orus (male) | Self-awareness, values alignment, conflict resolution maturity |
| `negotiation_agent.py` | **Ayan**, Hiring Manager | Fenrir (male) | Makes a salary offer, holds firm for 2 exchanges, tests candidate's confidence and justification |

Company context is derived from the job description — not hardcoded. The agent infers sector, size, and role from the JD provided.

**Every agent's prompt includes two additional sections beyond the interview structure:**

- **BEHAVIOURAL RULES** — one question per turn, no sycophancy, no interruptions, last question announcement, probe rule (one follow-up per vague/short answer — does not count toward question total), session closing script
- **GUARDRAILS** — how to handle: rude/abusive candidate (calm redirect, never terminate), "are you AI?" (deflect without confirming), "stop the interview" (acknowledge and wrap up), off-topic responses (one redirect, continue after third), short/silent answers (probe once with "could you expand on that?")

The output is a **plain string** — nothing more. This string is passed directly to `GeminiLiveLLMService` as `system_instruction`.

---

### Step 5 — User Connects, Interview Begins  →  `bot/main.py` event handlers

When the React frontend uses the `user_token` to join the LiveKit room, Pipecat fires `on_participant_connected`.

**`on_participant_connected` handler:**
1. Waits up to **60 seconds** for the Gemini WebSocket session to be established (Gemini retries up to 3 times with backoff on network failures)
2. Sets `llm._ready_for_realtime_input = True` so Gemini starts accepting audio from the user
3. Pushes a greeting trigger frame through the pipeline → Gemini speaks its intro

**Transcript collection — two event handlers:**

- `on_user_turn_message_added` — fires after the user finishes speaking. Saves text to `TranscriptCollector` with `role="candidate"`
- `on_assistant_turn_stopped` — fires after Gemini finishes speaking. Saves text with `role="agent"`

Both handlers deduplicate — if Pipecat fires the same event twice for the same content, only one entry is added.

**Auto-hangup — the bot manages its own session end:**

The bot detects when the interview is over and ends the call without waiting for the user to disconnect:

1. `on_assistant_turn_stopped` detects goodbye phrases in the bot's text (e.g. "have a great day", "it was a pleasure", "our team will be in touch") → starts a **20-second window**
2. If the user replies with a closing phrase (e.g. "bye", "thank you", "goodbye") within 20s → bot delivers a brief closing note → call ends after 15s
3. If no user reply within 20s → call ends immediately
4. If the user says "stop the interview" at any point → bot acknowledges, wraps up, call ends after 15s

All paths use `_end_session_once()` — a guard that prevents `_end_session()` from being called twice (e.g. if both the timeout and `on_participant_disconnected` fire simultaneously).

**Audio path during the live conversation:**
```
User mic (browser)
  → LiveKit cloud
    → LiveKitInputTransport (Pipecat receives audio frames)
      → UserAggregator (manages turn state, detects end of user speech)
        → GeminiLiveLLMService
            ├── Sends audio to Gemini Live via WebSocket
            ├── Gemini does STT + LLM reasoning + TTS in one step
            └── Streams audio response back
          → LiveKitOutputTransport (sends audio frames to LiveKit)
            → LiveKit cloud
              → User's browser speaker
```

---

### Step 6 — Storing Every Word  →  `bot/transcript.py`

`TranscriptCollector` is a simple in-memory list that accumulates everything said during the session.

```
TranscriptCollector
  ├── add(role, content)     called on every turn (agent or candidate)
  ├── to_dict_list()         converts to plain dicts for MongoDB writes
  ├── from_dict_list()       rebuilds from MongoDB data (used by scoring)
  └── get_pairs()            groups entries into Q&A pairs for scoring
```

Each entry stored looks like:
```json
{
  "role": "agent",
  "content": "Walk me through your experience at TechCorp.",
  "timestamp": "2026-06-25T15:10:00Z"
}
```

`get_pairs()` groups consecutive entries: one `agent` entry followed by one or more `candidate` entries = one pair. Multiple consecutive candidate entries are concatenated. If a question had no answer, it becomes `"[no response]"`.

---

### Step 7 — User Disconnects (or Bot Hangs Up), Session Ends  →  `bot/main.py` `_end_session()`

Triggered by either `on_participant_disconnected` (user closes browser) or the auto-hangup logic (Step 5). All paths go through `_end_session_once()` which ensures the following runs exactly once:

1. **Cancels the Pipecat pipeline** — stops all audio processing
2. **Serialises the transcript** — `transcript_collector.to_dict_list()`
3. **Writes transcript to MongoDB** — `api.db.write_transcript(session_id, transcript)`
4. **Stops LiveKit Egress** — calls `lk.egress.stop_egress(egress_id)` → LiveKit finalises and uploads the `.ogg` file to Cloudflare R2
5. **Writes recording metadata to MongoDB** — `api.db.write_recording(session_id, egress_id, r2_key)` — stores the R2 key so the frontend can retrieve a playback URL later
6. **Awaits scoring** — calls `await _run_scoring_async(...)` — keeps the process alive until scoring and the MongoDB write are both complete

> **Important:** The scoring is `await`-ed (not `asyncio.create_task`). The bot process must not exit before the scoring report is written to MongoDB.

There is also a **20-minute watchdog timer** (`MAX_SESSION_DURATION = 1200s`) that calls `_end_session_once()` automatically if neither the user disconnects nor the auto-hangup fires.

---

### Step 8 — MongoDB + R2 Layer  →  `api/db.py` and `api/r2.py`

**`api/db.py`** — used by both the bot subprocess (to write) and the FastAPI server (to read).

Write side (bot):
- `write_transcript(session_id, transcript)` — upserts into `transcripts` collection
- `write_scoring_report(session_id, report)` — upserts into `scoring_reports` collection
- `write_recording(session_id, egress_id, r2_key)` — upserts into `recordings` collection

Read side (API):
- `read_transcript(session_id)` → full transcript document or `None`
- `read_scoring_report(session_id)` → full report document or `None`
- `read_recording(session_id)` → recording metadata or `None`

Connection uses a **lazy singleton** — the `MongoClient` is created once on first use. Uses `certifi` for SSL certificate validation (required by Python 3.14 on Windows connecting to MongoDB Atlas).

Collections in MongoDB Atlas (`CareerPilot` database):
- `transcripts` — one document per session, all raw conversation entries
- `scoring_reports` — one document per session, full structured scoring report
- `recordings` — one document per session, R2 key + egress ID + format + timestamp

**`api/r2.py`** — Cloudflare R2 helper. The actual audio file is uploaded directly by LiveKit Egress — this module only generates **presigned GET URLs** for the frontend to stream the recording.

```python
generate_presigned_url(r2_key, expires_in=3600)
# Returns a temporary signed URL valid for 1 hour
```

The frontend uses this URL with an `<audio>` element — it never proxies the file through the API server.

---

### Step 9 — Scoring Pipeline  →  `bot/scoring/pipeline.py`

Called after the session ends. Orchestrates the full scoring run. Always returns a dict — never raises.

**Steps inside `run_scoring_pipeline()`:**

1. Rebuilds `TranscriptCollector` from the serialised transcript
2. Calls `get_pairs()` → list of `{question, answer, question_index}`
3. Calls `score_question()` for **every pair** — one Groq API call per question
4. Calls `score_session()` with all pairs + all per-question scores — one Groq API call for the whole session
5. Assembles the final `ScoringReport` Pydantic model and returns `.model_dump()`

**Scoring status:**
- `"complete"` — all questions scored successfully
- `"partial"` — some questions failed, some succeeded
- `"failed"` — all questions failed or zero pairs found

---

### Step 10 — Scoring Schemas  →  `bot/scoring/schemas.py`

Defines the exact shape of everything the LLM returns and what gets stored in MongoDB.

**`QuestionScoreOutput`** — what the LLM returns for each individual question:
```python
class QuestionScoreOutput(BaseModel):
    score: int          # 0–10
    strengths: list[str]
    gaps: list[str]
    suggestion: str
    question_en: str    # English translation of the question (empty if already English)
    answer_en: str      # English translation of the candidate's answer (empty if already English)
```

**`HolisticScoreOutput`** — what the LLM returns for the full session:
```python
class HolisticScoreOutput(BaseModel):
    overall_score: float          # 0.0–10.0
    hiring_signal: str            # "Recommend" | "Consider" | "Pass"
    summary_insights: str
    communication_quality: CommunicationQuality
    top_recommendations: list[str]   # exactly 3 items
    round_specific_insight: str
```

**`QuestionResult`** — per-question entry inside the final report:
```python
class QuestionResult(BaseModel):
    question_index: int
    question: str       # original question (may be Urdu)
    answer: str         # original answer (may be Urdu/mixed)
    question_en: str    # English translation (empty if already English)
    answer_en: str      # English translation (empty if already English)
    score: int          # 0–10 (-1 if scoring failed)
    strengths: list[str]
    gaps: list[str]
    suggestion: str
```

**`ScoringReport`** — the complete document stored in MongoDB:
```python
class ScoringReport(BaseModel):
    session_id: str
    round_type: str
    generated_at: str
    scoring_status: str       # complete | partial | failed
    overall_score: float
    hiring_signal: str        # Recommend | Consider | Pass
    summary_insights: str
    communication_quality: CommunicationQuality
    top_recommendations: list[str]
    round_specific_insight: str
    questions: list[QuestionResult]
    question_count: int
```

---

### Step 11 — Per-Question Scoring  →  `bot/scoring/per_question.py`

Called once per Q&A pair. Sends the question, answer, JD, and round-specific criteria to Groq.

**Score scale (applied consistently across all rounds):**

| Score | Label | Meaning |
|---|---|---|
| 0–3 | Poor | Unprepared, significantly off-target |
| 4–5 | Weak | Below expectations for the role |
| 6–7 | Average | Meets basic bar, room to improve |
| 8–9 | Strong | Solid answer, minor gaps only |
| 10 | Exceptional | Reserve for genuinely outstanding answers |

**Evaluation criteria differ by round type:**

| Round | What is checked |
|---|---|
| `hr` | STAR method usage, relevance to JD, communication clarity |
| `technical` | Factual accuracy, depth beyond definitions, problem decomposition, honesty about gaps |
| `cultural` | Self-awareness, values alignment, conflict resolution maturity |
| `negotiation` | Confidence of ask, justification quality, composure under pushback, professionalism |

---

### Step 12 — Holistic Scoring  →  `bot/scoring/holistic.py`

Called once per session after all per-question scores are done. Sends the full transcript + all per-question scores to Groq.

**Hiring signal thresholds:**

| Overall Score | Hiring Signal |
|---|---|
| 7.5 and above | **Recommend** |
| 5.5 – 7.4 | **Consider** |
| Below 5.5 | **Pass** |

The round-specific insight instruction varies per round:
- **HR** — did STAR method usage improve, decline, or stay consistent across questions?
- **Technical** — which specific domain showed the deepest knowledge gap?
- **Cultural** — did the candidate show genuine self-awareness or give rehearsed generic answers?
- **Negotiation** — did confidence increase or drop after the first pushback?

---

### Step 13 — Frontend Reads the Report  →  `api/session.py` endpoints

Once the session ends and scoring is written to MongoDB, the frontend can poll these endpoints:

**`GET /interview/{session_id}/report`** — returns the full `ScoringReport` JSON. Returns HTTP 202 if scoring is still in progress.

**`GET /interview/{session_id}/report/pdf`** — generates and downloads a PDF version of the same report via `api/pdf_report.py`.

**`GET /interview/{session_id}/transcript`** — returns the raw transcript with all entries.

**`GET /interview/{session_id}/recording`** — returns a presigned R2 URL for audio playback:
```json
{
  "session_id": "...",
  "url": "https://....r2.cloudflarestorage.com/...?X-Amz-Signature=...",
  "format": "ogg",
  "recorded_at": "2026-06-29T10:12:19Z"
}
```
The URL is valid for 1 hour. The frontend passes it directly to an `<audio>` element.

**`GET /health`** — returns `{"status": "ok", "active_sessions": N}`.

---

## Final Report JSON Shape (what the frontend receives)

```json
{
  "session_id": "3d625fef-...",
  "round_type": "hr",
  "generated_at": "2026-06-25T15:15:57Z",
  "scoring_status": "complete",
  "overall_score": 6.5,
  "hiring_signal": "Consider",
  "summary_insights": "The candidate showed genuine familiarity with FastAPI but struggled to structure answers using STAR...",
  "communication_quality": {
    "clarity": "Moderate — answers were mostly understandable but occasionally trailed off",
    "conciseness": "Inconsistent — Q1 was too brief, Q3 ran too long",
    "confidence_markers": "Confident when citing the 40% improvement, hesitant when asked about team leadership"
  },
  "top_recommendations": [
    "In Q1 you said 'I want to grow here' — instead, name something specific about this company's stack or product that excites you",
    "When describing the API fix, you gave the result (40%) without the action — walk through what you personally did step by step",
    "In Q3, switching mid-answer made the response harder to evaluate — practice answering pressure questions consistently"
  ],
  "round_specific_insight": "STAR usage was attempted in Q2 but dropped entirely in Q3 — consistency needs work.",
  "question_count": 3,
  "questions": [
    {
      "question_index": 0,
      "question": "Walk me through your experience at TechCorp.",
      "answer": "I led a team of 2 developers...",
      "question_en": "",
      "answer_en": "",
      "score": 7,
      "strengths": ["Gave specific numbers", "Referenced the resume directly"],
      "gaps": ["No STAR structure used"],
      "suggestion": "Add the Result — what was the measurable outcome of leading the team?"
    }
  ]
}
```

---

## Environment Variables (`.env`)

| Variable | Used by | Purpose |
|---|---|---|
| `LIVEKIT_URL` | `api/session.py`, `bot/main.py` | LiveKit server WebSocket URL |
| `LIVEKIT_API_KEY` | `api/token_helper.py`, `api/session.py`, `bot/main.py` | LiveKit token signing + Egress API |
| `LIVEKIT_API_SECRET` | `api/token_helper.py`, `api/session.py`, `bot/main.py` | LiveKit token signing + Egress API |
| `GOOGLE_API_KEY` | `bot/main.py` | Gemini Live API key (Phase 1 — live voice) |
| `GROQ_API_KEY` | `bot/scoring/per_question.py`, `bot/scoring/holistic.py` | Groq API key (Phase 2 — scoring) |
| `MONGODB_URI` | `api/db.py` | MongoDB Atlas connection string |
| `MONGODB_DB` | `api/db.py` | Database name — must match exactly: `CareerPilot` |
| `R2_ACCOUNT_ID` | `api/session.py`, `api/r2.py` | Cloudflare account ID for R2 endpoint |
| `R2_ACCESS_KEY_ID` | `api/session.py`, `api/r2.py` | R2 API access key |
| `R2_SECRET_ACCESS_KEY` | `api/session.py`, `api/r2.py` | R2 API secret key |
| `R2_BUCKET_NAME` | `api/session.py`, `api/r2.py` | R2 bucket name (`careerpilot-recordings`) |

---

## What is NOT in the Bot Code (Frontend's Responsibility)

| Thing | Why |
|---|---|
| LiveKit JS SDK integration | React joins the room using the `user_token` returned by the API |
| Displaying the score report | React reads from `GET /interview/{session_id}/report` and renders it |
| Audio playback of recording | React reads from `GET /interview/{session_id}/recording` and plays the URL |
| Microphone/speaker UI | Handled by the browser + LiveKit JS SDK |
| Uploading resume/JD files | Frontend calls `POST /upload/document` and gets plain text back — the API handles parsing |

The bot produces three outputs:
1. **Transcript** → written to MongoDB `transcripts` collection
2. **Scoring report** → written to MongoDB `scoring_reports` collection
3. **Audio recording** → uploaded to Cloudflare R2, metadata in MongoDB `recordings` collection
