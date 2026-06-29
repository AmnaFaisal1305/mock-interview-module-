import logging

logger = logging.getLogger("careerpilot.bot")

# ── Model strings ──────────────────────────────────────────────────────────────
GEMINI_LIVE_MODEL = "models/gemini-2.5-flash-native-audio-preview-12-2025"

# Scoring uses Groq (independent of the live pipeline)
GROQ_SCORING_MODEL = "llama-3.3-70b-versatile"

# ── VAD silence thresholds (milliseconds) ─────────────────────────────────────
VAD_SILENCE_THRESHOLDS: dict[str, int] = {
    "hr": 700,
    "technical": 700,
    "cultural": 700,
    "negotiation": 900,
}

# ── Session defaults ───────────────────────────────────────────────────────────
DEFAULT_QUESTION_COUNT: int = 5
MAX_SESSION_DURATION: int = 1200  # seconds (20 minutes)

# ── Supported types ────────────────────────────────────────────────────────────
SUPPORTED_ROUND_TYPES: list[str] = ["hr", "technical", "cultural", "negotiation"]
SUPPORTED_LANGUAGE_MODES: list[str] = ["english", "urdu", "mixed"]

# ── Voices — one per round (Amna = HR = female voice) ─────────────────────────
# Gemini Live available voices: Aoede, Charon, Fenrir, Kore, Leda, Orus, Puck, Zephyr
# Female: Aoede, Kore, Leda, Zephyr  |  Male: Charon, Fenrir, Orus, Puck
DEFAULT_VOICE: str = "Charon"  # fallback only
AGENT_VOICES: dict[str, str] = {
    "hr":          "Kore",     # Amna — female
    "technical":   "Charon",   # Ahmed — male
    "cultural":    "Orus",     # Hassan — male
    "negotiation": "Fenrir",   # Ayan — male
}

# Self-check:
# Returns: named constants only — no logic, no classes
# Failure modes: none at import time; bad values caught in consuming modules
