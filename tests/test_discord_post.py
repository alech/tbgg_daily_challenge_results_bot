"""The embed must stay inside Discord's limits and stay readable as the club grows."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, ClassVar

import discord
import pytest

from conftest import entry, guess, payload
from tbgg_bot import discord_post, scoring
from tbgg_bot.discord_post import build_embed

DESCRIPTION_LIMIT = 4096
FIELD_VALUE_LIMIT = 1024
TOTAL_LIMIT = 6000


def _embed_dict(data: dict[str, Any], date_str: str = "2026-09-16") -> dict[str, Any]:
    return build_embed(scoring.compute(data, date_str)).to_dict()


def _discord_length(embed: dict[str, Any]) -> int:
    """Count characters the way Discord does when enforcing the 6000 per-message cap.

    It sums the text of title, description, every field name and value, footer and author —
    not the serialised JSON, whose keys, quoting and \\uXXXX escapes inflate the figure.
    """
    total = len(embed.get("title", "")) + len(embed.get("description", ""))
    total += sum(len(f["name"]) + len(f["value"]) for f in embed.get("fields", []))
    total += len(embed.get("footer", {}).get("text", ""))
    return total + len(embed.get("author", {}).get("name", ""))


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
    assert _discord_length(embed) <= TOTAL_LIMIT


def test_a_large_club_is_split_across_fields_rather_than_truncated() -> None:
    # long nicknames so the leaderboard genuinely exceeds one 1024-character field
    data = payload(*(entry(f"LongNickname{i:02d}", [guess(5000 - i)] * 5) for i in range(40)))
    embed = _embed_dict(data)

    leaderboard = "".join(f["value"] for f in embed["fields"])
    for i in range(40):
        assert f"LongNickname{i:02d}" in leaderboard, "every player must appear somewhere"
    assert len(embed["fields"]) > 1, "this leaderboard cannot fit in one field"
    for field in embed["fields"]:
        assert len(field["value"]) <= FIELD_VALUE_LIMIT
    assert _discord_length(embed) <= TOTAL_LIMIT


def test_a_forty_player_club_with_short_names_still_fits_one_field() -> None:
    data = payload(*(entry(f"P{i:02d}", [guess(5000 - i)] * 5) for i in range(40)))
    embed = _embed_dict(data)

    assert len(embed["fields"]) == 1
    for i in range(40):
        assert f"P{i:02d}" in embed["fields"][0]["value"]


def test_a_realistic_club_all_tying_on_a_round_names_everyone() -> None:
    # 12 players tie on 5000 on round 1 — well within the 4096-char description budget
    data = payload(
        *(entry(f"Player{i:02d}", [guess(5000), guess(4000 - i)] * 1) for i in range(12))
    )
    description = _embed_dict(data)["description"]

    assert "more tied" not in description, "nobody should be summarised away at this size"
    for i in range(12):
        assert f"Player{i:02d}" in description


def test_tied_winners_appear_on_their_own_rows_without_repeating_the_score(
    three_players: dict[str, Any],
) -> None:
    table = _embed_dict(three_players)["description"]
    assert table.count("4,000") == 1
    assert "Alice" in table and "Bob" in table


def test_an_extreme_tie_is_summarised_only_once_discord_forces_it() -> None:
    # 40 players scoring 5000 on all 5 rounds is 200 table rows; that genuinely cannot fit
    data = payload(*(entry(f"Player{i:02d}", [guess(5000)] * 5) for i in range(40)))
    embed = _embed_dict(data)

    assert "more tied" in embed["description"]
    assert len(embed["description"]) <= DESCRIPTION_LIMIT
    assert _discord_length(embed) <= TOTAL_LIMIT


def test_the_table_uses_the_description_budget_rather_than_a_fixed_cap() -> None:
    # 8 players tied on one round fit easily and must all be named
    few = payload(*(entry(f"Nick{i}", [guess(5000)]) for i in range(8)))
    assert "more tied" not in _embed_dict(few)["description"]

    # the same 8 with 40-character names still fit; the cap is length, not headcount
    long_names = payload(*(entry(f"Player{i}" + "x" * 32, [guess(5000)]) for i in range(8)))
    assert "more tied" not in _embed_dict(long_names)["description"]


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


class _FakeHttp:
    """Records sends, refusing the channels named in `reject` the way Discord would."""

    def __init__(self, reject: set[int]) -> None:
        self.reject = reject
        self.sent: list[int] = []

    async def send_message(self, channel_id: int, _content: Any, **_kwargs: Any) -> None:
        if channel_id in self.reject:
            raise discord.Forbidden(
                SimpleNamespace(status=403, reason="Forbidden"), "Missing Access"
            )
        self.sent.append(channel_id)


class _FakeClient:
    """Stands in for discord.Client without touching the network."""

    last: ClassVar[_FakeClient | None] = None
    reject: ClassVar[set[int]] = set()

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.http = _FakeHttp(_FakeClient.reject)
        self.logged_in = False
        self.closed = False
        _FakeClient.last = self

    async def login(self, _token: str) -> None:
        self.logged_in = True

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_discord(monkeypatch: pytest.MonkeyPatch) -> type[_FakeClient]:
    _FakeClient.reject = set()
    _FakeClient.last = None
    monkeypatch.setattr(discord_post.discord, "Client", _FakeClient)
    return _FakeClient


def test_a_channel_refusing_does_not_prevent_the_others(
    fake_discord: type[_FakeClient], three_players: dict[str, Any]
) -> None:
    fake_discord.reject = {43}
    embed = build_embed(scoring.compute(three_players, "2026-09-16"))

    outcome = discord_post.post("token", [42, 43, 44], embed)

    assert outcome.posted == (42, 44)
    assert [cid for cid, _ in outcome.failed] == [43]
    assert "Missing Access" in outcome.failed[0][1]
    assert fake_discord.last is not None
    assert fake_discord.last.http.sent == [42, 44]


def test_the_client_is_closed_even_when_a_channel_refuses(
    fake_discord: type[_FakeClient], three_players: dict[str, Any]
) -> None:
    fake_discord.reject = {42}
    embed = build_embed(scoring.compute(three_players, "2026-09-16"))

    discord_post.post("token", [42], embed)

    assert fake_discord.last is not None
    assert fake_discord.last.closed, "the session must be released on every path"


def test_all_channels_refusing_is_reported_not_raised(
    fake_discord: type[_FakeClient], three_players: dict[str, Any]
) -> None:
    fake_discord.reject = {42, 43}
    embed = build_embed(scoring.compute(three_players, "2026-09-16"))

    outcome = discord_post.post("token", [42, 43], embed)

    assert outcome.posted == ()
    assert outcome.all_failed is True


def test_every_channel_succeeding_reports_no_failures(
    fake_discord: type[_FakeClient], three_players: dict[str, Any]
) -> None:
    embed = build_embed(scoring.compute(three_players, "2026-09-16"))

    outcome = discord_post.post("token", [42, 43], embed)

    assert outcome.posted == (42, 43)
    assert outcome.failed == ()
    assert outcome.all_failed is False
