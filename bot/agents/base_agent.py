import logging
from bot.config import SUPPORTED_ROUND_TYPES, SUPPORTED_LANGUAGE_MODES

logger = logging.getLogger("careerpilot.bot")

_LANGUAGE_INSTRUCTIONS: dict[str, str] = {
    "english": (
        "Conduct the entire interview in English only, "
        "regardless of what language the candidate uses."
    ),
    "urdu": (
        "Conduct the entire interview in Urdu only, "
        "regardless of what language the candidate uses."
    ),
    "mixed": (
        "Conduct the interview in a natural Urdu-English code-switched style — "
        "the way educated Pakistanis typically speak in professional settings. "
        "Mix Urdu and English words freely within the same sentence, for example: "
        "'Aap ka experience kya hai is field mein?' or "
        "'Tell me about a time jab aap ne koi challenging situation handle ki.' "
        "Do NOT switch fully to one language based on what the candidate says. "
        "Always maintain this mixed Urdu-English style regardless of how the candidate responds."
    ),
}

_REQUIRED_PLACEHOLDERS = (
    "{{CANDIDATE_RESUME}}",
    "{{JOB_DESCRIPTION}}",
    "{{NUM_QUESTIONS}}",
    "{{LANGUAGE_INSTRUCTION}}",
)


def _prepare_job_description(job_description: str) -> str:
    """
    If the caller provided only a job role title (short, single-line text) instead of
    a full JD, wrap it with a note so agents know to rely on the resume and general
    role expectations rather than trying to extract company/requirements from sparse text.
    """
    stripped = job_description.strip()
    if len(stripped) <= 100 and "\n" not in stripped:
        return (
            f"Job Role: {stripped}\n"
            "(Only a job role title was provided — no detailed job description. "
            "Base your questions on the candidate's resume and general expectations "
            "for this role. Do not invent company-specific details.)"
        )
    return stripped


def build_system_prompt(
    agent_template: str,
    resume: str,
    job_description: str,
    round_type: str,
    num_questions: int,
    language_mode: str,
) -> str:
    """
    Assembles a complete system prompt by substituting all named placeholders
    in the agent template with the session-specific values.

    Raises ValueError on unsupported round_type, unsupported language_mode,
    or any placeholder missing from the template.
    """
    logger.info(
        "Building system prompt | round=%s language=%s questions=%d",
        round_type, language_mode, num_questions,
    )

    if round_type not in SUPPORTED_ROUND_TYPES:
        raise ValueError(
            f"Unsupported round_type '{round_type}'. "
            f"Must be one of: {SUPPORTED_ROUND_TYPES}"
        )

    if language_mode not in SUPPORTED_LANGUAGE_MODES:
        raise ValueError(
            f"Unsupported language_mode '{language_mode}'. "
            f"Must be one of: {SUPPORTED_LANGUAGE_MODES}"
        )

    for placeholder in _REQUIRED_PLACEHOLDERS:
        if placeholder not in agent_template:
            raise ValueError(
                f"Agent template is missing required placeholder: {placeholder}"
            )

    prompt = agent_template
    prompt = prompt.replace("{{CANDIDATE_RESUME}}", resume)
    prompt = prompt.replace("{{JOB_DESCRIPTION}}", _prepare_job_description(job_description))
    prompt = prompt.replace("{{NUM_QUESTIONS}}", str(num_questions))
    prompt = prompt.replace("{{LANGUAGE_INSTRUCTION}}", _LANGUAGE_INSTRUCTIONS[language_mode])

    logger.info("System prompt assembled successfully (%d chars)", len(prompt))
    return prompt


# Self-check:
# Returns: fully substituted string ready to pass as system_instruction to GeminiLiveLLMService
# Failure modes: three distinct ValueError paths — all indicate template authoring bugs,
#   not runtime errors; callers should not swallow them
