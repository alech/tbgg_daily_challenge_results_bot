"""Build the Ladder Watch page and publish it to the site bucket behind CloudFront.

Runs hourly, a few minutes after each snapshot. Run it locally with
`uv run python -m tbgg_bot.ladder_page --bucket <ClubXpBucketName> --out ladder.html`.

Neighbours are the clubs within three places of TBGG at the latest snapshot, held fixed
across every week shown so each club keeps its colour and the weeks compare like with like.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
from pathlib import Path
from typing import Any, Protocol, cast

from tbgg_bot import ladder

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

HISTORY_BUCKET_ENV = "CLUB_XP_BUCKET"
SITE_BUCKET_ENV = "SITE_BUCKET"
# The page's path on the site, e.g. ladder_<random>. Kept out of the public repo, it is the
# only thing between a passer-by and the page. CloudFront maps a default object only for the
# site root, so the page is stored under the bare path rather than as <path>/index.html
PAGE_PATH_ENV = "LADDER_PATH"
ROOT_KEY = "index.html"
# The site root is deliberately empty; the ladder is the only page so far
ROOT_PAGE = (
    '<!doctype html><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    "<title>TBGG</title>"
    "<style>:root{color-scheme:light dark}body{margin:0;min-height:100vh;display:grid;"
    "place-items:center;font:16px/1.5 system-ui,sans-serif;background:Canvas;color:CanvasText}"
    "</style><p>Nothing to see here, move along.</p>\n"
)
TEMPLATE = Path(__file__).with_name("ladder_template.html")
WEEKS_SHOWN = 8
# CloudFront and browsers may serve a copy this old; a new page is built once an hour
CACHE_CONTROL = "public, max-age=300"


class S3Writer(Protocol):
    def put_object(
        self, *, Bucket: str, Key: str, Body: bytes, ContentType: str, CacheControl: str
    ) -> object: ...


def _iso(at: dt.datetime) -> str:
    return at.isoformat().replace("+00:00", "Z")


def page_data(snapshots: list[ladder.Snapshot]) -> dict[str, Any]:
    """The page's embedded data: the weeks, and each neighbour's hourly XP and rank."""
    weeks = ladder.weeks(snapshots)
    if not weeks:
        raise ValueError("No challenge week in the snapshots starts on time")
    offset = weeks[0].first
    snapshots = snapshots[offset:]
    clubs = ladder.neighbours(snapshots[-1])
    focus = next(c for c in clubs if c.tag == ladder.FOCUS_TAG)
    # Colour slots follow the club: the focus club first, then neighbours in rank order
    slots = [focus] + [c for c in clubs if c is not focus]

    def series(club_id: str, field: str) -> list[int | None]:
        return [getattr(s.clubs[club_id], field) if club_id in s.clubs else None for s in snapshots]

    return {
        "times": [_iso(s.at) for s in snapshots],
        "weeks": [
            {
                "start": _iso(w.start),
                "end": _iso(w.end),
                "i0": w.first - offset,
                "i1": w.last - offset,
                "complete": w.complete,
            }
            for w in weeks
        ],
        "clubs": [
            {
                "id": c.club_id,
                "tag": c.tag,
                "name": c.name,
                "slot": slots.index(c) + 1,
                "focus": c is focus,
                "xp": series(c.club_id, "xp"),
                "pos": series(c.club_id, "position"),
            }
            for c in clubs
        ],
    }


def render(data: dict[str, Any]) -> str:
    # `</` cannot appear inside the inline script, or a club name could end it early
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace("/*DATA*/null", payload, 1)


def build(client: ladder.S3Reader, bucket: str, now: dt.datetime | None = None) -> str:
    """Render the page from the last WEEKS_SHOWN challenge weeks of snapshots."""
    now = now or dt.datetime.now(dt.UTC)
    since = ladder.week_start_at_or_before(now) - (WEEKS_SHOWN - 1) * ladder.WEEK
    keys = ladder.snapshot_keys(client, bucket, since, now)
    LOGGER.info("Building the ladder page from %d snapshots", len(keys))
    return render(page_data(ladder.load(client, bucket, keys)))


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"{name} is not set")
    return value


def lambda_handler(_event: dict[str, Any], _context: object = None) -> dict[str, Any]:
    """EventBridge entry point; the event carries nothing this function needs."""
    history, site, path = (
        _required(n) for n in (HISTORY_BUCKET_ENV, SITE_BUCKET_ENV, PAGE_PATH_ENV)
    )
    client = ladder.s3_reader()
    html = build(client, history)
    for key, body in ((path, html), (ROOT_KEY, ROOT_PAGE)):
        cast(S3Writer, client).put_object(
            Bucket=site,
            Key=key,
            Body=body.encode(),
            ContentType="text/html; charset=utf-8",
            CacheControl=CACHE_CONTROL,
        )
    return {"status": "ok", "bytes": len(html)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Ladder Watch page locally.")
    parser.add_argument("--bucket", required=True, help="the ClubXpBucketName stack output")
    parser.add_argument("--out", type=Path, default=Path("ladder.html"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args.out.write_text(build(ladder.s3_reader(), args.bucket), encoding="utf-8")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
