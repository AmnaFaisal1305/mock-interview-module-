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
        "Respond in the same language the candidate uses. "
        "If they speak English, respond in English. "
        "If they speak Urdu, respond in Urdu. "
        "If they mix languages mid-sentence, match their style naturally."
    ),
}

_REQUIRED_PLACEHOLDERS = (
    "{{CANDIDATE_RESUME}}",
    "{{JOB_DESCRIPTION}}",
    "{{NUM_QUESTIONS}}",
    "{{LANGUAGE_INSTRUCTION}}",
)


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
    prompt = prompt.replace("{{JOB_DESCRIPTION}}", job_description)
    prompt = prompt.replace("{{NUM_QUESTIONS}}", str(num_questions))
    prompt = prompt.replace("{{LANGUAGE_INSTRUCTION}}", _LANGUAGE_INSTRUCTIONS[language_mode])

    logger.info("System prompt assembled successfully (%d chars)", len(prompt))
    return prompt


# Self-check:
# Returns: fully substituted string ready to pass as system_instruction to GeminiLiveLLMService
# Failure modes: three distinct ValueError paths — all indicate template authoring bugs,
#   not runtime errors; callers should not swallow them
