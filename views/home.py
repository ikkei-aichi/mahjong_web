"""ホーム。自分の直近の成績と、いちばんよく使う導線をまとめる。

「前回の続きを入力する」を最上段に置く。このアプリで最も回数の多い操作は
新規作成ではなく、進行中の卓にもう1半荘足すことなので。
"""

from __future__ import annotations

import streamlit as st

from mahjong import session, ui
from mahjong.errors import AppError
from mahjong.repo import games as games_repo
from mahjong.repo import groups as groups_repo
from mahjong.repo import queries, tournaments as tournaments_repo
from mahjong.stats import aggregate
from mahjong.timeutil import format_jst

ui.show_flashes()
group = session.require_group()
session.provisional_notice(group)

st.title(f"🀄 {group['name']}")

try:
    tournaments = tournaments_repo.list_tournaments(group["group_id"])
except AppError as exc:
    st.error(str(exc))
    st.stop()

if not tournaments:
    st.info("まだ大会がありません。大会を作るところから始めましょう。")
    ui.link_button(
        "🏆 大会を作る", "views/tournaments.py", key="home_new_tournament",
        primary=True, group=group["group_id"],
    )
    st.stop()


# --- 続きを入力 -------------------------------------------------------------
# 直近に対戦が行われた開催日を探して、そこへの導線を最上段に出す。

latest = tournaments[0]
try:
    recent_games = games_repo.list_games_in_tournament(latest["id"])
except AppError as exc:
    st.error(str(exc))
    recent_games = []

if recent_games:
    game = recent_games[0]
    names = "、".join(s["player_name"] for s in game["seats"])
    st.markdown("### ▶️ 続きを入力")
    with st.container(border=True):
        st.markdown(f"**{latest['name']}** ・ {game.get('held_on') or ''}")
        st.caption(f"{game['name']} ／ {names} ／ {game['round_count']}半荘")
        ui.link_button(
            "この卓にスコアを入力", "views/game.py", key="home_continue",
            primary=True, group=group["group_id"], game=game["id"],
        )


# --- 自分の成績 -------------------------------------------------------------

st.markdown("### 📊 このグループの通算成績")
try:
    entries = queries.fetch_entries("group_id", group["group_id"])
    names = groups_repo.player_names(group["group_id"])
except AppError as exc:
    st.error(str(exc))
    st.stop()

if not entries:
    st.info("まだ対戦記録がありません。")
else:
    # 通算表示なので、レートは直近の大会のものを借りる（金額列の有無だけに使う）
    rules, _ = tournaments_repo.get_ruleset(latest["id"])
    stats = aggregate(entries, names, rules)
    me = next((s for s in stats if s.player_id == group.get("my_player_id")), None)
    if me and me.games:
        col1, col2, col3 = st.columns(3)
        col1.metric("半荘", me.games)
        col2.metric("合計", ui.format_point(me.total_point))
        col3.metric("平均順位", f"{me.avg_rank:.2f}")
    ui.stats_table(stats, rules, key="home_stats")

    st.caption(f"全 {queries.count_rounds('group_id', group['group_id'])} 半荘")


# --- 大会一覧（抜粋） -------------------------------------------------------

st.markdown("### 🏆 大会")
for tournament in tournaments[:5]:
    with st.container(border=True):
        st.markdown(f"**{tournament['name']}**")
        st.caption(f"作成 {format_jst(tournament['created_at'], '%Y/%m/%d')}")
        ui.link_button(
            "開く", "views/tournament.py", key=f"home_t_{tournament['id']}",
            group=group["group_id"], tournament=tournament["id"],
        )

if len(tournaments) > 5:
    ui.link_button(
        f"すべての大会を見る（{len(tournaments)}件）", "views/tournaments.py",
        key="home_all_tournaments", group=group["group_id"],
    )
