"""Challenge weeks and TBGG's neighbours, read from the hourly club XP snapshots in S3.

Weekly club challenges start on Wednesday at 11:00 UTC. A week is measured from the first
snapshot at or after its start to the first one at or after the next week's start, so the
boundary snapshot (30 seconds in) closes one week and opens the next.
"""

from __future__ import annotations

import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Protocol, cast

from tbgg_bot.club_xp import KEY_PREFIX

FOCUS_TAG = "TBGG"
SPAN = 3
WEEK = dt.timedelta(days=7)
WEEK_START_WEEKDAY = 2  # Wednesday
WEEK_START_HOUR = 11
# A week whose first snapshot comes later than this after its start is missing its baseline
START_TOLERANCE = dt.timedelta(hours=2)
FETCH_THREADS = 16


class S3Reader(Protocol):
    """The subset of the boto3 S3 client this module uses, in boto3's PascalCase."""

    def list_objects_v2(
        self, *, Bucket: str, Prefix: str, ContinuationToken: str = ...
    ) -> dict[str, Any]: ...

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class Club:
    """One club's row in one snapshot."""

    club_id: str
    tag: str
    name: str
    position: int
    xp: int


@dataclass(frozen=True)
class Snapshot:
    at: dt.datetime
    clubs: dict[str, Club]

    def focus(self) -> Club | None:
        return next((c for c in self.clubs.values() if c.tag == FOCUS_TAG), None)


@dataclass(frozen=True)
class Week:
    """A challenge week as indexes of its first and last snapshot."""

    start: dt.datetime
    first: int
    last: int
    complete: bool

    @property
    def end(self) -> dt.datetime:
        return self.start + WEEK


def s3_reader(client: S3Reader | None = None) -> S3Reader:
    """Return the injected client, or a real one. Imported lazily to keep cold starts down."""
    if client is not None:
        return client
    import boto3
    from botocore.config import Config

    # One pooled connection per fetch thread, or botocore discards and reopens them
    return cast(S3Reader, boto3.client("s3", config=Config(max_pool_connections=FETCH_THREADS)))


def week_start_at_or_before(when: dt.datetime) -> dt.datetime:
    """The most recent Wednesday 11:00 UTC, counting `when` itself."""
    when = when.astimezone(dt.UTC)
    start = when.replace(hour=WEEK_START_HOUR, minute=0, second=0, microsecond=0)
    start -= dt.timedelta(days=(start.weekday() - WEEK_START_WEEKDAY) % 7)
    return start if start <= when else start - WEEK


def parse(at: dt.datetime, payload: dict[str, Any]) -> Snapshot:
    """Keep only what the ladder needs from one stored leaderboard response."""
    rows = payload.get("top", []) + payload.get("aroundUser", [])
    clubs = {
        row["club"]["clubId"]: Club(
            club_id=row["club"]["clubId"],
            tag=row["club"]["tag"],
            name=row["club"]["name"],
            position=row["position"],
            xp=row["xp"],
        )
        for row in rows
    }
    return Snapshot(at, clubs)


def key_time(key: str) -> dt.datetime:
    """The fetch time encoded in a `club-xp/YYYY-MM-DD/HHMMSSZ.json` key."""
    day, name = key.removeprefix(f"{KEY_PREFIX}/").split("/")
    return dt.datetime.fromisoformat(f"{day}T{name[:2]}:{name[2:4]}:{name[4:6]}+00:00")


def _keys_on(client: S3Reader, bucket: str, day: dt.date) -> list[str]:
    keys: list[str] = []
    kwargs: dict[str, str] = {"Bucket": bucket, "Prefix": f"{KEY_PREFIX}/{day.isoformat()}/"}
    while True:
        page = client.list_objects_v2(**kwargs)
        keys.extend(item["Key"] for item in page.get("Contents", []))
        if not page.get("IsTruncated"):
            return keys
        kwargs["ContinuationToken"] = page["NextContinuationToken"]


def snapshot_keys(
    client: S3Reader, bucket: str, since: dt.datetime, until: dt.datetime
) -> list[str]:
    """Keys of the snapshots taken in [since, until], oldest first."""
    days = (until.date() - since.date()).days
    keys = [
        key
        for offset in range(days + 1)
        for key in _keys_on(client, bucket, since.date() + dt.timedelta(days=offset))
    ]
    return sorted(k for k in keys if since <= key_time(k) <= until)


def load(client: S3Reader, bucket: str, keys: list[str]) -> list[Snapshot]:
    """Fetch and parse snapshots in parallel. boto3 clients are safe to share across threads."""

    def fetch(key: str) -> Snapshot:
        body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
        return parse(key_time(key), json.loads(body))

    with ThreadPoolExecutor(max_workers=FETCH_THREADS) as pool:
        return list(pool.map(fetch, keys))


def _first_at(snapshots: list[Snapshot], when: dt.datetime) -> int | None:
    return next((i for i, s in enumerate(snapshots) if s.at >= when), None)


def weeks(snapshots: list[Snapshot]) -> list[Week]:
    """Every challenge week the snapshots cover from its start, oldest first."""
    if not snapshots:
        return []
    result = []
    start = week_start_at_or_before(snapshots[0].at)
    while start <= snapshots[-1].at:
        first = _first_at(snapshots, start)
        if first is not None and snapshots[first].at - start <= START_TOLERANCE:
            last = _first_at(snapshots, start + WEEK)
            complete = last is not None
            result.append(
                Week(start, first, last if last is not None else len(snapshots) - 1, complete)
            )
        start += WEEK
    return result


def neighbours(snapshot: Snapshot) -> list[Club]:
    """TBGG and the clubs within SPAN places of it, best rank first."""
    focus = snapshot.focus()
    if focus is None:
        raise ValueError(f"{FOCUS_TAG} is not in the snapshot from {snapshot.at.isoformat()}")
    near = [c for c in snapshot.clubs.values() if abs(c.position - focus.position) <= SPAN]
    return sorted(near, key=lambda c: c.position)
