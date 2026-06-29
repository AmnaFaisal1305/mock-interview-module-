"""
Cloudflare R2 helper for CareerPilot.
Used only for generating presigned GET URLs so the frontend can stream recordings.
The actual upload is done by LiveKit Egress — it pushes directly to R2 when recording ends.
"""

import os
import logging
import boto3
from botocore.client import Config

logger = logging.getLogger("careerpilot.api")


def _get_r2_client():
    account_id = os.environ.get("R2_ACCOUNT_ID")
    access_key = os.environ.get("R2_ACCESS_KEY_ID")
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")

    if not all([account_id, access_key, secret_key]):
        raise ValueError("R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY must all be set")

    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def generate_presigned_url(r2_key: str, expires_in: int = 3600) -> str:
    """
    Returns a temporary URL valid for `expires_in` seconds (default 1 hour).
    The browser uses this URL to stream the audio file directly from R2.
    """
    bucket = os.environ.get("R2_BUCKET_NAME")
    if not bucket:
        raise ValueError("R2_BUCKET_NAME environment variable is not set")

    client = _get_r2_client()
    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": r2_key},
        ExpiresIn=expires_in,
    )
    logger.info("Generated R2 presigned URL | key=%s expires_in=%ds", r2_key, expires_in)
    return url
