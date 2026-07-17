"""
Hot process pool for CareerPilot bot workers.

Pre-warms N Python processes so all heavy imports (google-genai, livekit,
pipecat, silero) are already done.  Each session gets a ready process
instead of a cold subprocess, eliminating ~9 s of startup latency.

Usage
-----
Call init_pool() once when the FastAPI app starts (inside lifespan).
Call dispatch_session(args_dict, log_path) to start a session.
"""

import json
import logging
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time

logger = logging.getLogger("careerpilot.hot_pool")

_POOL_SIZE = 2          # warm processes to keep ready
_WARMUP_TIMEOUT = 90    # seconds to wait for imports to finish

_pool: "queue.Queue[subprocess.Popen]" = queue.Queue()
_initialized = False
_lock = threading.Lock()


def _spawn_warm_worker() -> None:
    """Spawn one warm worker and enqueue it when ready. Runs in a daemon thread."""
    fd, ready_path = tempfile.mkstemp(suffix=".careerpilot_ready")
    os.close(fd)
    os.unlink(ready_path)   # delete placeholder — worker recreates it as the signal

    env = {**os.environ, "CAREERPILOT_READY_FILE": ready_path, "PYTHONUNBUFFERED": "1"}
    try:
        p = subprocess.Popen(
            [sys.executable, "-u", "-m", "bot.warm_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,  # suppress warm-up banner / pipecat logs
            stderr=subprocess.DEVNULL,
            env=env,
            text=True,
        )
    except Exception as exc:
        logger.error("Failed to spawn warm worker: %s", exc)
        return

    deadline = time.monotonic() + _WARMUP_TIMEOUT
    while time.monotonic() < deadline:
        if os.path.exists(ready_path):
            try:
                os.unlink(ready_path)
            except OSError:
                pass
            _pool.put(p)
            logger.info("Hot worker ready | pid=%d pool_approx=%d", p.pid, _pool.qsize())
            return
        if p.poll() is not None:
            logger.error(
                "Warm worker exited before ready | pid=%d returncode=%d",
                p.pid, p.returncode,
            )
            return
        time.sleep(0.1)

    logger.warning("Warm worker warmup timed out | pid=%d — killing", p.pid)
    try:
        p.kill()
    except OSError:
        pass


def init_pool() -> None:
    """Fill the pool with pre-warmed workers. Call once at API startup."""
    global _initialized
    with _lock:
        if _initialized:
            return
        _initialized = True

    logger.info("Hot pool: starting %d warm workers in background", _POOL_SIZE)
    for _ in range(_POOL_SIZE):
        threading.Thread(target=_spawn_warm_worker, daemon=True).start()


def _cold_spawn(args_dict: dict, log_path: str) -> subprocess.Popen:
    """Fallback: spawn a fresh cold subprocess (old behaviour)."""
    cmd = [
        sys.executable, "-u", "-m", "bot.main",
        "--session_id",      args_dict["session_id"],
        "--room_name",       args_dict["room_name"],
        "--bot_token",       args_dict["bot_token"],
        "--round_type",      args_dict["round_type"],
        "--resume",          args_dict["resume"],
        "--job_description", args_dict["job_description"],
        "--num_questions",   str(args_dict["num_questions"]),
        "--language_mode",   args_dict["language_mode"],
    ]
    if args_dict.get("egress_id"):
        cmd += ["--egress_id", args_dict["egress_id"], "--s3_key", args_dict["s3_key"]]
    if args_dict.get("user_id"):
        cmd += ["--user_id", args_dict["user_id"]]

    log_file = open(log_path, "w", buffering=1, encoding="utf-8")
    return subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )


def dispatch_session(args_dict: dict, log_path: str) -> subprocess.Popen:
    """
    Start a bot session.

    Tries to use a pre-warmed process from the pool.  Falls back to a cold
    spawn if the pool is empty or the hot process has already exited.
    Always spawns a replacement warm worker to refill the pool.

    Parameters
    ----------
    args_dict : session args matching bot.main's argparse fields
    log_path  : absolute path to the bot's log file

    Returns
    -------
    The Popen object for the running bot process.
    """
    # Always top up the pool so it stays at POOL_SIZE
    threading.Thread(target=_spawn_warm_worker, daemon=True).start()

    # Try hot path
    p = None
    try:
        p = _pool.get_nowait()
    except queue.Empty:
        pass

    if p is not None:
        if p.poll() is not None:
            logger.warning("Hot worker already exited | pid=%d — using cold spawn", p.pid)
            p = None
        else:
            logger.info("Dispatching to hot worker | pid=%d", p.pid)
            full_args = {**args_dict, "_log_file": log_path}
            try:
                p.stdin.write(json.dumps(full_args) + "\n")
                p.stdin.flush()
                return p
            except Exception as exc:
                logger.error("Failed to send args to hot worker | pid=%d error=%s", p.pid, exc)
                try:
                    p.kill()
                except OSError:
                    pass
                p = None

    # Cold fallback
    logger.info("Cold spawning bot | session_id=%s", args_dict.get("session_id"))
    return _cold_spawn(args_dict, log_path)
