"""
MongoDB helpers for CareerPilot.

Collections:
  transcripts      — raw conversation entries per session
  scoring_reports  — Gemini Flash score report per session

Called by:
  bot/main.py      → write_transcript, write_scoring_report
  api/session.py   → read_transcript, read_scoring_report (for report endpoint)
"""

import os
import logging
import certifi
from datetime import datetime, timezone
from pymongo import MongoClient
from pymongo.errors import PyMongoError

logger = logging.getLogger("careerpilot.bot")

# ── Connection (lazy singleton) ───────────────────────────────────────────────

_client: MongoClient | None = None


def _get_db():
    global _client
    uri = os.environ.get("MONGODB_URI")
    db_name = os.environ.get("MONGODB_DB", "careerpilot")

    if not uri:
        raise ValueError("MONGODB_URI environment variable is not set")

    if _client is None:
        _client = MongoClient(uri, tlsCAFile=certifi.where())
        logger.info("MongoDB client created | db=%s", db_name)

    return _client[db_name]


# ── Write functions (called by bot/main.py) ───────────────────────────────────

def write_transcript(session_id: str, transcript: list) -> None:
    """
    Upserts the transcript for a session.
    transcript is the output of TranscriptCollector.to_dict_list().
    """
    try:
        db = _get_db()
        db.transcripts.update_one(
            {"session_id": session_id},
            {
                "$set": {
                    "session_id": session_id,
                    "entries": transcript,
                    "entry_count": len(transcript),
                    "written_at": datetime.now(timezone.utc).isoformat(),
                }
            },
            upsert=True,
        )
        logger.info(
            "Transcript written | session_id=%s entries=%d",
            session_id, len(transcript),
        )
    except PyMongoError as exc:
        logger.error("write_transcript failed | session_id=%s error=%s", session_id, exc)
        raise


def write_scoring_report(session_id: str, report: dict) -> None:
    """
    Upserts the scoring report for a session.
    report is the output of run_scoring_pipeline().
    """
    try:
        db = _get_db()
        db.scoring_reports.update_one(
            {"session_id": session_id},
            {"$set": {**report, "written_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
        logger.info(
            "Scoring report written | session_id=%s status=%s",
            session_id, report.get("scoring_status"),
        )
    except PyMongoError as exc:
        logger.error("write_scoring_report failed | session_id=%s error=%s", session_id, exc)
        raise


# ── Read functions (called by api/session.py report endpoint) ─────────────────

def read_transcript(session_id: str) -> dict | None:
    """Returns the transcript document or None if not found."""
    try:
        db = _get_db()
        doc = db.transcripts.find_one({"session_id": session_id}, {"_id": 0})
        return doc
    except PyMongoError as exc:
        logger.error("read_transcript failed | session_id=%s error=%s", session_id, exc)
        return None


def read_scoring_report(session_id: str) -> dict | None:
    """Returns the scoring report document or None if not found."""
    try:
        db = _get_db()
        doc = db.scoring_reports.find_one({"session_id": session_id}, {"_id": 0})
        return doc
    except PyMongoError as exc:
        logger.error("read_scoring_report failed | session_id=%s error=%s", session_id, exc)
        return None


def write_recording(session_id: str, egress_id: str, r2_key: str) -> None:
    """
    Saves recording metadata after LiveKit Egress stops.
    The actual audio file lives in R2 at r2_key — we store only the reference.
    """
    try:
        db = _get_db()
        db.recordings.update_one(
            {"session_id": session_id},
            {
                "$set": {
                    "session_id": session_id,
                    "egress_id": egress_id,
                    "r2_key": r2_key,
                    "format": "ogg",
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                }
            },
            upsert=True,
        )
        logger.info("Recording metadata written | session_id=%s r2_key=%s", session_id, r2_key)
    except PyMongoError as exc:
        logger.error("write_recording failed | session_id=%s error=%s", session_id, exc)
        raise


def read_recording(session_id: str) -> dict | None:
    """Returns the recording metadata document or None if not found."""
    try:
        db = _get_db()
        doc = db.recordings.find_one({"session_id": session_id}, {"_id": 0})
        return doc
    except PyMongoError as exc:
        logger.error("read_recording failed | session_id=%s error=%s", session_id, exc)
        return None
