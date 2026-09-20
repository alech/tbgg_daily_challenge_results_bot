"""Turn a raw club leaderboard payload into the best-of-rounds team result."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MAX_ROUND_SCORE = 5000


@dataclass(frozen=True)
class Winner:
    """A player who matched the best score on one round."""

    nick: str
    distance_m: float
    time_s: int


@dataclass(frozen=True)
class RoundBest:
    """The best score any club member achieved on a single round."""

    number: int
    country: str
    score: int
    winners: tuple[Winner, ...]


@dataclass(frozen=True)
class Standing:
    """One player's overall placement on the day."""

    nick: str
    score: int
    medal: str | None
    perfect_rounds: int = 0
    """How many rounds this player scored the full 5000 on."""


@dataclass(frozen=True)
class TeamResult:
    """The full computed result for one day's club daily challenge."""

    date: str
    rounds: tuple[RoundBest, ...]
    standings: tuple[Standing, ...]

    @property
    def player_count(self) -> int:
        return len(self.standings)

    @property
    def team_total(self) -> int:
        return sum(r.score for r in self.rounds)

    @property
    def max_total(self) -> int:
        return len(self.rounds) * MAX_ROUND_SCORE

    @property
    def solo_best(self) -> Standing:
        return max(self.standings, key=lambda s: s.score)

    @property
    def gain_over_solo(self) -> int:
        return self.team_total - self.solo_best.score


def playable_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return leaderboard entries that actually contain a played game."""
    return [e for e in payload.get("entries", []) if e.get("game", {}).get("player")]


def _round_countries(entries: list[dict[str, Any]]) -> list[str]:
    """Country codes per round, taken from the entry that played the most rounds."""
    richest = max(entries, key=lambda e: len(e["game"]["rounds"]))
    return [r.get("streakLocationCode") or "??" for r in richest["game"]["rounds"]]


def _perfect_rounds(entry: dict[str, Any]) -> int:
    """How many rounds this player scored the full 5000 on."""
    guesses = entry["game"]["player"]["guesses"]
    return sum(1 for g in guesses if int(g["roundScoreInPoints"]) == MAX_ROUND_SCORE)


def _best_on_round(entries: list[dict[str, Any]], index: int) -> tuple[int, list[Winner]]:
    """The top score on one round and every player who matched it, closest guess first.

    GeoGuessr rounds a round score to a whole number, so several players routinely share
    5000 from visibly different distances. They all earned the round, but ordering them by
    distance puts the tightest guess at the top where the score is printed.
    """
    best_score = -1
    winners: list[Winner] = []
    for entry in entries:
        guesses = entry["game"]["player"]["guesses"]
        if index >= len(guesses):
            continue
        guess = guesses[index]
        score = int(guess["roundScoreInPoints"])
        if score > best_score:
            best_score, winners = score, []
        if score == best_score:
            winners.append(
                Winner(
                    nick=entry["nick"],
                    distance_m=float(guess["distanceInMeters"]),
                    time_s=int(guess["time"]),
                )
            )
    return best_score, sorted(winners, key=lambda w: w.distance_m)


def compute(payload: dict[str, Any], date_str: str) -> TeamResult:
    """Compute the best-of-rounds team result for one day.

    The team score is the sum over rounds of the single highest round score any club
    member got: what the club would have scored if the best player on each round had
    played it alone.
    """
    entries = playable_entries(payload)
    if not entries:
        raise ValueError(f"No club leaderboard entries for {date_str}")

    countries = _round_countries(entries)
    rounds: list[RoundBest] = []
    for index, country in enumerate(countries):
        score, winners = _best_on_round(entries, index)
        if not winners:
            continue
        rounds.append(
            RoundBest(number=index + 1, country=country, score=score, winners=tuple(winners))
        )

    standings = tuple(
        Standing(
            nick=e["nick"],
            score=int(e["totalScore"]),
            medal=e.get("medal"),
            perfect_rounds=_perfect_rounds(e),
        )
        for e in sorted(entries, key=lambda e: int(e["totalScore"]), reverse=True)
    )
    return TeamResult(date=date_str, rounds=tuple(rounds), standings=standings)


def format_distance(meters: float) -> str:
    """Render a guess distance in the most readable unit."""
    return f"{meters:.0f} m" if meters < 1000 else f"{meters / 1000:,.1f} km"


def flag(country_code: str) -> str:
    """Render a two-letter country code as a regional-indicator flag emoji."""
    code = country_code.strip().lower()
    if len(code) != 2 or not code.isascii() or not code.isalpha():
        return "🏳️"
    return "".join(chr(0x1F1E6 + ord(char) - ord("a")) for char in code)
