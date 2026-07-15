# CareerPilot — Frontend User Journey

This document walks through every screen, every user action, every API call, and every UI state in the CareerPilot application — in the order the user experiences them. Use it as the implementation specification for the frontend.

**Related docs:**
- `FRONTEND_API.md` — full API reference with request/response shapes
- `API_DOCS.md` — complete endpoint docs with error codes
- `GUIDE.md` — backend architecture and flow

---

## Overview

```
Page 1: Setup
  ├── Upload resume + JD (or paste text)
  └── Fill interview form → click Start Interview

Page 2: Interview in Progress
  ├── Audio connects (handled internally — no technical UI)
  ├── Candidate speaks with AI interviewer
  └── Interview ends automatically when complete

Page 3: Results
  ├── Score + hiring signal
  ├── Per-question breakdown
  ├── Full transcript
  └── Audio recording player
```

---

## Page 1 — Setup

### What the user sees

- A form with two document sections (Resume, Job Description)
- Each document section has two tabs: **Upload file** | **Paste text**
- The Job Description section also accepts a plain job role title (e.g. "Software Engineer") if no full JD is available
- Interview settings: candidate name, round type, language, number of questions
- A **Start Interview** button — disabled until all inputs are ready

### Section 1A — Document Upload (Upload file tab)

**User action:** Clicks the Resume "Upload file" tab, clicks "Choose file", picks a PDF or DOCX.

**What happens immediately (on file select — do not wait for form submit):**

```
User selects file
   │
   ├── Show file name + "Parsing…" loading indicator
   │
   └── POST /upload/document
         body: multipart/form-data { file: <chosen file> }
         │
         ├── 200 OK
         │     → Save data.text as resumeText (internal state, not shown to user)
         │     → Show green chip: "✓ {data.word_count} words extracted"
         │     → Call checkReady()
         │
         ├── 413  → Show red chip: "File is too large. Max 5 MB."
         ├── 415  → Show red chip: "Only PDF or DOCX files are supported."
         └── 422  → Show red chip: "Could not read text. Make sure it's not a scanned image."
```

> **Critical:** Upload fires on `onChange` / file select, not on Start click. This lets PDF parsing run in the background while the user fills out the rest of the form — by the time they click Start, the text is already ready.

The same flow applies for Job Description — same endpoint, same logic, stored in `jdText`.

**State to track:**
- `resumeText: string | null` — set after successful upload or paste
- `jdText: string | null` — same for job description

---

### Section 1B — Document Paste (Paste text tab)

**User action:** Clicks "Paste text" tab, types or pastes into the textarea.

**What happens on every keystroke:**
- Count words in the textarea
- If length ≥ 10 characters: set `resumeText = textarea.value`, show green chip "✓ {wordCount} words"
- If length < 10 characters: set `resumeText = null`, show red chip "Too short" (or nothing if empty)
- Call `checkReady()`

No API call is made for pasted text — the text goes directly to `POST /interview/start`.

**Tab switching:** If the user switches from Paste → Upload tab, clear `resumeText` / `jdText` so the file upload state takes over. If they switch Upload → Paste, re-read the textarea immediately.

---

### Section 2 — Interview Form

Fields:

| Field | Type | Required | Options | Default |
|---|---|---|---|---|
| Candidate Name | text input | ✅ | Any name | — |
| Round Type | select | ✅ | `hr` \| `technical` \| `cultural` \| `negotiation` | `hr` |
| Language | select | ❌ | `english` \| `urdu` \| `mixed` | `english` |
| Number of Questions | number input | ❌ | 1–15 | `5` |

> **Agent names and voices are fixed server-side** — do not expose them as user-configurable options.
> HR → Amna (female), Technical → Ahmed (male), Cultural → Hassan (male), Negotiation → Ayan (male)

---

### Section 3 — Start Button Gate

The Start Interview button must be **disabled** until all three conditions are met:
1. `resumeText` is set (file uploaded successfully OR enough text pasted)
2. `jdText` is set
3. Candidate name field is not empty

Check `checkReady()` after every relevant state change: file upload response, text input, name input.

---

### On Start Interview click

**This click is a user gesture — use it to unlock Chrome's AudioContext:**

```js
async function startInterview() {
  // MUST be first, inside the click handler, before any async work
  try { const _ctx = new AudioContext(); await _ctx.resume(); } catch (_) {}

  // disable button to prevent double-submit
  setStartButtonDisabled(true);
  showStatus("Starting interview session…");

  const res = await fetch(`${BASE_URL}/interview/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      resume:          resumeText,
      job_description: jdText,
      candidate_name:  candidateName,
      round_type:      roundType,
      num_questions:   numQuestions,
      language:        language,
      user_id:         currentUserId,   // from your auth system — omit if not logged in
    }),
  });

  const data = await res.json();

  if (!res.ok) {
    showError(data.detail || 'Failed to start. Please try again.');
    setStartButtonDisabled(false);
    return;
  }

  // Save session_id — needed for every subsequent request
  sessionId = data.session_id;

  // Navigate to live interview
  connectToRoom(data.livekit_url, data.user_token);
}
```

**Response (200):**
```json
{
  "session_id": "88f0ad6e-ae6a-4087-ad8f-dec4dd3b771d",
  "room_name": "interview-88f0ad6e-ae6a-4087-ad8f-dec4dd3b771d",
  "livekit_url": "wss://mock-interview-6jl67ty0.livekit.cloud",
  "user_token": "eyJhbGciOiJIUzI1NiIs..."
}
```

Save `session_id` — you'll need it for every API call on Pages 2 and 3.

**Errors:**

| Code | Show user |
|---|---|
| 422 | "Invalid round type or language selected." |
| 500 | "Something went wrong on the server. Please try again." |

---

## Page 2 — Interview in Progress

### On entering this page

**What the user sees — keep it interview-focused, no technical terms:**
- Status message: "Connecting you to your interviewer…" (while audio is setting up)
- Once connected: "Your interview has started. Please speak clearly." 
- Mic active indicator — e.g. a subtle mic icon or pulse animation
- Instruction text: "Your interviewer will introduce themselves and begin. Answer naturally — the interview will end on its own when complete."
- Optional: "Leave Interview" button (do not call it "Leave Room" or reference any technical system)

> **Never show "LiveKit", "room", "bot", "session", or "token" to the candidate.** These are implementation details. From the candidate's perspective they are simply in an interview with a human-sounding interviewer.

---

### Audio Connection (LiveKit — internal, not visible to user)

Install the LiveKit client SDK:
```bash
npm install livekit-client
```

```js
import { Room, RoomEvent, Track } from 'livekit-client';

async function connectToRoom(lkUrl, token) {
  const room = new Room({
    audioCaptureDefaults: {
      echoCancellation: true,
      noiseSuppression: true,
    },
  });

  // 1. Receive the interviewer's audio
  room.on(RoomEvent.TrackSubscribed, (track) => {
    if (track.kind === Track.Kind.Audio) {
      const el = track.attach();
      el.autoplay = true;
      el.muted = false;
      el.volume = 1.0;
      document.body.appendChild(el);   // or your audio container
      el.play().catch(() => {});        // force playback — Chrome may block without this
    }
  });

  // 2. Handle Chrome's autoplay policy blocking audio
  room.on(RoomEvent.AudioPlaybackStatusChanged, () => {
    if (!room.canPlaybackAudio) {
      showEnableAudioButton();   // see "Audio Blocked" section below
    } else {
      hideEnableAudioButton();
      showStatus("Your interview has started. Please speak clearly.");
    }
  });

  // 3. Detect when the interview ends
  room.on(RoomEvent.Disconnected, () => {
    // User-facing: no technical wording
    showStatus("Your interview is complete. Preparing your results…");
    navigateToResults(sessionId);  // or start polling on same page
  });

  // Connect and enable mic
  await room.connect(lkUrl, token);
  await room.localParticipant.setMicrophoneEnabled(true);

  showStatus("Your interview has started. Please speak clearly.");
}
```

---

### Chrome Audio Policy — "Audio Blocked" fallback

Chrome blocks audio output until the user has interacted with the page. Two layers of defence are used:

**Layer 1 (primary — usually sufficient):** `new AudioContext(); await ctx.resume()` runs inside the "Start Interview" click handler. This click counts as the required user gesture, so audio is unlocked before the connection is made.

**Layer 2 (fallback):** If audio is still blocked after connecting, show a button the candidate can click. Use interview-friendly wording — no technical terms:

```jsx
// Show only when room.canPlaybackAudio === false
<button onClick={async () => {
  await room.startAudio();
  hideEnableAudioButton();
}}>
  🔊 Tap to hear your interviewer
</button>
```

Hide this button as soon as audio is unblocked. Do not mention "audio context", "autoplay", or "browser policy" to the user.

---

### Interviewer start delay

After audio connects, the interviewer takes a few seconds before speaking (server-side warm-up — the frontend does not control this). Show a holding message so the candidate is not confused by the silence:

> "Your interviewer is ready. They will introduce themselves shortly."

The interviewer speaks within approximately 4–6 seconds of the candidate connecting. No spinner or countdown is needed — just the reassuring message.

---

### During the interview

No API calls are made while the interview is running. Everything is real-time audio.

States to display:
- Mic active indicator — subtle pulse or icon so the candidate knows they are being heard
- Interviewer speaking indicator (optional) — use `RoomEvent.ActiveSpeakersChanged` to detect when the interviewer's audio is active; show something like "Interviewer is speaking…" or a waveform animation
- Do **not** show session IDs, room names, or any internal identifiers to the candidate

---

### Interview end

The interview ends automatically — the candidate does not need to press anything. The interviewer wraps up after asking all the questions and delivering a closing statement. The audio connection then closes on its own.

When this happens, `RoomEvent.Disconnected` fires internally — navigate to the Results page and show:

> "Your interview is complete. We're preparing your results — this usually takes under a minute."

**"Leave Interview" button (optional):** Allows the candidate to exit early. Internally calls `room.disconnect()`, which fires the same `RoomEvent.Disconnected` event. Scoring still runs on the server; the candidate can still see their results. Label this button "Leave Interview" — not "Disconnect", "Leave Room", or "Hang Up".

---

## Page 3 — Results

Enter this page after `RoomEvent.Disconnected` fires. Navigate with `session_id` in the URL (e.g. `/results/88f0ad6e-...`) so the user can bookmark or refresh.

### State: Results being prepared

Show a loading indicator with a friendly message — no technical terms:

> "Analysing your responses… This usually takes under a minute."

Poll `GET /interview/{session_id}/report` every 4 seconds in the background:

```js
async function pollReport(sessionId) {
  while (true) {
    const res = await fetch(`${BASE_URL}/interview/${sessionId}/report`);

    if (res.status === 200) {
      const report = await res.json();
      renderReport(report);
      return;
    }

    if (res.status === 202) {
      // Scoring still running — wait and retry
      await new Promise(r => setTimeout(r, 4000));
      continue;
    }

    // 404 or other error
    showError("Could not retrieve report. The session may have expired.");
    return;
  }
}
```

Scoring typically completes within 30–60 seconds of the call ending (depends on number of questions and Groq API latency).

---

### Report JSON shape

```json
{
  "session_id": "88f0ad6e-ae6a-4087-ad8f-dec4dd3b771d",
  "round_type": "hr",
  "generated_at": "2026-06-29T14:30:00Z",
  "scoring_status": "complete",
  "overall_score": 7.4,
  "hiring_signal": "Consider",
  "summary_insights": "The candidate demonstrated strong communication skills…",
  "communication_quality": {
    "clarity": "Good — answers were clear and well-paced",
    "conciseness": "Inconsistent — Q1 was too brief, Q3 ran too long",
    "confidence_markers": "Confident when citing achievements, hesitant on leadership"
  },
  "top_recommendations": [
    "Use the STAR method more consistently across all answers",
    "Provide specific numbers and outcomes, not just actions",
    "Prepare a stronger closing answer for 'tell me about yourself'"
  ],
  "round_specific_insight": "STAR usage was attempted in Q2 but dropped entirely in Q3.",
  "question_count": 5,
  "questions": [
    {
      "question_index": 0,
      "question": "Walk me through your experience.",
      "answer": "I worked at TechCorp for 2 years…",
      "question_en": "",
      "answer_en": "",
      "score": 7,
      "strengths": ["Gave specific timeframe", "Mentioned key technology"],
      "gaps": ["No measurable outcome mentioned"],
      "suggestion": "Add what you achieved — numbers, impact, or recognition."
    }
  ]
}
```

> If the interview was conducted in Urdu or mixed language, `question` and `answer` may contain Urdu/mixed text. `question_en` and `answer_en` contain English translations — use these for display if non-empty, otherwise use `question`/`answer`.

---

### Section A — Score summary

Display:
- `overall_score` (0.0–10.0) — large, prominent
- `hiring_signal` with colour coding:

| Value | Colour |
|---|---|
| `Recommend` | Green |
| `Consider` | Yellow / amber |
| `Pass` | Red |

- A score bar (fill = `overall_score / 10 * 100%`)
- `summary_insights` — a paragraph of narrative feedback

---

### Section B — Communication quality

`communication_quality` contains three string fields — each is a short descriptive sentence from the AI:

| Field | What to label it |
|---|---|
| `clarity` | Clarity |
| `conciseness` | Conciseness |
| `confidence_markers` | Confidence |

Display as a list or cards. These are free-form strings, not numeric scores.

---

### Section C — Top recommendations

`top_recommendations` is an array of exactly 3 strings. Each is a specific, actionable piece of advice referencing a particular question or moment.

Display as a bulleted or numbered list.

---

### Section D — Per-question breakdown

`questions[]` — one entry per Q&A pair. For each:

| Field | Display as |
|---|---|
| `question_index` | Question number (index + 1) |
| `question` / `question_en` | The question asked |
| `answer` / `answer_en` | Truncated answer preview |
| `score` | Numeric score + label |
| `strengths[]` | Green bullet list |
| `gaps[]` | Red / amber bullet list |
| `suggestion` | Italicised improvement tip |

Score labels:

| Score | Label |
|---|---|
| 0–3 | Poor |
| 4–5 | Weak |
| 6–7 | Average |
| 8–9 | Strong |
| 10 | Exceptional |

A score of `-1` means scoring failed for that question — show "Not scored".

---

### Section E — Round-specific insight

`round_specific_insight` — one paragraph, tailored per round:
- **HR**: whether STAR method usage improved, declined, or was consistent
- **Technical**: which domain showed the deepest knowledge gap
- **Cultural**: genuine self-awareness vs. rehearsed answers
- **Negotiation**: confidence trajectory after the first pushback

Display below the per-question section.

---

### Section F — PDF export

Generate the PDF **on the frontend** from the JSON report — there is no backend PDF endpoint.

Recommended libraries:
- [React-PDF](https://react-pdf.org/) — React components that render a PDF
- [jsPDF](https://github.com/parallax/jsPDF) + jsPDF-autotable — programmatic PDF generation
- `window.print()` — print-stylesheet approach, simplest but least control

All the data you need is in the report JSON from `GET /report`.

---

### Section G — Transcript

**On button/tab click:**

```
GET /interview/{session_id}/transcript
```

Response:
```json
{
  "session_id": "88f0ad6e-...",
  "round_type": "hr",
  "entries": [
    { "role": "agent",     "content": "Hi, I'm Amna…",          "timestamp": "2026-06-29T14:00:01Z" },
    { "role": "candidate", "content": "Thank you for having me.", "timestamp": "2026-06-29T14:00:08Z" }
  ],
  "entry_count": 12,
  "written_at": "2026-06-29T14:15:30Z"
}
```

Render as a chat-style transcript. Colour-code by role:
- `agent` — bot messages (e.g. purple / brand colour)
- `candidate` — user messages (e.g. green)

---

### Section H — Recording

**On button/tab click:**

```
GET /interview/{session_id}/recording
```

Response:
```json
{
  "session_id": "88f0ad6e-...",
  "url": "https://…r2.cloudflarestorage.com/recordings/88f0ad6e.ogg?X-Amz-Signature=…",
  "format": "ogg",
  "recorded_at": "2026-06-29T14:00:00Z"
}
```

Pass `data.url` directly to an `<audio>` element:

```jsx
<audio controls src={data.url} />
```

> The URL expires **1 hour** after it's generated. Do not cache it. If the user returns later, call `GET /recording` again to get a fresh URL.

OGG audio plays natively in Chrome, Firefox, and Edge. Safari requires a polyfill or transcoding — if Safari support is needed, note this as a future task.

---

## Full State Machine

```
HISTORY PAGE  (on login — optional, show before SETUP PAGE)
  ├── GET /user/{userId}/interviews
  │     → list of past sessions, newest first
  │     → each card shows: round_type, candidate_name, created_at, overall_score, hiring_signal
  │
  ├── Click a past session card
  │     → navigate to /results/{session_id}
  │     → load report, transcript, recording on demand
  │
  └── "Start New Interview" button → navigate to SETUP PAGE

SETUP PAGE
  ├── resumeText = null, jdText = null
  │     → "Start Interview" button disabled
  │
  ├── File selected for resume
  │     → show: "Reading your document…"
  │     → success: resumeText = data.text  → show: "✓ Resume ready"
  │     → failure: resumeText = null       → show error message
  │
  ├── Text pasted for resume
  │     → resumeText = textarea.value (if ≥ 10 chars), else null
  │
  ├── [same for job description]
  │
  ├── resumeText && jdText && candidateName
  │     → "Start Interview" button enabled
  │
  └── Start clicked
        → AudioContext unlocked (internal, invisible to user)
        → show: "Setting up your interview…"
        → POST /interview/start  (include user_id if logged in)
        → sessionId saved internally
        → navigate to INTERVIEW IN PROGRESS PAGE

INTERVIEW IN PROGRESS PAGE
  ├── Audio connects internally
  │     → show: "Connecting you to your interviewer…"
  │
  ├── Mic enabled + audio ready
  │     → show: "Your interview has started. Please speak clearly."
  │     → show: "Your interviewer is ready. They will introduce themselves shortly."
  │
  ├── [audio blocked edge case]
  │     → show button: "Tap to hear your interviewer"
  │
  ├── [interview in progress — no API calls, no technical UI]
  │
  └── Interview ends (internally: RoomEvent.Disconnected)
        → show: "Your interview is complete. Preparing your results…"
        → navigate to RESULTS PAGE

RESULTS PAGE
  ├── show: "Analysing your responses… This usually takes under a minute."
  ├── Poll GET /report every 4s (internal)
  │     → 202: keep showing loading message
  │     → 200: render report
  │     → 404: show: "Results not available. Please try again."
  │
  ├── Report rendered
  │     → Score + hiring signal
  │     → Summary feedback
  │     → Communication quality breakdown
  │     → Top recommendations
  │     → Per-question breakdown
  │     → Round-specific insight
  │
  ├── "Download Report" button → generate PDF from report JSON on frontend
  ├── "View Transcript" tab/button → GET /transcript → render as conversation
  └── "Listen to Recording" tab/button → GET /recording → audio player
```

---

## Implementation Notes

**`session_id` is the primary key for everything.** Save it as soon as `POST /interview/start` responds, and keep it available throughout Pages 2 and 3. Put it in the URL so the results page survives a refresh.

**`user_id` links sessions to a user account.** Pass it in `POST /interview/start` (from Firebase, Supabase, Auth0, or whichever auth system the frontend uses). The backend stores a lightweight session index in MongoDB keyed by `user_id`. On login, call `GET /user/{user_id}/interviews` to retrieve the full history — each entry has the `session_id` needed to load report, transcript, and recording.

**Text or file — same result.** The `resume` and `job_description` fields in `POST /interview/start` are always plain text strings — it makes no difference whether they came from an uploaded file or a textarea. The upload endpoint just converts file → text.

**Job role accepted in place of a full JD.** If the user does not have a job description, they can type just a role title (e.g. `"Software Engineer"`, `"Data Analyst"`). The backend detects short single-line input and instructs the AI interviewer to base questions on the resume and general role expectations instead of company-specific JD details.

**The bot ends the call.** You don't need an "End Interview" button (though a "Leave Early" button is useful). The `RoomEvent.Disconnected` event fires in both cases — handle it the same way.

**Don't store `user_token`.** It's a short-lived JWT — use it to connect and discard it.

**Scoring is async.** The report is written to MongoDB after the call ends. Expect a 30–60 second gap before the 200 lands. Show a loading state, not an error.

**OGG recording.** Chrome and Firefox play OGG natively. Safari does not — plan for this if Safari support is required.

**CORS is open.** No special headers are needed from the frontend. All origins are allowed.
