"""大会の一覧と作成。

「2026年春麻雀大会」のような単位。大会ごとにルール（ウマ・オカ・レート）を持つ。
"""

from __future__ import annotations

import streamlit as st

from mahjong import session, ui
from mahjong.errors import AppError
from mahjong.repo import queries, tournaments as tournaments_repo
from mahjong.rules import DEFAULT_RULESET, load_ruleset
from mahjong.timeutil import format_jst

ui.show_flashes()
group = session.require_group()

st.title("🏆 大会")

try:
    tournaments = tournaments_repo.list_tournaments(group["group_id"])
except AppError as exc:
    st.error(str(exc))
    st.stop()


# --- 新規作成 ---------------------------------------------------------------

with st.expander("🆕 大会を作る", expanded=not tournaments):
    name = st.text_input(
        "大会名", key="new_tournament_name", placeholder="例: 2026年春麻雀大会", max_chars=80
    )
    note = st.text_input("詳細・メモ", key="new_tournament_note", placeholder="会場、参加費など")

    st.markdown("**ルール**")
    rules = ui.ruleset_editor(DEFAULT_RULESET, key_prefix="new_tournament")

    if st.button("この内容で作成", type="primary", width="stretch", disabled=rules is None):
        user = st.session_state.get("auth_user") or {}
        try:
            tournament_id = tournaments_repo.create_tournament(
                group_id=group["group_id"],
                name=name,
                rules=rules,
                created_by=user.get("id"),
                note=note,
            )
        except AppError as exc:
            st.error(str(exc))
        else:
            ui.flash(f"大会「{name}」を作成しました。")
            # 入力欄を空に戻す。残しておくと連打で同じ大会が2つできる。
            for key in ("new_tournament_name", "new_tournament_note"):
                st.session_state.pop(key, None)
            ui.nav(
                "views/tournament.py", group=group["group_id"], tournament=tournament_id
            )


# --- 一覧 -------------------------------------------------------------------

if not tournaments:
    st.stop()

st.markdown("---")

for tournament in tournaments:
    rules, warnings = load_ruleset(tournament.get("ruleset"))
    with st.container(border=True):
        st.markdown(f"### {tournament['name']}")
        if tournament.get("note"):
            st.caption(tournament["note"])
        st.caption(ui.ruleset_summary(rules))
        if warnings:
            st.warning("ルール設定に不備があったため補正して表示しています: " + " / ".join(warnings))

        try:
            days = tournaments_repo.list_days(tournament["id"])
            rounds = queries.count_rounds("tournament_id", tournament["id"])
        except AppError as exc:
            st.error(str(exc))
            days, rounds = [], 0

        col1, col2, col3 = st.columns(3)
        col1.metric("開催日", len(days))
        col2.metric("半荘", rounds)
        col3.metric("作成", format_jst(tournament["created_at"], "%y/%m/%d"))

        ui.link_button(
            "開く", "views/tournament.py", key=f"open_{tournament['id']}", primary=True,
            group=group["group_id"], tournament=tournament["id"],
        )
