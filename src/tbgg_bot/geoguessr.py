"""GeoGuessr API access.

Auth is the `_ncfa` session cookie and nothing else. GeoGuessr signs users in with emailed
one-time codes rather than passwords, so there is no credential pair a bot could post to
obtain a session: the cookie is copied from a browser and refreshed by hand when it expires.
The nightly health check exists to give warning before that happens.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

LOGGER = logging.getLogger(__name__)

BASE = "https://www.geoguessr.com/api/v3"
PROFILES_URL = f"{BASE}/profiles"
LEADERBOARD_URL = f"{BASE}/challenges/daily-challenges/leaderboard/club"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
)
TIMEOUT_SECONDS = 30


class GeoGuessrError(Exception):
    """A GeoGuessr request failed in a way the caller cannot recover from."""


class SessionExpiredError(GeoGuessrError):
    """The `_ncfa` cookie was rejected; a human needs to paste in a fresh one."""


def _get(url: str, ncfa: str, referer: str) -> dict[str, Any]:
    """Perform an authenticated GET, mapping a rejected cookie to SessionExpiredError."""
    request = urllib.request.Request(
        url,
        headers={
            "accept": "*/*",
            "content-type": "application/json",
            "cookie": f"_ncfa={ncfa}",
            "referer": referer,
            "user-agent": USER_AGENT,
            "x-client": "web-1.7726-a159ff5",
            "x-locale": "en",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            data: dict[str, Any] = json.load(response)
            return data
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise SessionExpiredError(f"_ncfa cookie rejected (HTTP {error.code})") from error
        body = error.read(500).decode(errors="replace")
        raise GeoGuessrError(f"GET {url} failed: HTTP {error.code}: {body}") from error


def fetch_leaderboard(date_str: str, ncfa: str) -> dict[str, Any]:
    """Fetch the club daily-challenge leaderboard for one UTC day."""
    return _get(
        f"{LEADERBOARD_URL}?dateStr={date_str}",
        ncfa,
        referer=f"https://www.geoguessr.com/daily-challenges/{date_str}/club",
    )


def session_is_valid(ncfa: str) -> bool:
    """Whether the cookie still authenticates.

    Checks the profile endpoint rather than a leaderboard, so the answer does not depend on
    whether anyone in the club has played yet today.
    """
    try:
        _get(PROFILES_URL, ncfa, referer="https://www.geoguessr.com/me/profile")
    except SessionExpiredError:
        LOGGER.info("Cookie check: rejected")
        return False
    LOGGER.info("Cookie check: still valid")
    return True
