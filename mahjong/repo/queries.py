"""成績集計のための読み出しと、大会まるごとの再計算。

`v_round_entries` を1回叩けば、グループ通算・大会別・開催日別のどれも作れる。
スコープは group_id / tournament_id / day_id / game_id のいずれかで絞る。
"""

from __future__ import annotations

from typing import Any, Sequence

from ..rules import RuleSet
from ..stats import RoundEntry
from ._base import AppError, call, client, group_rounds, results_payload, rows

_SCOPES = ("group_id", "tournament_id", "day_id", "game_id")


def _fetch(scope: str, value: str) -> list[dict[str, Any]]:
    if scope not in _SCOPES:
        raise AppError(f"不正な集計スコープです: {scope}")

    def run():
        return (
            client()
            .table("v_round_entries")
            .select("*")
            .eq(scope, value)
            .order("round_created_at")
            .execute()
        )

    return rows(call(run))


def _entry(row: dict[str, Any]) -> RoundEntry:
    return RoundEntry(
        player_id=row["player_id"],
        rank=row["rank"],
        point=row["point"],
        tobi=bool(row["tobi"]),
        # その半荘の人数。3人卓と4人卓が混ざってもラス率を取り違えない。
        table_size=int(row.get("table_size") or 0),
        kaze=row.get("kaze") or "",
    )


def fetch_entries(scope: str, value: str) -> list[RoundEntry]:
    """集計に渡す全記録を取得する。

    順位は保存済みの `rank` をそのまま使うので、同点の解釈が
    書き込み時と読み出し時でずれることはない。
    """
    return [_entry(row) for row in _fetch(scope, value)]


def fetch_rounds_in_order(scope: str, value: str) -> list[list[RoundEntry]]:
    """半荘を時系列順にまとめて返す（累積推移グラフ用）。"""
    return [
        [_entry(r) for r in rnd["results"]]
        for rnd in group_rounds(_fetch(scope, value))
    ]


def count_rounds(scope: str, value: str) -> int:
    """半荘数。

    旧実装は「延べ人数 ÷ 現在のルール人数」で求めていたため、
    3人卓と4人卓が混ざる大会や、削除済みプレイヤーがいる大会で狂っていた。
    """
    return len({row["round_id"] for row in _fetch(scope, value)})


def fetch_stored_rounds_for_recalc(tournament_id: str) -> list[dict[str, Any]]:
    """ルール変更後の再計算に必要な、持ち点と風を取得する。

    持ち点(raw_score)を保存しているからこそ、ウマや返し点を変えても
    過去データを作り直せる。
    """
    stored = []
    for rnd in group_rounds(_fetch("tournament_id", tournament_id)):
        stored.append(
            {
                "round_id": rnd["id"],
                "seats": [r["seat"] for r in rnd["results"]],
                "raw_scores": [r["raw_score"] for r in rnd["results"]],
                "kazes": [r["kaze"] for r in rnd["results"]],
                "player_ids": [r["player_id"] for r in rnd["results"]],
            }
        )
    return stored


def apply_recalculated_rounds(
    tournament_id: str,
    rules: RuleSet,
    calculated: Sequence[tuple[str, Sequence[Any], dict[int, str]]],
) -> int:
    """再計算した全半荘を1トランザクションで適用する。

    旧実装は半荘ごとに RPC を呼ぶ Python ループで、途中で通信が切れると
    **新旧のルールが混在したまま、どれがどちらか判別できない**状態になった。
    しかも0件成功でも緑の「再計算しました」が出ていた。

    点数計算そのものは `mahjong.scoring` に一本化したまま、
    適用だけをまとめて原子的に行う。

    Args:
        calculated: (round_id, SeatResult のリスト, 席→player_id) のリスト。

    Returns:
        適用した半荘数。
    """
    payload = [
        {"round_id": round_id, "results": results_payload(results, seat_to_player)}
        for round_id, results, seat_to_player in calculated
    ]

    def run():
        return client().rpc(
            "apply_recalculated_rounds",
            {
                "p_tournament_id": tournament_id,
                "p_ruleset": rules.to_dict(),
                "p_rounds": payload,
            },
        ).execute()

    return int(call(run).data or 0)
