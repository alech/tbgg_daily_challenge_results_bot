"""The weekly summary: how TBGG's XP gain over a finished challenge week compares.

Posted to the club just after the week closes on Wednesday at 11:00 UTC. Compares TBGG with
the clubs within three places of it at the week's end, from the two boundary snapshots.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import discord

from tbgg_bot import ladder

EMBED_COLOUR = 0x2A78D6


@dataclass(frozen=True)
class Gain:
    club: ladder.Club
    start_position: int | None
    gained: int | None


@dataclass(frozen=True)
class WeekSummary:
    start: dt.datetime
    rows: tuple[Gain, ...]

    @property
    def end(self) -> dt.datetime:
        return self.start + ladder.WEEK

    @property
    def focus(self) -> Gain:
        return next(r for r in self.rows if r.club.tag == ladder.FOCUS_TAG)

    def margin(self, row: Gain) -> int | None:
        """TBGG's gain minus this club's; positive when TBGG out-gained it."""
        mine = self.focus.gained
        return None if mine is None or row.gained is None else mine - row.gained


def summarise(start: ladder.Snapshot, end: ladder.Snapshot, week_start: dt.datetime) -> WeekSummary:
    """Compare every neighbour's gain between the week's two boundary snapshots."""
    rows = []
    for club in ladder.neighbours(end):
        before = start.clubs.get(club.club_id)
        rows.append(
            Gain(
                club=club,
                start_position=before.position if before else None,
                gained=club.xp - before.xp if before else None,
            )
        )
    return WeekSummary(week_start, tuple(rows))


def boundary_snapshots(
    client: ladder.S3Reader, bucket: str, week_start: dt.datetime
) -> tuple[ladder.Snapshot, ladder.Snapshot]:
    """The first snapshots at or after the week's start and its end."""
    found = []
    for boundary in (week_start, week_start + ladder.WEEK):
        keys = ladder.snapshot_keys(client, bucket, boundary, boundary + ladder.START_TOLERANCE)
        if not keys:
            raise ValueError(f"No snapshot within two hours after {boundary.isoformat()}")
        found.append(keys[0])
    start, end = ladder.load(client, bucket, found)
    return start, end


def _signed(value: int | None) -> str:
    if value is None:
        return "—"
    return f"+{value:,}" if value > 0 else f"-{abs(value):,}" if value < 0 else "±0"


def _rank_change(row: Gain) -> str:
    if row.start_position is None or row.start_position == row.club.position:
        return ""
    moved = row.start_position - row.club.position
    return f"▲{moved}" if moved > 0 else f"▼{-moved}"


def _table(summary: WeekSummary) -> str:
    lines = [f"{'Rank':<7} {'Club':<5} {'Gained':>7}  {'TBGG ±':>7}"]
    for row in summary.rows:
        rank = f"#{row.club.position} {_rank_change(row)}".rstrip()
        margin = "" if row is summary.focus else _signed(summary.margin(row))
        lines.append(f"{rank:<7} {row.club.tag:<5} {_signed(row.gained):>7}  {margin:>7}".rstrip())
    return "\n".join(lines)


def _ordinal(place: int) -> str:
    return {2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth"}.get(place, "last")


def _headline(summary: WeekSummary) -> str:
    focus = summary.focus
    others = [r for r in summary.rows if r is not focus and r.gained is not None]
    beaten = sum(1 for r in others if (summary.margin(r) or 0) > 0)
    ahead = sorted(
        (r for r in others if (summary.margin(r) or 0) < 0), key=lambda r: -(r.gained or 0)
    )
    place = len(ahead) + 1
    tied = [r.club.tag for r in others if summary.margin(r) == 0]
    line = (
        f"TBGG gained **{_signed(focus.gained)} XP**, more than {beaten} of its "
        f"{len(others)} neighbours"
    )
    if place == 1:
        line += " — the most of the seven" + (f", tied with {', '.join(tied)}" if tied else "")
    else:
        leader = ahead[0]
        line += (
            f" — {_ordinal(place)} of the seven, behind {leader.club.tag} "
            f"({_signed(leader.gained)})"
        )
    return line + "."


def _rank_line(summary: WeekSummary) -> str:
    focus = summary.focus
    moved = _rank_change(focus)
    status = f"{moved} from #{focus.start_position}" if moved else "unchanged"
    above = next((r for r in summary.rows if r.club.position == focus.club.position - 1), None)
    gap = f" · {above.club.xp - focus.club.xp:,} XP behind {above.club.tag}" if above else ""
    return f"Rank **#{focus.club.position}** ({status}){gap}."


def build_embed(summary: WeekSummary, page_url: str | None) -> discord.Embed:
    # An en dash is the correct mark for a date range in the club's own channel
    span = f"{summary.start.day} {summary.start:%b} – {summary.end.day} {summary.end:%b}"  # noqa: RUF001
    embed = discord.Embed(
        title=f"🏁 TBGG challenge week — {span}",
        description=f"{_headline(summary)}\n{_rank_line(summary)}\n```\n{_table(summary)}\n```",
        colour=EMBED_COLOUR,
        url=page_url,
    )
    footer = "Weeks run Wednesday 11:00 UTC to Wednesday 11:00 UTC"
    embed.set_footer(text=f"{footer} · the title links to the chart" if page_url else footer)
    return embed
