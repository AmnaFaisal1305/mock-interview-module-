"""
MongoDB helpers for CareerPilot.

Collections:
  sessions         — one lightweight index doc per session, keyed by user_id
  transcripts      — raw conversation entries per session
  scoring_reports  — full score report per session
  recordings       — S3 key + egress metadata per session

Called by:
  bot/main.py      → write_transcript, write_scoring_report, update_session_index_score
  api/session.py   → write_session_index, read_user_sessions,
                     read_transcript, read_scoring_report, read_recording
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


# ── Session index (user → sessions mapping) ───────────────────────────────────

def write_session_index(
    user_id: str,
    session_id: str,
    round_type: str,
    candidate_name: str,
) -> None:
    """
    Creates a lightweight session entry in the `sessions` collection when an
    interview starts.  Scoring results are patched in later via
    update_session_index_score() once the bot finishes scoring.
    """
    try:
        db = _get_db()
        db.sessions.update_one(
            {"session_id": session_id},
            {
                "$set": {
                    "user_id": user_id,
                    "session_id": session_id,
                    "round_type": round_type,
                    "candidate_name": candidate_name,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "scoring_status": "pending",
                }
            },
            upsert=True,
        )
        logger.info("Session index written | user_id=%s session_id=%s", user_id, session_id)
    except PyMongoError as exc:
        logger.error("write_session_index failed | session_id=%s error=%s", session_id, exc)
        raise


def update_session_index_score(
    session_id: str,
    overall_score: float,
    hiring_signal: str,
    scoring_status: str,
) -> None:
    """Patches scoring results into the session index entry after scoring completes."""
    try:
        db = _get_db()
        db.sessions.update_one(
            {"session_id": session_id},
            {
                "$set": {
                    "overall_score": overall_score,
                    "hiring_signal": hiring_signal,
                    "scoring_status": scoring_status,
                }
            },
        )
        logger.info(
            "Session index score updated | session_id=%s score=%.1f signal=%s",
            session_id, overall_score, hiring_signal,
        )
    except PyMongoError as exc:
        logger.error("update_session_index_score failed | session_id=%s error=%s", session_id, exc)


def read_user_sessions(user_id: str) -> list[dict]:
    """
    Returns all session index documents for a user, sorted newest-first.
    Each document contains: session_id, round_type, candidate_name, created_at,
    overall_score, hiring_signal, scoring_status.
    """
    try:
        db = _get_db()
        docs = list(
            db.sessions.find(
                {"user_id": user_id},
                {"_id": 0},
            ).sort("created_at", -1)
        )
        return docs
    except PyMongoError as exc:
        logger.error("read_user_sessions failed | user_id=%s error=%s", user_id, exc)
        return []


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


def write_recording(session_id: str, egress_id: str | None, s3_key: str) -> None:
    """
    Saves recording metadata after the in-bot AudioBufferProcessor uploads to S3.
    The actual audio file lives in S3 at s3_key — we store only the reference.
    """
    try:
        db = _get_db()
        db.recordings.update_one(
            {"session_id": session_id},
            {
                "$set": {
                    "session_id": session_id,
                    "egress_id": egress_id,
                    "s3_key": s3_key,
                    "format": "wav",
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                }
            },
            upsert=True,
        )
        logger.info("Recording metadata written | session_id=%s s3_key=%s", session_id, s3_key)
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
