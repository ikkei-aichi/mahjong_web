"""対戦（卓）と半荘。

対戦は開催日にぶら下がる。半荘の登録・差し替えは RPC 関数を通す
（1回の関数呼び出しが1トランザクションになるため、
途中で失敗しても半端な記録が残らない）。
"""

from __future__ import annotations

from typing import Any, Sequence

from ._base import (
    AppError,
    SeatSpec,
    call,
    client,
    group_rounds,
    group_seats,
    now_iso,
    results_payload,
    rows,
)

MIN_SEATS = 3
MAX_SEATS = 4


# --- 対戦 -------------------------------------------------------------------


def list_games(day_id: str) -> list[dict[str, Any]]:
    """開催日の卓一覧を、席順・半荘数・各人の合計ポイント付きで1クエリで取得する。"""

    def run():
        return (
            client()
            .table("v_game_seats")
            .select("*")
            .eq("day_id", day_id)
            .order("game_created_at")
            .execute()
        )

    return group_seats(rows(call(run)))


def list_games_in_tournament(tournament_id: str) -> list[dict[str, Any]]:
    def run():
        return (
            client()
            .table("v_game_seats")
            .select("*")
            .eq("tournament_id", tournament_id)
            .order("game_created_at", desc=True)
            .execute()
        )

    return group_seats(rows(call(run)))


def get_game(game_id: str) -> dict[str, Any] | None:
    """対戦1件を席順つきで取得する。

    画面側が session_state に頼らず毎回取得できるようにするための入口。
    リロードやURL直アクセスでも壊れない。
    """

    def run():
        return client().table("v_game_seats").select("*").eq("game_id", game_id).execute()

    found = group_seats(rows(call(run)))
    return found[0] if found else None


def create_game(day_id: str, name: str, seats: Sequence[SeatSpec], player_count: int) -> str:
    """対戦を作成する。新規プレイヤーの作成も含めて不可分に行う。

    Args:
        player_count: 大会のルール人数。ここで照合しないと、4人で作った卓に
            3人用ルールが当たって**スコアを1件も入力できない対戦**ができてしまう
            （旧実装は席のループが range(4) 固定で、この検査も無かった）。
    """
    filled = [s for s in seats if not s.is_empty]
    if len(filled) != player_count:
        raise AppError(
            f"このルールは{player_count}人用です。{len(filled)}人ではなく"
            f"{player_count}人ぶんの席を埋めてください。"
        )
    if not MIN_SEATS <= len(filled) <= MAX_SEATS:
        raise AppError(f"プレイヤーは{MIN_SEATS}〜{MAX_SEATS}人にしてください。")

    names = [(s.new_name or "").strip() for s in filled if not s.player_id]
    if len(set(names)) != len(names):
        raise AppError("同じ名前を2つの席に入力しています。")
    ids = [s.player_id for s in filled if s.player_id]
    if len(set(ids)) != len(ids):
        raise AppError("同じプレイヤーが重複して選択されています。")

    payload = [
        {"player_id": s.player_id} if s.player_id else {"new_name": (s.new_name or "").strip()}
        for s in filled
    ]

    def run():
        return client().rpc(
            "create_game_with_players",
            {"p_day_id": day_id, "p_name": (name or "").strip(), "p_seats": payload},
        ).execute()

    return call(run).data


def rename_game(game_id: str, name: str) -> None:
    name = (name or "").strip()
    if not name:
        raise AppError("卓の名前を入力してください。")

    def run():
        return client().table("games").update({"name": name}).eq("id", game_id).execute()

    call(run)


def delete_game(game_id: str) -> None:
    def run():
        return (
            client()
            .table("games")
            .update({"deleted_at": now_iso()})
            .eq("id", game_id)
            .execute()
        )

    call(run)


# --- 半荘 -------------------------------------------------------------------


def list_rounds(game_id: str) -> list[dict[str, Any]]:
    """対戦の半荘を古い順に返す。表示用の連番 `no` は1から振り直す。"""

    def run():
        return (
            client()
            .table("v_round_entries")
            .select("*")
            .eq("game_id", game_id)
            .order("round_created_at")
            .execute()
        )

    return group_rounds(rows(call(run)))


def add_round(game_id: str, results: Sequence[Any], seat_to_player: dict[int, str]) -> str:
    """半荘1回分を保存する。持ち点・ポイント・順位をまとめて記録する。"""
    payload = results_payload(results, seat_to_player)

    def run():
        return client().rpc(
            "add_round_with_results", {"p_game_id": game_id, "p_results": payload}
        ).execute()

    return call(run).data


def update_round(round_id: str, results: Sequence[Any], seat_to_player: dict[int, str]) -> None:
    """既存の半荘を差し替える。入力ミスの修正に使う。

    差し替えは物理的な上書きで、元の持ち点は残らない。呼び出し側で必ず確認を取ること。
    """
    payload = results_payload(results, seat_to_player)

    def run():
        return client().rpc(
            "update_round_results", {"p_round_id": round_id, "p_results": payload}
        ).execute()

    call(run)


def delete_round(round_id: str) -> None:
    """論理削除。表示上の「回」は取得時に振り直すため番号に穴は空かない。"""

    def run():
        return (
            client()
            .table("game_rounds")
            .update({"deleted_at": now_iso()})
            .eq("id", round_id)
            .execute()
        )

    call(run)
