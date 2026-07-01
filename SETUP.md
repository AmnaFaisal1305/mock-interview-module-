# CareerPilot — Backend Setup Guide

This guide is for the **frontend developer**. Follow these steps to get the backend running locally so you can start integrating your frontend against it.

---

## Prerequisites

Make sure you have these installed:

- **Python 3.11 or higher** — check with `python --version`
- **pip** — comes with Python
- **Git**

You will also need API credentials (get these from the backend team):

| What | Where to get it |
|---|---|
| LiveKit URL, API Key, API Secret | LiveKit Cloud project settings |
| Google Gemini API Key | Google AI Studio |
| Groq API Key | console.groq.com |
| MongoDB URI + DB name | MongoDB Atlas |
| Cloudflare R2 credentials | Cloudflare dashboard → R2 → API tokens |

---

## Step 1 — Clone the repository

```bash
git clone https://github.com/YOUR_ORG/careerpilot.git
cd careerpilot
```

---

## Step 2 — Create a virtual environment

```bash
python -m venv venv
```

Activate it:

```bash
# Windows
venv\Scripts\activate

# Mac / Linux
source venv/bin/activate
```

You should see `(venv)` in your terminal prompt.

---

## Step 3 — Install dependencies

```bash
pip install -r requirements.txt
pip install fastapi uvicorn pymongo[srv] certifi openai
```

---

## Step 4 — Create the `.env` file

Create a file named `.env` in the project root (same folder as `requirements.txt`).  
Copy the template below and fill in the values from the backend team:

```env
# LiveKit
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_API_SECRET=your_livekit_api_secret

# Google Gemini (voice AI)
GOOGLE_API_KEY=your_gemini_api_key

# Groq (scoring AI)
GROQ_API_KEY=your_groq_api_key

# MongoDB Atlas
MONGODB_URI=mongodb+srv://<user>:<password>@cluster0.xxxxx.mongodb.net/?appName=Cluster0
MONGODB_DB=CareerPilot

# Cloudflare R2 (audio recordings)
R2_ACCOUNT_ID=your_r2_account_id
R2_TOKEN=your_r2_token
R2_ACCESS_KEY_ID=your_r2_access_key_id
R2_SECRET_ACCESS_KEY=your_r2_secret_access_key
R2_BUCKET_NAME=your-bucket-name
```

> **Important:** `MONGODB_DB` must be exactly `CareerPilot` — the database name is case-sensitive.

> **Never commit `.env` to Git.** It is already in `.gitignore`.

---

## Step 5 — Run the backend

```bash
uvicorn api.session:app --host 127.0.0.1 --port 8000
```

You should see output like:

```
INFO:     Started server process [12345]
INFO:     Uvicorn running on http://127.0.0.1:8000
```

---

## Step 6 — Verify it's working

Open a browser or run this in a new terminal:

```bash
curl http://127.0.0.1:8000/health
```

Expected response:

```json
{"status": "ok", "active_sessions": 0}
```

If you see that — the backend is fully running and ready for frontend integration.

---

## Step 7 — Start integrating your frontend

Your backend is live at `http://127.0.0.1:8000`.

**Read these docs before writing any code:**

| File | What it covers |
|---|---|
| [`FRONTEND_API.md`](FRONTEND_API.md) | Every endpoint with JavaScript examples, polling logic, error codes, suggested page flow |
| [`API_DOCS.md`](API_DOCS.md) | Full API reference with request/response shapes |

**The integration flow in short:**

```
1. User uploads resume + JD
   POST /upload/document  →  get back plain text

2. User fills interview form and clicks Start
   POST /interview/start  →  get back session_id + livekit_url + user_token

3. Connect to the LiveKit room using user_token + livekit_url
   (LiveKit JS SDK — the AI agent joins automatically)

4. Interview runs in real time — no API calls needed

5. Call ends automatically when the AI says goodbye
   Poll GET /interview/{session_id}/report every 4–5s until 200

6. Show results
   GET /report      → score + feedback JSON (use this to generate PDF on frontend)
   GET /transcript  → full conversation
   GET /recording   → audio playback URL
```

---

## Troubleshooting

**`ModuleNotFoundError`**  
You are either not inside the virtual environment or missed a package. Run `pip install -r requirements.txt` again with `(venv)` active.

**`MONGODB_URI not configured` or MongoDB connection error**  
Check that your `.env` file is in the project root and the URI is correct. The `MONGODB_DB` value must be `CareerPilot` (capital C and P).

**`LIVEKIT_URL not configured`**  
Make sure `LIVEKIT_URL` is set in `.env` and starts with `wss://`.

**`GOOGLE_API_KEY` errors or bot not speaking**  
The Gemini Live API requires special access. Confirm the key has Gemini Live enabled in Google AI Studio.

**Port already in use**  
Something else is on port 8000. Either stop it or run on a different port:
```bash
uvicorn api.session:app --host 127.0.0.1 --port 8001
```
Then update your frontend's base URL to match.

**Bot log files**  
Each interview session writes a log to `logs/bot_{session_id}.log`. If something goes wrong during an interview (scoring fails, recording missing, etc.), check that file first.
