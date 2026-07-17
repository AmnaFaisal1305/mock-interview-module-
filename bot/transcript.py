import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import List, Dict

logger = logging.getLogger("careerpilot.bot")

# Pure repeat — candidate wants the same question re-read, no penalty
_REPEAT_PHRASES = [
    "repeat", "say that again", "say again", "pardon", "didn't hear",
    "can't hear", "couldn't hear", "didn't catch", "couldn't catch",
    "what did you say", "what was that", "please repeat", "can you say",
    "come again", "once more", "once again",
    # Urdu / mixed
    "dobara", "phir se", "phir say", "samjha nahi", "samajh nahi",
    "sunai nahi", "suna nahi", "aik baar phir", "ek baar phir",
]

# Simplification request — candidate asks for easier explanation (-1 penalty)
_CLARIFICATION_PHRASES = [
    "easy words", "simple words", "simpler", "simplify", "easier",
    "what do you mean", "don't understand", "dont understand", "didn't understand",
    "can you explain", "what does that mean", "meaning of", "i don't get",
    "i didn't get", "in simple", "easy language", "simple language",
    "easy way", "simple way", "layman", "in other words", "rephrase",
    # Urdu / mixed
    "aasan alfaz", "aasan zaban", "aasaan", "samjhao", "asaan",
    "آسان", "سمجھاؤ",
]

# Bot probe phrases (follow-up on weak answer, not a new question)
_PROBE_PHRASES = [
    "didn't quite catch", "could you tell me more", "could you give me",
    "let me rephrase", "could you elaborate", "can you give me a specific",
    "could you expand", "could you clarify", "tell me more about",
    "could you be more specific",
]

# Bot closing/farewell — not a question, should not be scored
_CLOSING_PHRASES = [
    "our team will be in touch", "it was a pleasure speaking with you",
    "have a great day", "have a wonderful day", "that concludes our interview",
    "that concludes the interview", "end of the interview", "end of our interview",
    "thank you for your time", "wish you all the best", "best of luck",
    "good luck with your",
]


def _is_repeat_request(text: str) -> bool:
    """Candidate wants the exact question repeated — no penalty."""
    t = text.lower().strip()
    if len(t.split()) > 15:
        return False
    return any(phrase in t for phrase in _REPEAT_PHRASES)


def _is_clarification_request(text: str) -> bool:
    """Candidate asks for a simpler explanation — triggers -1 score penalty."""
    t = text.lower().strip()
    if len(t.split()) > 20:
        return False
    return any(phrase in t for phrase in _CLARIFICATION_PHRASES)


def _is_probe_turn(text: str) -> bool:
    """Bot follow-up on a weak answer, not a new question."""
    t = text.lower()
    return any(phrase in t for phrase in _PROBE_PHRASES)


def _is_closing_statement(text: str) -> bool:
    """Bot farewell — never a scoreable question."""
    t = text.lower()
    return any(phrase in t for phrase in _CLOSING_PHRASES)


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

            # Skip closing/farewell statements — not scoreable questions
            if _is_closing_statement(question):
                # Consume any trailing candidate turns
                while i < len(self._entries) and self._entries[i].role == "candidate":
                    i += 1
                continue

            # Merge loop: one iteration per agent turn within the same question
            accumulated_answers: List[str] = []
            clarifications: List[Dict] = []
            has_penalty: bool = False  # True if candidate asked for simpler explanation

            while True:
                # Collect all consecutive candidate turns
                round_answers: List[str] = []
                round_non_answers: List[str] = []
                while i < len(self._entries) and self._entries[i].role == "candidate":
                    txt = self._entries[i].content.strip()
                    i += 1
                    if not txt:
                        continue
                    if _is_repeat_request(txt):
                        round_non_answers.append(txt)  # no penalty
                    elif _is_clarification_request(txt):
                        round_non_answers.append(txt)
                        has_penalty = True             # -1 penalty
                    else:
                        round_answers.append(txt)

                # If candidate gave no real answer (only repeat/clarification requests)
                # and there's another agent turn, it's the rephrased/simplified question
                if not round_answers and i < len(self._entries) and self._entries[i].role == "agent":
                    agent_response = self._entries[i].content
                    i += 1
                    clarifications.append({
                        "candidate": " ".join(round_non_answers) if round_non_answers else "[silence]",
                        "agent": agent_response,
                        "penalty": has_penalty,
                    })
                    continue

                # If the next agent turn is a bot probe, record it and keep collecting
                if round_answers and i < len(self._entries) and self._entries[i].role == "agent":
                    if _is_probe_turn(self._entries[i].content):
                        probe_text = self._entries[i].content
                        i += 1
                        clarifications.append({
                            "candidate": " ".join(round_answers),
                            "agent": probe_text,
                            "penalty": False,
                        })
                        continue  # collect post-probe answer

                accumulated_answers.extend(round_answers)
                break

            combined = " ".join(accumulated_answers).strip()
            answer = combined if combined else "[no response]"

            pairs.append({
                "question": question,
                "answer": answer,
                "question_index": question_index,
                "clarifications": clarifications,
                "penalty": has_penalty,
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
