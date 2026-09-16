"""Which day a run targets, and the routing promise: results public, problems private."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from tbgg_bot import handler

CHANNEL_ID = 42
ALERT_USER_ID = 99


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", str(CHANNEL_ID))
    monkeypatch.setenv("DISCORD_ALERT_USER_ID", str(ALERT_USER_ID))
    monkeypatch.setenv("GEOGUESSR_NCFA", "test-cookie")
    # Force the local EnvStore path so no test ever reaches for AWS
    monkeypatch.delenv("DISCORD_TOKEN_PARAM", raising=False)
    monkeypatch.delenv("GEOGUESSR_PARAM_PREFIX", raising=False)


@pytest.fixture
def channel_posts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, Any]]:
    sent: list[tuple[int, Any]] = []
    monkeypatch.setattr(
        handler.discord_post, "post", lambda _t, channel, embed: sent.append((channel, embed))
    )
    return sent


@pytest.fixture
def dms(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, Any]]:
    sent: list[tuple[int, Any]] = []
    monkeypatch.setattr(
        handler.discord_post, "post_dm", lambda _t, user, embed: sent.append((user, embed))
    )
    return sent


def test_the_run_targets_the_utc_day_that_just_ended() -> None:
    expected = (dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)).isoformat()
    assert handler.previous_utc_day() == expected


def test_an_event_without_a_date_falls_back_to_yesterday(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(handler, "run", seen.append)

    for event in ({}, {"date": None}, {"source": "aws.events"}):
        handler.lambda_handler(dict(event))

    assert seen == [handler.previous_utc_day()] * 3


def test_an_explicit_date_in_the_event_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(handler, "run", seen.append)

    result = handler.lambda_handler({"date": "2026-01-31"})

    assert seen == ["2026-01-31"]
    assert result == {"status": "ok", "action": "post", "date": "2026-01-31"}


def test_a_failure_is_dmed_not_posted_to_the_club_channel(
    monkeypatch: pytest.MonkeyPatch,
    channel_posts: list[tuple[int, Any]],
    dms: list[tuple[int, Any]],
) -> None:
    def explode(date_str: str, cookie: str) -> dict[str, Any]:
        raise RuntimeError("geoguessr is down")

    monkeypatch.setattr(handler.geoguessr, "fetch_leaderboard", explode)

    with pytest.raises(RuntimeError, match="geoguessr is down"):
        handler.run("2026-09-16")

    assert channel_posts == [], "the club should never see a stack trace"
    assert len(dms) == 1
    user, embed = dms[0]
    assert user == ALERT_USER_ID
    assert "geoguessr is down" in embed.to_dict()["description"]


def test_a_missing_cookie_is_reported_as_such(
    monkeypatch: pytest.MonkeyPatch, dms: list[tuple[int, Any]]
) -> None:
    monkeypatch.delenv("GEOGUESSR_NCFA")

    with pytest.raises(handler.geoguessr.SessionExpiredError):
        handler.run("2026-09-16")

    assert "No _ncfa cookie is stored" in dms[0][1].to_dict()["description"]


def test_a_valid_cookie_passes_the_check_silently(
    monkeypatch: pytest.MonkeyPatch, dms: list[tuple[int, Any]]
) -> None:
    monkeypatch.setattr(handler.geoguessr, "session_is_valid", lambda _cookie: True)

    assert handler.check_cookie() is True
    assert dms == [], "a healthy cookie should not generate a notification"


def test_an_expired_cookie_dms_a_warning_with_refresh_instructions(
    monkeypatch: pytest.MonkeyPatch, dms: list[tuple[int, Any]]
) -> None:
    monkeypatch.setattr(handler.geoguessr, "session_is_valid", lambda _cookie: False)

    assert handler.check_cookie() is False

    user, embed = dms[0]
    assert user == ALERT_USER_ID
    description = embed.to_dict()["description"]
    assert "expired" in description
    assert "put-parameter" in description, "the warning must say how to fix it"


def test_the_check_warns_when_no_cookie_is_stored_at_all(
    monkeypatch: pytest.MonkeyPatch, dms: list[tuple[int, Any]]
) -> None:
    monkeypatch.delenv("GEOGUESSR_NCFA")

    assert handler.check_cookie() is False
    assert "No `_ncfa` cookie is stored" in dms[0][1].to_dict()["description"]


def test_the_check_action_never_posts_the_daily_result(
    monkeypatch: pytest.MonkeyPatch, channel_posts: list[tuple[int, Any]]
) -> None:
    monkeypatch.setattr(handler.geoguessr, "session_is_valid", lambda _cookie: True)

    result = handler.lambda_handler({"action": "check"})

    assert result == {"status": "ok", "action": "check", "cookieValid": True}
    assert channel_posts == []


def test_a_missing_alert_user_id_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DISCORD_ALERT_USER_ID")
    monkeypatch.setattr(handler.geoguessr, "session_is_valid", lambda _cookie: False)

    with pytest.raises(ValueError, match="DISCORD_ALERT_USER_ID"):
        handler.check_cookie()
