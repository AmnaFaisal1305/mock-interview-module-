"""
Pre-warmed bot worker.

Spawned at API startup by the hot process pool.  It imports all heavy
libraries (google-genai, livekit, pipecat, silero, etc.) once, then waits
for session arguments via stdin.  When args arrive it runs the bot session
and exits — eliminating ~9 s of Python import latency per session.

Protocol
--------
1. Worker imports everything and writes a marker file whose path is in
   env var CAREERPILOT_READY_FILE.
2. Pool manager detects the file and queues this process as ready.
3. Pool manager sends a JSON line to stdin with all session args plus a
   special "_log_file" key.
4. Worker pops "_log_file", reconfigures logging, then calls run_bot().
"""

import json
import logging
import os
import sys
import argparse
import asyncio

# ── Heavy imports — this is the whole point of pre-warming ────────────────────
from bot.main import run_bot  # triggers pipecat, google-genai, livekit, silero

# ── Signal readiness ──────────────────────────────────────────────────────────
_ready_file = os.environ.get("CAREERPILOT_READY_FILE")
if _ready_file:
    try:
        with open(_ready_file, "w") as _f:
            _f.write("READY")
    except Exception:
        pass  # best-effort

# ── Wait for session args ─────────────────────────────────────────────────────
_raw = sys.stdin.readline().strip()
if not _raw:
    sys.exit(0)

_args_dict = json.loads(_raw)
_log_path = _args_dict.pop("_log_file", None)

# ── Reconfigure logging to session log file ───────────────────────────────────
if _log_path:
    # Standard-library logging
    for _h in logging.root.handlers[:]:
        logging.root.removeHandler(_h)
    _fh = logging.FileHandler(_log_path, mode="w", encoding="utf-8")
    _fh.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logging.root.addHandler(_fh)
    logging.root.setLevel(logging.DEBUG)

    # loguru (used by pipecat for INFO/DEBUG output)
    try:
        from loguru import logger as _loguru
        _loguru.remove()
        _loguru.add(
            _log_path,
            mode="a",
            colorize=True,
            level="DEBUG",
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan>"
                " - <level>{message}</level>"
            ),
        )
    except ImportError:
        pass

# ── Run the session ───────────────────────────────────────────────────────────
_args = argparse.Namespace(**_args_dict)
asyncio.run(run_bot(_args))
