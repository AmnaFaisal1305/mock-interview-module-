import os
import logging
from livekit import api

logger = logging.getLogger("careerpilot.bot")


def generate_livekit_token(room_name: str, participant_identity: str) -> str:
    """
    Returns a signed LiveKit JWT for the given room and identity.
    Call once with identity="user" (→ React) and once with identity="bot" (→ Pipecat bot).
    Reads LIVEKIT_API_KEY and LIVEKIT_API_SECRET automatically from environment.
    """
    # Fail early with clear messages if any required var is missing
    livekit_url = os.environ.get("LIVEKIT_URL")
    api_key = os.environ.get("LIVEKIT_API_KEY")
    api_secret = os.environ.get("LIVEKIT_API_SECRET")

    if not livekit_url:
        raise ValueError("LIVEKIT_URL environment variable is not set")
    if not api_key:
        raise ValueError("LIVEKIT_API_KEY environment variable is not set")
    if not api_secret:
        raise ValueError("LIVEKIT_API_SECRET environment variable is not set")

    token = (
        api.AccessToken(api_key, api_secret)
        .with_identity(participant_identity)
        .with_name(participant_identity)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
            )
        )
    )

    jwt = token.to_jwt()
    logger.info(
        "Generated LiveKit token | identity='%s' room='%s'",
        participant_identity,
        room_name,
    )
    return jwt


# Self-check:
# Returns: plain JWT string
# Failure modes: ValueError on missing env vars; livekit SDK exceptions bubble up —
#   callers (Amna's session.py) should wrap in try/except for HTTP error responses
