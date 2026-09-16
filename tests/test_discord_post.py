"""The embed must stay inside Discord's limits and stay readable as the club grows."""

from __future__ import annotations

import json
from typing import Any

from conftest import entry, guess, payload
from tbgg_bot import scoring
from tbgg_bot.discord_post import build_embed

DESCRIPTION_LIMIT = 4096
FIELD_VALUE_LIMIT = 1024
TOTAL_LIMIT = 6000


def _embed_dict(data: dict[str, Any], date_str: str = "2026-09-16") -> dict[str, Any]:
    return build_embed(scoring.compute(data, date_str)).to_dict()


def test_the_embed_reports_the_team_total_and_the_day(three_players: dict[str, Any]) -> None:
    embed = _embed_dict(three_players)
    assert "2026-09-16" in embed["title"]
    assert "21,300" in embed["description"]
    assert "25,000" in embed["description"]


def test_a_full_club_of_long_nicknames_stays_within_discord_limits() -> None:
    data = payload(
        *(entry(f"PlayerWithAVeryLongName{i:02d}", [guess(5000 - i * 7)] * 5) for i in range(60))
    )
    embed = _embed_dict(data)

    assert len(embed["description"]) <= DESCRIPTION_LIMIT
    for field in embed["fields"]:
        assert len(field["value"]) <= FIELD_VALUE_LIMIT
    assert len(json.dumps(embed)) <= TOTAL_LIMIT


def test_a_club_larger_than_the_shown_leaderboard_says_how_many_were_hidden() -> None:
    data = payload(*(entry(f"P{i:02d}", [guess(5000 - i)] * 5) for i in range(40)))
    leaderboard = _embed_dict(data)["fields"][0]["value"]
    assert "and 15 more" in leaderboard


def test_tied_winners_appear_on_their_own_rows_without_repeating_the_score(
    three_players: dict[str, Any],
) -> None:
    table = _embed_dict(three_players)["description"]
    assert table.count("4,000") == 1
    assert "Alice" in table and "Bob" in table


def test_a_round_the_whole_club_tied_on_is_summarised_not_listed_in_full() -> None:
    # 40 players all scoring 5000 on every round would otherwise be 200 table rows
    data = payload(*(entry(f"Player{i:02d}", [guess(5000)] * 5) for i in range(40)))
    embed = _embed_dict(data)

    assert "+37 more tied" in embed["description"]
    assert len(embed["description"]) <= DESCRIPTION_LIMIT
    assert len(json.dumps(embed)) <= TOTAL_LIMIT


def test_the_leaderboard_shows_each_player_s_5k_count() -> None:
    data = payload(
        entry("Sharp", [guess(5000), guess(5000), guess(5000), guess(10), guess(10)]),
        entry("Steady", [guess(4000)] * 5),
    )
    leaderboard = _embed_dict(data)["fields"][0]["value"]

    assert "3x5k" in leaderboard
    assert "0x5k" not in leaderboard, "a player with no 5k should get blank space, not a zero"


def test_the_5k_column_stays_aligned_when_some_players_have_none() -> None:
    data = payload(
        entry("Sharp", [guess(5000)] * 5),
        entry("Steady", [guess(4000)] * 5),
        entry("Solid", [guess(5000), guess(4000), guess(4000), guess(4000), guess(4000)]),
    )
    lines = _embed_dict(data)["fields"][0]["value"].strip("`\n").split("\n")

    # every row puts the medal in the same column, blank 5k count or not
    assert len({line.index("🥇") for line in lines if "🥇" in line}) == 1


def test_the_route_shows_one_flag_per_round(three_players: dict[str, Any]) -> None:
    description = _embed_dict(three_players)["description"]
    assert "🇿🇦 🇹🇷 🇨🇴 🇮🇱 🇨🇾" in description


def test_the_footer_counts_rounds_the_team_maxed(three_players: dict[str, Any]) -> None:
    # only round 1 reached 5000 among the three players
    assert _embed_dict(three_players)["footer"]["text"] == "1/5 rounds maxed by the team"


def test_a_single_player_day_is_not_pluralised() -> None:
    data = payload(entry("Solo", [guess(4000)] * 5))
    assert "1 player)" in _embed_dict(data)["fields"][0]["name"]
