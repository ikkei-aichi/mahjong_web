"""データアクセス層の共通部品。

読み取りはビュー（v_round_entries / v_game_seats / v_my_groups）を1回叩いて
Python 側で組み立てる。複数テーブルにまたがる書き込みは RPC 関数を呼ぶ
（PostgREST には複数文トランザクションが無いため）。

RLS が有効なので、所属していないグループのデータは
「エラー」ではなく「0件」として返ってくる。件数0を権限エラーと取り違えないこと。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from ..db import get_client
from ..errors import AppError, call

__all__ = [
    "AppError",
    "SeatSpec",
    "call",
    "client",
    "group_rounds",
    "group_seats",
    "now_iso",
    "results_payload",
    "rows",
    "single",
]


def client():
    return get_client()


def now_iso() -> str:
    """論理削除用のタイムスタンプ。REST 経由では now() を呼べないため Python 側で作る。"""
    return datetime.now(timezone.utc).isoformat()


def rows(response: Any) -> list[dict[str, Any]]:
    return response.data or []


def single(response: Any) -> dict[str, Any] | None:
    data = response.data or []
    return data[0] if data else None


@dataclass(frozen=True)
class SeatSpec:
    """対戦作成時の1席分の指定。既存プレイヤーか新規作成かのどちらか。"""

    player_id: str | None = None
    new_name: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.player_id and not (self.new_name or "").strip()


def group_seats(source: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """v_game_seats の行を対戦ごとにまとめる。"""
    games: dict[str, dict[str, Any]] = {}
    for row in source:
        game = games.setdefault(
            row["game_id"],
            {
                "id": row["game_id"],
                "name": row["game_name"],
                "created_at": row["game_created_at"],
                "group_id": row["group_id"],
                "tournament_id": row["tournament_id"],
                "day_id": row["day_id"],
                "held_on": row.get("held_on"),
                "round_count": row.get("round_count", 0),
                "seats": [],
            },
        )
        game["seats"].append(
            {
                "seat": row["seat"],
                "player_id": row["player_id"],
                "player_name": row["player_name"],
                "user_id": row.get("user_id"),
                "total_point": row.get("total_point", 0),
            }
        )
    for game in games.values():
        game["seats"].sort(key=lambda s: s["seat"])
    return list(games.values())


def group_rounds(source: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """v_round_entries の行を半荘ごとにまとめ、表示用の連番を振り直す。

    途中の回を削除しても番号に穴が空かない。
    """
    rounds: dict[str, dict[str, Any]] = {}
    for row in source:
        rnd = rounds.setdefault(
            row["round_id"],
            {
                "id": row["round_id"],
                "game_id": row["game_id"],
                "game_name": row.get("game_name"),
                "created_at": row["round_created_at"],
                "ruleset": row.get("round_ruleset"),
                "results": [],
            },
        )
        rnd["results"].append(
            {
                "player_id": row["player_id"],
                "player_name": row.get("player_name"),
                "seat": row["seat"],
                "raw_score": row["raw_score"],
                "point": row["point"],
                "rank": row["rank"],
                "kaze": row["kaze"],
                "tobi": row["tobi"],
            }
        )

    ordered = sorted(rounds.values(), key=lambda r: (r["created_at"], r["id"]))
    for no, rnd in enumerate(ordered, start=1):
        rnd["no"] = no
        rnd["results"].sort(key=lambda x: x["seat"])
    return ordered


def results_payload(
    results: Sequence[Any], seat_to_player: dict[int, str]
) -> list[dict[str, Any]]:
    """SeatResult のリストを RPC に渡す JSON へ変換する。"""
    payload = []
    for r in results:
        player_id = seat_to_player.get(r.seat)
        if player_id is None:
            raise AppError(f"席{r.seat + 1}に対応するプレイヤーが見つかりません。")
        payload.append(
            {
                "player_id": player_id,
                "seat": r.seat,
                "raw_score": r.raw_score,
                "point": r.point,
                "rank": r.rank,
                "kaze": r.kaze,
                "tobi": r.tobi,
            }
        )
    return payload
