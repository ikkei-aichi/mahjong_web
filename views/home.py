"""タイトル（対戦グループ・大会）の一覧と作成。"""

from __future__ import annotations

import streamlit as st

from mahjong import repo, ui
from mahjong.rules import RuleSet
from mahjong.timeutil import format_jst

st.title("🀄 麻雀管理アプリ")
st.markdown("対戦グループや大会ごとにタイトルを作成して管理します。")

# --- 新規作成 ---------------------------------------------------------------

with st.expander("🆕 新しいタイトルを作る", expanded=False):
    name = st.text_input("タイトル名", placeholder="例：2026年 正月大会", key="new_title_name")

    st.markdown("##### ルール設定")
    st.caption("あとから変更できます。変更しても過去のデータは持ち点から再計算できます。")
    rules = ui.ruleset_editor(RuleSet(), key_prefix="new_title")

    if st.button("このルールで作成", type="primary", use_container_width=True):
        try:
            title_id = repo.create_title(name, rules, owner_id=st.session_state["auth_user"]["id"])
        except repo.RepoError as exc:
            st.error(str(exc))
        else:
            ui.goto("views/game_list.py", title=title_id)

st.divider()

# --- 一覧 -------------------------------------------------------------------

st.markdown("### 登録済みタイトル")

try:
    titles = repo.list_titles()
except repo.RepoError as exc:
    st.error(f"タイトルを取得できませんでした: {exc}")
    st.stop()

if not titles:
    st.info("まだタイトルがありません。上のフォームから作成してください。")
    st.stop()

for row in titles:
    rules = RuleSet.from_dict(row.get("ruleset"))
    with st.container(border=True):
        st.subheader(row["name"])
        st.caption(f"📅 {format_jst(row['created_at'])}　|　{ui.ruleset_summary(rules)}")

        col_open, col_stats, col_settings = st.columns(3)
        with col_open:
            if st.button("▶ 開く", key=f"open_{row['id']}", use_container_width=True):
                ui.goto("views/game_list.py", title=row["id"])
        with col_stats:
            if st.button("📊 成績", key=f"stats_{row['id']}", use_container_width=True):
                ui.goto("views/stats.py", title=row["id"])
        with col_settings:
            if st.button("⚙️ 設定", key=f"cfg_{row['id']}", use_container_width=True):
                ui.goto("views/settings.py", title=row["id"])
