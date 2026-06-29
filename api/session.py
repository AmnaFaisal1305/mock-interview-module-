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

load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from livekit import api as livekit_api

from api.token_helper import generate_livekit_token
from api.db import read_transcript, read_scoring_report, read_recording
from api.pdf_report import generate_report_pdf
from api.r2 import generate_presigned_url
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

app.include_router(upload_router)


# ── Request / Response models ─────────────────────────────────────────────────

class StartInterviewRequest(BaseModel):
    round_type: str = Field(..., description="hr | technical | cultural | negotiation")
    resume: str = Field(..., min_length=10, description="Full resume text")
    job_description: str = Field(..., min_length=10, description="Full job description text")
    num_questions: int = Field(DEFAULT_QUESTION_COUNT, ge=1, le=15)
    language: str = Field("english", description="english | urdu | mixed")
    candidate_name: str = Field("Candidate", description="Used in bot greeting")


class StartInterviewResponse(BaseModel):
    session_id: str
    room_name: str
    livekit_url: str
    user_token: str


# ── POST /interview/start ─────────────────────────────────────────────────────

@app.post("/interview/start", response_model=StartInterviewResponse)
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

    # ── Create room + start LiveKit Egress (audio-only recording → Cloudflare R2) ──
    # The room must exist before an egress can be attached to it. We pre-create it
    # here so egress can start immediately, before the bot subprocess joins.
    egress_id = None
    r2_key = f"recordings/{session_id}.ogg"
    try:
        lk_url = livekit_url.replace("wss://", "https://").replace("ws://", "http://")
        r2_endpoint = f"https://{os.environ.get('R2_ACCOUNT_ID', '')}.r2.cloudflarestorage.com"
        async with livekit_api.LiveKitAPI(
            url=lk_url,
            api_key=os.environ.get("LIVEKIT_API_KEY", ""),
            api_secret=os.environ.get("LIVEKIT_API_SECRET", ""),
        ) as lk:
            await lk.room.create_room(
                livekit_api.CreateRoomRequest(name=room_name)
            )
            logger.info("Room pre-created | session_id=%s room=%s", session_id, room_name)

            egress_info = await lk.egress.start_room_composite_egress(
                livekit_api.RoomCompositeEgressRequest(
                    room_name=room_name,
                    audio_only=True,
                    file_outputs=[
                        livekit_api.EncodedFileOutput(
                            file_type=livekit_api.OGG,
                            filepath=r2_key,
                            s3=livekit_api.S3Upload(
                                access_key=os.environ.get("R2_ACCESS_KEY_ID", ""),
                                secret=os.environ.get("R2_SECRET_ACCESS_KEY", ""),
                                bucket=os.environ.get("R2_BUCKET_NAME", ""),
                                endpoint=r2_endpoint,
                                region="auto",
                                force_path_style=True,
                            ),
                        )
                    ],
                )
            )
        egress_id = egress_info.egress_id
        logger.info("Egress started | session_id=%s egress_id=%s", session_id, egress_id)
    except Exception as exc:
        logger.error("Egress start failed | session_id=%s error=%s — continuing without recording", session_id, exc)

    cmd = [
        sys.executable, "-u", "-m", "bot.main",
        "--session_id",      session_id,
        "--room_name",       room_name,
        "--bot_token",       bot_token,
        "--round_type",      req.round_type,
        "--resume",          req.resume,
        "--job_description", req.job_description,
        "--num_questions",   str(req.num_questions),
        "--language_mode",   req.language,
    ]
    if egress_id:
        cmd += ["--egress_id", egress_id, "--r2_key", r2_key]

    logs_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
    os.makedirs(logs_dir, exist_ok=True)
    log_path = os.path.join(logs_dir, f"bot_{session_id}.log")

    try:
        log_file = open(log_path, "w")
        proc = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        _active_bots[session_id] = proc
        logger.info(
            "Bot spawned | session_id=%s room=%s pid=%d round=%s",
            session_id, room_name, proc.pid, req.round_type,
        )
    except Exception as exc:
        logger.error("Bot spawn failed | session_id=%s error=%s", session_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to start bot: {exc}")

    return StartInterviewResponse(
        session_id=session_id,
        room_name=room_name,
        livekit_url=livekit_url,
        user_token=user_token,
    )


# ── GET /interview/{session_id}/report ────────────────────────────────────────

@app.get("/interview/{session_id}/report")
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


# ── GET /interview/{session_id}/report/pdf ───────────────────────────────────

@app.get("/interview/{session_id}/report/pdf")
async def get_report_pdf(session_id: str):
    report = read_scoring_report(session_id)
    if report is None:
        proc = _active_bots.get(session_id)
        if proc and proc.poll() is None:
            raise HTTPException(status_code=202, detail="Scoring not yet complete")
        raise HTTPException(status_code=404, detail="Report not found for this session_id")

    try:
        pdf_bytes = generate_report_pdf(report)
    except Exception as exc:
        logger.error("PDF generation failed | session_id=%s error=%s", session_id, exc)
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {exc}")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=report_{session_id[:8]}.pdf"},
    )


# ── GET /interview/{session_id}/transcript ────────────────────────────────────

@app.get("/interview/{session_id}/transcript")
async def get_transcript(session_id: str):
    doc = read_transcript(session_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Transcript not found for this session_id")
    return doc


# ── GET /interview/{session_id}/recording ────────────────────────────────────

@app.get("/interview/{session_id}/recording")
async def get_recording(session_id: str):
    doc = read_recording(session_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Recording not found for this session_id")
    try:
        url = generate_presigned_url(doc["r2_key"])
    except Exception as exc:
        logger.error("Presigned URL generation failed | session_id=%s error=%s", session_id, exc)
        raise HTTPException(status_code=500, detail=f"Could not generate recording URL: {exc}")
    return {
        "session_id": session_id,
        "url": url,
        "format": doc.get("format", "ogg"),
        "recorded_at": doc.get("recorded_at"),
    }


# ── GET /health ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "active_sessions": len(_active_bots)}
