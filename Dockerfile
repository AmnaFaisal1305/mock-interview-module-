# ── Stage 1: build ────────────────────────────────────────────────────────────
# gcc + build-essential are needed to compile cryptography, grpcio, etc.
# They are NOT copied to the final image.
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN python -m venv /venv && \
    /venv/bin/pip install --no-cache-dir --upgrade pip && \
    /venv/bin/pip install --no-cache-dir -r requirements.txt


# ── Stage 2: final ────────────────────────────────────────────────────────────
# Clean slim image — no compiler, no ffmpeg, no build tools.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH="/venv/bin:$PATH"

WORKDIR /app

COPY --from=builder /venv /venv

COPY . .

EXPOSE 5000

CMD ["python", "-m", "uvicorn", "api.session:app", "--host", "0.0.0.0", "--port", "5000"]
