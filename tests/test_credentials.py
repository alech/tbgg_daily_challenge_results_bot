"""Cookie lookup: SecureString decryption, and the absent-parameter case."""

from __future__ import annotations

from typing import Any

import pytest

from tbgg_bot.credentials import EnvStore, ParameterStore, get_discord_token

PREFIX = "/tbgg-bot/geoguessr"
COOKIE_PARAM = f"{PREFIX}/ncfa"


class ParameterNotFound(Exception):
    """Mirrors the dynamically built boto3 exception of the same name."""


class FakeSsm:
    """A stand-in for the boto3 SSM client that records how it was called."""

    def __init__(self, values: dict[str, str]) -> None:
        self.values = dict(values)
        self.calls: list[dict[str, Any]] = []

    def get_parameter(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        name = kwargs["Name"]
        if name not in self.values:
            raise ParameterNotFound(name)
        return {"Parameter": {"Name": name, "Value": self.values[name]}}


def test_the_cookie_is_read_from_its_parameter() -> None:
    client = FakeSsm({COOKIE_PARAM: "a-real-cookie"})

    assert ParameterStore(PREFIX, client).cookie() == "a-real-cookie"


def test_secure_strings_are_requested_decrypted() -> None:
    client = FakeSsm({COOKIE_PARAM: "a-real-cookie"})
    ParameterStore(PREFIX, client).cookie()
    assert client.calls[0]["WithDecryption"] is True


def test_an_absent_cookie_parameter_reads_as_no_cookie() -> None:
    # Before anyone has stored one, the parameter does not exist at all
    assert ParameterStore(PREFIX, FakeSsm({})).cookie() is None


def test_an_empty_cookie_parameter_reads_as_no_cookie() -> None:
    assert ParameterStore(PREFIX, FakeSsm({COOKIE_PARAM: ""})).cookie() is None


def test_an_unexpected_ssm_error_is_not_swallowed() -> None:
    class Broken:
        def get_parameter(self, **_: Any) -> dict[str, Any]:
            raise PermissionError("AccessDeniedException")

    with pytest.raises(PermissionError):
        ParameterStore(PREFIX, Broken()).cookie()


def test_a_trailing_slash_on_the_prefix_does_not_double_up() -> None:
    client = FakeSsm({COOKIE_PARAM: "c"})
    ParameterStore(f"{PREFIX}/", client).cookie()
    assert client.calls[0]["Name"] == COOKIE_PARAM


def test_the_discord_token_is_read_decrypted() -> None:
    client = FakeSsm({"/tbgg-bot/discord/token": "bot-token"})

    assert get_discord_token("/tbgg-bot/discord/token", client) == "bot-token"
    assert client.calls[0]["WithDecryption"] is True


def test_a_missing_discord_token_names_the_parameter() -> None:
    with pytest.raises(ValueError, match="/tbgg-bot/discord/token"):
        get_discord_token("/tbgg-bot/discord/token", FakeSsm({}))


def test_the_env_store_reads_the_cookie_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEOGUESSR_NCFA", "from-env")
    assert EnvStore().cookie() == "from-env"

    monkeypatch.delenv("GEOGUESSR_NCFA")
    assert EnvStore().cookie() is None
