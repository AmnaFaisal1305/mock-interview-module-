"""
AWS S3 helper for CareerPilot.
Used only for generating presigned GET URLs so the frontend can stream recordings.
The actual upload is done by LiveKit Egress — it pushes directly to S3 when recording ends.
"""

import os
import logging
import boto3
from botocore.client import Config

logger = logging.getLogger("careerpilot.api")


def _get_s3_client():
    access_key = os.environ.get("AWS_ACCESS_KEY_ID")
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
    region = os.environ.get("AWS_REGION", "ap-southeast-1")

    if not all([access_key, secret_key]):
        raise ValueError("AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must both be set")

    return boto3.client(
        "s3",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=Config(signature_version="s3v4"),
    )


def generate_presigned_url(s3_key: str, expires_in: int = 3600) -> str:
    """
    Returns a temporary URL valid for `expires_in` seconds (default 1 hour).
    The browser uses this URL to stream the audio file directly from S3.
    """
    bucket = os.environ.get("AWS_BUCKET_NAME")
    if not bucket:
        raise ValueError("AWS_BUCKET_NAME environment variable is not set")

    client = _get_s3_client()
    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": s3_key},
        ExpiresIn=expires_in,
    )
    logger.info("Generated S3 presigned URL | key=%s expires_in=%ds", s3_key, expires_in)
    return url
