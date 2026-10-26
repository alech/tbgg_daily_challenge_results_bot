"""Hourly snapshot of the global club XP leaderboard, kept in S3 to chart XP over time.

Each run stores the response verbatim as `club-xp/YYYY-MM-DD/HHMMSSZ.json` (UTC fetch time),
so the history carries every field GeoGuessr returns and the date prefix lists one day.

Failures are not DMed. An expired cookie already has the 20:00 health check, and an hourly
DM during a GeoGuessr outage would be noise; a missed snapshot is only a gap in the chart.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from typing import Any, Protocol, cast

from tbgg_bot import geoguessr
from tbgg_bot.credentials import cookie_store

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

BUCKET_ENV = "CLUB_XP_BUCKET"
KEY_PREFIX = "club-xp"


class S3Client(Protocol):
    """The subset of the boto3 S3 client this module uses, in boto3's PascalCase."""

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> object: ...


def _s3_client(client: S3Client | None = None) -> S3Client:
    """Return the injected client, or a real one. Imported lazily to keep cold starts down."""
    if client is not None:
        return client
    import boto3

    return cast(S3Client, boto3.client("s3"))


def snapshot_key(fetched_at: dt.datetime) -> str:
    """Where a snapshot taken at `fetched_at` is stored."""
    utc = fetched_at.astimezone(dt.UTC)
    return f"{KEY_PREFIX}/{utc:%Y-%m-%d}/{utc:%H%M%S}Z.json"


def snapshot(client: S3Client | None = None, now: dt.datetime | None = None) -> str:
    """Fetch the club leaderboard and store it. Returns the S3 key written."""
    bucket = os.environ.get(BUCKET_ENV)
    if not bucket:
        raise ValueError(f"{BUCKET_ENV} is not set")
    cookie = cookie_store().cookie()
    if not cookie:
        raise geoguessr.SessionExpiredError("No _ncfa cookie is stored")

    fetched_at = now or dt.datetime.now(dt.UTC)
    data = geoguessr.fetch_club_leaderboard(cookie)
    clubs = data.get("top") or []
    if not clubs:
        raise geoguessr.GeoGuessrError("Club leaderboard came back without a top list")

    key = snapshot_key(fetched_at)
    _s3_client(client).put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(data, ensure_ascii=False).encode(),
        ContentType="application/json",
    )
    LOGGER.info("Stored %d clubs at s3://%s/%s", len(clubs), bucket, key)
    return key


def lambda_handler(_event: dict[str, Any], _context: object = None) -> dict[str, Any]:
    """EventBridge entry point; the event carries nothing this function needs."""
    return {"status": "ok", "key": snapshot()}
