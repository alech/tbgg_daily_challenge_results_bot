"""Challenge weeks, neighbours, the Ladder Watch page and the weekly summary."""

from __future__ import annotations

import datetime as dt
import io
import json
import re
from typing import Any

import pytest

from tbgg_bot import ladder, ladder_page, weekly
from tbgg_bot.club_xp import snapshot_key

BUCKET = "history"
UTC = dt.UTC
# A Wednesday, the day weekly challenges start
WED = dt.datetime(2026, 10, 28, 11, 0, tzinfo=UTC)

TAGS = ["AAAA", "BBBB", "CCCC", "DDDD", "TBGG", "EEEE", "FFFF", "GGGG", "HHHH"]


def payload(xp: dict[str, int], order: list[str] | None = None) -> dict[str, Any]:
    """A leaderboard response ranking the given clubs in order, 18th place upwards."""
    tags = order or TAGS
    rows = [
        {
            "position": 18 + i,
            "xp": xp[tag],
            "club": {"clubId": f"id-{tag}", "tag": tag, "name": tag},
        }
        for i, tag in enumerate(tags)
    ]
    return {"top": rows[:3], "aroundUser": rows[3:]}


def flat(xp: int) -> dict[str, int]:
    return dict.fromkeys(TAGS, xp)


class FakeS3:
    """Stored snapshots behind list_objects_v2 pagination and get_object."""

    def __init__(self, page_size: int = 1000) -> None:
        self.objects: dict[str, bytes] = {}
        self.page_size = page_size
        self.puts: list[dict[str, Any]] = []

    def store(self, at: dt.datetime, body: dict[str, Any]) -> None:
        self.objects[snapshot_key(at)] = json.dumps(body).encode()

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        keys = sorted(k for k in self.objects if k.startswith(kwargs["Prefix"]))
        start = int(kwargs.get("ContinuationToken", 0))
        page = keys[start : start + self.page_size]
        more = start + self.page_size < len(keys)
        result: dict[str, Any] = {"Contents": [{"Key": k} for k in page], "IsTruncated": more}
        if more:
            result["NextContinuationToken"] = str(start + self.page_size)
        return result

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        return {"Body": io.BytesIO(self.objects[kwargs["Key"]])}

    def put_object(self, **kwargs: Any) -> object:
        self.puts.append(kwargs)
        return {}


def hourly(s3: FakeS3, start: dt.datetime, hours: int, xp_at: Any) -> None:
    """Store one snapshot every hour, 30 seconds past, as the real schedule does."""
    for h in range(hours):
        at = start + dt.timedelta(hours=h, seconds=30)
        s3.store(at, payload(xp_at(h)))


# ---------- weeks ----------


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        (WED, WED),
        (WED - dt.timedelta(seconds=1), WED - ladder.WEEK),
        (WED + dt.timedelta(days=6, hours=23), WED),
        (WED + ladder.WEEK, WED + ladder.WEEK),
        # Thursday morning before 11:00 is still the week that started on Wednesday
        (dt.datetime(2026, 10, 29, 9, 0, tzinfo=UTC), WED),
    ],
)
def test_week_starts_are_wednesday_at_eleven_utc(when: dt.datetime, expected: dt.datetime) -> None:
    assert ladder.week_start_at_or_before(when) == expected


def test_the_boundary_snapshot_closes_one_week_and_opens_the_next() -> None:
    s3 = FakeS3()
    hourly(s3, WED, 24 * 7 + 5, lambda h: flat(h))
    snaps = ladder.load(s3, BUCKET, sorted(s3.objects))

    first, second = ladder.weeks(snaps)

    assert first.complete and not second.complete
    assert snaps[first.first].at == WED + dt.timedelta(seconds=30)
    assert first.last == second.first, "one snapshot is both the end and the next start"
    assert snaps[second.last].at == snaps[-1].at


def test_a_week_whose_start_was_missed_is_left_out() -> None:
    s3 = FakeS3()
    # Snapshots only begin on Thursday, a day after the week opened
    hourly(s3, WED + dt.timedelta(days=1), 24 * 7, lambda h: flat(h))
    snaps = ladder.load(s3, BUCKET, sorted(s3.objects))

    weeks = ladder.weeks(snaps)

    assert [w.start for w in weeks] == [WED + ladder.WEEK]


def test_snapshot_keys_follow_pagination_and_the_time_window() -> None:
    s3 = FakeS3(page_size=2)
    hourly(s3, WED - dt.timedelta(hours=3), 10, lambda h: flat(h))

    keys = ladder.snapshot_keys(s3, BUCKET, WED, WED + dt.timedelta(hours=4))

    assert [ladder.key_time(k).hour for k in keys] == [11, 12, 13, 14]


def test_neighbours_are_three_places_either_side_of_tbgg() -> None:
    snap = ladder.parse(WED, payload(flat(1)))

    tags = [c.tag for c in ladder.neighbours(snap)]

    assert tags == ["BBBB", "CCCC", "DDDD", "TBGG", "EEEE", "FFFF", "GGGG"]


def test_a_snapshot_without_tbgg_is_an_error() -> None:
    snap = ladder.parse(WED, payload(flat(1), order=["AAAA", "BBBB"]))

    with pytest.raises(ValueError, match="TBGG"):
        ladder.neighbours(snap)


# ---------- the page ----------


def embedded(html: str) -> dict[str, Any]:
    match = re.search(r"const DATA = (\{.*?\});\n", html)
    assert match, "the page must embed its data"
    data: dict[str, Any] = json.loads(match.group(1).replace("<\\/", "</"))
    return data


def test_the_page_embeds_every_week_and_the_neighbours_series() -> None:
    s3 = FakeS3()
    hourly(s3, WED, 24 * 7 + 3, lambda h: flat(1000 + h))
    now = WED + ladder.WEEK + dt.timedelta(hours=3)

    data = embedded(ladder_page.build(s3, BUCKET, now))

    assert len(data["weeks"]) == 2
    assert data["weeks"][0]["complete"] is True
    assert data["weeks"][0]["i1"] == data["weeks"][1]["i0"]
    tbgg = next(c for c in data["clubs"] if c["focus"])
    assert tbgg["slot"] == 1, "TBGG always takes the first colour"
    assert tbgg["xp"][0] == 1000
    assert len(tbgg["xp"]) == len(data["times"])


def test_a_club_name_cannot_close_the_inline_script() -> None:
    snap = ladder.parse(WED + dt.timedelta(seconds=30), payload(flat(1)))
    rows = {**snap.clubs}
    tbgg = rows["id-TBGG"]
    rows["id-TBGG"] = ladder.Club(tbgg.club_id, tbgg.tag, "</script><b>", tbgg.position, 1)

    html = ladder_page.render(ladder_page.page_data([ladder.Snapshot(snap.at, rows)]))

    assert "</script><b>" not in html


def test_only_the_last_eight_weeks_are_fetched() -> None:
    s3 = FakeS3()
    hourly(s3, WED - 9 * ladder.WEEK, 2, lambda h: flat(h))
    hourly(s3, WED, 2, lambda h: flat(h))

    data = embedded(ladder_page.build(s3, BUCKET, WED + dt.timedelta(hours=2)))

    assert data["times"][0].startswith("2026-10-28")


def test_the_lambda_writes_a_cacheable_page_to_the_site_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s3 = FakeS3()
    now = dt.datetime.now(UTC)
    start = ladder.week_start_at_or_before(now)
    hourly(s3, start, int((now - start).total_seconds() // 3600), lambda h: flat(h))
    monkeypatch.setattr(ladder_page.ladder, "s3_reader", lambda: s3)
    monkeypatch.setenv(ladder_page.HISTORY_BUCKET_ENV, BUCKET)
    monkeypatch.setenv(ladder_page.SITE_BUCKET_ENV, "site")
    monkeypatch.setenv(ladder_page.PAGE_PATH_ENV, "ladder_s3cr3t")

    ladder_page.lambda_handler({})

    pages = {put["Key"]: put for put in s3.puts}
    assert set(pages) == {"ladder_s3cr3t", "index.html"}, "the page lives at its secret path"
    assert all(put["Bucket"] == "site" for put in s3.puts)
    assert b"const DATA = {" in pages["ladder_s3cr3t"]["Body"]
    assert b"ladder_s3cr3t" not in pages["index.html"]["Body"], "the root must not reveal it"
    assert b"Nothing to see here" in pages["index.html"]["Body"]
    for put in s3.puts:
        assert put["ContentType"].startswith("text/html"), "an extensionless key needs its type"
        assert "max-age" in put["CacheControl"]


# ---------- the weekly summary ----------


def summary(end_xp: dict[str, int], end_order: list[str] | None = None) -> weekly.WeekSummary:
    start = ladder.parse(WED, payload(flat(1000)))
    end = ladder.parse(WED + ladder.WEEK, payload(end_xp, end_order))
    return weekly.summarise(start, end, WED)


def gains(**by_tag: int) -> dict[str, int]:
    return {tag: 1000 + by_tag.get(tag, 0) for tag in TAGS}


def test_margins_are_tbgg_gain_minus_each_neighbours() -> None:
    result = summary(gains(TBGG=500, BBBB=300, CCCC=700))

    margins = {r.club.tag: result.margin(r) for r in result.rows}

    assert margins["BBBB"] == 200
    assert margins["CCCC"] == -200
    assert margins["TBGG"] == 0


def test_the_headline_names_the_club_that_out_gained_tbgg() -> None:
    text = weekly.build_embed(summary(gains(TBGG=500, CCCC=700)), None).to_dict()["description"]

    assert "+500 XP" in text
    assert "more than 5 of its 6 neighbours" in text
    assert "second of the seven, behind CCCC (+700)" in text


def test_an_outright_lead_says_so() -> None:
    text = weekly.build_embed(summary(gains(TBGG=900, CCCC=700)), None).to_dict()["description"]

    assert "the most of the seven" in text
    assert "tied" not in text


def test_a_tie_for_the_lead_is_not_an_outright_win() -> None:
    text = weekly.build_embed(summary(gains(TBGG=700, CCCC=700)), None).to_dict()["description"]

    assert "the most of the seven, tied with CCCC" in text
    assert "more than 5 of its 6" in text, "a tied club was not out-gained"


def test_rank_changes_show_in_the_table() -> None:
    # CCCC and DDDD swap places over the week
    order = ["AAAA", "BBBB", "DDDD", "CCCC", "TBGG", "EEEE", "FFFF", "GGGG", "HHHH"]
    text = weekly.build_embed(summary(gains(), order), None).to_dict()["description"]

    assert "#20 ▲1  DDDD" in text
    assert "#21 ▼1  CCCC" in text
    assert "Rank **#22** (unchanged)" in text


def test_the_title_links_to_the_chart_when_there_is_one() -> None:
    linked = weekly.build_embed(summary(gains()), "https://ladder.example/").to_dict()
    plain = weekly.build_embed(summary(gains()), None).to_dict()

    assert linked["url"] == "https://ladder.example/"
    assert "url" not in plain
    assert "links to the chart" not in plain["footer"]["text"]
    assert linked["title"] == "🏁 TBGG challenge week — 28 Oct \u2013 4 Nov"


def test_the_boundary_snapshots_are_the_first_after_each_wednesday() -> None:
    s3 = FakeS3()
    hourly(s3, WED - dt.timedelta(hours=2), 24 * 7 + 5, lambda h: flat(h))

    start, end = weekly.boundary_snapshots(s3, BUCKET, WED)

    assert start.at == WED + dt.timedelta(seconds=30)
    assert end.at == WED + ladder.WEEK + dt.timedelta(seconds=30)


def test_a_missing_boundary_snapshot_is_an_error() -> None:
    s3 = FakeS3()
    hourly(s3, WED, 24, lambda h: flat(h))

    with pytest.raises(ValueError, match="No snapshot"):
        weekly.boundary_snapshots(s3, BUCKET, WED)


def test_the_page_fails_rather_than_guess_a_path(monkeypatch: pytest.MonkeyPatch) -> None:
    s3 = FakeS3()
    now = dt.datetime.now(UTC)
    hourly(s3, ladder.week_start_at_or_before(now), 1, lambda h: flat(h))
    monkeypatch.setattr(ladder_page.ladder, "s3_reader", lambda: s3)
    monkeypatch.setenv(ladder_page.HISTORY_BUCKET_ENV, BUCKET)
    monkeypatch.setenv(ladder_page.SITE_BUCKET_ENV, "site")
    monkeypatch.delenv(ladder_page.PAGE_PATH_ENV, raising=False)

    with pytest.raises(ValueError, match="LADDER_PATH"):
        ladder_page.lambda_handler({})

    assert s3.puts == [], "nothing may be published under a default, guessable path"
