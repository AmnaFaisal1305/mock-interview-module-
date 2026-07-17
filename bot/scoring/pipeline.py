import logging
from datetime import datetime, timezone
from bot.transcript import TranscriptCollector
from bot.scoring.per_question import score_question
from bot.scoring.holistic import score_session
from bot.scoring.schemas import Clarification, CommunicationQuality, QuestionResult, ScoringReport

logger = logging.getLogger("careerpilot.bot")


def run_scoring_pipeline(
    session_id: str,
    transcript: list,
    job_description: str,
    round_type: str,
    language_mode: str = "english",
) -> dict:
    """
    Orchestrates the full scoring run for a completed session.
    Always returns a dict — never raises.
    """
    logger.info(
        "Scoring pipeline started | session_id=%s round=%s entries=%d",
        session_id, round_type, len(transcript),
    )
    generated_at = datetime.now(timezone.utc).isoformat()

    try:
        collector = TranscriptCollector.from_dict_list(transcript)
        pairs = collector.get_pairs()

        if not pairs:
            logger.warning("Scoring pipeline: zero pairs | session_id=%s", session_id)
            return ScoringReport(
                session_id=session_id,
                round_type=round_type,
                generated_at=generated_at,
                scoring_status="failed",
                overall_score=-1.0,
                hiring_signal="Pass",
                summary_insights="No question-answer pairs found in transcript.",
                communication_quality=CommunicationQuality(
                    clarity="unavailable",
                    conciseness="unavailable",
                    confidence_markers="unavailable",
                ),
                top_recommendations=[],
                round_specific_insight="unavailable",
                questions=[],
                question_count=0,
            ).model_dump()

        # ── Per-question scoring ───────────────────────────────────────────────
        raw_scores = []
        for pair in pairs:
            raw_scores.append(score_question(
                question=pair["question"],
                answer=pair["answer"],
                job_description=job_description,
                round_type=round_type,
                question_index=pair["question_index"],
                language_mode=language_mode,
            ))

        # ── Holistic assessment ────────────────────────────────────────────────
        holistic = score_session(
            transcript_pairs=pairs,
            per_question_scores=raw_scores,
            job_description=job_description,
            round_type=round_type,
        )

        # ── Determine status ───────────────────────────────────────────────────
        failed_count = sum(1 for s in raw_scores if "error" in s)
        if failed_count == 0:
            scoring_status = "complete"
        elif failed_count < len(raw_scores):
            scoring_status = "partial"
        else:
            scoring_status = "failed"

        # ── Assemble typed questions list ──────────────────────────────────────
        # Build a lookup from question_index → clarifications collected during pairing
        clarifications_map = {
            p["question_index"]: [
                Clarification(candidate=c["candidate"], agent=c["agent"])
                for c in p.get("clarifications", [])
            ]
            for p in pairs
        }

        questions = [
            QuestionResult(
                question_index=s["question_index"],
                question=s["question"],
                answer=s["answer"],
                question_en=s.get("question_en", ""),
                answer_en=s.get("answer_en", ""),
                score=s["score"],
                strengths=s.get("strengths", []),
                gaps=s.get("gaps", []),
                suggestion=s.get("suggestion", ""),
                clarifications=clarifications_map.get(s["question_index"], []),
            )
            for s in raw_scores
        ]

        # ── Build final report ─────────────────────────────────────────────────
        cq_raw = holistic.get("communication_quality", {})
        report = ScoringReport(
            session_id=session_id,
            round_type=round_type,
            generated_at=generated_at,
            scoring_status=scoring_status,
            overall_score=holistic.get("overall_score", -1.0),
            hiring_signal=holistic.get("hiring_signal", "Pass"),
            summary_insights=holistic.get("summary_insights", ""),
            communication_quality=CommunicationQuality(
                clarity=cq_raw.get("clarity", ""),
                conciseness=cq_raw.get("conciseness", ""),
                confidence_markers=cq_raw.get("confidence_markers", ""),
            ),
            top_recommendations=holistic.get("top_recommendations", []),
            round_specific_insight=holistic.get("round_specific_insight", ""),
            questions=questions,
            question_count=len(questions),
        )

        logger.info(
            "Scoring pipeline complete | session_id=%s status=%s questions=%d",
            session_id, scoring_status, len(questions),
        )
        return report.model_dump()

    except Exception as exc:
        logger.error("Scoring pipeline failure | session_id=%s error=%s", session_id, str(exc))
        return ScoringReport(
            session_id=session_id,
            round_type=round_type,
            generated_at=generated_at,
            scoring_status="failed",
            overall_score=-1.0,
            hiring_signal="Pass",
            summary_insights=f"Scoring pipeline failed: {exc}",
            communication_quality=CommunicationQuality(
                clarity="unavailable",
                conciseness="unavailable",
                confidence_markers="unavailable",
            ),
            top_recommendations=[],
            round_specific_insight="unavailable",
            questions=[],
            question_count=0,
        ).model_dump()
