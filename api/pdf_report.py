"""
PDF report generator for CareerPilot scoring reports.
Produces a clean A4 PDF from the MongoDB scoring_report document.
"""

from fpdf import FPDF
from datetime import datetime


# ── Colours ───────────────────────────────────────────────────────────────────
C_DARK   = (30,  30,  30)
C_BRAND  = (37,  99, 235)   # blue
C_GREEN  = (22, 163,  74)
C_AMBER  = (217, 119,   6)
C_RED    = (220,  38,  38)
C_LIGHT  = (248, 250, 252)
C_BORDER = (203, 213, 225)
C_MUTED  = (100, 116, 139)


def _score_colour(score) -> tuple:
    try:
        s = float(score)
    except (TypeError, ValueError):
        return C_MUTED
    if s >= 7:
        return C_GREEN
    if s >= 4:
        return C_AMBER
    return C_RED


def _safe(text) -> str:
    if text is None:
        return ""
    return str(text).encode("latin-1", "replace").decode("latin-1")


class ReportPDF(FPDF):
    def __init__(self, session_id: str, round_type: str):
        super().__init__()
        self._session_id = session_id
        self._round_type = round_type.upper() if round_type else ""

    def header(self):
        self.set_fill_color(*C_BRAND)
        self.rect(0, 0, 210, 14, "F")
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(255, 255, 255)
        self.set_xy(10, 3)
        self.cell(0, 8, "CareerPilot  |  Interview Report", ln=False)
        self.set_font("Helvetica", "", 8)
        self.set_xy(0, 4)
        self.cell(200, 6, f"{self._round_type} Round", align="R")
        self.set_text_color(*C_DARK)
        self.ln(8)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*C_MUTED)
        self.cell(0, 8, f"Session: {self._session_id}   |   Page {self.page_no()}", align="C")

    # ── helpers ────────────────────────────────────────────────────────────────

    def section_title(self, text: str):
        self.ln(4)
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(*C_BRAND)
        self.cell(0, 7, _safe(text), ln=True)
        self.set_draw_color(*C_BRAND)
        self.set_line_width(0.4)
        self.line(10, self.get_y(), 200, self.get_y())
        self.set_text_color(*C_DARK)
        self.ln(2)

    def body_text(self, text: str, indent: int = 0):
        self.set_font("Helvetica", "", 10)
        self.set_x(10 + indent)
        self.multi_cell(190 - indent, 5, _safe(text))
        self.ln(1)

    def label_value(self, label: str, value: str):
        self.set_font("Helvetica", "B", 10)
        self.set_x(10)
        self.cell(45, 6, _safe(label + ":"), ln=False)
        self.set_font("Helvetica", "", 10)
        self.multi_cell(145, 6, _safe(value))

    def score_badge(self, score, label: str = ""):
        colour = _score_colour(score)
        x, y = self.get_x(), self.get_y()
        # badge box
        self.set_fill_color(*colour)
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 14)
        self.set_xy(10, y)
        self.cell(24, 10, _safe(str(score)), align="C", fill=True)
        self.set_text_color(*C_DARK)
        self.set_font("Helvetica", "", 10)
        self.set_xy(36, y + 2)
        self.cell(0, 6, _safe(label))
        self.ln(12)

    def bullet_list(self, items: list, colour=C_DARK):
        self.set_text_color(*colour)
        self.set_font("Helvetica", "", 10)
        for item in items:
            self.set_x(14)
            self.multi_cell(184, 5, _safe(f"•  {item}"))
            self.ln(1)
        self.set_text_color(*C_DARK)

    def shaded_box(self, content: str, fill=C_LIGHT):
        self.set_fill_color(*fill)
        self.set_draw_color(*C_BORDER)
        self.set_line_width(0.3)
        self.set_x(10)
        y_before = self.get_y()
        self.multi_cell(190, 5, _safe(content), border=1, fill=True)
        self.ln(2)


# ── Public function ────────────────────────────────────────────────────────────

def generate_report_pdf(report: dict) -> bytes:
    session_id  = report.get("session_id", "")
    round_type  = report.get("round_type", "")
    generated   = report.get("generated_at", "")
    status      = report.get("scoring_status", "")
    per_q       = report.get("questions", [])

    # format date
    try:
        dt = datetime.fromisoformat(generated)
        date_str = dt.strftime("%B %d, %Y  %H:%M UTC")
    except Exception:
        date_str = str(generated)

    pdf = ReportPDF(session_id=session_id, round_type=round_type)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # ── Title block ────────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(*C_DARK)
    pdf.cell(0, 10, "Interview Score Report", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*C_MUTED)
    pdf.cell(0, 5, f"Generated: {date_str}   |   Status: {status.upper()}", ln=True)
    pdf.set_text_color(*C_DARK)
    pdf.ln(3)

    # ── Overall score ──────────────────────────────────────────────────────────
    pdf.section_title("Overall Score")
    overall_score = report.get("overall_score", "N/A")
    hiring_signal = report.get("hiring_signal", "")
    signal_label  = f"  |  Signal: {hiring_signal}" if hiring_signal else ""
    pdf.score_badge(overall_score, f"out of 10  ({round_type.upper()} Round){signal_label}")

    # ── Summary ────────────────────────────────────────────────────────────────
    pdf.section_title("Summary")
    pdf.body_text(report.get("summary_insights", ""))

    # ── Communication quality ──────────────────────────────────────────────────
    cq = report.get("communication_quality", {})
    if cq:
        pdf.section_title("Communication Quality")
        pdf.label_value("Clarity",            cq.get("clarity", ""))
        pdf.label_value("Conciseness",        cq.get("conciseness", ""))
        pdf.label_value("Confidence markers", cq.get("confidence_markers", ""))

    # ── Recommendations ────────────────────────────────────────────────────────
    recs = report.get("top_recommendations", [])
    if recs:
        pdf.section_title("Top Recommendations")
        pdf.bullet_list(recs, colour=C_DARK)

    # ── Round-specific insight ─────────────────────────────────────────────────
    rsi = report.get("round_specific_insight", "")
    if rsi:
        pdf.section_title("Round-Specific Insight")
        pdf.shaded_box(rsi)

    # ── Per-question breakdown ─────────────────────────────────────────────────
    if per_q:
        pdf.section_title("Question-by-Question Breakdown")

        for q in per_q:
            idx        = q.get("question_index", 0)
            score      = q.get("score", "N/A")
            score_label= q.get("score_label", "")
            colour     = _score_colour(score)
            strengths  = q.get("strengths", [])
            gaps       = q.get("gaps", [])
            suggestion = q.get("suggestion", "")
            question   = q.get("question_en") or q.get("question", "")
            answer     = q.get("answer_en") or q.get("answer", "")

            # question header bar
            header = f"  Q{idx + 1}   Score: {score} / 10"
            if score_label:
                header += f"  ({score_label})"
            pdf.set_fill_color(*colour)
            pdf.set_text_color(255, 255, 255)
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_x(10)
            pdf.cell(190, 7, header, fill=True, ln=True)
            pdf.set_text_color(*C_DARK)
            pdf.ln(1)

            if question:
                pdf.set_font("Helvetica", "B", 9)
                pdf.set_x(14)
                pdf.cell(0, 5, "Question:", ln=True)
                pdf.set_font("Helvetica", "", 9)
                pdf.set_x(18)
                pdf.multi_cell(180, 5, _safe(question))
                pdf.ln(1)

            if answer:
                pdf.set_font("Helvetica", "B", 9)
                pdf.set_x(14)
                pdf.cell(0, 5, "Answer:", ln=True)
                pdf.set_font("Helvetica", "", 9)
                pdf.set_x(18)
                pdf.multi_cell(180, 5, _safe(answer))
                pdf.ln(1)

            if strengths:
                pdf.set_font("Helvetica", "B", 9)
                pdf.set_x(14)
                pdf.cell(0, 5, "Strengths:", ln=True)
                pdf.bullet_list(strengths, colour=C_GREEN)

            if gaps:
                pdf.set_font("Helvetica", "B", 9)
                pdf.set_x(14)
                pdf.cell(0, 5, "Gaps:", ln=True)
                pdf.bullet_list(gaps, colour=C_RED)

            if suggestion:
                pdf.set_font("Helvetica", "B", 9)
                pdf.set_x(14)
                pdf.cell(0, 5, "Suggestion:", ln=True)
                pdf.set_font("Helvetica", "I", 9)
                pdf.set_x(18)
                pdf.multi_cell(180, 5, _safe(suggestion))

            pdf.ln(3)

    return bytes(pdf.output())
