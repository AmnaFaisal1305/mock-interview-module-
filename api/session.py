"""
FastAPI session endpoints for CareerPilot.

POST /interview/start   — validate input, spawn bot, return LiveKit token to React
GET  /interview/{id}/report — return score report from MongoDB
"""

import os
import sys
import uuid
import logging
import asyncio
import subprocess
from contextlib import asynccontextmanager
from dotenv import load_dotenv

from api.hot_pool import init_pool, dispatch_session as _pool_dispatch

load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from livekit import api as livekit_api

from api.token_helper import generate_livekit_token
from api.db import (
    read_transcript,
    read_scoring_report,
    read_recording,
    write_session_index,
    read_user_sessions,
)
from api.s3 import generate_presigned_url
from api.upload import router as upload_router
from bot.config import (
    SUPPORTED_ROUND_TYPES,
    SUPPORTED_LANGUAGE_MODES,
    DEFAULT_QUESTION_COUNT,
    MAX_SESSION_DURATION,
    VAD_SILENCE_THRESHOLDS,
)

logger = logging.getLogger("careerpilot.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")

# ── Active bot processes ──────────────────────────────────────────────────────
# Maps session_id → subprocess.Popen so we can check/kill if needed
_active_bots: dict[str, subprocess.Popen] = {}


# ── Lifespan: clean up zombie processes on shutdown ───────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pre-warm bot processes in the background so first sessions get fast startup
    init_pool()
    yield
    for sid, proc in list(_active_bots.items()):
        if proc.poll() is None:
            proc.terminate()
            logger.info("Bot process terminated on shutdown | session_id=%s", sid)


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="CareerPilot API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to React origin in production
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload_router, prefix="/ai/interview")


# ── Request / Response models ─────────────────────────────────────────────────

class StartInterviewRequest(BaseModel):
    round_type: str = Field(..., description="hr | technical | cultural | negotiation")
    resume: str = Field(..., min_length=10, description="Full resume text")
    job_description: str = Field(..., min_length=2, description="Full job description text OR a job role title (e.g. 'Software Engineer')")
    num_questions: int = Field(DEFAULT_QUESTION_COUNT, ge=3, le=15)
    language: str = Field("english", description="english | urdu | mixed")
    candidate_name: str = Field("Candidate", description="Used in bot greeting")
    user_id: str | None = Field(None, description="Authenticated user ID — links this session to the user's history")


class StartInterviewResponse(BaseModel):
    session_id: str
    room_name: str
    livekit_url: str
    user_token: str


# ── POST /interview/start ─────────────────────────────────────────────────────

@app.post("/ai/interview/start", response_model=StartInterviewResponse)
async def start_interview(req: StartInterviewRequest):
    # ── Validate enums ────────────────────────────────────────────────────────
    if req.round_type not in SUPPORTED_ROUND_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"round_type must be one of {SUPPORTED_ROUND_TYPES}",
        )
    if req.language not in SUPPORTED_LANGUAGE_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"language must be one of {SUPPORTED_LANGUAGE_MODES}",
        )

    session_id = str(uuid.uuid4())
    room_name = f"interview-{session_id}"
    livekit_url = os.environ.get("LIVEKIT_URL", "")
    livekit_public_url = os.environ.get("LIVEKIT_PUBLIC_URL", livekit_url)

    if not livekit_url:
        raise HTTPException(status_code=500, detail="LIVEKIT_URL not configured")

    # ── Generate tokens ───────────────────────────────────────────────────────
    try:
        user_token = generate_livekit_token(room_name, participant_identity="user")
        bot_token = generate_livekit_token(room_name, participant_identity="bot")
    except Exception as exc:
        logger.error("Token generation failed | session_id=%s error=%s", session_id, exc)
        raise HTTPException(status_code=500, detail=f"LiveKit token error: {exc}")

    # ── Spawn bot process ─────────────────────────────────────────────────────
    vad_threshold = VAD_SILENCE_THRESHOLDS.get(req.round_type, 700)

    # ── Pre-create room so the user token is valid before the bot subprocess joins ──
    try:
        lk_url = livekit_url.replace("wss://", "https://").replace("ws://", "http://")
        async with livekit_api.LiveKitAPI(
            url=lk_url,
            api_key=os.environ.get("LIVEKIT_API_KEY", ""),
            api_secret=os.environ.get("LIVEKIT_API_SECRET", ""),
        ) as lk:
            await lk.room.create_room(
                livekit_api.CreateRoomRequest(name=room_name)
            )
            logger.info("Room pre-created | session_id=%s room=%s", session_id, room_name)
    except Exception as exc:
        logger.error("Room creation failed | session_id=%s error=%s", session_id, exc)

    logs_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
    os.makedirs(logs_dir, exist_ok=True)
    log_path = os.path.abspath(os.path.join(logs_dir, f"bot_{session_id}.log"))

    session_args = {
        "session_id":      session_id,
        "room_name":       room_name,
        "bot_token":       bot_token,
        "round_type":      req.round_type,
        "resume":          req.resume,
        "job_description": req.job_description,
        "num_questions":   req.num_questions,
        "language_mode":   req.language,
        "user_id":         req.user_id,
    }

    try:
        proc = _pool_dispatch(session_args, log_path)
        _active_bots[session_id] = proc
        logger.info(
            "Bot spawned | session_id=%s room=%s pid=%d round=%s",
            session_id, room_name, proc.pid, req.round_type,
        )
    except Exception as exc:
        logger.error("Bot spawn failed | session_id=%s error=%s", session_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to start bot: {exc}")

    # ── Persist session in user history (if user_id provided) ────────────────
    if req.user_id:
        try:
            write_session_index(
                user_id=req.user_id,
                session_id=session_id,
                round_type=req.round_type,
                candidate_name=req.candidate_name,
            )
        except Exception as exc:
            logger.error(
                "Session index write failed (non-fatal) | session_id=%s user_id=%s error=%s",
                session_id, req.user_id, exc,
            )

    return StartInterviewResponse(
        session_id=session_id,
        room_name=room_name,
        livekit_url=livekit_public_url,
        user_token=user_token,
    )


# ── GET /interview/{session_id}/report ────────────────────────────────────────

@app.get("/ai/interview/{session_id}/report")
async def get_report(session_id: str):
    report = read_scoring_report(session_id)

    if report is None:
        # Check if scoring is still in progress (bot process still running)
        proc = _active_bots.get(session_id)
        if proc and proc.poll() is None:
            raise HTTPException(
                status_code=202,
                detail="Interview still in progress or scoring not yet complete",
            )
        raise HTTPException(status_code=404, detail="Report not found for this session_id")

    return report


# ── GET /interview/{session_id}/transcript ────────────────────────────────────

@app.get("/ai/interview/{session_id}/transcript")
async def get_transcript(session_id: str):
    doc = read_transcript(session_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Transcript not found for this session_id")
    return doc


# ── GET /interview/{session_id}/recording ────────────────────────────────────

@app.get("/ai/interview/{session_id}/recording")
async def get_recording(session_id: str):
    doc = read_recording(session_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Recording not found for this session_id")
    try:
        url = generate_presigned_url(doc["s3_key"])
    except Exception as exc:
        logger.error("Presigned URL generation failed | session_id=%s error=%s", session_id, exc)
        raise HTTPException(status_code=500, detail=f"Could not generate recording URL: {exc}")
    return {
        "session_id": session_id,
        "url": url,
        "format": doc.get("format", "ogg"),
        "recorded_at": doc.get("recorded_at"),
    }


# ── GET /user/{user_id}/interviews ───────────────────────────────────────────

@app.get("/ai/interview/user/{user_id}/interviews")
async def get_user_interviews(user_id: str):
    """
    Returns all past interview sessions for a user, newest first.
    Each entry includes session_id, round_type, candidate_name, created_at,
    overall_score, hiring_signal, and scoring_status.
    Use session_id with /interview/{session_id}/report|transcript|recording
    to fetch full details for any past session.
    """
    sessions = read_user_sessions(user_id)
    return {"user_id": user_id, "interviews": sessions, "count": len(sessions)}


# ── GET /health ───────────────────────────────────────────────────────────────

@app.get("/ai/interview/health")
async def health():
    return {"status": "ok", "active_sessions": len(_active_bots)}
