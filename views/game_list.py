"""1タイトル配下の対戦一覧と、総合ランキング。"""

from __future__ import annotations

import datetime

import pandas as pd
import streamlit as st

from mahjong import repo, ui
from mahjong.rules import RuleSet
from mahjong.stats import aggregate
from mahjong.timeutil import JST, format_jst

title_id = ui.require_param(
    "title", "タイトルが選択されていません。", "views/home.py", "タイトル一覧へ"
)
if not title_id:
    st.stop()

title = repo.get_title(title_id)
if not title:
    st.error("タイトルが見つかりません。削除された可能性があります。")
    if st.button("タイトル一覧へ"):
        ui.goto("views/home.py")
    st.stop()

rules = RuleSet.from_dict(title.get("ruleset"))

# --- ヘッダー ---------------------------------------------------------------

col_back, col_title = st.columns([1, 4])
with col_back:
    if st.button("← 戻る", use_container_width=True):
        ui.goto("views/home.py")
with col_title:
    st.title(f"🀄 {title['name']}")

st.caption(ui.ruleset_summary(rules))

col_stats, col_settings = st.columns(2)
with col_stats:
    if st.button("📊 詳しい成績を見る", use_container_width=True):
        ui.goto("views/stats.py", title=title_id)
with col_settings:
    if st.button("⚙️ ルール・プレイヤー設定", use_container_width=True):
        ui.goto("views/settings.py", title=title_id)

st.divider()

# --- 新規対戦の作成 ---------------------------------------------------------

players = repo.list_players(title_id)
player_names = {p["id"]: p["name"] for p in players}

with st.expander("🆕 新しい対戦をはじめる", expanded=not players):
    st.caption(f"{rules.player_count}人ぶんの席を埋めてください。3人まで減らせます。")

    NONE = "__none__"
    NEW = "__new__"
    options = [*player_names.keys(), NEW, NONE]

    def label_for(value: str) -> str:
        if value == NEW:
            return "＋ 新規プレイヤー"
        if value == NONE:
            return "（空席）"
        return player_names.get(value, value)

    specs: list[repo.SeatSpec] = []
    cols = st.columns(2)
    for seat in range(4):
        with cols[seat % 2]:
            # 既定は既存プレイヤーを順に、足りなければ新規入力を促す
            if seat < len(options) - 2:
                default_index = seat
            elif seat < rules.player_count:
                default_index = options.index(NEW)
            else:
                default_index = options.index(NONE)

            selected = st.selectbox(
                f"席{seat + 1}",
                options,
                index=default_index,
                format_func=label_for,
                key=f"seat_{title_id}_{seat}",
            )
            new_name = None
            if selected == NEW:
                new_name = st.text_input(
                    f"席{seat + 1} の名前", key=f"seat_name_{title_id}_{seat}"
                )

            if selected == NONE:
                specs.append(repo.SeatSpec())
            elif selected == NEW:
                specs.append(repo.SeatSpec(new_name=new_name))
            else:
                specs.append(repo.SeatSpec(player_id=selected))

    now_jst = datetime.datetime.now(JST)
    game_name = st.text_input(
        "対戦名", value=now_jst.strftime("%Y-%m-%d %H:%M"), key=f"game_name_{title_id}"
    )

    if st.button("この面子で開始", type="primary", use_container_width=True):
        try:
            game_id = repo.create_game(title_id, game_name, specs)
        except repo.RepoError as exc:
            # 検証はすべて DB 関数の中で書き込み前に行われるため、
            # ここでエラーになっても中途半端なプレイヤーは残らない
            st.error(str(exc))
        else:
            ui.goto("views/game_detail.py", title=title_id, game=game_id)

# --- 総合ランキング ---------------------------------------------------------

st.markdown("### 🏆 総合ランキング")

entries = repo.fetch_round_entries(title_id)
stats = aggregate(entries, player_names, rules)
rows = ui.stats_table_rows(stats, rules)

if not rows:
    st.info("まだ記録がありません。対戦を作ってスコアを入力してください。")
else:
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        use_container_width=True,
    )

# --- 対戦履歴 ---------------------------------------------------------------

st.markdown("### 📋 対戦履歴")

games = repo.list_games(title_id)
if not games:
    st.write("まだ対戦がありません。")

for game in games:
    with st.container(border=True):
        col_main, col_btn = st.columns([3, 1])
        with col_main:
            st.markdown(f"**{game['name']}**")
            names = " / ".join(s["player_name"] for s in game["seats"])
            st.caption(f"👤 {names}")
            st.caption(f"🕒 {format_jst(game['created_at'])}　|　{game['round_count']}半荘")

        with col_btn:
            label = "入力・編集" if game["round_count"] else "スコア入力"
            if st.button(label, key=f"open_{game['id']}", use_container_width=True):
                ui.goto("views/game_detail.py", title=title_id, game=game["id"])

        if game["round_count"]:
            ordered = sorted(game["seats"], key=lambda s: -s["total_point"])
            metric_cols = st.columns(len(ordered))
            for col, seat in zip(metric_cols, ordered):
                col.metric(seat["player_name"], ui.format_point(seat["total_point"]))
        else:
            st.info("まだスコアが入力されていません。")
