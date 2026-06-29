import os
import json
import time
import logging
from openai import OpenAI
from bot.config import GROQ_SCORING_MODEL
from bot.scoring.schemas import QuestionScoreOutput

logger = logging.getLogger("careerpilot.bot")

_ROUND_CRITERIA: dict[str, str] = {
    "hr": (
        "- STAR method usage: did the answer include Situation, Task, Action, and Result?\n"
        "- Relevance to the role described in the job description\n"
        "- Communication clarity: was the answer structured, focused, and easy to follow?"
    ),
    "technical": (
        "- Technical accuracy: was the candidate's answer factually correct?\n"
        "- Depth beyond surface-level definitions: did they explain how and why, not just what?\n"
        "- Problem decomposition ability: did they break the problem into logical steps?\n"
        "- Honesty about knowledge gaps: did they acknowledge uncertainty rather than bluff?"
    ),
    "cultural": (
        "- Self-awareness: did the candidate reflect honestly on their own behaviour and tendencies?\n"
        "- Values alignment: does the answer reflect values consistent with the job description and company culture?\n"
        "- Conflict resolution maturity: did they address difficulties constructively rather than deflecting?"
    ),
    "negotiation": (
        "- Confidence of ask: did the candidate state their counter clearly and directly?\n"
        "- Justification quality: did they back their ask with specific reasoning (experience, market rate, etc.)?\n"
        "- Pressure handling: did they maintain composure and professionalism under pushback?\n"
        "- Professionalism: was the tone appropriate throughout?"
    ),
}

_SYSTEM_INSTRUCTION = (
    "You are an expert interview evaluator, not a general AI assistant. "
    "You evaluate candidate responses to interview questions objectively and precisely, "
    "based only on what the candidate actually said. "
    "You return only a JSON object — no preamble, no explanation, no markdown."
)


def score_question(
    question: str,
    answer: str,
    job_description: str,
    round_type: str,
    question_index: int,
    language_mode: str = "english",
) -> dict:
    """
    Scores a single question-answer pair using Groq structured output.
    Always returns a dict — never raises.
    Returned dict includes question and answer text for the full report.
    """
    logger.info("Scoring question index=%d round=%s", question_index, round_type)
    start = time.monotonic()

    criteria = _ROUND_CRITERIA.get(
        round_type,
        "Evaluate the answer for relevance, clarity, and depth.",
    )

    no_response = answer.strip() == "[no response]"

    user_message = (
        f"JOB DESCRIPTION:\n{job_description}\n\n"
        f"ROUND TYPE:\n{round_type}\n\n"
        f"ROUND-SPECIFIC EVALUATION CRITERIA:\n{criteria}\n\n"
        f"INTERVIEW QUESTION:\n{question}\n\n"
        f"CANDIDATE ANSWER:\n{answer}"
        + (" [NOTE: The candidate did not provide any answer for this question.]" if no_response else "")
        + "\n\n"
        "Return a JSON object with exactly these fields:\n"
        "- score: integer 0-10. Use this scale strictly: "
        "0–3 = Poor (unprepared or severely off-topic), "
        "4–5 = Weak (below expectations for the role), "
        "6–7 = Average (meets the basic bar, room to improve), "
        "8–9 = Strong (solid answer, only minor gaps), "
        "10 = Exceptional (reserve for genuinely outstanding answers only).\n"
        "- strengths: list of strings, each referencing something specific the candidate actually said\n"
        "- gaps: list of strings identifying specific weaknesses in this answer\n"
        "- suggestion: one specific, actionable improvement — reference what the candidate actually said\n"
        + (
            "- question_en: English translation of the interview question above\n"
            "- answer_en: English translation of the candidate's answer above\n"
            if language_mode != "english" else
            "- question_en: empty string\n"
            "- answer_en: empty string\n"
        )
        + (
            "Since the candidate gave no answer: score must be 0, "
            "gaps must include 'Candidate did not answer', "
            "suggestion must recommend preparing an answer for this type of question.\n"
            if no_response else ""
        )
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

        elapsed = time.monotonic() - start
        parsed = QuestionScoreOutput.model_validate(
            json.loads(response.choices[0].message.content)
        )

        logger.info("Question %d scored: %s/10 in %.1fs", question_index, parsed.score, elapsed)
        return {
            "question_index": question_index,
            "question": question,
            "answer": answer,
            "question_en": parsed.question_en,
            "answer_en": parsed.answer_en,
            "score": parsed.score,
            "strengths": parsed.strengths,
            "gaps": parsed.gaps,
            "suggestion": parsed.suggestion,
        }

    except Exception as exc:
        logger.error(
            "score_question failed | question_index=%d round=%s error=%s",
            question_index, round_type, str(exc),
        )
        return {
            "question_index": question_index,
            "question": question,
            "answer": answer,
            "score": -1,
            "strengths": [],
            "gaps": ["Scoring failed"],
            "suggestion": "Manual review required",
            "error": str(exc),
        }
