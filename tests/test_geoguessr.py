"""The HTTP error mapping that decides whether the cookie is treated as expired."""

from __future__ import annotations

import io
import urllib.error
from email.message import Message
from typing import Any

import pytest

from tbgg_bot import geoguessr


class FakeResponse:
    """A urlopen context manager returning the given JSON body."""

    def __init__(self, body: bytes = b"{}", set_cookies: tuple[str, ...] = ()) -> None:
        self._body = io.BytesIO(body)
        self.headers = Message()
        for value in set_cookies:
            self.headers["Set-Cookie"] = value

    def read(self, *args: int) -> bytes:
        return self._body.read(*args)

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _raiser(code: int) -> Any:
    def raise_error(*_: Any, **__: Any) -> None:
        raise urllib.error.HTTPError(
            url="https://www.geoguessr.com",
            code=code,
            msg="nope",
            hdrs=Message(),
            fp=io.BytesIO(b"{}"),
        )

    return raise_error


@pytest.mark.parametrize("code", [401, 403])
def test_a_rejected_cookie_raises_the_expired_session_error(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    monkeypatch.setattr(geoguessr.urllib.request, "urlopen", _raiser(code))

    with pytest.raises(geoguessr.SessionExpiredError):
        geoguessr.fetch_leaderboard("2026-09-16", "stale")


@pytest.mark.parametrize("code", [429, 500, 503])
def test_other_http_errors_are_not_treated_as_an_expired_session(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    monkeypatch.setattr(geoguessr.urllib.request, "urlopen", _raiser(code))

    with pytest.raises(geoguessr.GeoGuessrError) as caught:
        geoguessr.fetch_leaderboard("2026-09-16", "fine")
    assert not isinstance(caught.value, geoguessr.SessionExpiredError)


def test_a_working_cookie_reports_the_session_as_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        geoguessr.urllib.request, "urlopen", lambda *a, **k: FakeResponse(b'{"nick":"alech"}')
    )

    assert geoguessr.session_is_valid("good") is True


def test_a_rejected_cookie_reports_the_session_as_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(geoguessr.urllib.request, "urlopen", _raiser(401))

    assert geoguessr.session_is_valid("stale") is False


def test_an_outage_is_not_mistaken_for_an_expired_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    # A 500 must raise rather than return False, or the nightly check would tell the user to
    # refresh a cookie that is perfectly fine.
    monkeypatch.setattr(geoguessr.urllib.request, "urlopen", _raiser(500))

    with pytest.raises(geoguessr.GeoGuessrError):
        geoguessr.session_is_valid("fine")


def test_the_club_leaderboard_is_fetched_with_the_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    def fake_urlopen(request: Any, **_: Any) -> FakeResponse:
        seen.append(request)
        return FakeResponse(b'{"top":[{"position":1,"xp":10}]}')

    monkeypatch.setattr(geoguessr.urllib.request, "urlopen", fake_urlopen)

    assert geoguessr.fetch_club_leaderboard("good") == {"top": [{"position": 1, "xp": 10}]}
    assert seen[0].full_url == geoguessr.CLUB_XP_URL
    assert seen[0].get_header("Cookie") == "_ncfa=good"


def test_a_rejected_cookie_on_the_club_leaderboard_is_an_expired_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(geoguessr.urllib.request, "urlopen", _raiser(401))

    with pytest.raises(geoguessr.SessionExpiredError):
        geoguessr.fetch_club_leaderboard("stale")


def _responding(*set_cookies: str) -> Any:
    return lambda *_a, **_k: FakeResponse(b'{"nick":"alech"}', set_cookies)


def test_the_reissued_cookie_is_read_from_set_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        geoguessr.urllib.request,
        "urlopen",
        _responding(
            "session=abc; expires=Mon, 26 Oct 2026 19:30:40 GMT; path=/; secure; httponly",
            "_ncfa=new%3Dvalue%2Fx; expires=Tue, 26 Oct 2027 19:10:40 GMT; domain=.geoguessr.com;"
            " path=/; secure; httponly",
        ),
    )

    # stored exactly as sent: the request header passes the cookie on still URL-encoded
    assert geoguessr.reissued_cookie("old") == "new%3Dvalue%2Fx"


def test_no_set_cookie_means_no_reissued_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(geoguessr.urllib.request, "urlopen", _responding("session=abc; path=/"))

    assert geoguessr.reissued_cookie("old") is None


def test_a_malformed_neighbouring_cookie_does_not_hide_ours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        geoguessr.urllib.request, "urlopen", _responding('bad"name=1', "_ncfa=fresh; path=/")
    )

    assert geoguessr.reissued_cookie("old") == "fresh"


def test_a_rejected_cookie_is_not_reissued(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(geoguessr.urllib.request, "urlopen", _raiser(401))

    with pytest.raises(geoguessr.SessionExpiredError):
        geoguessr.reissued_cookie("stale")
