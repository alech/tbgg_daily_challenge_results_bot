"""Where the `_ncfa` session cookie is read from.

The cookie is the only credential, and nothing in this project ever writes it: GeoGuessr
signs users in with emailed one-time codes, so refreshing the session is a human pasting a
new cookie in. That keeps the Lambda's SSM access strictly read-only.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol, TypedDict, cast

LOGGER = logging.getLogger(__name__)

COOKIE_KEY = "ncfa"


class _Parameter(TypedDict):
    Name: str
    Value: str


class _GetParameterResult(TypedDict):
    Parameter: _Parameter


class SsmClient(Protocol):
    """The subset of the boto3 SSM client this module uses.

    boto3 is untyped at runtime, so this Protocol is what pins the call shapes. The arguments
    keep boto3's PascalCase names, which is why N803 is disabled for this file.
    """

    def get_parameter(self, *, Name: str, WithDecryption: bool) -> _GetParameterResult: ...


class CookieStore(Protocol):
    """Somewhere the `_ncfa` cookie is read from."""

    def cookie(self) -> str | None:
        """Return the stored cookie, or None if none has been set."""
        ...


def _ssm_client(client: SsmClient | None = None) -> SsmClient:
    """Return the injected client, or a real one. Imported lazily to keep cold starts down."""
    if client is not None:
        return client
    import boto3

    return cast(SsmClient, boto3.client("ssm"))


def _read_parameter(name: str, client: SsmClient | None = None) -> str | None:
    """Read one SecureString parameter, treating absent and empty alike."""
    try:
        value = _ssm_client(client).get_parameter(Name=name, WithDecryption=True)
    # boto3 builds ParameterNotFound dynamically at runtime, so it cannot be imported and
    # caught by type; matching the class name is the practical alternative.
    except Exception as error:
        if type(error).__name__ != "ParameterNotFound":
            raise
        LOGGER.warning("SSM parameter %s does not exist", name)
        return None
    return value["Parameter"]["Value"] or None


class ParameterStore:
    """Reads the cookie from a SecureString parameter such as `/tbgg-bot/geoguessr/ncfa`."""

    def __init__(self, prefix: str, client: SsmClient | None = None) -> None:
        self._name = f"{prefix.rstrip('/')}/{COOKIE_KEY}"
        self._client = client

    def cookie(self) -> str | None:
        return _read_parameter(self._name, self._client)


class EnvStore:
    """Reads the cookie from GEOGUESSR_NCFA; used for local runs."""

    def cookie(self) -> str | None:
        return os.environ.get("GEOGUESSR_NCFA") or None


def get_discord_token(parameter_name: str, client: SsmClient | None = None) -> str:
    """Read the Discord bot token from its SecureString parameter."""
    token = _read_parameter(parameter_name, client)
    if not token:
        raise ValueError(f"SSM parameter {parameter_name} is missing or empty")
    return token
