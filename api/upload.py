"""
Document upload and text extraction for CareerPilot.

POST /upload/document  — accepts PDF or DOCX, returns extracted plain text
"""

import io
import logging

from fastapi import APIRouter, HTTPException, UploadFile, File

logger = logging.getLogger("careerpilot.api")

router = APIRouter()

_SUPPORTED_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
}
_MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


def _extract_pdf(data: bytes) -> str:
    import pdfplumber
    text_parts = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text.strip())
    return "\n\n".join(text_parts)


def _extract_docx(data: bytes) -> str:
    from docx import Document
    doc = Document(io.BytesIO(data))
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


@router.post("/upload/document")
async def upload_document(file: UploadFile = File(...)):
    """
    Upload a resume or job description as PDF or DOCX.
    Returns the extracted plain text to pass into POST /interview/start.
    """
    # ── Size check ────────────────────────────────────────────────────────────
    data = await file.read()
    if len(data) > _MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum allowed size is 5 MB.",
        )

    # ── Type check ────────────────────────────────────────────────────────────
    content_type = file.content_type or ""
    filename = file.filename or ""

    is_pdf = content_type == "application/pdf" or filename.lower().endswith(".pdf")
    is_docx = (
        content_type in {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/msword",
        }
        or filename.lower().endswith((".docx", ".doc"))
    )

    if not is_pdf and not is_docx:
        raise HTTPException(
            status_code=415,
            detail="Unsupported file type. Upload a PDF (.pdf) or Word document (.docx).",
        )

    # ── Extract text ──────────────────────────────────────────────────────────
    try:
        if is_pdf:
            text = _extract_pdf(data)
        else:
            text = _extract_docx(data)
    except Exception as exc:
        logger.error("Document extraction failed | filename=%s error=%s", filename, exc)
        raise HTTPException(
            status_code=422,
            detail=f"Could not extract text from the document: {exc}",
        )

    text = text.strip()
    if len(text) < 10:
        raise HTTPException(
            status_code=422,
            detail="Extracted text is too short. Make sure the document contains readable text (not a scanned image).",
        )

    logger.info("Document extracted | filename=%s chars=%d", filename, len(text))

    return {
        "filename": filename,
        "char_count": len(text),
        "word_count": len(text.split()),
        "text": text,
    }
