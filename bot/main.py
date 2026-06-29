"""
CareerPilot bot entry point.
Spawned by Amna's FastAPI session endpoint as a subprocess.

Usage:
    python -m bot.main \
        --room_name <room> \
        --bot_token <jwt> \
        --round_type <hr|technical|cultural|negotiation> \
        --resume "<text>" \
        --job_description "<text>" \
        --num_questions <int> \
        --language_mode <english|urdu|mixed> \
        --session_id <str>
"""

import argparse
import asyncio
import logging
import os
from datetime import datetime, timezone

# ── Pipecat imports (verified against pipecat 1.4.0) ─────────────────────────
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    AssistantTurnStoppedMessage,
    LLMContextAggregatorPair,
    UserTurnMessageAddedMessage,
    UserTurnStoppedMessage,
)
from pipecat.services.google.gemini_live.llm import GeminiLiveLLMService, GeminiVADParams
from pipecat.transports.livekit.transport import LiveKitParams, LiveKitTransport
from pipecat.workers.runner import WorkerRunner

from bot.agents.cultural_agent import get_cultural_prompt
from bot.agents.hr_agent import get_hr_prompt
from bot.agents.negotiation_agent import get_negotiation_prompt
from bot.agents.technical_agent import get_technical_prompt
from bot.config import (
    AGENT_VOICES,
    DEFAULT_VOICE,
    GEMINI_LIVE_MODEL,
    MAX_SESSION_DURATION,
    VAD_SILENCE_THRESHOLDS,
)
from bot.transcript import TranscriptCollector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("careerpilot.bot")

_AGENT_PROMPT_BUILDERS = {
    "hr": get_hr_prompt,
    "technical": get_technical_prompt,
    "cultural": get_cultural_prompt,
    "negotiation": get_negotiation_prompt,
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CareerPilot interview bot")
    parser.add_argument("--room_name", required=True)
    parser.add_argument("--bot_token", required=True)
    parser.add_argument("--round_type", required=True, choices=list(_AGENT_PROMPT_BUILDERS))
    parser.add_argument("--resume", required=True)
    parser.add_argument("--job_description", required=True)
    parser.add_argument("--num_questions", type=int, required=True)
    parser.add_argument("--language_mode", required=True, choices=["english", "urdu", "mixed"])
    parser.add_argument("--session_id", required=True)
    parser.add_argument("--egress_id", required=False, default=None)
    parser.add_argument("--r2_key", required=False, default=None)
    return parser.parse_args()


async def run_bot(args: argparse.Namespace) -> None:
    session_start = datetime.now(timezone.utc)
    logger.info(
        "Session started | session_id=%s round=%s room=%s",
        args.session_id, args.round_type, args.room_name,
    )

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY environment variable is not set")
    livekit_url = os.environ.get("LIVEKIT_URL")
    if not livekit_url:
        raise ValueError("LIVEKIT_URL environment variable is not set")

    # ── Build system prompt ────────────────────────────────────────────────────
    system_prompt = _AGENT_PROMPT_BUILDERS[args.round_type](
        resume=args.resume,
        job_description=args.job_description,
        num_questions=args.num_questions,
        language_mode=args.language_mode,
    )

    vad_threshold = VAD_SILENCE_THRESHOLDS[args.round_type]
    transcript_collector = TranscriptCollector()

    # ── Transport ──────────────────────────────────────────────────────────────
    transport = LiveKitTransport(
        url=livekit_url,
        token=args.bot_token,
        room_name=args.room_name,
        params=LiveKitParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
        ),
    )

    # ── LLM (Gemini Live — handles STT + LLM + TTS in one WebSocket) ──────────
    # inference_on_context_initialization=False: prevents Gemini from auto-speaking
    # at pipeline start (before the user connects to LiveKit). We manually trigger
    # the greeting in on_participant_connected instead.
    llm = GeminiLiveLLMService(
        api_key=api_key,
        system_instruction=system_prompt,
        settings=GeminiLiveLLMService.Settings(
            model=GEMINI_LIVE_MODEL,
            voice=AGENT_VOICES.get(args.round_type, DEFAULT_VOICE),
            vad=GeminiVADParams(silence_duration_ms=vad_threshold),
        ),
        inference_on_context_initialization=False,
    )

    # ── Context + aggregators for transcript capture ───────────────────────────
    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        realtime_service_mode=True,
    )

    # ── Transcript capture ─────────────────────────────────────────────────────
    # In realtime_service_mode=True, on_user_turn_stopped fires with content=None.
    # The actual transcript text arrives via on_user_turn_message_added instead.
    # Both events can fire twice for the same content; track last-added text per
    # role to deduplicate without dropping genuine back-to-back identical answers.
    _last_transcript: dict[str, str] = {"agent": "", "candidate": ""}

    # ── Auto-hangup state ──────────────────────────────────────────────────────
    # When the bot says goodbye the call ends automatically:
    #   • if user replies with a closing phrase → bot delivers closing note → hangup
    #   • if no user reply within 10 s → hangup immediately
    _hangup_state: dict = {
        "goodbye_said": False,    # bot has said its goodbye
        "closing_triggered": False,  # user replied; closing note in flight
        "session_ending": False,  # _end_session already scheduled/running
        "timeout_task": None,     # asyncio.Task for the 10 s window
    }

    _BOT_GOODBYE_PHRASES = [
        "goodbye", "good luck", "best of luck", "all the best", "take care",
        "farewell", "that concludes", "interview is complete", "interview has concluded",
        "thank you for your time", "end of the interview", "end of our interview",
        "good luck with your", "wish you all the best", "best wishes",
        "have a great day", "have a wonderful day", "have a good day",
        "it was a pleasure", "our team will be in touch",
    ]
    _USER_GOODBYE_PHRASES = [
        "goodbye", "good bye", "bye", "thank you", "thanks", "appreciate",
        "take care", "have a good", "have a nice", "see you", "cheers",
        "all the best", "good luck",
    ]
    _USER_STOP_PHRASES = [
        "stop the interview", "end the interview", "stop this interview",
        "i want to stop", "i'd like to stop", "end this session",
        "cancel the interview", "i want to end", "please stop",
    ]

    def _has_goodbye(text: str, phrases: list[str]) -> bool:
        t = text.lower()
        return any(ph in t for ph in phrases)

    @user_aggregator.event_handler("on_user_turn_message_added")
    async def on_user_turn_message_added(aggregator, message: UserTurnMessageAddedMessage) -> None:
        text = (message.content or "").strip()
        if text and text != _last_transcript["candidate"]:
            _last_transcript["candidate"] = text
            transcript_collector.add("candidate", text)
            logger.info("Transcript entry | role=candidate chars=%d", len(text))

        # Candidate explicitly requests to stop → skip goodbye window, trigger wrap-up immediately.
        if (
            not _hangup_state["session_ending"]
            and not _hangup_state["closing_triggered"]
            and text
            and _has_goodbye(text, _USER_STOP_PHRASES)
        ):
            _hangup_state["goodbye_said"] = True
            _hangup_state["closing_triggered"] = True
            logger.info("User requested stop — triggering wrap-up | session_id=%s", args.session_id)
            from pipecat.frames.frames import LLMMessagesAppendFrame
            from pipecat.processors.frame_processor import FrameDirection
            stop_frame = LLMMessagesAppendFrame(messages=[
                {"role": "user", "content": "The candidate has asked to stop the interview. Please say your polite wrap-up line as described in your guardrails and end the session."},
            ])
            await user_aggregator.push_frame(stop_frame, FrameDirection.DOWNSTREAM)

            async def _end_after_stop_wrap() -> None:
                await asyncio.sleep(15)
                logger.info("Wrap-up after stop request — ending call | session_id=%s", args.session_id)
                await _end_session_once()

            asyncio.create_task(_end_after_stop_wrap())

        # If bot already said goodbye and user responds with a closing phrase,
        # cancel the 20 s timeout, trigger a closing note, then hang up.
        if (
            _hangup_state["goodbye_said"]
            and not _hangup_state["closing_triggered"]
            and text
            and _has_goodbye(text, _USER_GOODBYE_PHRASES)
        ):
            _hangup_state["closing_triggered"] = True
            task = _hangup_state["timeout_task"]
            if task and not task.done():
                task.cancel()
            logger.info("User said goodbye — triggering closing note | session_id=%s", args.session_id)

            from pipecat.frames.frames import LLMMessagesAppendFrame
            from pipecat.processors.frame_processor import FrameDirection
            closing_frame = LLMMessagesAppendFrame(messages=[
                {"role": "user", "content": "Please give your brief closing note and end the interview."},
            ])
            await user_aggregator.push_frame(closing_frame, FrameDirection.DOWNSTREAM)

            async def _end_after_closing() -> None:
                await asyncio.sleep(15)
                logger.info("Closing note delivered — ending call | session_id=%s", args.session_id)
                await _end_session_once()

            asyncio.create_task(_end_after_closing())

    @assistant_aggregator.event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(aggregator, message: AssistantTurnStoppedMessage) -> None:
        text = (message.content or "").strip()
        if text and text != _last_transcript["agent"]:
            _last_transcript["agent"] = text
            transcript_collector.add("agent", text)
            logger.info("Transcript entry | role=agent chars=%d", len(text))

        # Detect bot goodbye — start 10 s window for user to respond.
        # Skip if we're already in the closing/ending phase.
        if (
            not _hangup_state["goodbye_said"]
            and not _hangup_state["closing_triggered"]
            and text
            and _has_goodbye(text, _BOT_GOODBYE_PHRASES)
        ):
            _hangup_state["goodbye_said"] = True
            logger.info("Bot said goodbye — 20 s window for user response | session_id=%s", args.session_id)

            async def _goodbye_timeout() -> None:
                # 20 s from transcript fire: ~10 s covers bot audio still playing,
                # leaving the user a real ~10 s window to respond after hearing the goodbye.
                await asyncio.sleep(20)
                if not _hangup_state["closing_triggered"]:
                    logger.info("No user goodbye in 20 s — ending call | session_id=%s", args.session_id)
                    await _end_session_once()

            _hangup_state["timeout_task"] = asyncio.create_task(_goodbye_timeout())

    # ── Pipeline ───────────────────────────────────────────────────────────────
    pipeline = Pipeline([
        transport.input(),
        user_aggregator,
        llm,
        transport.output(),
        assistant_aggregator,
    ])

    worker = PipelineWorker(
        pipeline,
        name=f"careerpilot-{args.session_id}",
        params=PipelineParams(
            allow_interruptions=True,
            enable_metrics=False,
        ),
    )
    runner = WorkerRunner(handle_sigint=True)

    # ── Greeting trigger — fires when the human participant connects ──────────
    @transport.event_handler("on_participant_connected")
    async def on_participant_connected(transport_obj, participant) -> None:
        identity = getattr(participant, "identity", str(participant))
        if identity == "bot":
            return

        logger.info(
            "User connected, triggering greeting | identity=%s session_id=%s",
            identity, args.session_id,
        )

        # Wait up to 60 s for the Gemini WebSocket session to be established.
        # Gemini retries up to 3 times with backoff on DNS/network failures —
        # 5 s was not enough when the initial connection attempt fails.
        for _ in range(600):
            if llm._session:
                break
            await asyncio.sleep(0.1)
        else:
            logger.error("Gemini session not ready after 60 s | session_id=%s", args.session_id)
            return

        # In realtime_service_mode=True, LLMContextAggregatorPair never pushes an
        # initial LLMContextFrame at startup (only after completed user turns).
        # That leaves llm._context = None and _ready_for_realtime_input = False,
        # which blocks both the greeting and all subsequent user audio.
        # Fix both directly before triggering the greeting:
        if llm._context is None:
            llm._context = context           # prevent NoneType crash in adapter calls
        llm._ready_for_realtime_input = True  # allow user audio to reach Gemini

        # Push the greeting trigger through the pipeline so it reaches
        # GeminiLiveLLMService.process_frame → _create_single_response →
        # send_client_content(turn_complete=True) → Gemini speaks.
        # _ready_for_realtime_input is already True above, so user replies
        # will flow to Gemini after the greeting.
        from pipecat.frames.frames import LLMMessagesAppendFrame
        from pipecat.processors.frame_processor import FrameDirection
        greeting_frame = LLMMessagesAppendFrame(messages=[
            {"role": "user", "content": "Please begin the interview by introducing yourself."},
        ])
        await user_aggregator.push_frame(greeting_frame, FrameDirection.DOWNSTREAM)
        logger.info("Greeting triggered | session_id=%s", args.session_id)

    # ── Single-fire session end (prevents double _end_session calls) ─────────
    async def _end_session_once() -> None:
        if _hangup_state["session_ending"]:
            return
        _hangup_state["session_ending"] = True
        await _end_session(args, session_start, transcript_collector, worker)

    # ── Session end — correct event name for LiveKit transport is on_participant_disconnected
    @transport.event_handler("on_participant_disconnected")
    async def on_participant_disconnected(transport_obj, participant) -> None:
        logger.info(
            "Participant disconnected: %s | session_id=%s",
            getattr(participant, "identity", participant),
            args.session_id,
        )
        await _end_session_once()

    # ── Session timeout watchdog ───────────────────────────────────────────────
    async def _timeout_watchdog() -> None:
        await asyncio.sleep(MAX_SESSION_DURATION)
        logger.warning(
            "Session exceeded max duration (%ds) | session_id=%s — forcing end",
            MAX_SESSION_DURATION, args.session_id,
        )
        await _end_session_once()

    asyncio.create_task(_timeout_watchdog())

    logger.info("Pipeline running | session_id=%s", args.session_id)
    await runner.add_workers(worker)
    await runner.run()


async def _end_session(
    args: argparse.Namespace,
    session_start: datetime,
    transcript_collector: TranscriptCollector,
    worker: PipelineWorker,
) -> None:
    session_end = datetime.now(timezone.utc)
    duration_secs = (session_end - session_start).total_seconds()
    pairs_count = len(transcript_collector.get_pairs())

    # Step 1 — stop pipeline
    try:
        await worker.cancel()
        logger.info("Pipeline cancelled | session_id=%s", args.session_id)
    except Exception as exc:
        logger.error("Failed to cancel worker | session_id=%s error=%s", args.session_id, exc)

    # Step 2-3 — serialise transcript
    try:
        serialised_transcript = transcript_collector.to_dict_list()
    except Exception as exc:
        logger.error("Failed to serialise transcript | session_id=%s error=%s", args.session_id, exc)
        serialised_transcript = []

    # Step 4 — write transcript to MongoDB (skip gracefully if api.db not yet available)
    try:
        from api.db import write_transcript  # type: ignore[import]
        write_transcript(session_id=args.session_id, transcript=serialised_transcript)
        logger.info("Transcript written to MongoDB | session_id=%s", args.session_id)
    except ImportError:
        logger.warning(
            "api.db not found — skipping MongoDB write (local test mode) | session_id=%s",
            args.session_id,
        )
    except Exception as exc:
        logger.error("MongoDB transcript write failed | session_id=%s error=%s", args.session_id, exc)

    logger.info("Gemini Live connection closed | session_id=%s", args.session_id)
    logger.info("LiveKit room disconnected | session_id=%s", args.session_id)

    # Step 5 — stop LiveKit Egress and save recording metadata to MongoDB
    if args.egress_id and args.r2_key:
        try:
            from livekit import api as livekit_api
            lk_url = os.environ.get("LIVEKIT_URL", "").replace("wss://", "https://").replace("ws://", "http://")
            async with livekit_api.LiveKitAPI(
                url=lk_url,
                api_key=os.environ.get("LIVEKIT_API_KEY", ""),
                api_secret=os.environ.get("LIVEKIT_API_SECRET", ""),
            ) as lk:
                await lk.egress.stop_egress(
                    livekit_api.StopEgressRequest(egress_id=args.egress_id)
                )
            logger.info("Egress stopped | session_id=%s egress_id=%s", args.session_id, args.egress_id)

            from api.db import write_recording  # type: ignore[import]
            write_recording(
                session_id=args.session_id,
                egress_id=args.egress_id,
                r2_key=args.r2_key,
            )
            logger.info("Recording metadata written | session_id=%s", args.session_id)
        except Exception as exc:
            logger.error("Egress stop/recording write failed | session_id=%s error=%s", args.session_id, exc)

    # Step 7 — run scoring and write report (awaited so process stays alive)
    await _run_scoring_async(
        session_id=args.session_id,
        transcript=serialised_transcript,
        job_description=args.job_description,
        round_type=args.round_type,
        language_mode=args.language_mode,
    )

    logger.info(
        "Session ended | session_id=%s duration=%.1fs questions=%d",
        args.session_id, duration_secs, pairs_count,
    )


async def _run_scoring_async(
    session_id: str,
    transcript: list,
    job_description: str,
    round_type: str,
    language_mode: str = "english",
) -> None:
    try:
        loop = asyncio.get_event_loop()
        from bot.scoring.pipeline import run_scoring_pipeline

        report = await loop.run_in_executor(
            None, run_scoring_pipeline, session_id, transcript, job_description, round_type, language_mode,
        )
        logger.info(
            "Scoring complete | session_id=%s status=%s",
            session_id, report.get("scoring_status"),
        )

        try:
            from api.db import write_scoring_report  # type: ignore[import]
            write_scoring_report(session_id=session_id, report=report)
            logger.info("Scoring report written to MongoDB | session_id=%s", session_id)
        except ImportError:
            logger.warning(
                "api.db not found — skipping scoring report write (local test mode) | session_id=%s",
                session_id,
            )
        except Exception as exc:
            logger.error("MongoDB scoring report write failed | session_id=%s error=%s", session_id, exc)

    except Exception as exc:
        logger.error("_run_scoring_async error | session_id=%s error=%s", session_id, str(exc))


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(run_bot(args))
