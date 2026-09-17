"""Lambda entry point.

Two actions share one function:

* `post` (the default, 00:05 UTC) publishes the finished result for the day that just ended.
* `check` (20:00 Europe/Berlin) verifies the `_ncfa` cookie and DMs a warning if it has
  expired, leaving the evening free to paste a fresh one in before the nightly post.

Results go to the club channel. Anything the club should not see — failures, cookie
expiry — goes to one person's DMs instead.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from typing import Any

import discord

from tbgg_bot import discord_post, geoguessr, scoring
from tbgg_bot.credentials import CookieStore, EnvStore, ParameterStore, get_discord_token

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

GEOGUESSR_PREFIX_ENV = "GEOGUESSR_PARAM_PREFIX"
DISCORD_TOKEN_PARAM_ENV = "DISCORD_TOKEN_PARAM"
CHANNEL_ENV = "DISCORD_CHANNEL_IDS"
ALERT_USER_ENV = "DISCORD_ALERT_USER_ID"

ALERT_COLOUR = 0xD9534F
REFRESH_HELP = (
    "Grab a fresh cookie: open geoguessr.com signed in → DevTools → "
    "Application → Cookies → copy `_ncfa`, then run\n"
    "```\naws ssm put-parameter --type SecureString \\\n"
    "  --name /tbgg-bot/geoguessr/ncfa --value '<cookie>' --overwrite\n```"
)


def previous_utc_day() -> str:
    """The UTC day that just ended, which is the challenge this run reports on."""
    return (dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)).isoformat()


def _cookie_store() -> CookieStore:
    prefix = os.environ.get(GEOGUESSR_PREFIX_ENV)
    return ParameterStore(prefix) if prefix else EnvStore()


def _discord_token() -> str:
    parameter_name = os.environ.get(DISCORD_TOKEN_PARAM_ENV)
    if parameter_name:
        return get_discord_token(parameter_name)
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise ValueError(f"Set {DISCORD_TOKEN_PARAM_ENV} or DISCORD_TOKEN")
    return token


def _required_id(name: str) -> int:
    raw = os.environ.get(name)
    if not raw:
        raise ValueError(f"{name} is not set")
    return int(raw)


def _channel_ids() -> list[int]:
    """The channels to post to, as a comma-separated list of IDs."""
    raw = os.environ.get(CHANNEL_ENV, "")
    ids = [int(part) for part in (p.strip() for p in raw.split(",")) if part]
    if not ids:
        raise ValueError(f"{CHANNEL_ENV} is not set")
    return ids


def _alert(token: str, title: str, description: str) -> None:
    """DM the maintainer. Never posts to the club channel."""
    embed = discord.Embed(title=title, description=description, colour=ALERT_COLOUR)
    discord_post.post_dm(token, _required_id(ALERT_USER_ENV), embed)


def run(date_str: str) -> None:
    """Fetch, compute and post the result for one UTC day."""
    token = _discord_token()
    try:
        cookie = _cookie_store().cookie()
        if not cookie:
            raise geoguessr.SessionExpiredError("No _ncfa cookie is stored")
        result = scoring.compute(geoguessr.fetch_leaderboard(date_str, cookie), date_str)
    except Exception as error:
        LOGGER.exception("Failed to build the result for %s", date_str)
        _alert(
            token,
            f"⚠️ TBGG daily challenge — {date_str}",
            f"Could not post today's result.\n```\n{type(error).__name__}: {error}\n```\n"
            f"{REFRESH_HELP}",
        )
        raise

    outcome = discord_post.post(token, _channel_ids(), discord_post.build_embed(result))
    if outcome.failed:
        # Getting the result out to the channels that do work matters more than a clean run,
        # so a refused channel is reported privately rather than aborting the post.
        detail = "\n".join(f"<#{cid}> — `{reason}`" for cid, reason in outcome.failed)
        _alert(
            token,
            f"📪 Could not post to every channel — {date_str}",
            f"Delivered to {len(outcome.posted)} of "
            f"{len(outcome.posted) + len(outcome.failed)} channels.\n\n{detail}\n\n"
            "Usually this means the bot has not been invited to that server, or lacks "
            "**View Channel** / **Send Messages** / **Embed Links** there.",
        )
    if outcome.all_failed:
        raise RuntimeError(f"No channel accepted the result for {date_str}")


def check_cookie() -> bool:
    """Verify the cookie and DM a warning if it has expired. Returns whether it is usable."""
    token = _discord_token()
    cookie = _cookie_store().cookie()
    if cookie and geoguessr.session_is_valid(cookie):
        return True

    reason = (
        "No `_ncfa` cookie is stored." if not cookie else "The stored `_ncfa` cookie has expired."
    )
    _alert(
        token,
        "🔑 GeoGuessr cookie needs refreshing",
        f"{reason} Tonight's 00:05 UTC post will fail unless it is replaced.\n\n{REFRESH_HELP}",
    )
    return False


def lambda_handler(event: dict[str, Any], _context: object = None) -> dict[str, Any]:
    """EventBridge entry point. `action` selects the mode; `date` overrides the target day."""
    payload = event or {}
    action = str(payload.get("action") or "post")

    if action == "check":
        LOGGER.info("Running cookie health check")
        return {"status": "ok", "action": action, "cookieValid": check_cookie()}

    date_str = str(payload.get("date") or previous_utc_day())
    LOGGER.info("Posting club daily challenge result for %s", date_str)
    run(date_str)
    return {"status": "ok", "action": action, "date": date_str}
