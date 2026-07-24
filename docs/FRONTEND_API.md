# CareerPilot — Frontend Integration Guide

**Base URL:** `http://127.0.0.1:8000` (local) · `https://api.career-pilot.tech` (production)  
**Every endpoint below lives under the `/ai/interview` prefix** — e.g. locally that's `http://127.0.0.1:8000/ai/interview/start`, in production `https://api.career-pilot.tech/ai/interview/start`. This includes `/upload/document`, which is *not* a top-level route.  
**All requests/responses:** `application/json` unless noted  
**CORS:** Restricted to `https://www.career-pilot.tech` (production) and `http://localhost:<any port>` (Vite dev — 5173, 5174, etc. all work). Requests from any other origin will be blocked by the browser.

---

## Complete User Flow

```
1. User uploads resume + JD files
         │  POST /ai/interview/upload/document  (×2)
         ▼
2. User fills interview form (round, name, questions)
         │  POST /ai/interview/start
         ▼
3. Frontend joins LiveKit room with user_token
         │  LiveKit JS SDK
         ▼
4. Live voice interview with AI agent
         │  (real-time, no API calls)
         ▼
5. Call ends (bot auto-hangs up)
         │  Poll GET /ai/interview/{session_id}/report
         ▼
6. Show score report + generate PDF on frontend + play recording
         │  GET /report  /transcript  /recording
         ▼
```

---

## Endpoints

---

### 1. Upload Document
**Use when:** User selects a resume or job description file from their device.  
**Call twice** — once for resume, once for job description.

```
POST /ai/interview/upload/document
Content-Type: multipart/form-data
```

**Form field:**

| Field | Type | Value |
|---|---|---|
| `file` | File | PDF or DOCX — max 5 MB |

> **Trigger this on file select (`onChange`), not on form submit.** This way the PDF is parsed in the background while the user fills in the rest of the form. By the time they click "Start Interview", the text is already ready — zero extra wait.

**Example (JavaScript):**
```js
let resumeText = null;
let jdText     = null;

async function handleFileSelect(file, type) {
  // type = 'resume' | 'jd'
  const formData = new FormData();
  formData.append('file', file);  // send the raw file — don't set Content-Type header

  // Show a loading indicator here ("Parsing…")
  const res  = await fetch('http://127.0.0.1:8000/ai/interview/upload/document', {
    method: 'POST',
    body: formData,
  });
  const data = await res.json();

  if (!res.ok) {
    // Show error to user: data.detail
    return;
  }

  // Show confirmation: `${data.word_count} words extracted`
  if (type === 'resume') resumeText = data.text;
  else                   jdText     = data.text;

  // Enable "Start Interview" button once both files are parsed + form is filled
  checkReady();
}

// Wire up to your file input
resumeInput.addEventListener('change', e => handleFileSelect(e.target.files[0], 'resume'));
jdInput.addEventListener('change',     e => handleFileSelect(e.target.files[0], 'jd'));

// Then in POST /ai/interview/start — pass the stored text, not the file
body: JSON.stringify({
  resume:          resumeText,   // text extracted by the backend
  job_description: jdText,
  // ... other fields
})
```

**Success — 200:**
```json
{
  "filename": "ali_khan_resume.pdf",
  "char_count": 1842,
  "word_count": 298,
  "text": "Ali Khan\nSoftware Engineer\n\nExperience\nBackend Engineer..."
}
```

> Save `data.text` and use it as the `resume` or `job_description` field in `POST /ai/interview/start`.

**Errors:**

| Code | Reason | Show user |
|---|---|---|
| 413 | File larger than 5 MB | "File is too large. Please upload a file under 5 MB." |
| 415 | Not a PDF or DOCX | "Please upload a PDF or Word document (.docx)." |
| 422 | Scanned image / no readable text | "Could not read text from this file. Make sure it's not a scanned image." |

---

### 2. Start Interview
**Use when:** User submits the interview form.

```
POST /ai/interview/start
Content-Type: application/json
```

**Request body:**
```json
{
  "round_type": "hr",
  "candidate_name": "Ali Khan",
  "resume": "<text from upload step>",
  "job_description": "<text from upload step>",
  "num_questions": 5,
  "language": "english"
}
```

**Fields:**

| Field | Type | Required | Options | Default |
|---|---|---|---|---|
| `round_type` | string | ✅ | `hr` `technical` `cultural` `negotiation` | — |
| `candidate_name` | string | ✅ | Any name — used in greeting | `"Candidate"` |
| `resume` | string | ✅ | Plain text (from upload or typed) | — |
| `job_description` | string | ✅ | Full JD text **or** a job role title (e.g. `"Software Engineer"`) — from upload, typed, or just the role name | — |
| `num_questions` | integer | ❌ | 3–15 | `5` |
| `language` | string | ❌ | `english` `urdu` `mixed` | `english` |
| `user_id` | string | ❌ | Authenticated user ID from your auth system (Firebase UID, Supabase UUID, etc.). Include this to link the session to the user's history. Omit for anonymous sessions. | `null` |

> **Agent names and voices are fixed** — the frontend cannot set them.  
> HR → Amna, Technical → Ahmed, Cultural → Hassan, Negotiation → Ayan

**Success — 200:**
```json
{
  "session_id": "88f0ad6e-ae6a-4087-ad8f-dec4dd3b771d",
  "room_name": "interview-88f0ad6e-ae6a-4087-ad8f-dec4dd3b771d",
  "livekit_url": "ws://18.143.170.133:7880",
  "user_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6..."
}
```

> **`livekit_url` is currently a plain `ws://` URL to the self-hosted LiveKit server's IP** (not `wss://`/TLS — self-hosted on EC2, not LiveKit Cloud). This works fine as long as the frontend itself is also served over plain HTTP. If the frontend moves to HTTPS, browsers will block a `ws://` connection from a secure page (mixed-content policy) — at that point the backend needs `LIVEKIT_PUBLIC_URL` set to a `wss://` domain (behind TLS) for this to keep working.

**What to do with the response:**

| Field | What to do |
|---|---|
| `session_id` | **Save this** — you'll need it for every subsequent API call |
| `livekit_url` | Pass to LiveKit JS SDK as the server URL |
| `user_token` | Pass to LiveKit JS SDK to join the room |
| `room_name` | Optional — for display purposes only |

**Errors:**

| Code | Reason | Show user |
|---|---|---|
| 422 | Invalid `round_type` or `language` | "Invalid round type selected." |
| 500 | Server misconfiguration | "Something went wrong. Please try again." |

---

### 3. Join the LiveKit Room
**Not an API call** — use the [LiveKit JS SDK](https://docs.livekit.io/client-sdk-js/).

```js
import { Room } from 'livekit-client';

const room = new Room();
await room.connect(livekit_url, user_token);
```

The AI agent joins automatically. The interview begins as soon as the user's mic is active.  
The call ends automatically when the agent says goodbye — you don't need to call any API to end it.

---

### 4. Get Score Report
**Use when:** Call ends. Poll this endpoint until you get a 200.

```
GET /ai/interview/{session_id}/report
```

**Success — 200** (interview ended + scoring complete):
```json
{
  "session_id": "88f0ad6e-...",
  "round_type": "hr",
  "generated_at": "2026-06-29T14:30:00Z",
  "scoring_status": "complete",
  "overall_score": 7.4,
  "hiring_signal": "Consider",
  "summary_insights": "The candidate demonstrated strong communication skills...",
  "communication_quality": {
    "clarity": "Good — answers were clear and well-paced",
    "conciseness": "Inconsistent — Q1 was too brief, Q3 ran too long",
    "confidence_markers": "Confident when citing achievements, hesitant on leadership"
  },
  "top_recommendations": [
    "Use the STAR method more consistently across all answers",
    "Provide specific numbers and outcomes, not just actions",
    "Prepare a stronger answer for 'tell me about yourself'"
  ],
  "round_specific_insight": "STAR usage was attempted in Q2 but dropped in Q3.",
  "question_count": 5,
  "questions": [
    {
      "question_index": 0,
      "question": "Walk me through your experience.",
      "answer": "I worked at TechCorp for 2 years...",
      "question_en": "",
      "answer_en": "",
      "score": 6,
      "score_before_penalty": 7,
      "score_penalty": -1,
      "strengths": ["Gave specific timeframe", "Mentioned key technology"],
      "gaps": ["No measurable outcome mentioned"],
      "suggestion": "Add what you achieved — numbers, impact, or recognition.",
      "clarifications": [
        {
          "candidate": "can you explain in easy words",
          "agent": "Sure — let me rephrase that question more simply...",
          "penalty": true,
          "marking": "-1"
        }
      ]
    }
  ]
}
```

**`clarifications[]`** — repeat/probe sub-turns within a question, visible in the report:

| Field | Meaning |
|---|---|
| `candidate` | What the candidate said (repeat request, simplification request, or weak answer before a probe) |
| `agent` | How the agent responded (rephrased, simplified, or probed) |
| `penalty` | `true` if candidate asked for a simpler explanation — `-1` was deducted from the score |
| `marking` | `"No Marking"` for plain repeats / probes — `"-1"` for simplification requests |

Show these under the question in the report so the candidate can see where marks were deducted.

**`score_before_penalty`** — the raw LLM score before any penalty.  
**`score_penalty`** — `0` or `-1`. Applied when the candidate asked for the question in simpler words.  
**`score`** — final score (`score_before_penalty + score_penalty`, floored at 0). Use this for display.

**Score labels to display** (derive on the frontend from `score`):

| Score | Label | Color suggestion |
|---|---|---|
| 0–3 | Poor | Red |
| 4–5 | Weak | Orange |
| 6–7 | Average | Yellow |
| 8–9 | Strong | Green |
| 10 | Exceptional | Dark green |

> A score of `-1` means scoring failed for that question — show "Not scored" instead of a label.

**Hiring signal:**

| Value | Meaning |
|---|---|
| `Recommend` | Overall score ≥ 7.5 |
| `Consider` | Overall score 5.5–7.4 |
| `Pass` | Overall score < 5.5 |

**Polling logic:**

```js
async function waitForReport(sessionId) {
  while (true) {
    const res = await fetch(`/ai/interview/${sessionId}/report`);
    if (res.status === 200) return res.json();
    if (res.status === 202) {
      // still in progress — wait and retry
      await new Promise(r => setTimeout(r, 4000));
      continue;
    }
    throw new Error('Report not available');
  }
}
```

**Responses:**

| Code | Meaning |
|---|---|
| 200 | Report ready — render it |
| 202 | Scoring still running — keep polling every 4–5 seconds |
| 404 | Session not found |

---

### 5. Download PDF Report

PDF generation is handled **on the frontend** — use the JSON from `GET /report` and render it with a library of your choice (e.g. [jsPDF](https://github.com/parallax/jsPDF), [React-PDF](https://react-pdf.org/), or `window.print()`).

The report JSON from step 4 contains everything you need:
- `overall_score`, `hiring_signal`, `summary_insights`
- `communication_quality`, `top_recommendations`, `round_specific_insight`
- `questions[]` — per-question `score`, `score_before_penalty`, `score_penalty`, `strengths`, `gaps`, `suggestion`, `clarifications[]`

---

### 6. Get Transcript
**Use when:** Showing the full conversation history.

```
GET /ai/interview/{session_id}/transcript
```

**Success — 200:**
```json
{
  "session_id": "88f0ad6e-...",
  "round_type": "hr",
  "entries": [
    {
      "role": "agent",
      "content": "Hi, I'm Amna from the HR team. Thank you for joining us today.",
      "timestamp": "2026-06-29T14:00:01Z"
    },
    {
      "role": "candidate",
      "content": "Thank you for having me.",
      "timestamp": "2026-06-29T14:00:08Z"
    }
  ],
  "entry_count": 12,
  "written_at": "2026-06-29T14:15:30Z"
}
```

| `role` value | Meaning |
|---|---|
| `agent` | AI interviewer |
| `candidate` | User / candidate |

**Responses:**

| Code | Meaning |
|---|---|
| 200 | Transcript ready |
| 404 | Session not found or interview not yet ended |

---

### 7. Get Recording
**Use when:** User wants to listen back to the interview.

```
GET /ai/interview/{session_id}/recording
```

**Success — 200:**
```json
{
  "session_id": "88f0ad6e-...",
  "url": "https://careepilot.s3.ap-southeast-1.amazonaws.com/recordings/88f0ad6e-....wav?X-Amz-Signature=...",
  "format": "wav",
  "recorded_at": "2026-06-29T14:00:00Z"
}
```

Pass `data.url` directly to an `<audio>` element:

```jsx
<audio controls src={data.url} />
```

> The URL expires after **24 hours**. If the user revisits the next day, call this endpoint again to get a fresh URL.

**Responses:**

| Code | Meaning |
|---|---|
| 200 | URL ready |
| 404 | Recording not found — upload to S3 may have failed or session not ended yet |

---

### 8. Health Check
**Use when:** Checking if the server is up.

```
GET /ai/interview/health
```

**Success — 200:**
```json
{
  "status": "ok",
  "active_sessions": 2
}
```

---

### 9. Get User Interview History
**Use when:** User logs in — fetch their past interviews to show a history page.

```
GET /ai/interview/user/{user_id}/interviews
```

**Example (JavaScript):**
```js
async function getUserHistory(userId) {
  const res = await fetch(`${BASE_URL}/ai/interview/user/${userId}/interviews`);
  const data = await res.json();
  // data.interviews — array of past sessions, newest first
  return data.interviews;
}
```

**Success — 200:**
```json
{
  "user_id": "firebase_uid_abc123",
  "count": 2,
  "interviews": [
    {
      "session_id": "88f0ad6e-...",
      "round_type": "hr",
      "candidate_name": "Ali Khan",
      "created_at": "2026-07-14T10:00:00Z",
      "scoring_status": "complete",
      "overall_score": 7.4,
      "hiring_signal": "Consider"
    }
  ]
}
```

Use each entry's `session_id` with `/interview/{session_id}/report`, `/transcript`, and `/recording` to load the full details for any past session.

> **`scoring_status: "pending"`** means the interview ended but scoring hasn't completed yet. Poll `/interview/{session_id}/report` (202 → 200) for that session.

**Responses:**

| Code | Meaning |
|---|---|
| 200 | Always returns 200 — empty `interviews` array if user has no sessions |

---

## Error Handling — Quick Reference

| Code | What happened | What to show |
|---|---|---|
| 200 | OK | Render the data |
| 202 | Still processing | Loading spinner — poll again in 4–5s |
| 404 | Not found | "Result not available yet" or "Session not found" |
| 413 | File too large | "File must be under 5 MB" |
| 415 | Wrong file type | "Only PDF or DOCX files are supported" |
| 422 | Bad input | Show the `detail` field from the response body |
| 500 | Server error | "Something went wrong. Please try again later." |

---

## Suggested Page Flow

```
/history  (on login)
  └── GET /ai/interview/user/{user_id}/interviews → list of past sessions with scores

/start
  ├── Upload Resume     → POST /ai/interview/upload/document  → save resume_text
  ├── Upload JD         → POST /ai/interview/upload/document  → save jd_text
  ├── Fill form         → round_type, candidate_name, num_questions, language
  └── Click Start       → POST /ai/interview/start (include user_id) → save session_id

/interview
  ├── Connect to LiveKit room using user_token + livekit_url
  ├── Show mic/speaker UI
  └── Detect call end   → navigate to /results

/results/{session_id}
  ├── Poll GET /report  → show score, hiring_signal, per-question breakdown
  ├── Button: Download  → generate PDF from report JSON on frontend
  ├── Tab: Transcript   → GET /transcript          → show conversation
  └── Tab: Recording    → GET /recording           → <audio> player
```

---

## Notes for the Frontend

- **Don't store the `user_token`** beyond the session — it expires.
- **Do store `session_id`** — it's the key to everything after the interview.
- **Recording URL expires in 24 hours** — always fetch fresh rather than caching across sessions.
- **PDF is generated on the frontend** — use the JSON from `/report` with jsPDF, React-PDF, or any PDF library.
- **The call ends itself** — the AI agent auto-hangs up. You don't need to end it via API.
- **Text or file for resume/JD** — both work. If the user types their resume, skip the upload step and pass the text directly.
