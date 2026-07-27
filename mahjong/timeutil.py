"""日時の表示ユーティリティ。

旧実装はファイルごとに扱いがバラバラだった
（pytz を使う / 手で +9時間する / 変換しない）。ここに集約する。

Supabase は timestamptz を ISO8601 文字列（UTCオフセット付き）で返すため、
パースしてから JST に変換する。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9), "JST")


def to_jst(value: str | datetime | None) -> datetime | None:
    """ISO文字列または datetime を JST の datetime に変換する。

    タイムゾーン情報が無い値は UTC とみなす（DB側が UTC 保存のため）。
    解釈できない値は None を返し、表示側で握りつぶせるようにする。
    """
    if value is None:
        return None

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # Python 3.11 未満の fromisoformat は 'Z' を解釈できない
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(text)
        except ValueError:
            return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(JST)


def format_jst(value: str | datetime | None, fmt: str = "%Y/%m/%d %H:%M") -> str:
    """JST に変換して整形する。変換できない場合は空文字。"""
    dt = to_jst(value)
    return dt.strftime(fmt) if dt else ""


def format_time(value: str | datetime | None) -> str:
    """時刻のみ（表内で使う）。"""
    return format_jst(value, "%H:%M")
