"""データアクセス層。

旧 repo.py（542行の平坦なモジュール）を、扱う対象ごとに分割したもの。

    groups      グループ・参加者・招待コード
    tournaments 大会・開催日
    games       対戦（卓）・半荘
    queries     成績集計の読み出し・大会の再計算

呼び出し側は `from mahjong.repo import games` のように名前空間で使う。
どの関数も失敗時は `mahjong.errors.AppError`（またはその派生）を送出し、
そのまま画面に出せる日本語メッセージを持つ。
"""

from __future__ import annotations

from ..errors import AppError, AuthExpired, NetworkError, PermissionDenied
from . import games, groups, queries, tournaments
from ._base import SeatSpec

__all__ = [
    "AppError",
    "AuthExpired",
    "NetworkError",
    "PermissionDenied",
    "SeatSpec",
    "games",
    "groups",
    "queries",
    "tournaments",
]
