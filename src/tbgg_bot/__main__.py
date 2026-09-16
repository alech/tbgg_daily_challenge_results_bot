"""Run the bot locally: `uv run python -m tbgg_bot [YYYY-MM-DD] [--dry-run|--check]`.

Auth comes from GEOGUESSR_NCFA in the environment. `--dry-run` prints the rendered embed
instead of posting; `--check` runs the cookie health check, which DMs you only if it fails.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from tbgg_bot import discord_post, geoguessr, handler, scoring
from tbgg_bot.credentials import EnvStore


def _dry_run(date_str: str) -> int:
    cookie = EnvStore().cookie()
    if not cookie:
        print("No GEOGUESSR_NCFA in the environment", file=sys.stderr)
        return 1
    result = scoring.compute(geoguessr.fetch_leaderboard(date_str, cookie), date_str)
    print(json.dumps(discord_post.build_embed(result).to_dict(), indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tbgg_bot", description=__doc__)
    parser.add_argument("date", nargs="?", default=None, help="UTC day, defaults to yesterday")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", help="print the embed as JSON instead of posting"
    )
    mode.add_argument(
        "--check", action="store_true", help="verify the cookie, DMing only if it has expired"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    date_str = args.date or handler.previous_utc_day()
    try:
        if args.check:
            print("cookie valid" if handler.check_cookie() else "cookie EXPIRED — DM sent")
            return 0
        if args.dry_run:
            return _dry_run(date_str)
        handler.run(date_str)
    except (geoguessr.GeoGuessrError, ValueError) as error:
        # A full traceback here is noise; the Lambda path still raises so the invocation fails.
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
