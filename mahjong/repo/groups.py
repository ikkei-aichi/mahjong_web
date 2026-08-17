"""グループ・参加者（メンバー／ゲスト）・招待コード。

参加者は `players` テーブル1本で表す。
    user_id IS NOT NULL … ログインアカウントを持つメンバー
    user_id IS NULL     … ゲスト（名前だけの参加者）
ゲストは後から本人のアカウントに紐付けられる（link_me_to_player）。

権限に関わる列（role / user_id / group_id）は列単位の GRANT で
直接更新できないようにしてあるので、変更は必ず RPC を通る。
"""

from __future__ import annotations

from typing import Any

from ._base import AppError, call, client, now_iso, rows, single

MEMBER_ROLES = ("owner", "admin", "member")
ROLE_LABELS = {"owner": "オーナー", "admin": "管理者", "member": "メンバー"}


# --- 自分の所属 -------------------------------------------------------------


def list_my_groups() -> list[dict[str, Any]]:
    """自分が参加しているグループと、そこでの自分の立場。"""

    def run():
        return client().table("v_my_groups").select("*").order("created_at").execute()

    return rows(call(run))


def get_group(group_id: str) -> dict[str, Any] | None:
    def run():
        return (
            client()
            .table("groups")
            .select("id, name, description, created_by, created_at")
            .eq("id", group_id)
            .is_("deleted_at", "null")
            .limit(1)
            .execute()
        )

    return single(call(run))


def create_group(name: str, display_name: str | None = None) -> str:
    """グループを作り、自分をオーナーとして登録する。

    グループ行とオーナー行は不可分に作る必要があるため RPC で行う
    （どのグループにも属していない状態では INSERT ポリシーを通れない、
    というブートストラップ問題への対処でもある）。
    """
    name = (name or "").strip()
    if not name:
        raise AppError("グループ名を入力してください。")

    def run():
        return client().rpc(
            "create_group",
            {"p_name": name, "p_display_name": (display_name or "").strip() or None},
        ).execute()

    return call(run).data


def update_group(group_id: str, name: str | None = None, description: str | None = None) -> None:
    payload: dict[str, Any] = {}
    if name is not None:
        name = name.strip()
        if not name:
            raise AppError("グループ名を入力してください。")
        payload["name"] = name
    if description is not None:
        payload["description"] = description.strip() or None
    if not payload:
        return

    def run():
        return client().table("groups").update(payload).eq("id", group_id).execute()

    call(run)


# --- 参加者 -----------------------------------------------------------------


def list_players(group_id: str) -> list[dict[str, Any]]:
    """対戦の席に選べる参加者（削除済みを除く）。"""

    def run():
        return (
            client()
            .table("players")
            .select("id, name, user_id, role, is_provisional")
            .eq("group_id", group_id)
            .is_("deleted_at", "null")
            .order("name")
            .execute()
        )

    return rows(call(run))


def list_all_players(group_id: str) -> list[dict[str, Any]]:
    """削除済みも含む全参加者。

    成績集計にはこちらを使う。削除済みを除いてしまうと、その人の記録が
    集計から落ちて**順位表の合計がゼロサムでなくなる**（旧実装の不具合）。
    """

    def run():
        return (
            client()
            .table("players")
            .select("id, name, user_id, role, is_provisional, deleted_at, merged_into")
            .eq("group_id", group_id)
            .order("name")
            .execute()
        )

    return rows(call(run))


def player_names(group_id: str) -> dict[str, str]:
    """player_id -> 表示名。削除済みは「(退会) 名前」にして残す。"""
    names: dict[str, str] = {}
    for row in list_all_players(group_id):
        label = row["name"]
        if row.get("merged_into"):
            continue  # 統合された側は代表に集約済みなので出さない
        if row.get("deleted_at"):
            label = f"(退会) {label}"
        names[row["id"]] = label
    return names


def create_player(group_id: str, name: str) -> str:
    """ゲスト参加者を追加する。アカウントとは紐付かない。"""
    name = (name or "").strip()
    if not name:
        raise AppError("名前を入力してください。")

    def run():
        return (
            client()
            .table("players")
            .insert({"group_id": group_id, "name": name})
            .execute()
        )

    created = single(call(run))
    if not created:
        raise AppError("参加者を追加できませんでした。")
    return created["id"]


def rename_player(player_id: str, name: str) -> None:
    name = (name or "").strip()
    if not name:
        raise AppError("名前を入力してください。")

    def run():
        return client().table("players").update({"name": name}).eq("id", player_id).execute()

    call(run)


def delete_player(player_id: str) -> None:
    """論理削除。過去の成績は player_id で紐づいたまま残り、集計にも含まれ続ける。"""

    def run():
        return (
            client()
            .table("players")
            .update({"deleted_at": now_iso()})
            .eq("id", player_id)
            .execute()
        )

    call(run)


# --- メンバー管理（RPC 経由） -----------------------------------------------


def link_me_to_player(player_id: str) -> str:
    """自分のアカウントを、既存の参加者（例:「田中」）に紐付ける。

    移行時に作られた暫定行から、実際の名前へ乗り換えるための操作。
    """

    def run():
        return client().rpc("link_me_to_player", {"p_target_player_id": player_id}).execute()

    return call(run).data


def set_member_role(player_id: str, role: str) -> None:
    if role not in MEMBER_ROLES:
        raise AppError("不正な役割です。")

    def run():
        return client().rpc(
            "set_member_role", {"p_player_id": player_id, "p_role": role}
        ).execute()

    call(run)


def remove_member(player_id: str) -> None:
    """メンバーを外す。参加者行とその成績は残り、ゲスト扱いに戻る。"""

    def run():
        return client().rpc("remove_member", {"p_player_id": player_id}).execute()

    call(run)


# --- 招待 -------------------------------------------------------------------


def list_invites(group_id: str) -> list[dict[str, Any]]:
    def run():
        return (
            client()
            .table("group_invites")
            .select("id, code, expires_at, max_uses, used_count, revoked_at, created_at")
            .eq("group_id", group_id)
            .order("created_at", desc=True)
            .execute()
        )

    return rows(call(run))


def create_invite(
    group_id: str, expires_at: str | None = None, max_uses: int | None = 20
) -> str:
    """招待コードを発行する（管理者のみ）。"""
    params: dict[str, Any] = {"p_group_id": group_id}
    if expires_at is not None:
        params["p_expires_at"] = expires_at
    if max_uses is not None:
        params["p_max_uses"] = max_uses

    def run():
        return client().rpc("create_invite", params).execute()

    return call(run).data


def revoke_invite(invite_id: str) -> None:
    def run():
        return (
            client()
            .table("group_invites")
            .update({"revoked_at": now_iso()})
            .eq("id", invite_id)
            .execute()
        )

    call(run)


def preview_invite(code: str) -> dict[str, Any]:
    """参加前の下見。グループ名と「自分はこの人です」の候補を返す。"""
    code = (code or "").strip().upper()
    if not code:
        raise AppError("招待コードを入力してください。")

    def run():
        return client().rpc("preview_invite", {"p_code": code}).execute()

    return call(run).data or {}


def join_group_by_code(
    code: str, claim_player_id: str | None = None, new_name: str | None = None
) -> str:
    """招待コードでグループに参加する。

    既存の未紐付け参加者を選ぶ(claim_player_id)か、新しい名前で登録する。
    """
    code = (code or "").strip().upper()
    if not code:
        raise AppError("招待コードを入力してください。")

    def run():
        return client().rpc(
            "join_group_by_code",
            {
                "p_code": code,
                "p_claim_player_id": claim_player_id,
                "p_new_name": (new_name or "").strip() or None,
            },
        ).execute()

    return call(run).data
