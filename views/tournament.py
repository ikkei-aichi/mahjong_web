"""大会の詳細。開催日の一覧と、大会通算の成績。

大会は複数日にわたって開催されるので、この画面が日付ごとの入り口になる。
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from mahjong import session, ui
from mahjong.errors import AppError
from mahjong.repo import games as games_repo
from mahjong.repo import groups as groups_repo
from mahjong.repo import queries, tournaments as tournaments_repo
from mahjong.stats import aggregate

ui.show_flashes()
group = session.require_group()

tournament_id = ui.require_param(
    "tournament", "大会が指定されていません。", "views/tournaments.py", "大会一覧へ戻る"
)
if not tournament_id:
    st.stop()

try:
    tournament = tournaments_repo.get_tournament(tournament_id)
except AppError as exc:
    st.error(str(exc))
    st.stop()

if not tournament or tournament["group_id"] != group["group_id"]:
    st.warning("この大会は見つかりません（別のグループのものかもしれません）。")
    ui.link_button("大会一覧へ戻る", "views/tournaments.py", key="t_back_missing",
                   group=group["group_id"])
    st.stop()

rules, warnings = tournaments_repo.get_ruleset(tournament_id)

ui.link_button("← 大会一覧", "views/tournaments.py", key="t_back", group=group["group_id"])
st.title(tournament["name"])
if tournament.get("note"):
    st.caption(tournament["note"])
st.caption(ui.ruleset_summary(rules))
if warnings:
    st.warning("ルール設定に不備があったため補正して表示しています: " + " / ".join(warnings))


# --- 開催日 -----------------------------------------------------------------

st.markdown("### 📅 開催日")

try:
    days = tournaments_repo.list_days(tournament_id)
except AppError as exc:
    st.error(str(exc))
    st.stop()

with st.expander("🆕 開催日を追加", expanded=not days):
    with st.form("new_day"):
        held_on = st.date_input("日付", value=date.today(), format="YYYY/MM/DD")
        label = st.text_input("ラベル", placeholder="例: 初日、決勝")
        note = st.text_input("メモ", placeholder="会場など")
        if st.form_submit_button("追加する", type="primary", width="stretch"):
            try:
                tournaments_repo.create_day(
                    tournament_id, group["group_id"], held_on, label, note
                )
            except AppError as exc:
                st.error(str(exc))
            else:
                ui.flash(f"{held_on:%Y/%m/%d} を追加しました。")
                st.rerun()

for day in days:
    try:
        day_games = games_repo.list_games(day["id"])
    except AppError as exc:
        st.error(str(exc))
        day_games = []

    rounds = sum(g["round_count"] for g in day_games)
    title = f"{day['held_on']}"
    if day.get("label"):
        title += f"（{day['label']}）"

    with st.container(border=True):
        st.markdown(f"**{title}**")
        if day.get("note"):
            st.caption(day["note"])
        st.caption(f"{len(day_games)}卓 ／ {rounds}半荘")
        ui.link_button(
            "開く", "views/day.py", key=f"day_{day['id']}", primary=True,
            group=group["group_id"], tournament=tournament_id, day=day["id"],
        )


# --- 大会成績 ---------------------------------------------------------------

st.markdown("### 📊 この大会の成績")

try:
    entries = queries.fetch_entries("tournament_id", tournament_id)
    names = groups_repo.player_names(group["group_id"])
except AppError as exc:
    st.error(str(exc))
    st.stop()

if not entries:
    st.info("まだ記録がありません。開催日を開いて卓を作りましょう。")
else:
    stats = aggregate(entries, names, rules)
    ui.stats_table(stats, rules, key="tournament_stats")
    st.caption(f"全 {queries.count_rounds('tournament_id', tournament_id)} 半荘")

    ui.link_button(
        "📊 くわしい成績・グラフ", "views/stats.py", key="t_stats",
        group=group["group_id"], tournament=tournament_id,
    )

ui.link_button(
    "⚙️ この大会の設定", "views/settings.py", key="t_settings",
    group=group["group_id"], tournament=tournament_id,
)
