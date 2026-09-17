"""Which day a run targets, and the routing promise: results public, problems private."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from conftest import entry, guess, payload
from tbgg_bot import handler

CHANNEL_ID = 42
SECOND_CHANNEL_ID = 43
ALERT_USER_ID = 99


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_CHANNEL_IDS", f"{CHANNEL_ID},{SECOND_CHANNEL_ID}")
    monkeypatch.setenv("DISCORD_ALERT_USER_ID", str(ALERT_USER_ID))
    monkeypatch.setenv("GEOGUESSR_NCFA", "test-cookie")
    # Force the local EnvStore path so no test ever reaches for AWS
    monkeypatch.delenv("DISCORD_TOKEN_PARAM", raising=False)
    monkeypatch.delenv("GEOGUESSR_PARAM_PREFIX", raising=False)


def _outcome(posted: tuple[int, ...], failed: tuple[tuple[int, str], ...] = ()) -> Any:
    return handler.discord_post.PostOutcome(posted=posted, failed=failed)


@pytest.fixture
def leaderboard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for GeoGuessr so a run reaches the posting step without a network call."""
    data = payload(entry("Alice", [guess(5000)] * 5), entry("Bob", [guess(4000)] * 5))
    monkeypatch.setattr(handler.geoguessr, "fetch_leaderboard", lambda *_args: data)


@pytest.fixture
def channel_posts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[int], Any]]:
    """Record every post and pretend all channels accepted it."""
    sent: list[tuple[list[int], Any]] = []

    def fake_post(_token: str, channels: list[int], embed: Any) -> Any:
        sent.append((channels, embed))
        return _outcome(tuple(channels))

    monkeypatch.setattr(handler.discord_post, "post", fake_post)
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


def test_the_result_goes_to_every_configured_channel(
    leaderboard: None, channel_posts: list[tuple[list[int], Any]]
) -> None:
    handler.run("2026-09-16")

    channels, _embed = channel_posts[0]
    assert channels == [CHANNEL_ID, SECOND_CHANNEL_ID]


def test_channel_ids_tolerate_spacing_and_trailing_commas(
    monkeypatch: pytest.MonkeyPatch, leaderboard: None, channel_posts: list[tuple[list[int], Any]]
) -> None:
    monkeypatch.setenv("DISCORD_CHANNEL_IDS", " 42 , 43 ,")

    handler.run("2026-09-16")

    assert channel_posts[0][0] == [42, 43]


def test_one_channel_refusing_does_not_stop_the_others(
    monkeypatch: pytest.MonkeyPatch, leaderboard: None, dms: list[tuple[int, Any]]
) -> None:
    # the new channel has not been granted access yet: the established one must still get it
    monkeypatch.setattr(
        handler.discord_post,
        "post",
        lambda _t, _c, _e: _outcome(
            posted=(CHANNEL_ID,), failed=((SECOND_CHANNEL_ID, "Forbidden: Missing Access"),)
        ),
    )

    handler.run("2026-09-16")  # must not raise

    assert len(dms) == 1, "the maintainer should be told which channel refused"
    description = dms[0][1].to_dict()["description"]
    assert str(SECOND_CHANNEL_ID) in description
    assert "Missing Access" in description
    assert "Delivered to 1 of 2 channels" in description


def test_a_partial_failure_is_reported_privately_not_to_the_working_channel(
    monkeypatch: pytest.MonkeyPatch, leaderboard: None, dms: list[tuple[int, Any]]
) -> None:
    posts: list[Any] = []

    def fake_post(_t: str, channels: list[int], embed: Any) -> Any:
        posts.append(embed)
        return _outcome(posted=(CHANNEL_ID,), failed=((SECOND_CHANNEL_ID, "Forbidden"),))

    monkeypatch.setattr(handler.discord_post, "post", fake_post)

    handler.run("2026-09-16")

    assert len(posts) == 1, "the club channel gets the result once, not a failure notice"
    assert dms[0][0] == ALERT_USER_ID


def test_every_channel_refusing_fails_the_run(
    monkeypatch: pytest.MonkeyPatch, leaderboard: None, dms: list[tuple[int, Any]]
) -> None:
    monkeypatch.setattr(
        handler.discord_post,
        "post",
        lambda _t, _c, _e: _outcome(
            posted=(), failed=((CHANNEL_ID, "Forbidden"), (SECOND_CHANNEL_ID, "Forbidden"))
        ),
    )

    with pytest.raises(RuntimeError, match="No channel accepted"):
        handler.run("2026-09-16")

    assert len(dms) == 1


def test_a_single_configured_channel_still_works(
    monkeypatch: pytest.MonkeyPatch, leaderboard: None, channel_posts: list[tuple[list[int], Any]]
) -> None:
    monkeypatch.setenv("DISCORD_CHANNEL_IDS", str(CHANNEL_ID))

    handler.run("2026-09-16")

    assert channel_posts[0][0] == [CHANNEL_ID]


def test_no_configured_channels_fails_loudly(
    monkeypatch: pytest.MonkeyPatch, leaderboard: None
) -> None:
    monkeypatch.setenv("DISCORD_CHANNEL_IDS", "")

    with pytest.raises(ValueError, match="DISCORD_CHANNEL_IDS"):
        handler.run("2026-09-16")


def test_a_missing_alert_user_id_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DISCORD_ALERT_USER_ID")
    monkeypatch.setattr(handler.geoguessr, "session_is_valid", lambda _cookie: False)

    with pytest.raises(ValueError, match="DISCORD_ALERT_USER_ID"):
        handler.check_cookie()
