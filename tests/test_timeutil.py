"""JST変換のテスト。旧実装ではテストが1本も無かった。

Supabase は timestamptz を ISO8601 文字列で返すため、
入力の型・タイムゾーン表記のゆれをすべてここで吸収する必要がある。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mahjong.timeutil import JST, format_jst, format_time, to_jst

UTC = timezone.utc


def test_utc_string_is_converted_to_jst():
    dt = to_jst("2026-04-05T00:30:00+00:00")
    assert dt.year == 2026 and dt.month == 4 and dt.day == 5
    assert dt.hour == 9 and dt.minute == 30
    assert dt.utcoffset() == timedelta(hours=9)


def test_z_suffix_is_accepted():
    """Supabase は 'Z' 付きで返すことがある。"""
    assert to_jst("2026-04-05T00:30:00Z") == to_jst("2026-04-05T00:30:00+00:00")


def test_naive_string_is_treated_as_utc():
    """タイムゾーン情報が無い値はUTC扱い（DB側がUTC保存のため）。"""
    dt = to_jst("2026-04-05T00:00:00")
    assert dt.hour == 9


def test_naive_datetime_is_treated_as_utc():
    dt = to_jst(datetime(2026, 4, 5, 0, 0, 0))
    assert dt.hour == 9


def test_aware_datetime_is_converted():
    dt = to_jst(datetime(2026, 4, 5, 0, 0, 0, tzinfo=UTC))
    assert dt.hour == 9 and dt.tzinfo is not None


def test_already_jst_is_unchanged():
    original = datetime(2026, 4, 5, 21, 0, 0, tzinfo=JST)
    assert to_jst(original) == original


def test_date_rolls_over_at_jst_boundary():
    """UTC 15:00 は JST の翌日 00:00。開催日の切り出しで効いてくる。"""
    dt = to_jst("2026-04-05T15:00:00Z")
    assert (dt.year, dt.month, dt.day, dt.hour) == (2026, 4, 6, 0)


def test_microseconds_are_preserved():
    dt = to_jst("2026-04-05T00:00:00.123456+00:00")
    assert dt.microsecond == 123456


@pytest.mark.parametrize("value", [None, "", "   ", "not a date", "2026-13-45"])
def test_unparsable_values_return_none(value):
    """表示側で握りつぶせるよう、例外ではなく None を返す。"""
    assert to_jst(value) is None


def test_format_jst_default_pattern():
    assert format_jst("2026-04-05T00:30:00Z") == "2026/04/05 09:30"


def test_format_jst_custom_pattern():
    assert format_jst("2026-04-05T00:30:00Z", "%Y-%m-%d") == "2026-04-05"


def test_format_time_shows_only_time():
    assert format_time("2026-04-05T00:30:00Z") == "09:30"


@pytest.mark.parametrize("value", [None, "", "こわれた値"])
def test_formatters_return_empty_string_for_bad_input(value):
    assert format_jst(value) == ""
    assert format_time(value) == ""
