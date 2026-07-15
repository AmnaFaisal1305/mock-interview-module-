# CareerPilot API Documentation

**Base URL:** `http://127.0.0.1:8000`  
**Version:** 1.0.0  
**Server:** FastAPI + Uvicorn  
**Start command:** `uvicorn api.session:app --host 127.0.0.1 --port 8000`

---

## Overview

The CareerPilot API manages AI-powered voice interview sessions. Each session spawns a Pipecat bot that joins a LiveKit room, conducts the interview, records audio to Cloudflare R2, scores the candidate using Groq, and writes results to MongoDB Atlas.

**Session lifecycle:**

```
POST /upload/document  (optional — extract text from PDF/DOCX)
        │
        ▼
POST /interview/start
        │
        ▼
  Bot joins LiveKit room
  Egress recording starts (→ R2)
        │
        ▼
  [Interview in progress]
        │
        ▼
  Bot says goodbye → auto-hangup
  Scoring runs (Groq)
  Transcript + Report + Recording saved to MongoDB
  Session index updated with score + hiring_signal (sessions collection)
        │
        ▼
GET /report  |  GET /transcript  |  GET /recording
        │
        ▼
GET /user/{user_id}/interviews  (lists all past sessions for the user)
```

---

## Endpoints

### 1. POST /upload/document

Upload a resume or job description as a PDF or DOCX file. Returns extracted plain text to pass into `POST /interview/start`.

**Request**
```
POST http://127.0.0.1:8000/upload/document
Content-Type: multipart/form-data
```

**Form Field**

| Field | Type | Required | Description |
|---|---|---|---|
| `file` | file | ✅ | PDF (`.pdf`) or Word document (`.docx`) — max 5 MB |

**Response — 200 OK**
```json
{
  "filename": "ali_khan_resume.pdf",
  "char_count": 1842,
  "word_count": 298,
  "text": "Ali Khan\nSoftware Engineer\n\nExperience\nBackend Engineer at TechStartup..."
}
```

| Field | Type | Description |
|---|---|---|
| `filename` | string | Original uploaded filename |
| `char_count` | integer | Total characters in extracted text |
| `word_count` | integer | Total words in extracted text |
| `text` | string | Extracted plain text — pass directly as `resume` or `job_description` in `POST /interview/start` |

**Response — 413 Request Entity Too Large**
```json
{
  "detail": "File too large. Maximum allowed size is 5 MB."
}
```

**Response — 415 Unsupported Media Type**
```json
{
  "detail": "Unsupported file type. Upload a PDF (.pdf) or Word document (.docx)."
}
```

**Response — 422 Unprocessable Entity** (corrupt file or scanned image with no text)
```json
{
  "detail": "Extracted text is too short. Make sure the document contains readable text (not a scanned image)."
}
```

> **Note:** Scanned PDFs (images inside a PDF) are not supported — the file must contain selectable text. If the PDF was generated from a Word processor or exported digitally, it will work.

---

### 2. GET /health

Check that the server is running.

**Request**
```
GET http://127.0.0.1:8000/health
```

**Response — 200 OK**
```json
{
  "status": "ok",
  "active_sessions": 1
}
```

| Field | Type | Description |
|---|---|---|
| `status` | string | Always `"ok"` when server is up |
| `active_sessions` | integer | Number of bot subprocesses currently running |

---

### 3. POST /interview/start

Start a new interview session. This will:
- Generate a LiveKit room and tokens
- Pre-create the room on LiveKit server
- Start an Egress recording to Cloudflare R2
- Spawn the Pipecat bot subprocess

**Request**
```
POST http://127.0.0.1:8000/interview/start
Content-Type: application/json
```

**Request Body**
```json
{
  "round_type": "hr",
  "candidate_name": "Ali Khan",
  "resume": "Ali Khan is a software engineer with 3 years of experience in Python and FastAPI...",
  "job_description": "We are hiring a Backend Engineer. Role requires Python, FastAPI, REST API design...",
  "num_questions": 5,
  "language": "english"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `round_type` | string | ✅ | `hr` \| `technical` \| `cultural` \| `negotiation` |
| `candidate_name` | string | ✅ | Used in the bot's greeting. Default: `"Candidate"` |
| `resume` | string | ✅ | Full resume text (min 10 chars) |
| `job_description` | string | ✅ | Full JD text **or** a job role title (e.g. `"Software Engineer"`) — min 2 chars |
| `num_questions` | integer | ❌ | Number of questions (1–15). Default: `5` |
| `language` | string | ❌ | `english` \| `urdu` \| `mixed`. Default: `english` |
| `user_id` | string | ❌ | Authenticated user ID from your auth system — links this session to the user's interview history. Omit for anonymous sessions. |

**Agent names are fixed server-side:**

| round_type | Agent | Voice |
|---|---|---|
| `hr` | Amna (female) | Kore |
| `technical` | Ahmed (male) | Charon |
| `cultural` | Hassan (male) | Orus |
| `negotiation` | Ayan (male) | Fenrir |

**Response — 200 OK**
```json
{
  "session_id": "88f0ad6e-ae6a-4087-ad8f-dec4dd3b771d",
  "room_name": "interview-88f0ad6e-ae6a-4087-ad8f-dec4dd3b771d",
  "livekit_url": "wss://mock-interview-6jl67ty0.livekit.cloud",
  "user_token": "<LiveKit JWT token>"
}
```

| Field | Type | Description |
|---|---|---|
| `session_id` | string (UUID) | Unique session identifier — save this for all subsequent requests |
| `room_name` | string | LiveKit room name to join |
| `livekit_url` | string | LiveKit server WebSocket URL |
| `user_token` | string | JWT token — pass to LiveKit SDK to join the room as the candidate |

**Response — 422 Unprocessable Entity** (invalid input)
```json
{
  "detail": "round_type must be one of ['hr', 'technical', 'cultural', 'negotiation']"
}
```

**Response — 500 Internal Server Error** (LiveKit/config issue)
```json
{
  "detail": "LIVEKIT_URL not configured"
}
```

---

### 4. GET /interview/{session_id}/report

Get the JSON scoring report after the interview ends.

**Request**
```
GET http://127.0.0.1:8000/interview/{session_id}/report
```

**Path Parameter**

| Parameter | Type | Description |
|---|---|---|
| `session_id` | string (UUID) | Returned by `POST /interview/start` |

**Response — 200 OK** (session complete, scoring done)
```json
{
  "session_id": "88f0ad6e-ae6a-4087-ad8f-dec4dd3b771d",
  "round_type": "hr",
  "generated_at": "2026-06-29T14:30:00Z",
  "scoring_status": "completed",
  "overall_score": 7.4,
  "hiring_signal": "Consider",
  "summary_insights": "The candidate demonstrated strong communication skills and relevant experience...",
  "communication_quality": {
    "clarity": 8,
    "confidence": 7,
    "conciseness": 6
  },
  "top_recommendations": [
    "Improve conciseness in technical explanations",
    "Provide more specific examples from past experience",
    "Demonstrate deeper knowledge of system design"
  ],
  "round_specific_insight": "Strong cultural alignment with startup environment...",
  "questions": [
    {
      "question": "Tell me about yourself.",
      "answer": "I am a software engineer with 3 years...",
      "score": 7,
      "score_label": "Average",
      "feedback": "Good overview but lacked specific achievements."
    }
  ],
  "question_count": 5
}
```

**Score Labels:**

| Score | Label |
|---|---|
| 0–3 | Poor |
| 4–5 | Weak |
| 6–7 | Average |
| 8–9 | Strong |
| 10 | Exceptional |

**Hiring Signal Thresholds:**

| Signal | Condition |
|---|---|
| `Recommend` | overall_score ≥ 7.5 |
| `Consider` | 5.5 ≤ overall_score < 7.5 |
| `Pass` | overall_score < 5.5 |

**Response — 202 Accepted** (bot still running / scoring in progress)
```json
{
  "detail": "Interview still in progress or scoring not yet complete"
}
```

**Response — 404 Not Found**
```json
{
  "detail": "Report not found for this session_id"
}
```

---

### 5. GET /interview/{session_id}/transcript

Get the full conversation transcript for a session.

**Request**
```
GET http://127.0.0.1:8000/interview/{session_id}/transcript
```

**Response — 200 OK**
```json
{
  "session_id": "88f0ad6e-ae6a-4087-ad8f-dec4dd3b771d",
  "round_type": "hr",
  "turns": [
    {
      "role": "bot",
      "text": "Hi, I'm Amna from the HR team. Thank you for joining us today...",
      "timestamp": "2026-06-29T14:00:01Z"
    },
    {
      "role": "user",
      "text": "Thank you for having me.",
      "timestamp": "2026-06-29T14:00:08Z"
    }
  ],
  "recorded_at": "2026-06-29T14:00:00Z"
}
```

**Response — 404 Not Found** (session not yet ended or doesn't exist)
```json
{
  "detail": "Transcript not found for this session_id"
}
```

---

### 6. GET /interview/{session_id}/recording

Get a presigned URL to play back the audio recording (`.ogg` format, hosted on Cloudflare R2). URL expires in 1 hour.

**Request**
```
GET http://127.0.0.1:8000/interview/{session_id}/recording
```

**Response — 200 OK**
```json
{
  "session_id": "88f0ad6e-ae6a-4087-ad8f-dec4dd3b771d",
  "url": "https://<account>.r2.cloudflarestorage.com/recordings/88f0ad6e-ae6a-4087-ad8f-dec4dd3b771d.ogg?X-Amz-Signature=...",
  "format": "ogg",
  "recorded_at": "2026-06-29T14:00:00Z"
}
```

| Field | Type | Description |
|---|---|---|
| `url` | string | Presigned S3-compatible URL. Valid for 1 hour. Open in browser or audio player. |
| `format` | string | Always `"ogg"` |
| `recorded_at` | string | ISO 8601 timestamp of when recording was saved |

**Response — 404 Not Found** (session not yet ended or egress failed)
```json
{
  "detail": "Recording not found for this session_id"
}
```

---

### 7. GET /user/{user_id}/interviews

Get all past interview sessions for a user account, newest first. Use this to build a history page after the user logs in.

**Request**
```
GET http://127.0.0.1:8000/user/{user_id}/interviews
```

**Path Parameter**

| Parameter | Type | Description |
|---|---|---|
| `user_id` | string | The user ID passed to `POST /interview/start` |

**Response — 200 OK**
```json
{
  "user_id": "firebase_uid_abc123",
  "count": 2,
  "interviews": [
    {
      "session_id": "88f0ad6e-ae6a-4087-ad8f-dec4dd3b771d",
      "round_type": "hr",
      "candidate_name": "Ali Khan",
      "created_at": "2026-07-14T10:00:00Z",
      "scoring_status": "complete",
      "overall_score": 7.4,
      "hiring_signal": "Consider"
    },
    {
      "session_id": "3d625fef-1234-5678-abcd-ef0123456789",
      "round_type": "technical",
      "candidate_name": "Ali Khan",
      "created_at": "2026-07-10T14:30:00Z",
      "scoring_status": "complete",
      "overall_score": 8.1,
      "hiring_signal": "Recommend"
    }
  ]
}
```

| Field | Type | Description |
|---|---|---|
| `session_id` | string | Use with `/interview/{session_id}/report`, `/transcript`, `/recording` |
| `round_type` | string | Interview round type |
| `candidate_name` | string | Name submitted at session start |
| `created_at` | string | ISO 8601 timestamp of when the interview started |
| `scoring_status` | string | `pending` while interview is in progress · `complete` · `partial` · `failed` |
| `overall_score` | float | 0.0–10.0 — only present once scoring is complete |
| `hiring_signal` | string | `Recommend` / `Consider` / `Pass` — only present once scoring is complete |

> Sessions where `user_id` was not provided in `POST /interview/start` will not appear here.

---

## Error Reference

| HTTP Code | Meaning | When |
|---|---|---|
| 200 | OK | Request succeeded |
| 202 | Accepted | Bot still running / scoring in progress — poll again after session ends |
| 404 | Not Found | Session doesn't exist or data not yet written (transcript/recording only available after session ends) |
| 413 | Request Entity Too Large | Uploaded file exceeds 5 MB limit |
| 415 | Unsupported Media Type | Uploaded file is not PDF or DOCX |
| 422 | Unprocessable Entity | Invalid input (bad `round_type`, `language`, missing fields) or corrupt/image-only document |
| 500 | Internal Server Error | LiveKit/Gemini/Groq/MongoDB config error |

---

## Testing Flow

### Step 0 — Upload documents (optional)
```bash
# Upload resume PDF
curl -X POST http://127.0.0.1:8000/upload/document \
  -F "file=@/path/to/ali_khan_resume.pdf"

# Upload job description DOCX
curl -X POST http://127.0.0.1:8000/upload/document \
  -F "file=@/path/to/job_description.docx"
```

Copy the `text` field from each response and use it in Step 1 below.

### Step 1 — Start a session
```bash
curl -X POST http://127.0.0.1:8000/interview/start \
  -H "Content-Type: application/json" \
  -d '{
    "round_type": "hr",
    "candidate_name": "Ali Khan",
    "resume": "Ali Khan is a software engineer with 3 years of Python experience...",
    "job_description": "Backend Engineer role requiring FastAPI, REST API design, cloud infrastructure.",
    "num_questions": 3,
    "language": "english"
  }'
```

Save the `session_id` from the response.

### Step 2 — Join the interview
Serve and open the browser test client:
```bash
python -m http.server 5500 --bind 127.0.0.1
# then open http://127.0.0.1:5500/test_client.html in Chrome
```
The test client handles everything — paste or upload your resume/JD, fill the form, click "Start Interview". It calls `POST /interview/start` automatically, joins the LiveKit room, and connects the bot audio. Speak with the agent until it says goodbye and ends the call.

### Step 3 — Fetch results
```bash
# Scoring report
curl http://127.0.0.1:8000/interview/{session_id}/report

# Transcript
curl http://127.0.0.1:8000/interview/{session_id}/transcript

# Recording URL
curl http://127.0.0.1:8000/interview/{session_id}/recording
```

> **Note:** Report, transcript, and recording are only available **after the session ends**. While the bot is running, `/report` returns 202 and `/transcript`/`/recording` return 404.

---

## Postman Collection

A ready-to-use Postman collection is included in the repo as `careerpilot.postman_collection.json`. Import it into any Postman account via **File → Import**.

- `{{base_url}}` collection variable defaults to `http://127.0.0.1:8000`
- Running `POST /interview/start` automatically saves `{{session_id}}` for all subsequent requests
- Running `POST /upload/document` automatically saves `{{resume_text}}` for use in start
- All result endpoints use `{{session_id}}` — no manual copy-paste needed
- Test scripts validate status codes and response shapes on every request
