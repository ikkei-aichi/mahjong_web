"""グループを作る／招待コードで参加する。

どのグループにも属していないユーザーが最初に見る画面。
RLS の都合でこの状態では通常のテーブルが1行も見えないため、
ここでの操作はすべて SECURITY DEFINER の RPC を通る。
"""

from __future__ import annotations

import streamlit as st

from mahjong import ui
from mahjong.errors import AppError
from mahjong.repo import groups as groups_repo

ui.show_flashes()

st.title("👋 はじめに")
st.caption("仲間内で成績を共有するには、グループを作るか、招待コードで参加します。")

tab_join, tab_create = st.tabs(["招待コードで参加", "グループを作る"])


# --- 招待コードで参加 -------------------------------------------------------

with tab_join:
    st.markdown("#### 招待コードを入力")
    code = st.text_input(
        "招待コード", key="join_code", placeholder="ABCD2345", max_chars=16
    ).strip().upper()

    if code:
        try:
            info = groups_repo.preview_invite(code)
        except AppError as exc:
            st.error(str(exc))
        else:
            st.success(f"「{info['group_name']}」への招待です。")

            if info.get("already_member"):
                st.info("あなたはすでにこのグループのメンバーです。")
                if st.button("このグループを開く", type="primary", width="stretch"):
                    ui.nav("views/home.py", group=info["group_id"])
            else:
                claimable = info.get("claimable") or []
                st.markdown("#### あなたは誰ですか？")
                st.caption(
                    "すでに対戦記録がある名前を選ぶと、その成績が自分のものになります。"
                    "初めての方は「新しく登録する」を選んでください。"
                )

                options = [("__new__", "＋ 新しく登録する")] + [
                    (p["id"], p["name"]) for p in claimable
                ]
                labels = [label for _, label in options]
                chosen = st.radio(
                    "名前", labels, key="join_pick", label_visibility="collapsed"
                )
                picked_id = options[labels.index(chosen)][0]

                new_name = ""
                if picked_id == "__new__":
                    new_name = st.text_input("あなたの名前", key="join_new_name")

                if st.button("このグループに参加する", type="primary", width="stretch"):
                    try:
                        group_id = groups_repo.join_group_by_code(
                            code,
                            claim_player_id=None if picked_id == "__new__" else picked_id,
                            new_name=new_name or None,
                        )
                    except AppError as exc:
                        st.error(str(exc))
                    else:
                        ui.flash("グループに参加しました。")
                        ui.nav("views/home.py", group=group_id)


# --- グループを作る ---------------------------------------------------------

with tab_create:
    st.markdown("#### 新しいグループ")
    with st.form("create_group"):
        name = st.text_input("グループ名", placeholder="例: 金曜麻雀会", max_chars=60)
        display_name = st.text_input(
            "あなたの表示名", placeholder="例: 田中",
            help="空欄ならメールアドレスから自動で決めます。あとから変更できます。",
        )
        submitted = st.form_submit_button("作成する", type="primary", width="stretch")

    if submitted:
        try:
            group_id = groups_repo.create_group(name, display_name)
        except AppError as exc:
            st.error(str(exc))
        else:
            ui.flash(f"グループ「{name}」を作成しました。")
            ui.nav("views/home.py", group=group_id)
