"""Render the team result as a Discord embed and post it with pycord.

Posting uses pycord's REST client directly (login, send, close) rather than opening a
gateway websocket: a Lambda invocation is too short-lived to justify the connect handshake.
"""

from __future__ import annotations

import asyncio
import logging

import discord

from tbgg_bot.scoring import MAX_ROUND_SCORE, RoundBest, Standing, TeamResult, flag, format_distance

LOGGER = logging.getLogger(__name__)

EMBED_COLOUR = 0x4FAE4F
MEDAL_EMOJI = {"Gold": "🥇", "Silver": "🥈", "Bronze": "🥉"}

MAX_STANDINGS_SHOWN = 25
MAX_WINNERS_PER_ROUND = 3
FENCE_OVERHEAD = len("```\n\n```")
# Discord caps a field value at 1024 characters and the description at 4096.
STANDINGS_BUDGET = 1024 - FENCE_OVERHEAD


def _table(rounds: tuple[RoundBest, ...]) -> str:
    """An aligned monospace table of the best guess on each round.

    An easy round can leave a whole club tied on 5000, so only the first few tied
    winners get a row and the rest are summarised.
    """
    shown = {r.number: r.winners[:MAX_WINNERS_PER_ROUND] for r in rounds}
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


def _fives(standing: Standing) -> str:
    """A player's 5k count, or blank if they had none."""
    return f"{standing.perfect_rounds}x5k" if standing.perfect_rounds else ""


def _render_standings(standings: tuple[Standing, ...], count: int) -> str:
    shown = standings[:count]
    nick_width = max((len(s.nick) for s in shown), default=6)
    # A player with no 5k gets blank space rather than a distracting "0x5k".
    fives_width = max((len(_fives(s)) for s in shown), default=0)
    lines = [
        f"{rank:>2}. {s.nick:<{nick_width}}  {s.score:>6,}  "
        f"{_fives(s):>{fives_width}} {MEDAL_EMOJI.get(s.medal or '', '')}".rstrip()
        for rank, s in enumerate(shown, start=1)
    ]
    if len(standings) > count:
        lines.append(f"    ... and {len(standings) - count} more")
    return "\n".join(lines)


def _standings_block(standings: tuple[Standing, ...]) -> str:
    """An aligned monospace list of the day's totals, trimmed to fit Discord's field limit."""
    start = min(len(standings), MAX_STANDINGS_SHOWN)
    for count in range(start, 1, -1):
        block = _render_standings(standings, count)
        if len(block) <= STANDINGS_BUDGET:
            return block
    return _render_standings(standings, 1)


def build_embed(result: TeamResult) -> discord.Embed:
    """Render the day's team result as a Discord embed."""
    percentage = 100 * result.team_total / result.max_total if result.max_total else 0.0
    route = " ".join(flag(r.country) for r in result.rounds)
    solo = result.solo_best

    summary = (
        f"**{result.team_total:,}** / {result.max_total:,} · {percentage:.2f}%\n"
        f"Best solo **{solo.score:,}** ({solo.nick}) · "
        f"team gain **+{result.gain_over_solo:,}**\n\n"
        f"{route}\n"
        f"```\n{_table(result.rounds)}\n```"
    )

    embed = discord.Embed(
        title=f"🌍 TBGG Daily Challenge — {result.date}",
        description=summary,
        colour=EMBED_COLOUR,
    )
    embed.add_field(
        name=f"Leaderboard ({result.player_count} player{'s' if result.player_count != 1 else ''})",
        value=f"```\n{_standings_block(result.standings)}\n```",
        inline=False,
    )
    perfect = sum(1 for r in result.rounds if r.score == MAX_ROUND_SCORE)
    embed.set_footer(text=f"{perfect}/{len(result.rounds)} rounds maxed by the team")
    return embed


async def _send(token: str, channel_id: int, embed: discord.Embed) -> None:
    client = discord.Client(intents=discord.Intents.none())
    try:
        await client.login(token)
        await client.http.send_message(channel_id, None, embeds=[embed.to_dict()])
        LOGGER.info("Posted result to channel %s", channel_id)
    finally:
        await client.close()


async def _send_dm(token: str, user_id: int, embed: discord.Embed) -> None:
    client = discord.Client(intents=discord.Intents.none())
    try:
        await client.login(token)
        channel = await client.http.start_private_message(user_id)
        await client.http.send_message(int(channel["id"]), None, embeds=[embed.to_dict()])
        LOGGER.info("Sent DM to user %s", user_id)
    finally:
        await client.close()


def post(token: str, channel_id: int, embed: discord.Embed) -> None:
    """Post an embed to a channel using a bot token, without connecting to the gateway."""
    asyncio.run(_send(token, channel_id, embed))


def post_dm(token: str, user_id: int, embed: discord.Embed) -> None:
    """Send an embed to one user's DMs.

    Requires that the bot shares a server with them and that they accept DMs from server
    members. Used for anything the club should not see: failures and cookie expiry.
    """
    asyncio.run(_send_dm(token, user_id, embed))
