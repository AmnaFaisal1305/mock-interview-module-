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
        # Build a lookup from question_index → clarifications + penalty flag
        pair_meta = {
            p["question_index"]: {
                "clarifications": [
                    Clarification(
                        candidate=c["candidate"],
                        agent=c["agent"],
                        penalty=c.get("penalty", False),
                        marking=c.get("marking", "No Marking"),
                    )
                    for c in p.get("clarifications", [])
                ],
                "penalty": p.get("penalty", False),
            }
            for p in pairs
        }

        questions = []
        for s in raw_scores:
            meta = pair_meta.get(s["question_index"], {"clarifications": [], "penalty": False})
            raw_score = s["score"]
            score_penalty = -1 if meta["penalty"] else 0
            # Apply penalty: floor at 0, but preserve -1 error sentinel
            if raw_score >= 0:
                final_score = max(0, raw_score + score_penalty)
            else:
                final_score = raw_score  # keep -1 error sentinel intact

            questions.append(QuestionResult(
                question_index=s["question_index"],
                question=s["question"],
                answer=s["answer"],
                question_en=s.get("question_en", ""),
                answer_en=s.get("answer_en", ""),
                score=final_score,
                score_before_penalty=raw_score if raw_score >= 0 else 0,
                score_penalty=score_penalty,
                strengths=s.get("strengths", []),
                gaps=s.get("gaps", []),
                suggestion=s.get("suggestion", ""),
                clarifications=meta["clarifications"],
            ))

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
