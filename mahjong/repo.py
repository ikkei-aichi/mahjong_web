"""データアクセス層（Supabase REST API 経由）。旧 sqlite_db.py の置き換え。

読み取りはビュー（v_round_entries / v_game_seats）を1回叩いて Python 側で
組み立てる。書き込みのうち複数テーブルにまたがるものは RPC 関数を呼ぶ
（PostgREST には複数文トランザクションが無いため）。

RLS が有効なので、ログインしていない状態では空の結果か権限エラーになる。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from postgrest.exceptions import APIError

from .db import get_client
from .rules import DEFAULT_RULESET, RuleSet
from .scoring import SeatResult
from .stats import RoundEntry


class RepoError(RuntimeError):
    """呼び出し側にそのまま見せられるエラーメッセージを持つ例外。"""


@dataclass(frozen=True)
class SeatSpec:
    """対戦作成時の1席分の指定。既存プレイヤーか新規作成かのどちらか。"""

    player_id: str | None = None
    new_name: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.player_id and not (self.new_name or "").strip()


def _now() -> str:
    """論理削除用のタイムスタンプ。REST 経由では now() を呼べないため Python 側で作る。"""
    return datetime.now(timezone.utc).isoformat()


def _unwrap(exc: APIError) -> str:
    """Postgres の RAISE EXCEPTION メッセージを取り出して読める形にする。"""
    message = getattr(exc, "message", None) or str(exc)
    details = getattr(exc, "details", None)
    hint = getattr(exc, "hint", None)
    return " / ".join(str(x) for x in (message, details, hint) if x)


def _call(fn, *args, **kwargs):
    """PostgREST 呼び出しをラップし、失敗を RepoError に変換する。"""
    try:
        return fn(*args, **kwargs)
    except APIError as exc:
        raise RepoError(_unwrap(exc)) from exc


# --- タイトル ---------------------------------------------------------------


def list_titles() -> list[dict[str, Any]]:
    def run():
        return (
            get_client()
            .table("titles")
            .select("id, name, ruleset, owner_id, created_at")
            .is_("deleted_at", "null")
            .order("created_at", desc=True)
            .execute()
        )

    return _call(run).data or []


def get_title(title_id: str) -> dict[str, Any] | None:
    def run():
        return (
            get_client()
            .table("titles")
            .select("id, name, ruleset, owner_id, created_at")
            .eq("id", title_id)
            .is_("deleted_at", "null")
            .limit(1)
            .execute()
        )

    rows = _call(run).data or []
    return rows[0] if rows else None


def get_ruleset(title_id: str) -> RuleSet:
    """タイトルのルールを返す。未設定なら既定値。"""
    title = get_title(title_id)
    if not title:
        raise RepoError("タイトルが見つかりません。")
    return RuleSet.from_dict(title.get("ruleset")) if title.get("ruleset") else DEFAULT_RULESET


def create_title(
    name: str, rules: RuleSet | None = None, owner_id: str | None = None
) -> str:
    name = (name or "").strip()
    if not name:
        raise RepoError("タイトル名を入力してください。")

    payload = {
        "name": name,
        "ruleset": (rules or DEFAULT_RULESET).to_dict(),
        "owner_id": owner_id,
    }

    def run():
        return get_client().table("titles").insert(payload).execute()

    rows = _call(run).data or []
    if not rows:
        raise RepoError("タイトルを作成できませんでした。")
    return rows[0]["id"]


def rename_title(title_id: str, name: str) -> None:
    name = (name or "").strip()
    if not name:
        raise RepoError("タイトル名を入力してください。")

    def run():
        return get_client().table("titles").update({"name": name}).eq("id", title_id).execute()

    _call(run)


def update_ruleset(title_id: str, rules: RuleSet) -> None:
    def run():
        return (
            get_client()
            .table("titles")
            .update({"ruleset": rules.to_dict()})
            .eq("id", title_id)
            .execute()
        )

    _call(run)


def delete_title(title_id: str) -> None:
    """論理削除。一覧から消えるだけで、配下のデータは残る。"""

    def run():
        return (
            get_client()
            .table("titles")
            .update({"deleted_at": _now()})
            .eq("id", title_id)
            .execute()
        )

    _call(run)


# --- プレイヤー -------------------------------------------------------------


def list_players(title_id: str) -> list[dict[str, Any]]:
    def run():
        return (
            get_client()
            .table("players")
            .select("id, name")
            .eq("title_id", title_id)
            .is_("deleted_at", "null")
            .order("name")
            .execute()
        )

    return _call(run).data or []


def create_player(title_id: str, name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise RepoError("プレイヤー名を入力してください。")

    def run():
        return (
            get_client()
            .table("players")
            .insert({"title_id": title_id, "name": name})
            .execute()
        )

    try:
        rows = _call(run).data or []
    except RepoError as exc:
        if "duplicate key" in str(exc) or "players_title_name_uniq" in str(exc):
            raise RepoError(f"「{name}」はすでに登録されています。") from exc
        raise
    if not rows:
        raise RepoError("プレイヤーを作成できませんでした。")
    return rows[0]["id"]


def rename_player(player_id: str, name: str) -> None:
    name = (name or "").strip()
    if not name:
        raise RepoError("プレイヤー名を入力してください。")

    def run():
        return get_client().table("players").update({"name": name}).eq("id", player_id).execute()

    try:
        _call(run)
    except RepoError as exc:
        if "duplicate key" in str(exc) or "players_title_name_uniq" in str(exc):
            raise RepoError(f"「{name}」はすでに登録されています。") from exc
        raise


def delete_player(player_id: str) -> None:
    """論理削除。過去の成績は player_id で紐づいたまま残る。"""

    def run():
        return (
            get_client()
            .table("players")
            .update({"deleted_at": _now()})
            .eq("id", player_id)
            .execute()
        )

    _call(run)


# --- 対戦 -------------------------------------------------------------------


def _group_seats(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    games: dict[str, dict[str, Any]] = {}
    for row in rows:
        game = games.setdefault(
            row["game_id"],
            {
                "id": row["game_id"],
                "name": row["game_name"],
                "created_at": row["game_created_at"],
                "round_count": row.get("round_count", 0),
                "seats": [],
            },
        )
        game["seats"].append(
            {
                "seat": row["seat"],
                "player_id": row["player_id"],
                "player_name": row["player_name"],
                "total_point": row.get("total_point", 0),
            }
        )
    for game in games.values():
        game["seats"].sort(key=lambda s: s["seat"])
    return list(games.values())


def list_games(title_id: str) -> list[dict[str, Any]]:
    """対戦一覧を、席順・半荘数・各人の合計ポイント付きで1クエリで取得する。

    旧実装は一覧のループ内で対戦ごとに再クエリしていた（N+1）。
    """

    def run():
        return (
            get_client()
            .table("v_game_seats")
            .select("*")
            .eq("title_id", title_id)
            .order("game_created_at", desc=True)
            .execute()
        )

    return _group_seats(_call(run).data or [])


def get_game(game_id: str) -> dict[str, Any] | None:
    """対戦1件を席順つきで取得する。

    画面側が session_state に頼らず毎回取得できるようにするための入口。
    リロードやURL直アクセスでも壊れない。
    """

    def run():
        return get_client().table("v_game_seats").select("*").eq("game_id", game_id).execute()

    rows = _call(run).data or []
    if not rows:
        return None
    games = _group_seats(rows)
    game = games[0]
    game["title_id"] = rows[0]["title_id"]
    return game


def create_game(title_id: str, name: str, seats: Sequence[SeatSpec]) -> str:
    """対戦を作成する。新規プレイヤーの作成も含めて不可分に行う。

    実体は RPC 関数 create_game_with_players。関数呼び出しが1トランザクションに
    なるため、途中で失敗しても孤立したプレイヤーは残らない。
    """
    filled = [s for s in seats if not s.is_empty]
    if len(filled) < 3:
        raise RepoError("3人以上のプレイヤーを選択してください。")

    payload = [
        {"player_id": s.player_id} if s.player_id else {"new_name": (s.new_name or "").strip()}
        for s in filled
    ]

    def run():
        return get_client().rpc(
            "create_game_with_players",
            {
                "p_title_id": title_id,
                "p_name": (name or "").strip(),
                "p_seats": payload,
            },
        ).execute()

    return _call(run).data


def rename_game(game_id: str, name: str) -> None:
    name = (name or "").strip()
    if not name:
        raise RepoError("対戦名を入力してください。")

    def run():
        return get_client().table("games").update({"name": name}).eq("id", game_id).execute()

    _call(run)


def delete_game(game_id: str) -> None:
    def run():
        return (
            get_client()
            .table("games")
            .update({"deleted_at": _now()})
            .eq("id", game_id)
            .execute()
        )

    _call(run)


# --- 半荘 -------------------------------------------------------------------


def _results_payload(
    results: Sequence[SeatResult], seat_to_player: dict[int, str]
) -> list[dict[str, Any]]:
    payload = []
    for r in results:
        player_id = seat_to_player.get(r.seat)
        if player_id is None:
            raise RepoError(f"席{r.seat}に対応するプレイヤーが見つかりません。")
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


def add_round(
    game_id: str, results: Sequence[SeatResult], seat_to_player: dict[int, str]
) -> str:
    """半荘1回分を保存する。持ち点・ポイント・順位をまとめて記録する。"""
    payload = _results_payload(results, seat_to_player)

    def run():
        return get_client().rpc(
            "add_round_with_results", {"p_game_id": game_id, "p_results": payload}
        ).execute()

    return _call(run).data


def update_round(
    round_id: str, results: Sequence[SeatResult], seat_to_player: dict[int, str]
) -> None:
    """既存の半荘を差し替える。入力ミスの修正やルール変更後の再計算に使う。"""
    payload = _results_payload(results, seat_to_player)

    def run():
        return get_client().rpc(
            "update_round_results", {"p_round_id": round_id, "p_results": payload}
        ).execute()

    _call(run)


def delete_round(round_id: str) -> None:
    """論理削除。表示上の「回」は取得時に振り直すため番号に穴は空かない。"""

    def run():
        return (
            get_client()
            .table("game_rounds")
            .update({"deleted_at": _now()})
            .eq("id", round_id)
            .execute()
        )

    _call(run)


def _group_rounds(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """v_round_entries の行を半荘ごとにまとめ、表示用の連番を振り直す。"""
    rounds: dict[str, dict[str, Any]] = {}
    for row in rows:
        rnd = rounds.setdefault(
            row["round_id"],
            {
                "id": row["round_id"],
                "game_id": row["game_id"],
                "created_at": row["round_created_at"],
                "results": [],
            },
        )
        rnd["results"].append(
            {
                "player_id": row["player_id"],
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


def list_rounds(game_id: str) -> list[dict[str, Any]]:
    """対戦の半荘を古い順に返す。表示用の連番 `no` は1から振り直す。

    途中の回を削除しても番号が飛ばない（旧実装は MAX(renban)+1 採番＋
    物理削除で穴が残っていた）。
    """

    def run():
        return (
            get_client()
            .table("v_round_entries")
            .select("*")
            .eq("game_id", game_id)
            .order("round_created_at")
            .execute()
        )

    return _group_rounds(_call(run).data or [])


# --- 集計 -------------------------------------------------------------------


def _fetch_title_entries(title_id: str) -> list[dict[str, Any]]:
    def run():
        return (
            get_client()
            .table("v_round_entries")
            .select("*")
            .eq("title_id", title_id)
            .order("round_created_at")
            .execute()
        )

    return _call(run).data or []


def fetch_round_entries(title_id: str) -> list[RoundEntry]:
    """タイトル配下の全記録を stats.aggregate に渡せる形で取得する。

    順位は保存済みの rank をそのまま使うため、旧実装のような
    巨大な SUM(CASE WHEN ... MAX(COALESCE(...))) は不要になった。
    """
    return [
        RoundEntry(
            player_id=row["player_id"],
            rank=row["rank"],
            point=row["point"],
            tobi=bool(row["tobi"]),
        )
        for row in _fetch_title_entries(title_id)
    ]


def fetch_rounds_in_order(title_id: str) -> list[list[RoundEntry]]:
    """タイトル配下の半荘を時系列順にまとめて返す（推移グラフ用）。"""
    return [
        [
            RoundEntry(
                player_id=r["player_id"],
                rank=r["rank"],
                point=r["point"],
                tobi=bool(r["tobi"]),
            )
            for r in rnd["results"]
        ]
        for rnd in _group_rounds(_fetch_title_entries(title_id))
    ]


def fetch_stored_rounds_for_recalc(title_id: str) -> list[dict[str, Any]]:
    """ルール変更後の再計算に必要な、持ち点と風を取得する。

    持ち点を保存しているからこそ、ウマや返し点を変えても過去データを
    作り直せる（旧実装は計算後のポイントしか残していなかった）。
    """
    rounds = []
    for rnd in _group_rounds(_fetch_title_entries(title_id)):
        rounds.append(
            {
                "round_id": rnd["id"],
                "seats": [r["seat"] for r in rnd["results"]],
                "raw_scores": [r["raw_score"] for r in rnd["results"]],
                "kazes": [r["kaze"] for r in rnd["results"]],
                "player_ids": [r["player_id"] for r in rnd["results"]],
            }
        )
    return rounds
