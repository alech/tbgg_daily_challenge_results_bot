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

    def __init__(self, body: bytes = b"{}") -> None:
        self._body = io.BytesIO(body)

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
