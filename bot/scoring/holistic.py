import os
import json
import logging
from openai import OpenAI
from bot.config import GROQ_SCORING_MODEL
from bot.scoring.schemas import HolisticScoreOutput

logger = logging.getLogger("careerpilot.bot")

_ROUND_SPECIFIC_INSIGHT: dict[str, str] = {
    "hr": (
        "Comment on STAR method consistency across the session — did the candidate improve, "
        "decline, or stay consistent in structuring their answers across multiple questions?"
    ),
    "technical": (
        "Identify the specific domain or concept area where the candidate showed the deepest gap. "
        "Name it explicitly based on the questions and answers in the transcript."
    ),
    "cultural": (
        "Comment on whether the candidate demonstrated genuine self-awareness or gave "
        "rehearsed-sounding, generic answers. Cite specific moments from the transcript."
    ),
    "negotiation": (
        "Comment on how the candidate handled the moment of pushback — did their confidence "
        "increase, decrease, or hold steady after the first rejection of their counter?"
    ),
}

_SYSTEM_INSTRUCTION = (
    "You are an expert interview evaluator, not a general AI assistant. "
    "You assess complete interview sessions holistically, synthesising across all "
    "questions and answers to produce actionable, specific feedback. "
    "You return only a JSON object — no preamble, no explanation, no markdown."
)


def score_session(
    transcript_pairs: list,
    per_question_scores: list,
    job_description: str,
    round_type: str,
) -> dict:
    """
    Produces a holistic assessment of the full interview session.
    Always returns a dict — never raises.
    """
    logger.info("Holistic scoring | round=%s pairs=%d", round_type, len(transcript_pairs))

    formatted_transcript = "\n".join(
        f"Q{p['question_index'] + 1}: {p['question']}\nA{p['question_index'] + 1}: {p['answer']}"
        for p in transcript_pairs
    )

    round_insight_instruction = _ROUND_SPECIFIC_INSIGHT.get(
        round_type,
        "Provide a round-specific observation based on the transcript.",
    )

    user_message = (
        f"JOB DESCRIPTION:\n{job_description}\n\n"
        f"ROUND TYPE:\n{round_type}\n\n"
        f"FULL INTERVIEW TRANSCRIPT:\n{formatted_transcript}\n\n"
        f"PER-QUESTION SCORES:\n{json.dumps(per_question_scores, indent=2)}\n\n"
        "Assess the following and return a JSON object:\n"
        "- overall_score: float 0.0–10.0 reflecting overall session performance. "
        "Use this scale strictly: 0–3 = Poor (unprepared, significant gaps), "
        "4–5 = Weak (below expectations), 6–7 = Average (meets basic bar), "
        "8–9 = Strong (solid, minor gaps only), 10 = Exceptional (reserve for genuinely outstanding sessions).\n"
        "- hiring_signal: exactly one of 'Recommend', 'Consider', or 'Pass'. "
        "Derive from overall_score: 7.5+ → Recommend, 5.5–7.4 → Consider, below 5.5 → Pass.\n"
        "- summary_insights: a paragraph summarising the candidate's overall performance in this round\n"
        "- communication_quality: object with three string fields:\n"
        "    clarity: how clearly the candidate expressed their thoughts\n"
        "    conciseness: whether answers were appropriately brief or verbose\n"
        "    confidence_markers: specific language patterns that revealed high or low confidence\n"
        "- top_recommendations: exactly 3 specific, actionable recommendations for the candidate. "
        "Each must reference something concrete from the transcript — do not write generic advice.\n"
        f"- round_specific_insight: {round_insight_instruction}\n"
    )

    try:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY environment variable is not set")

        client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")

        response = client.chat.completions.create(
            model=GROQ_SCORING_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_INSTRUCTION},
                {"role": "user",   "content": user_message},
            ],
            response_format={"type": "json_object"},
        )

        parsed = HolisticScoreOutput.model_validate(
            json.loads(response.choices[0].message.content)
        )
        logger.info("Holistic scoring complete | overall_score=%s", parsed.overall_score)
        return parsed.model_dump()

    except Exception as exc:
        logger.error("score_session failed | round=%s error=%s", round_type, str(exc))
        return {
            "overall_score": -1.0,
            "hiring_signal": "Pass",
            "summary_insights": "Holistic scoring failed — manual review required.",
            "communication_quality": {
                "clarity": "unavailable",
                "conciseness": "unavailable",
                "confidence_markers": "unavailable",
            },
            "top_recommendations": ["Manual review required"] * 3,
            "round_specific_insight": "unavailable",
            "error": str(exc),
        }
