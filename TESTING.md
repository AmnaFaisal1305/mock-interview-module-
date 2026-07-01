# CareerPilot — Manual Testing Guide (Postman)

## Before You Start

1. Make sure Python virtual environment is active (`venv\Scripts\activate` on Windows)
2. Start the backend:
   ```
   uvicorn api.session:app --host 127.0.0.1 --port 8000
   ```
3. Open Postman

---

## Step 1 — Health Check

Confirm the server is running.

| Field | Value |
|---|---|
| Method | `GET` |
| URL | `http://127.0.0.1:8000/health` |

**Expected response:**
```json
{
  "status": "ok",
  "active_sessions": 0
}
```

If you see this, the server is up. Move to Step 2.

---

## Step 2 — Start the Interview

Skip the upload step — use the sample resume and JD text at the bottom of this file.

| Field | Value |
|---|---|
| Method | `POST` |
| URL | `http://127.0.0.1:8000/interview/start` |
| Body type | `raw` → `JSON` |

**Request body:**
```json
{
  "round_type": "hr",
  "candidate_name": "Ali Hassan",
  "resume": "Ali Hassan\nSoftware Engineer\n\nEducation\nBSc Computer Science, FAST-NUCES Lahore, 2022\n\nExperience\nJunior Backend Developer — TechVentures Pvt Ltd, Lahore (Jan 2023 – Present)\n- Built and maintained REST APIs using Python and FastAPI\n- Integrated third-party payment gateway (JazzCash) reducing checkout failures by 20%\n- Wrote unit tests with pytest, maintained 85% code coverage\n- Collaborated with a 5-person team using Git and Jira\n\nIntern — CodeBase Solutions, Lahore (Jun 2022 – Dec 2022)\n- Assisted in migrating legacy PHP endpoints to Django REST framework\n- Set up CI/CD pipeline using GitHub Actions\n\nSkills\nPython, FastAPI, Django, PostgreSQL, Redis, Docker, Git, AWS (EC2, S3)\n\nProjects\nExpense Tracker App — personal project, FastAPI backend + React frontend, deployed on AWS EC2",
  "job_description": "Company: NovaTech Solutions (Lahore-based SaaS company)\n\nRole: Backend Engineer\n\nWe are looking for a Backend Engineer to join our growing product team. You will design and build scalable APIs, work closely with the frontend team, and own features end to end.\n\nRequirements:\n- 1–3 years of experience in backend development\n- Strong proficiency in Python (FastAPI or Django preferred)\n- Experience with relational databases (PostgreSQL or MySQL)\n- Familiarity with Docker and cloud platforms (AWS or GCP)\n- Good understanding of REST API design principles\n- Experience working in an Agile team\n\nNice to have:\n- Experience with message queues (Redis, Celery)\n- Knowledge of CI/CD pipelines\n\nSalary: PKR 120,000 – 160,000 per month\nWork type: On-site, Lahore\nTeam size: 12 engineers",
  "num_questions": 3,
  "language": "english"
}
```

**Expected response:**
```json
{
  "session_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "room_name": "interview-xxxxxxxx-...",
  "livekit_url": "wss://your-project.livekit.cloud",
  "user_token": "eyJhbGci..."
}
```

**Save the `session_id`** — you will use it in every step after this.

---

## Step 3 — Join the LiveKit Room

1. Open `test_client.html` in Chrome
2. Paste the `livekit_url` and `user_token` from Step 2
3. Click **Connect**
4. Allow microphone access when prompted
5. The AI agent will greet you and begin the interview
6. Answer the questions — the agent will ask 3 questions then say goodbye and hang up automatically

> **Tip:** Keep the terminal visible — you can watch the bot logs in real time.

---

## Step 4 — Poll for the Report

After the call ends, the bot runs scoring (takes about 30–60 seconds).

| Field | Value |
|---|---|
| Method | `GET` |
| URL | `http://127.0.0.1:8000/interview/{session_id}/report` |

Replace `{session_id}` with the value you saved in Step 2.

- **202** → scoring still running. Wait 30 seconds and send again.
- **200** → report is ready. You will see the full JSON score report.

---

## Step 5 — Get the Transcript

| Field | Value |
|---|---|
| Method | `GET` |
| URL | `http://127.0.0.1:8000/interview/{session_id}/transcript` |

Returns the full conversation — every agent and candidate turn with timestamps.

---

## Step 6 — Get the Recording

| Field | Value |
|---|---|
| Method | `GET` |
| URL | `http://127.0.0.1:8000/interview/{session_id}/recording` |

Returns a presigned URL. Copy the `url` field and paste it in a browser to play the audio.

> The URL expires after 1 hour. If it stops working, call this endpoint again to get a fresh one.

---

## Try Other Round Types

Change `"round_type"` in Step 2 to test different agents:

| round_type | Agent | Persona |
|---|---|---|
| `hr` | Amna | HR screening — motivation, STAR method, culture fit |
| `technical` | Ahmed | Technical depth — stack walkthrough, concepts, scenario |
| `cultural` | Hassan | People Ops — team conflict, self-management, values |
| `negotiation` | Ayan | Hiring Manager — offer, counteroffers, firmness |

---

## Try Other Languages

Change `"language"` in Step 2:

| language | Behaviour |
|---|---|
| `english` | Agent speaks English only |
| `urdu` | Agent speaks Urdu only |
| `mixed` | Agent mirrors whatever language you speak |

---

## Sample Texts (copy-paste ready)

### Resume — Ali Hassan (Junior Backend Developer)

```
Ali Hassan
Software Engineer

Education
BSc Computer Science, FAST-NUCES Lahore, 2022

Experience
Junior Backend Developer — TechVentures Pvt Ltd, Lahore (Jan 2023 – Present)
- Built and maintained REST APIs using Python and FastAPI
- Integrated third-party payment gateway (JazzCash) reducing checkout failures by 20%
- Wrote unit tests with pytest, maintained 85% code coverage
- Collaborated with a 5-person team using Git and Jira

Intern — CodeBase Solutions, Lahore (Jun 2022 – Dec 2022)
- Assisted in migrating legacy PHP endpoints to Django REST framework
- Set up CI/CD pipeline using GitHub Actions

Skills
Python, FastAPI, Django, PostgreSQL, Redis, Docker, Git, AWS (EC2, S3)

Projects
Expense Tracker App — personal project, FastAPI backend + React frontend, deployed on AWS EC2
```

---

### Job Description — Backend Engineer at NovaTech Solutions

```
Company: NovaTech Solutions (Lahore-based SaaS company)

Role: Backend Engineer

We are looking for a Backend Engineer to join our growing product team. You will
design and build scalable APIs, work closely with the frontend team, and own
features end to end.

Requirements:
- 1–3 years of experience in backend development
- Strong proficiency in Python (FastAPI or Django preferred)
- Experience with relational databases (PostgreSQL or MySQL)
- Familiarity with Docker and cloud platforms (AWS or GCP)
- Good understanding of REST API design principles
- Experience working in an Agile team

Nice to have:
- Experience with message queues (Redis, Celery)
- Knowledge of CI/CD pipelines

Salary: PKR 120,000 – 160,000 per month
Work type: On-site, Lahore
Team size: 12 engineers
```

---

## Error Reference

| Response code | Meaning | What to do |
|---|---|---|
| `200` | Success | Read the response |
| `202` | Still processing | Wait 30s and try again |
| `404` | Not found | Session may not exist or interview not ended yet |
| `422` | Bad input | Check your JSON body — wrong `round_type` or missing field |
| `500` | Server error | Check the terminal where uvicorn is running |
