from __future__ import annotations

import logging
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.config import Settings
from app.services.payload.python_literal import format_payload_python

logger = logging.getLogger("hhs.s3")


class S3PayloadStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = None
        if settings.s3_configured:
            self._client = boto3.client(
                "s3",
                region_name=settings.aws_region,
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
            )
            logger.info(
                "S3 payload store ready (bucket=%s region=%s)",
                settings.aws_bucket_name,
                settings.aws_region,
            )
        else:
            logger.warning(
                "S3 is not configured; engine payloads will not be uploaded"
            )

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def build_key(self, roster_date: str, run_id: str) -> str:
        prefix = (self.settings.s3_payload_prefix or "hhs/engine-payloads").strip("/")
        # Python-literal file (None/True/False) to match offline optimizer inputs.
        return f"{prefix}/{roster_date}/{run_id}.py"

    def upload_payload(self, key: str, payload: dict[str, Any]) -> str:
        if not self._client:
            raise RuntimeError("S3 is not configured")
        body = format_payload_python(payload).encode("utf-8")
        try:
            self._client.put_object(
                Bucket=self.settings.aws_bucket_name,
                Key=key,
                Body=body,
                ContentType="text/x-python; charset=utf-8",
            )
        except (BotoCoreError, ClientError) as exc:
            logger.error("S3 upload failed for key=%s: %s", key, exc)
            raise RuntimeError(f"S3 upload failed: {exc}") from exc
        logger.info("Uploaded engine payload to s3://%s/%s (%s bytes)", self.settings.aws_bucket_name, key, len(body))
        return key

    def presigned_download_url(self, key: str) -> str:
        if not self._client:
            raise RuntimeError("S3 is not configured")
        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self.settings.aws_bucket_name,
                    "Key": key,
                },
                ExpiresIn=self.settings.s3_presign_expires_seconds,
            )
        except (BotoCoreError, ClientError) as exc:
            logger.error("S3 presign failed for key=%s: %s", key, exc)
            raise RuntimeError(f"S3 presign failed: {exc}") from exc
