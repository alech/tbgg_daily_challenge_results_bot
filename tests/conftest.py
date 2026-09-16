"""Builders for synthetic club leaderboard payloads shaped like the real GeoGuessr response."""

from __future__ import annotations

from typing import Any

import pytest

COUNTRIES = ["za", "tr", "co", "il", "cy"]


def guess(score: int, distance_m: float = 10.0, time_s: int = 30) -> dict[str, Any]:
    """One round guess as the API returns it."""
    return {
        "roundScoreInPoints": score,
        "distanceInMeters": distance_m,
        "time": time_s,
        "timedOut": score == 0,
    }


def entry(
    nick: str,
    guesses: list[dict[str, Any]],
    medal: str | None = "Gold",
    rounds: int = 5,
) -> dict[str, Any]:
    """One leaderboard entry with its embedded game."""
    return {
        "nick": nick,
        "medal": medal,
        "totalScore": sum(g["roundScoreInPoints"] for g in guesses),
        "game": {
            "rounds": [{"streakLocationCode": COUNTRIES[i]} for i in range(rounds)],
            "player": {"nick": nick, "guesses": guesses},
        },
    }


def payload(*entries: dict[str, Any]) -> dict[str, Any]:
    """A full leaderboard response wrapping the given entries."""
    return {"totalEntries": len(entries), "entries": list(entries)}


@pytest.fixture
def three_players() -> dict[str, Any]:
    """Three players: a tie on round 2, and one who only played three rounds."""
    return payload(
        entry(
            "Alice", [guess(5000), guess(4000, 120.0, 25), guess(3000), guess(2500), guess(1000)]
        ),
        entry("Bob", [guess(4500), guess(4000, 95.0, 40), guess(4800), guess(2000), guess(4900)]),
        entry("Cara", [guess(1000), guess(1500), guess(4900, 5.0, 12)], medal="Bronze", rounds=3),
    )
