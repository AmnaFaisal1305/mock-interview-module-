import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import List, Dict

logger = logging.getLogger("careerpilot.bot")

# Phrases that indicate the candidate is asking to repeat the question (not answering)
_REPEAT_PHRASES = [
    "repeat", "say that again", "say again", "pardon", "didn't hear",
    "can't hear", "couldn't hear", "didn't catch", "couldn't catch",
    "what did you say", "what was that", "please repeat", "can you say",
    "come again", "once more", "once again",
    # Urdu / mixed equivalents
    "dobara", "phir se", "phir say", "samjha nahi", "samajh nahi",
    "sunai nahi", "suna nahi", "aik baar phir", "ek baar phir",
]

# Phrases the bot uses when probing a weak/short answer (from agent template)
_PROBE_PHRASES = [
    "didn't quite catch", "could you tell me more", "could you give me",
    "let me rephrase", "could you elaborate", "can you give me a specific",
    "could you expand", "could you clarify", "tell me more about",
    "could you be more specific",
]


def _is_repeat_request(text: str) -> bool:
    """True if the candidate is asking to repeat the question rather than answering it."""
    t = text.lower().strip()
    if len(t.split()) > 15:
        return False
    return any(phrase in t for phrase in _REPEAT_PHRASES)


def _is_probe_turn(text: str) -> bool:
    """True if the agent turn is a probe/follow-up on a weak answer, not a new question."""
    t = text.lower()
    return any(phrase in t for phrase in _PROBE_PHRASES)


@dataclass
class TranscriptEntry:
    role: str       # "agent" or "candidate"
    content: str
    timestamp: str  # ISO 8601 UTC


class TranscriptCollector:
    def __init__(self) -> None:
        self._entries: List[TranscriptEntry] = []
        logger.info("TranscriptCollector initialised")

    def add(self, role: str, content: str) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        self._entries.append(TranscriptEntry(role=role, content=content, timestamp=timestamp))
        logger.info("Transcript entry | role=%s chars=%d", role, len(content))

    def get_all(self) -> List[TranscriptEntry]:
        return list(self._entries)

    def to_dict_list(self) -> List[Dict]:
        """Serialisable list for MongoDB writes."""
        return [asdict(e) for e in self._entries]

    @classmethod
    def from_dict_list(cls, entries: List[Dict]) -> "TranscriptCollector":
        """Reconstruct from serialised data (used by scoring pipeline)."""
        collector = cls()
        for e in entries:
            collector._entries.append(TranscriptEntry(**e))
        return collector

    def get_pairs(self) -> List[Dict]:
        """
        Extract question-answer pairs from the transcript.

        Merging rules (handles repeat requests and bot probes as sub-turns):
          - If the candidate responds only with a repeat request ("can you repeat?"),
            the next agent turn (the repeated question) is merged — the candidate's
            repeat phrase is discarded and the actual answer collected after.
          - If the next agent turn is a bot probe ("could you tell me more?"),
            it is merged into the same question and the subsequent candidate answer
            is collected as the final answer to that question.
          - Multiple rounds of repeat/probe within one question are all merged.
          - Whitespace-only candidate content → "[no response]".
          - Never raises; returns only completed pairs.

        The first pair (greeting exchange) is always excluded from scoring.
        """
        logger.info("Extracting pairs from %d transcript entries", len(self._entries))
        pairs: List[Dict] = []
        question_index = 0
        i = 0

        while i < len(self._entries):
            if self._entries[i].role != "agent":
                i += 1
                continue

            question = self._entries[i].content
            i += 1

            # Merge loop: one iteration per agent turn within the same question
            accumulated_answers: List[str] = []

            while True:
                # Collect all consecutive candidate turns
                round_answers: List[str] = []
                while i < len(self._entries) and self._entries[i].role == "candidate":
                    txt = self._entries[i].content.strip()
                    i += 1
                    if not txt:
                        continue
                    if _is_repeat_request(txt):
                        continue  # discard "can you repeat?" — not an answer
                    round_answers.append(txt)

                # If candidate said nothing substantive (only repeat requests or silence)
                # and there's another agent turn, it's the repeated question — skip it
                if not round_answers and i < len(self._entries) and self._entries[i].role == "agent":
                    i += 1  # consume the repeated/rephrased agent turn
                    continue

                accumulated_answers.extend(round_answers)

                # If the next agent turn is a probe, merge it into this question
                if i < len(self._entries) and self._entries[i].role == "agent":
                    if _is_probe_turn(self._entries[i].content):
                        i += 1  # consume probe turn
                        continue  # collect the post-probe candidate answer

                break  # done with this question

            combined = " ".join(accumulated_answers).strip()
            answer = combined if combined else "[no response]"

            pairs.append({
                "question": question,
                "answer": answer,
                "question_index": question_index,
            })
            question_index += 1

        # Drop the first pair — it's always the greeting, not an interview question
        pairs = pairs[1:]
        for idx, p in enumerate(pairs):
            p["question_index"] = idx

        logger.info("get_pairs → %d pairs extracted (greeting + repeat/probe turns merged)", len(pairs))
        return pairs


# Self-check:
# Returns: dataclasses and plain dicts — all JSON-serialisable (no datetime objects)
# Failure modes: from_dict_list raises TypeError on bad keys — callers should validate
#   before passing; get_pairs never raises and handles partial sessions gracefully
