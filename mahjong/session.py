"""「いまどのグループを見ているか」の解決。

グループIDは URL のクエリパラメータ `?group=` に載せる。
リロードやブックマーク、他の人へのリンク共有でも同じ画面が開く。

所属が1つしかない人にグループ選択を見せても邪魔なだけなので、
サイドバーの切替は2つ以上あるときだけ出す。
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from .errors import AppError
from .repo import groups as groups_repo

_GROUP_PARAM = "group"


def my_groups() -> list[dict[str, Any]]:
    """自分が参加しているグループ。失敗しても例外は投げない。"""
    try:
        return groups_repo.list_my_groups()
    except AppError as exc:
        st.error(str(exc))
        st.stop()


def active_group(groups: list[dict[str, Any]]) -> dict[str, Any] | None:
    """いま見ているグループ。

    URL の指定を優先し、無効なら最初のグループにフォールバックする。
    （他グループのリンクを踏んだときに真っ白な画面にしないため）
    """
    if not groups:
        return None
    wanted = st.query_params.get(_GROUP_PARAM)
    for group in groups:
        if group["group_id"] == wanted:
            return group
    return groups[0]


def require_group() -> dict[str, Any]:
    """グループ必須のページの先頭で呼ぶ。

    Returns:
        v_my_groups の1行（group_id / name / my_player_id / role / is_provisional）。
    """
    groups = my_groups()
    group = active_group(groups)
    if group is None:
        st.warning("まずグループを作るか、招待コードで参加してください。")
        if st.button("グループを作る／参加する", width="stretch"):
            st.switch_page("views/onboarding.py")
        st.stop()
    # URL に載っていなければ書き戻す（リロードで同じ画面に戻れるように）
    if st.query_params.get(_GROUP_PARAM) != group["group_id"]:
        st.query_params[_GROUP_PARAM] = group["group_id"]
    return group


def is_admin(group: dict[str, Any]) -> bool:
    return group.get("role") in ("owner", "admin")


def sidebar_group_picker(groups: list[dict[str, Any]], current: dict[str, Any]) -> None:
    """サイドバーのグループ切替。2つ以上あるときだけ出す。"""
    with st.sidebar:
        if len(groups) > 1:
            names = [g["name"] for g in groups]
            index = next(
                (i for i, g in enumerate(groups) if g["group_id"] == current["group_id"]), 0
            )
            chosen = st.selectbox("グループ", names, index=index, key="_group_picker")
            picked = groups[names.index(chosen)]
            if picked["group_id"] != current["group_id"]:
                st.query_params.clear()
                st.query_params[_GROUP_PARAM] = picked["group_id"]
                st.rerun()
        else:
            st.caption(f"🀄 {current['name']}")


def provisional_notice(group: dict[str, Any]) -> None:
    """本人紐付けがまだなら促す。

    移行で自動作成されたメンバー行は表示名がメールアドレスのままなので、
    自分の実際の名前に紐付けてもらわないと過去の成績と繋がらない。
    """
    if not group.get("is_provisional"):
        return
    st.info(
        f"いまの表示名は「{group.get('my_player_name')}」です。"
        "メンバー画面で自分の名前を選ぶと、過去の対戦成績が自分のものとして表示されます。"
    )
    if st.button("自分の名前を選ぶ", key="_link_me", type="primary"):
        st.switch_page("views/members.py")
