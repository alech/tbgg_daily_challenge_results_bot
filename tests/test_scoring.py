"""Behavioural tests for the best-of-rounds team scoring."""

from __future__ import annotations

from typing import Any

import pytest

from conftest import entry, guess, payload
from tbgg_bot import scoring


def test_team_total_sums_the_best_score_on_each_round(three_players: dict[str, Any]) -> None:
    result = scoring.compute(three_players, "2026-09-16")
    # round bests: 5000 (Alice), 4000 (tie), 4900 (Cara), 2500 (Alice), 4900 (Bob)
    assert [r.score for r in result.rounds] == [5000, 4000, 4900, 2500, 4900]
    assert result.team_total == 21300
    assert result.max_total == 25000


def test_every_player_tied_for_a_round_is_listed(three_players: dict[str, Any]) -> None:
    result = scoring.compute(three_players, "2026-09-16")
    tied = result.rounds[1]
    assert tied.score == 4000
    assert sorted(w.nick for w in tied.winners) == ["Alice", "Bob"]
    # the tie is broken by nothing: both rows keep their own distance and time
    assert {w.distance_m for w in tied.winners} == {120.0, 95.0}


def test_a_player_who_played_fewer_rounds_only_counts_where_they_guessed(
    three_players: dict[str, Any],
) -> None:
    result = scoring.compute(three_players, "2026-09-16")
    assert [w.nick for w in result.rounds[2].winners] == ["Cara"]
    # Cara played 3 rounds, so she cannot win rounds 4 or 5
    for round_best in result.rounds[3:]:
        assert "Cara" not in [w.nick for w in round_best.winners]


def test_round_countries_come_from_the_player_who_played_the_most_rounds() -> None:
    # Cara (3 rounds) is last, so a naive implementation would read countries from her game
    data = payload(
        entry("Alice", [guess(5000)] * 5),
        entry("Cara", [guess(1000)] * 3, rounds=3),
    )
    result = scoring.compute(data, "2026-09-16")
    assert len(result.rounds) == 5
    assert [r.country for r in result.rounds] == ["za", "tr", "co", "il", "cy"]


def test_gain_over_solo_is_team_minus_the_best_single_player(
    three_players: dict[str, Any],
) -> None:
    result = scoring.compute(three_players, "2026-09-16")
    assert result.solo_best.nick == "Bob"
    assert result.solo_best.score == 20200
    assert result.gain_over_solo == result.team_total - 20200


def test_standings_are_ranked_by_total_score(three_players: dict[str, Any]) -> None:
    result = scoring.compute(three_players, "2026-09-16")
    scores = [s.score for s in result.standings]
    assert scores == sorted(scores, reverse=True)
    assert result.player_count == 3


def test_each_standing_counts_that_player_s_5ks() -> None:
    data = payload(
        entry("Perfect", [guess(5000)] * 5),
        entry("Mixed", [guess(5000), guess(4999), guess(5000), guess(0), guess(1)]),
        entry("None", [guess(4999)] * 5),
    )
    result = scoring.compute(data, "2026-09-16")

    by_nick = {s.nick: s.perfect_rounds for s in result.standings}
    assert by_nick == {"Perfect": 5, "Mixed": 2, "None": 0}


def test_a_player_who_stopped_early_only_counts_the_rounds_they_played() -> None:
    data = payload(entry("Quitter", [guess(5000), guess(5000)], rounds=5))
    (standing,) = scoring.compute(data, "2026-09-16").standings
    assert standing.perfect_rounds == 2


def test_entries_without_a_played_game_are_ignored() -> None:
    data = payload(entry("Alice", [guess(5000)] * 5), {"nick": "Ghost", "game": {}})
    result = scoring.compute(data, "2026-09-16")
    assert result.player_count == 1


def test_a_day_with_no_entries_is_an_error() -> None:
    with pytest.raises(ValueError, match="No club leaderboard entries"):
        scoring.compute(payload(), "2026-09-16")


def test_a_zero_score_round_still_counts_as_the_best_if_nobody_did_better() -> None:
    data = payload(entry("Alice", [guess(0)], rounds=1))
    result = scoring.compute(data, "2026-09-16")
    assert result.rounds[0].score == 0
    assert result.team_total == 0


@pytest.mark.parametrize(
    ("meters", "expected"),
    [(0.4, "0 m"), (2.4, "2 m"), (999.4, "999 m"), (1000.0, "1.0 km"), (1_234_567.0, "1,234.6 km")],
)
def test_distances_render_in_the_most_readable_unit(meters: float, expected: str) -> None:
    assert scoring.format_distance(meters) == expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [("za", "🇿🇦"), ("TR", "🇹🇷"), ("??", "🏳️"), ("", "🏳️"), ("usa", "🏳️"), ("1a", "🏳️")],
)
def test_country_codes_render_as_flags(code: str, expected: str) -> None:
    assert scoring.flag(code) == expected
