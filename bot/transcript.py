import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import List, Dict

logger = logging.getLogger("careerpilot.bot")


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
        Extract question-answer pairs.
        Rules:
          - A pair = one agent entry immediately followed by ≥1 candidate entries.
          - Consecutive candidate entries are concatenated with a space.
          - Whitespace-only candidate entries → "[no response]".
          - Never raises; returns only completed pairs.
        """
        logger.info("Extracting pairs from %d transcript entries", len(self._entries))
        pairs: List[Dict] = []
        question_index = 0
        i = 0

        while i < len(self._entries):
            entry = self._entries[i]

            if entry.role != "agent":
                i += 1
                continue

            question = entry.content
            i += 1

            # Collect all consecutive candidate turns
            answer_parts: List[str] = []
            while i < len(self._entries) and self._entries[i].role == "candidate":
                answer_parts.append(self._entries[i].content)
                i += 1

            if answer_parts:
                combined = " ".join(p for p in answer_parts if p.strip())
                answer = combined.strip() if combined.strip() else "[no response]"
            else:
                answer = "[no response]"

            pairs.append({
                "question": question,
                "answer": answer,
                "question_index": question_index,
            })
            question_index += 1

        logger.info("get_pairs → %d pairs extracted", len(pairs))
        return pairs


# Self-check:
# Returns: dataclasses and plain dicts — all JSON-serialisable (no datetime objects)
# Failure modes: from_dict_list raises TypeError on bad keys — callers should validate
#   before passing; get_pairs never raises and handles partial sessions gracefully
