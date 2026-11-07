"""Build the TBGG Ladder Watch page from the hourly club XP snapshots in S3.

    AWS_PROFILE=... uv run python tools/ladder/build.py [--out ladder.html]

Snapshots are cached under `.cache/club-xp/`, and only keys not cached yet are downloaded.
Neighbours are the clubs within three places of TBGG at the latest snapshot, held fixed
across every week so each club keeps its colour and the weeks compare like with like.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / ".cache" / "club-xp"
TEMPLATE = Path(__file__).with_name("template.html")
STACK = "TbggBotStack"
BUCKET_OUTPUT = "ClubXpBucketName"
PREFIX = "club-xp/"

FOCUS = "TBGG"
SPAN = 3
# Weekly club challenges start on Wednesday at 11:00 UTC
WEEK_START_WEEKDAY = 2
WEEK_START_HOUR = 11
# A week whose first snapshot comes later than this after the start did not begin on time
START_TOLERANCE = dt.timedelta(hours=2)


@dataclass(frozen=True)
class Snapshot:
    at: dt.datetime
    clubs: dict[str, dict[str, Any]]


def bucket_name() -> str:
    stacks = boto3.client("cloudformation").describe_stacks(StackName=STACK)["Stacks"]
    for output in stacks[0].get("Outputs", []):
        if output["OutputKey"] == BUCKET_OUTPUT:
            return str(output["OutputValue"])
    raise SystemExit(f"{STACK} has no {BUCKET_OUTPUT} output")


def sync(bucket: str) -> int:
    """Download every snapshot not cached yet. Returns how many were fetched."""
    s3 = boto3.client("s3")
    fetched = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=PREFIX):
        for item in page.get("Contents", []):
            target = CACHE / item["Key"].removeprefix(PREFIX)
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, item["Key"], str(target))
            fetched += 1
    return fetched


def load() -> list[Snapshot]:
    snapshots = []
    for path in sorted(CACHE.glob("*/*Z.json")):
        hms = path.name[:6]
        at = dt.datetime.fromisoformat(f"{path.parent.name}T{hms[:2]}:{hms[2:4]}:{hms[4:6]}+00:00")
        data = json.loads(path.read_text())
        clubs = {c["club"]["clubId"]: c for c in data["top"] + data.get("aroundUser", [])}
        snapshots.append(Snapshot(at, clubs))
    if not snapshots:
        raise SystemExit(f"No snapshots in {CACHE}")
    return snapshots


def first_week_start(after: dt.datetime) -> dt.datetime:
    """The first Wednesday 11:00 UTC at or after `after`."""
    start = after.replace(hour=WEEK_START_HOUR, minute=0, second=0, microsecond=0)
    start += dt.timedelta(days=(WEEK_START_WEEKDAY - start.weekday()) % 7)
    return start if start >= after else start + dt.timedelta(days=7)


def first_index_at(snapshots: list[Snapshot], when: dt.datetime) -> int | None:
    return next((i for i, s in enumerate(snapshots) if s.at >= when), None)


def weeks(snapshots: list[Snapshot]) -> list[dict[str, Any]]:
    """Each challenge week as indexes of its first and last snapshot."""
    result = []
    start = first_week_start(snapshots[0].at)
    while start <= snapshots[-1].at:
        end = start + dt.timedelta(days=7)
        i0 = first_index_at(snapshots, start)
        if i0 is not None and snapshots[i0].at - start <= START_TOLERANCE:
            i1 = first_index_at(snapshots, end)
            result.append(
                {
                    "start": start.isoformat().replace("+00:00", "Z"),
                    "end": end.isoformat().replace("+00:00", "Z"),
                    "i0": i0,
                    "i1": len(snapshots) - 1 if i1 is None else i1,
                    "complete": i1 is not None,
                }
            )
        start = end
    return result


def build(snapshots: list[Snapshot]) -> dict[str, Any]:
    week_list = weeks(snapshots)
    if not week_list:
        raise SystemExit("No complete-from-the-start challenge week in the snapshots yet")
    snapshots = snapshots[week_list[0]["i0"] :]
    offset = week_list[0]["i0"]
    for week in week_list:
        week["i0"] -= offset
        week["i1"] -= offset

    latest = snapshots[-1].clubs
    focus_id = next(k for k, v in latest.items() if v["club"]["tag"] == FOCUS)
    rank = latest[focus_id]["position"]
    ids = [
        k
        for k, v in sorted(latest.items(), key=lambda kv: kv[1]["position"])
        if abs(v["position"] - rank) <= SPAN
    ]
    # Colour slots follow the club: the focus club first, then neighbours in rank order
    slot_order = [focus_id] + [k for k in ids if k != focus_id]

    def series(cid: str, field: str) -> list[int | None]:
        return [s.clubs[cid][field] if cid in s.clubs else None for s in snapshots]

    return {
        "times": [s.at.isoformat().replace("+00:00", "Z") for s in snapshots],
        "weeks": week_list,
        "clubs": [
            {
                "id": cid,
                "tag": latest[cid]["club"]["tag"],
                "name": latest[cid]["club"]["name"],
                "slot": slot_order.index(cid) + 1,
                "focus": cid == focus_id,
                "xp": series(cid, "xp"),
                "pos": series(cid, "position"),
            }
            for cid in ids
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / ".cache" / "ladder.html")
    parser.add_argument("--offline", action="store_true", help="use the cache as it is")
    args = parser.parse_args()

    if not args.offline:
        print(f"Fetched {sync(bucket_name())} new snapshots")
    data = build(load())
    html = TEMPLATE.read_text().replace(
        "/*DATA*/null", json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html)
    times = data["times"]
    print(f"{len(times)} snapshots, {times[0]} to {times[-1]}, {len(data['weeks'])} weeks")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
