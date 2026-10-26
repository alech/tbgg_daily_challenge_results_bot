"""The hourly club XP snapshot: what gets stored, where, and when a run refuses to store."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import pytest

from tbgg_bot import club_xp

BUCKET = "club-xp-test"


def club(position: int, tag: str, xp: int) -> dict[str, Any]:
    """One leaderboard row as the API returns it, trimmed to the fields that matter here."""
    return {"position": position, "xp": xp, "club": {"clubId": f"id-{tag}", "tag": tag}}


LEADERBOARD = {
    "top": [club(1, "SNUP", 472280), club(2, "BTBC", 459920)],
    "aroundUser": [club(23, "TBGG", 311660)],
}


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, str]] = {}

    def put_object(self, **kwargs: Any) -> object:
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = (kwargs["Body"], kwargs["ContentType"])
        return {}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(club_xp.BUCKET_ENV, BUCKET)
    monkeypatch.setenv("GEOGUESSR_NCFA", "test-cookie")
    # Force the local EnvStore path so no test ever reaches for AWS
    monkeypatch.delenv("GEOGUESSR_PARAM_PREFIX", raising=False)


@pytest.fixture
def fetched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stand in for GeoGuessr, recording the cookie each fetch was made with."""
    cookies: list[str] = []

    def fake_fetch(cookie: str) -> dict[str, Any]:
        cookies.append(cookie)
        return LEADERBOARD

    monkeypatch.setattr(club_xp.geoguessr, "fetch_club_leaderboard", fake_fetch)
    return cookies


def test_the_response_is_stored_verbatim_under_its_utc_fetch_time(fetched: list[str]) -> None:
    s3 = FakeS3()
    berlin = dt.timezone(dt.timedelta(hours=1))

    key = club_xp.snapshot(s3, now=dt.datetime(2026, 10, 27, 0, 30, 5, tzinfo=berlin))

    assert key == "club-xp/2026-10-26/233005Z.json", "keys must be UTC, not local time"
    body, content_type = s3.objects[(BUCKET, key)]
    assert json.loads(body) == LEADERBOARD, "our own club's neighbourhood must be kept too"
    assert content_type == "application/json"
    assert fetched == ["test-cookie"]


def test_keys_sort_chronologically_across_days() -> None:
    times = [
        dt.datetime(2026, 10, 26, 23, 0, tzinfo=dt.UTC),
        dt.datetime(2026, 10, 27, 0, 0, tzinfo=dt.UTC),
        dt.datetime(2026, 10, 27, 9, 0, tzinfo=dt.UTC),
        dt.datetime(2026, 10, 27, 10, 0, tzinfo=dt.UTC),
    ]

    keys = [club_xp.snapshot_key(t) for t in times]

    assert keys == sorted(keys)


def test_a_missing_cookie_stores_nothing(
    monkeypatch: pytest.MonkeyPatch, fetched: list[str]
) -> None:
    monkeypatch.delenv("GEOGUESSR_NCFA")
    s3 = FakeS3()

    with pytest.raises(club_xp.geoguessr.SessionExpiredError):
        club_xp.snapshot(s3)

    assert s3.objects == {}
    assert fetched == []


def test_an_empty_leaderboard_is_an_error_not_a_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(club_xp.geoguessr, "fetch_club_leaderboard", lambda _c: {"top": []})
    s3 = FakeS3()

    with pytest.raises(club_xp.geoguessr.GeoGuessrError, match="without a top list"):
        club_xp.snapshot(s3)

    assert s3.objects == {}


def test_a_missing_bucket_fails_before_calling_geoguessr(
    monkeypatch: pytest.MonkeyPatch, fetched: list[str]
) -> None:
    monkeypatch.delenv(club_xp.BUCKET_ENV)

    with pytest.raises(ValueError, match=club_xp.BUCKET_ENV):
        club_xp.snapshot(FakeS3())

    assert fetched == []


def test_the_lambda_reports_the_key_it_wrote(
    monkeypatch: pytest.MonkeyPatch, fetched: list[str]
) -> None:
    s3 = FakeS3()
    monkeypatch.setattr(club_xp, "_s3_client", lambda _client: s3)

    result = club_xp.lambda_handler({"source": "aws.events"})

    assert result["status"] == "ok"
    assert (BUCKET, result["key"]) in s3.objects
