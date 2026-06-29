"""
Pydantic schemas for CareerPilot scoring.

LLM output schemas  — passed as JSON schema to Groq structured outputs.
Report schemas      — assembled by the pipeline and stored in MongoDB.
"""

from pydantic import BaseModel, Field


# ── LLM output: per-question ──────────────────────────────────────────────────

class QuestionScoreOutput(BaseModel):
    score: int = Field(..., ge=0, le=10, description="0–10 integer score for this answer")
    strengths: list[str] = Field(default_factory=list, description="Specific things the candidate said well")
    gaps: list[str] = Field(default_factory=list, description="Specific weaknesses in the answer")
    suggestion: str = Field(..., description="One actionable improvement referencing what the candidate said")
    question_en: str = Field(default="", description="English translation of the question (empty if already English)")
    answer_en: str = Field(default="", description="English translation of the candidate's answer (empty if already English)")


# ── LLM output: holistic ──────────────────────────────────────────────────────

class CommunicationQuality(BaseModel):
    clarity: str = Field(..., description="How clearly the candidate expressed their thoughts")
    conciseness: str = Field(..., description="Whether answers were appropriately brief or verbose")
    confidence_markers: str = Field(..., description="Specific language patterns that revealed high or low confidence")


class HolisticScoreOutput(BaseModel):
    overall_score: float = Field(..., ge=0.0, le=10.0, description="Overall session performance, 0.0–10.0")
    summary_insights: str = Field(..., description="Paragraph summarising overall performance")
    communication_quality: CommunicationQuality
    top_recommendations: list[str] = Field(..., min_length=3, max_length=3, description="Exactly 3 actionable recommendations referencing specific transcript moments")
    round_specific_insight: str = Field(..., description="Round-specific observation based on the transcript")
    hiring_signal: str = Field(..., description="Recommend | Consider | Pass")


# ── Full report (assembled by pipeline, stored in MongoDB) ────────────────────

class QuestionResult(BaseModel):
    question_index: int
    question: str = Field(..., description="The exact question the bot asked")
    answer: str = Field(..., description="The candidate's answer (or '[no response]')")
    question_en: str = Field(default="", description="English translation of the question (empty if already English)")
    answer_en: str = Field(default="", description="English translation of the candidate's answer (empty if already English)")
    score: int = Field(..., ge=-1, le=10)  # -1 is the error sentinel when scoring fails
    strengths: list[str]
    gaps: list[str]
    suggestion: str


class ScoringReport(BaseModel):
    session_id: str
    round_type: str
    generated_at: str
    scoring_status: str                         # complete | partial | failed
    overall_score: float
    hiring_signal: str                          # Recommend | Consider | Pass
    summary_insights: str
    communication_quality: CommunicationQuality
    top_recommendations: list[str]
    round_specific_insight: str
    questions: list[QuestionResult]
    question_count: int
