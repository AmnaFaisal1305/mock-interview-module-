# CareerPilot

AI-powered mock interview platform with real-time voice and automated scoring.

Candidates speak with an AI interviewer over a live voice call. After the call ends, the system scores every answer and generates a structured JSON report — per-question scores, strengths, gaps, recommendations, and a hiring signal. The frontend uses this JSON to render results and generate a PDF.

---

## Features

- **Live voice interview** — AI agent speaks and listens in real time using Google Gemini Live
- **4 interview round types** — HR, Technical, Cultural, Negotiation — each with a unique agent persona and evaluation criteria
- **Multilingual** — English, Urdu, or Mixed mode (agent mirrors candidate's language)
- **Automatic scoring** — per-question scores (0–10) + holistic session assessment via Groq
- **Hiring signal** — Recommend / Consider / Pass derived from overall score
- **PDF report** — generated on the frontend from the JSON report (scores, strengths, gaps, recommendations)
- **Audio recording** — full call recorded and stored on Cloudflare R2, playback via presigned URL
- **Document upload** — candidates can upload PDF or DOCX resume and JD (text extracted server-side)

---

## Tech Stack

| Layer | Technology |
|---|---|
| API server | FastAPI + Uvicorn |
| Voice pipeline | Pipecat 1.4.0 |
| AI voice (STT + LLM + TTS) | Google Gemini Live (`gemini-3.1-flash-live-preview`) |
| Scoring LLM | Groq (`llama-3.3-70b-versatile`) |
| Real-time room | LiveKit Cloud |
| Audio recording | LiveKit Egress → Cloudflare R2 |
| Database | MongoDB Atlas |
| Document parsing | pdfplumber (PDF), python-docx (DOCX) |

---

## Project Structure

```
careerpilot/
├── api/
│   ├── session.py          # FastAPI app — all HTTP endpoints
│   ├── upload.py           # POST /upload/document — PDF/DOCX text extraction
│   ├── db.py               # MongoDB read/write helpers (sessions, transcripts, scoring_reports, recordings)
│   ├── r2.py               # Cloudflare R2 presigned URL generation
│   └── token_helper.py     # LiveKit JWT token generation
│
├── bot/
│   ├── main.py             # Bot entry point — spawned as subprocess per session
│   ├── config.py           # Models, voices, timeouts, supported types
│   ├── transcript.py       # TranscriptCollector — captures Q&A pairs from live call
│   ├── agents/
│   │   ├── base_agent.py   # Builds system prompt from template + language instruction
│   │   ├── hr_agent.py
│   │   ├── technical_agent.py
│   │   ├── cultural_agent.py
│   │   └── negotiation_agent.py
│   └── scoring/
│       ├── pipeline.py     # Orchestrates per-question + holistic scoring
│       ├── per_question.py # Scores one Q&A pair via Groq structured output
│       ├── holistic.py     # Full-session assessment via Groq
│       └── schemas.py      # Pydantic models for scoring output + report
│
├── logs/                               # Bot subprocess log files (one per session)
├── test_client.html                    # Browser test client — full flow: upload/paste docs, start interview, live room, results
├── careerpilot.postman_collection.json # Postman collection — import to test all endpoints
├── requirements.txt
├── API_DOCS.md                         # Full API reference
├── FRONTEND_API.md                     # Frontend integration guide (JS examples)
├── SETUP.md                            # Backend setup guide for frontend developers
└── GUIDE.md                            # System architecture deep-dive
```

---

## Setup

### Prerequisites

- Python 3.11+
- A [LiveKit Cloud](https://livekit.io) project
- A [Google AI Studio](https://aistudio.google.com) API key (Gemini Live access)
- A [Groq](https://console.groq.com) API key
- A [MongoDB Atlas](https://www.mongodb.com/atlas) cluster
- A [Cloudflare R2](https://www.cloudflare.com/developer-platform/r2/) bucket

### Install dependencies

```bash
pip install -r requirements.txt
```

### Environment variables

Create a `.env` file in the project root:

```env
# LiveKit
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_api_key
LIVEKIT_API_SECRET=your_api_secret

# Google Gemini
GOOGLE_API_KEY=your_gemini_api_key

# Groq
GROQ_API_KEY=your_groq_api_key

# MongoDB Atlas
MONGODB_URI=mongodb+srv://<user>:<password>@cluster0.xxxxx.mongodb.net/?appName=Cluster0
MONGODB_DB=CareerPilot

# Cloudflare R2
R2_ACCOUNT_ID=your_account_id
R2_TOKEN=your_r2_token
R2_ACCESS_KEY_ID=your_access_key
R2_SECRET_ACCESS_KEY=your_secret_key
R2_BUCKET_NAME=your-bucket-name
```

### Run the server

```bash
uvicorn api.session:app --host 127.0.0.1 --port 8000
```

---

## How It Works

```
1. Frontend uploads resume + JD
        POST /upload/document  (×2)
        Returns extracted plain text

2. Frontend starts an interview session
        POST /interview/start  (include user_id to link session to user account)
        Server: creates LiveKit room → starts Egress recording → spawns bot subprocess
                → writes session index to MongoDB (sessions collection)
        Returns: session_id, livekit_url, user_token

3. Frontend joins the LiveKit room using user_token
        LiveKit JS SDK — real-time voice connection

4. Live interview
        Bot (Gemini Live): greets candidate → asks questions → listens → responds
        TranscriptCollector: captures every agent turn and candidate turn
        Auto-hangup: bot says goodbye → 20s window → call ends automatically

5. Session ends — bot cleanup pipeline
        ├── Write transcript → MongoDB
        ├── Stop LiveKit Egress → .ogg saved to Cloudflare R2
        ├── Write recording metadata → MongoDB
        └── Run scoring (Groq):
              ├── Per-question: score + strengths + gaps + suggestion + English translation
              └── Holistic: overall score + hiring signal + summary + recommendations
              Write scoring report → MongoDB
              Patch session index → MongoDB (sessions collection — adds score + hiring signal)

6. Frontend polls GET /interview/{session_id}/report
        202 while scoring runs → 200 when ready

7. Frontend fetches results
        GET /report      → JSON score report
        GET /transcript  → full conversation
        GET /recording   → presigned R2 URL (1 hr expiry)
        PDF generated on the frontend from the JSON report
```

---

## API Overview

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/upload/document` | Extract text from PDF or DOCX (max 5 MB) |
| `POST` | `/interview/start` | Start a session — returns LiveKit credentials |
| `GET` | `/interview/{id}/report` | JSON score report (202 while scoring) |
| `GET` | `/interview/{id}/transcript` | Full conversation transcript |
| `GET` | `/interview/{id}/recording` | Presigned URL to `.ogg` recording |
| `GET` | `/user/{user_id}/interviews` | All past sessions for a user — newest first |
| `GET` | `/health` | Server health + active session count |

Full API reference: [`API_DOCS.md`](API_DOCS.md)  
Frontend integration guide: [`FRONTEND_API.md`](FRONTEND_API.md)

---

## Interview Round Types

| `round_type` | Agent | Voice | Focus |
|---|---|---|---|
| `hr` | Amna (female) | Kore | Motivation, STAR method, culture fit |
| `technical` | Ahmed (male) | Charon | Technical depth, problem decomposition |
| `cultural` | Hassan (male) | Orus | Values alignment, self-awareness |
| `negotiation` | Ayan (male) | Fenrir | Confidence, justification, pushback handling |

---

## Language Modes

| `language` | Behaviour |
|---|---|
| `english` | Agent speaks English only regardless of candidate's language |
| `urdu` | Agent speaks Urdu only regardless of candidate's language |
| `mixed` | Agent mirrors the candidate — responds in whichever language they use |

For non-English sessions, Groq translates Q&A pairs to English during scoring so the report and PDF are always in English.

---

## Score Reference

| Score | Label | Hiring Signal |
|---|---|---|
| 0–3 | Poor | Pass (< 5.5) |
| 4–5 | Weak | Pass (< 5.5) |
| 6–7 | Average | Consider (5.5–7.4) |
| 8–9 | Strong | Recommend (≥ 7.5) |
| 10 | Exceptional | Recommend (≥ 7.5) |

---

## Local Testing

A browser-based test client (`test_client.html`) is included that covers the full flow:

1. Start the backend: `uvicorn api.session:app --host 127.0.0.1 --port 8000`
2. Serve the HTML file (Chrome blocks `file://` URLs):
   ```bash
   python -m http.server 5500 --bind 127.0.0.1
   ```
3. Open `http://127.0.0.1:5500/test_client.html` in Chrome
4. **Step 1** — Upload a PDF/DOCX resume and JD, or switch to "Paste text" and type directly
5. **Step 2** — Fill in candidate name, round type, language, number of questions
6. **Step 3** — Click "Start Interview" — the client calls `POST /interview/start`, joins LiveKit, and the bot greets you automatically
7. **Step 4** — After the call ends, the client polls for the score report and renders it inline along with transcript and recording buttons
