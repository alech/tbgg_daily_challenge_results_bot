"""Render the team result as a Discord embed and post it with pycord.

Posting uses pycord's REST client directly (login, send, close) rather than opening a
gateway websocket: a Lambda invocation is too short-lived to justify the connect handshake.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass

import discord

from tbgg_bot.scoring import MAX_ROUND_SCORE, RoundBest, Standing, TeamResult, flag, format_distance

LOGGER = logging.getLogger(__name__)

EMBED_COLOUR = 0x4FAE4F
MEDAL_EMOJI = {"Gold": "🥇", "Silver": "🥈", "Bronze": "🥉"}

# Discord's own limits. Everyone who scored should be named, so these drive the layout
# instead of arbitrary caps: omitting a player who tied for a round reads as a slight.
DESCRIPTION_LIMIT = 4096
FIELD_VALUE_LIMIT = 1024
MAX_FIELDS = 25
# Discord also caps the *sum* of title, description, field names and values, and footer
# across the whole message, so the table and the leaderboard compete for one pool.
TOTAL_LIMIT = 6000
# Kept back from the leaderboard so the round table always has room for a usable form.
MIN_TABLE_BUDGET = 500
FENCE_OVERHEAD = len("```\n\n```")


def _table(rounds: tuple[RoundBest, ...], cap: int) -> str:
    """An aligned monospace table of the best guess on each round, naming up to `cap` ties."""
    shown = {r.number: r.winners[:cap] for r in rounds}
    nick_width = max((len(w.nick) for winners in shown.values() for w in winners), default=6)
    lines = [f"{'R':<2} {'Cty':<4} {'Score':>5}  {'Distance':>9}  {'Time':>5}  Player"]
    for info in rounds:
        for position, winner in enumerate(shown[info.number]):
            first = position == 0
            lines.append(
                f"{str(info.number) if first else '':<2} "
                f"{info.country.upper() if first else '':<4} "
                f"{f'{info.score:,}' if first else '':>5}  "
                f"{format_distance(winner.distance_m):>9}  {winner.time_s:>4}s  "
                f"{winner.nick:<{nick_width}}".rstrip()
            )
        hidden = len(info.winners) - len(shown[info.number])
        if hidden:
            lines.append(f"{'':<2} {'':<4} {'':>5}  {'':>9}  {'':>5}  +{hidden} more tied")
    return "\n".join(lines)


def _fit_table(rounds: tuple[RoundBest, ...], budget: int) -> str:
    """Name as many tied winners as the description budget allows.

    Ties are normal on an easy round and every tied player earned their mention, so the
    only thing that trims the list is Discord's 4096-character description limit.
    """
    widest = max((len(r.winners) for r in rounds), default=1)
    for cap in range(widest, 1, -1):
        table = _table(rounds, cap)
        if len(table) <= budget:
            return table
    return _table(rounds, 1)


def _fives(standing: Standing) -> str:
    """A player's 5k count, or blank if they had none."""
    return f"{standing.perfect_rounds}x5k" if standing.perfect_rounds else ""


def _standings_lines(standings: tuple[Standing, ...]) -> list[str]:
    """One aligned row per player, with widths shared across the whole leaderboard."""
    rank_width = len(str(len(standings)))
    nick_width = max((len(s.nick) for s in standings), default=6)
    # A player with no 5k gets blank space rather than a distracting "0x5k".
    fives_width = max((len(_fives(s)) for s in standings), default=0)
    return [
        f"{rank:>{rank_width}}. {s.nick:<{nick_width}}  {s.score:>6,}  "
        f"{_fives(s):>{fives_width}} {MEDAL_EMOJI.get(s.medal or '', '')}".rstrip()
        for rank, s in enumerate(standings, start=1)
    ]


def _standings_blocks(standings: tuple[Standing, ...], total_budget: int) -> list[str]:
    """Split the leaderboard over as many fields as it takes to name every player.

    A field value caps at 1024 characters but an embed allows 25 fields, so the binding
    constraint is the shared message budget rather than the per-field one.
    """
    per_field = FIELD_VALUE_LIMIT - FENCE_OVERHEAD
    blocks: list[str] = []
    current: list[str] = []
    spent = 0

    for line in _standings_lines(standings):
        candidate = [*current, line]
        if current and len("\n".join(candidate)) > per_field:
            blocks.append("\n".join(current))
            spent += len(blocks[-1]) + FENCE_OVERHEAD
            current = [line]
        else:
            current = candidate
        if len(blocks) >= MAX_FIELDS or spent + len("\n".join(current)) > total_budget:
            break

    if current and len(blocks) < MAX_FIELDS:
        block = "\n".join(current)
        if spent + len(block) + FENCE_OVERHEAD <= total_budget:
            blocks.append(block)
    return blocks


def build_embed(result: TeamResult) -> discord.Embed:
    """Render the day's team result as a Discord embed."""
    percentage = 100 * result.team_total / result.max_total if result.max_total else 0.0
    route = " ".join(flag(r.country) for r in result.rounds)
    solo = result.solo_best

    header = (
        f"**{result.team_total:,}** / {result.max_total:,} · {percentage:.2f}%\n"
        f"Best solo **{solo.score:,}** ({solo.nick}) · "
        f"team gain **+{result.gain_over_solo:,}**\n\n"
        f"{route}\n"
    )
    title = f"🌍 TBGG Daily Challenge — {result.date}"
    perfect = sum(1 for r in result.rounds if r.score == MAX_ROUND_SCORE)
    footer = f"{perfect}/{len(result.rounds)} rounds maxed by the team"
    plural = "s" if result.player_count != 1 else ""
    first_name = f"Leaderboard ({result.player_count} player{plural})"

    # The whole message shares one 6000-character pool. Spend it on the leaderboard first,
    # holding back enough for the table, then give the table whatever is left over.
    fixed = len(title) + len(footer) + len(header) + FENCE_OVERHEAD + len(first_name)
    blocks = _standings_blocks(result.standings, TOTAL_LIMIT - fixed - MIN_TABLE_BUDGET)
    spent = sum(len(b) + FENCE_OVERHEAD for b in blocks)
    table = _fit_table(
        result.rounds,
        min(DESCRIPTION_LIMIT - len(header) - FENCE_OVERHEAD, TOTAL_LIMIT - fixed - spent),
    )

    embed = discord.Embed(
        title=title, description=f"{header}```\n{table}\n```", colour=EMBED_COLOUR
    )
    for index, block in enumerate(blocks):
        embed.add_field(
            # A zero-width space: a continued block needs no second heading.
            name=first_name if index == 0 else "​",
            value=f"```\n{block}\n```",
            inline=False,
        )
    embed.set_footer(text=footer)
    return embed


@dataclass(frozen=True)
class PostOutcome:
    """Which channels took the post and which refused it."""

    posted: tuple[int, ...]
    failed: tuple[tuple[int, str], ...]

    @property
    def all_failed(self) -> bool:
        return not self.posted and bool(self.failed)


async def _send(token: str, channel_ids: Sequence[int], embed: discord.Embed) -> PostOutcome:
    """Post to each channel in turn, on one login, isolating per-channel failures."""
    client = discord.Client(intents=discord.Intents.none())
    posted: list[int] = []
    failed: list[tuple[int, str]] = []
    try:
        await client.login(token)
        payload = embed.to_dict()
        for channel_id in channel_ids:
            try:
                await client.http.send_message(channel_id, None, embeds=[payload])
            except discord.HTTPException as error:
                # Missing Access, Missing Permissions, unknown channel: one channel being
                # misconfigured must never cost the others their post.
                LOGGER.warning("Could not post to channel %s: %s", channel_id, error)
                failed.append((channel_id, f"{type(error).__name__}: {error}"))
            else:
                LOGGER.info("Posted result to channel %s", channel_id)
                posted.append(channel_id)
    finally:
        await client.close()
    return PostOutcome(posted=tuple(posted), failed=tuple(failed))


async def _send_dm(token: str, user_id: int, embed: discord.Embed) -> None:
    client = discord.Client(intents=discord.Intents.none())
    try:
        await client.login(token)
        channel = await client.http.start_private_message(user_id)
        await client.http.send_message(int(channel["id"]), None, embeds=[embed.to_dict()])
        LOGGER.info("Sent DM to user %s", user_id)
    finally:
        await client.close()


def post(token: str, channel_ids: Sequence[int], embed: discord.Embed) -> PostOutcome:
    """Post an embed to every channel, without connecting to the gateway.

    Never raises for a channel that rejects the post; the caller decides what a partial
    delivery means. A channel the bot has not been granted access to yet is the expected
    case, not an error worth losing the other channels over.
    """
    return asyncio.run(_send(token, channel_ids, embed))


def post_dm(token: str, user_id: int, embed: discord.Embed) -> None:
    """Send an embed to one user's DMs.

    Requires that the bot shares a server with them and that they accept DMs from server
    members. Used for anything the club should not see: failures and cookie expiry.
    """
    asyncio.run(_send_dm(token, user_id, embed))
