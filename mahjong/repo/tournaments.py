"""大会（旧タイトル）と開催日。

大会は複数の開催日にまたがる（「2026年春麻雀大会」を3日に分けて開催、など）。
対戦は必ずいずれかの開催日にぶら下がる。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ..rules import RuleSet, load_ruleset
from ._base import AppError, call, client, now_iso, rows, single

_COLUMNS = "id, group_id, name, ruleset, note, created_by, created_at"


# --- 大会 -------------------------------------------------------------------


def list_tournaments(group_id: str) -> list[dict[str, Any]]:
    def run():
        return (
            client()
            .table("tournaments")
            .select(_COLUMNS)
            .eq("group_id", group_id)
            .is_("deleted_at", "null")
            .order("created_at", desc=True)
            .execute()
        )

    return rows(call(run))


def get_tournament(tournament_id: str) -> dict[str, Any] | None:
    def run():
        return (
            client()
            .table("tournaments")
            .select(_COLUMNS)
            .eq("id", tournament_id)
            .is_("deleted_at", "null")
            .limit(1)
            .execute()
        )

    return single(call(run))


def get_ruleset(tournament_id: str) -> tuple[RuleSet, list[str]]:
    """大会のルールを返す。壊れた値でも例外を投げず、警告と一緒に返す。

    旧実装は `RuleSet.from_dict` の例外が誰にも捕まえられておらず、
    壊れたレコードが1件あるだけで一覧ページ全体が落ちていた。
    """
    tournament = get_tournament(tournament_id)
    if not tournament:
        raise AppError("大会が見つかりません。")
    return load_ruleset(tournament.get("ruleset"))


def create_tournament(
    group_id: str,
    name: str,
    rules: RuleSet | None = None,
    created_by: str | None = None,
    note: str | None = None,
) -> str:
    name = (name or "").strip()
    if not name:
        raise AppError("大会名を入力してください。")

    payload = {
        "group_id": group_id,
        "name": name,
        "ruleset": (rules or RuleSet()).to_dict(),
        "note": (note or "").strip() or None,
        "created_by": created_by,
    }

    def run():
        return client().table("tournaments").insert(payload).execute()

    created = single(call(run))
    if not created:
        raise AppError("大会を作成できませんでした。")
    return created["id"]


def update_tournament(
    tournament_id: str,
    name: str | None = None,
    note: str | None = None,
    rules: RuleSet | None = None,
) -> None:
    payload: dict[str, Any] = {}
    if name is not None:
        name = name.strip()
        if not name:
            raise AppError("大会名を入力してください。")
        payload["name"] = name
    if note is not None:
        payload["note"] = note.strip() or None
    if rules is not None:
        payload["ruleset"] = rules.to_dict()
    if not payload:
        return

    def run():
        return client().table("tournaments").update(payload).eq("id", tournament_id).execute()

    call(run)


def delete_tournament(tournament_id: str) -> None:
    def run():
        return (
            client()
            .table("tournaments")
            .update({"deleted_at": now_iso()})
            .eq("id", tournament_id)
            .execute()
        )

    call(run)


# --- 開催日 -----------------------------------------------------------------

_DAY_COLUMNS = "id, tournament_id, group_id, held_on, label, note, created_at"


def list_days(tournament_id: str) -> list[dict[str, Any]]:
    """開催日を新しい順に返す。"""

    def run():
        return (
            client()
            .table("tournament_days")
            .select(_DAY_COLUMNS)
            .eq("tournament_id", tournament_id)
            .is_("deleted_at", "null")
            .order("held_on", desc=True)
            .execute()
        )

    return rows(call(run))


def get_day(day_id: str) -> dict[str, Any] | None:
    def run():
        return (
            client()
            .table("tournament_days")
            .select(_DAY_COLUMNS)
            .eq("id", day_id)
            .is_("deleted_at", "null")
            .limit(1)
            .execute()
        )

    return single(call(run))


def create_day(
    tournament_id: str,
    group_id: str,
    held_on: date | str,
    label: str | None = None,
    note: str | None = None,
) -> str:
    payload = {
        "tournament_id": tournament_id,
        "group_id": group_id,
        "held_on": held_on.isoformat() if isinstance(held_on, date) else str(held_on),
        "label": (label or "").strip() or None,
        "note": (note or "").strip() or None,
    }

    def run():
        return client().table("tournament_days").insert(payload).execute()

    created = single(call(run))
    if not created:
        raise AppError("開催日を追加できませんでした。")
    return created["id"]


def update_day(
    day_id: str,
    held_on: date | str | None = None,
    label: str | None = None,
    note: str | None = None,
) -> None:
    payload: dict[str, Any] = {}
    if held_on is not None:
        payload["held_on"] = held_on.isoformat() if isinstance(held_on, date) else str(held_on)
    if label is not None:
        payload["label"] = label.strip() or None
    if note is not None:
        payload["note"] = note.strip() or None
    if not payload:
        return

    def run():
        return client().table("tournament_days").update(payload).eq("id", day_id).execute()

    call(run)


def delete_day(day_id: str) -> None:
    def run():
        return (
            client()
            .table("tournament_days")
            .update({"deleted_at": now_iso()})
            .eq("id", day_id)
            .execute()
        )

    call(run)
