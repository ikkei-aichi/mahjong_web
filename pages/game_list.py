import datetime
import streamlit as st
import sqlite_db
import pandas as pd

st.set_page_config(
    page_title="麻雀管理アプリ - 対戦一覧",
    page_icon="🀄",
    layout="centered",
)

# セッションチェック
if "title_id" not in st.session_state:
    st.error("タイトルが選択されていません。")
    st.stop()

title_id = st.session_state["title_id"]
title_name = st.session_state.get("title_name", "不明なタイトル")

# --- 戻るボタン ---
if st.button("← タイトル一覧へ戻る"):
    st.switch_page("app.py")

st.title(f"🀄 {title_name}")
st.caption(f"タイトルID:{title_id}")

# --- 🆕 新規対戦作成エリア ---
st.markdown("### 🆕 新規対戦作成")

# プレイヤーリスト取得
players = sqlite_db.fetch_players(title_id)
player_map = {p["player_id"]: p["player_name"] for p in players}

# 選択肢の定義: 既存, 新規作成(-1), なし(-2)
options = list(player_map.keys()) + [-1, -2]


def player_selector(label, default_val):
    return st.selectbox(
        label,
        options,
        index=options.index(default_val) if default_val in options else 0,
        format_func=lambda x: (
            player_map[x]
            if x in player_map
            else "＋ 新規プレイヤーを追加" if x == -1 else "（なし：3人麻雀用）"
        ),
        key=f"sel_{label}",
    )


col1, col2 = st.columns(2)
with col1:
    p1_id = player_selector("プレイヤー1", options[0] if len(options) > 2 else -1)
    p1_new = st.text_input("P1 新規名", key="n1") if p1_id == -1 else None
    p2_id = player_selector("プレイヤー2", options[1] if len(options) > 2 else -1)
    p2_new = st.text_input("P2 新規名", key="n2") if p2_id == -1 else None
with col2:
    p3_id = player_selector("プレイヤー3", options[2] if len(options) > 2 else -1)
    p3_new = st.text_input("P3 新規名", key="n3") if p3_id == -1 else None
    # 4人目はデフォルトを「なし」に設定
    p4_id = player_selector("プレイヤー4", -2)
    p4_new = st.text_input("P4 新規名", key="n4") if p4_id == -1 else None

with st.form("create_game_form"):
    game_name = st.text_input(
        "対戦名", value=datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    )
    submitted = st.form_submit_button("新規作成")

    if submitted:
        # ID確定ロジック
        selected_choices = [
            (p1_id, p1_new),
            (p2_id, p2_new),
            (p3_id, p3_new),
            (p4_id, p4_new),
        ]
        final_ids = []

        for pid, new_name in selected_choices:
            if pid == -2:
                final_ids.append(None)
            elif pid == -1:
                if not new_name:
                    st.error("新規プレイヤー名を入力してください。")
                    st.stop()
                new_id = sqlite_db.insert_player(title_id, new_name.strip())
                final_ids.append(new_id)
            else:
                final_ids.append(pid)

        # 3人以上かつ重複なしチェック
        active_ids = [i for i in final_ids if i is not None]
        if len(set(active_ids)) < 3:
            st.error("3人以上の異なるプレイヤーを選択してください。")
        elif len(set(active_ids)) != len(active_ids):
            st.error("プレイヤーが重複しています。")
        else:
            sqlite_db.insert_game(title_id, game_name, final_ids)
            st.success("対戦を作成しました！")
            st.rerun()

st.markdown("---")

# --- 🧮 対戦サマリー表示 ---
st.markdown("### 🧮 プレイヤー別合計成績")
summary_rows = sqlite_db.fetch_game_summary(title_id)

if summary_rows:
    df_summary = pd.DataFrame(
        summary_rows, columns=["ID", "プレイヤー", "1位", "最下位", "合計スコア"]
    )
    # スコアがあるプレイヤーのみ表示
    df_summary = df_summary[df_summary["合計スコア"] != 0].sort_values(
        "合計スコア", ascending=False
    )
    # indexに+1を振る
    df_summary.index = range(1, len(df_summary) + 1)
    # indexカラム名を順位に変更
    df_summary.index.name = "順位"
    st.dataframe(df_summary, use_container_width=True)

    # トップ回数最多賞・最下位回数最多賞　一位・最下位　の計４名表示
    if not df_summary.empty:
        top_count = df_summary["1位"].max()
        bottom_count = df_summary["最下位"].max()

        top_players = df_summary[df_summary["1位"] == top_count]["プレイヤー"].tolist()
        bottom_players = df_summary[df_summary["最下位"] == bottom_count][
            "プレイヤー"
        ].tolist()

        st.markdown(
            f"🏆 **1位のプレイヤー**: {', '.join(df_summary[df_summary['合計スコア'] == df_summary['合計スコア'].max()]['プレイヤー'].tolist())}"
        )
        st.markdown(
            f"🥉 **最下位のプレイヤー**: {', '.join(df_summary[df_summary['合計スコア'] == df_summary['合計スコア'].min()]['プレイヤー'].tolist())}"
        )

        st.markdown(
            f"🏆 **トップ回数最多賞**: {', '.join(top_players)} （{top_count}回）"
        )
        st.markdown(
            f"🥉 **最下位回数最多賞**: {', '.join(bottom_players)} （{bottom_count}回）"
        )


else:
    st.info("データがありません。")

# --- 📋 対戦一覧表示 ---
st.markdown("### 📋 対戦履歴")
games = sqlite_db.fetch_games(title_id)

for g in games:
    with st.container(border=True):
        col_main, col_btn = st.columns([4, 1])
        with col_main:
            st.subheader(g["game_name"])
            # 参加プレイヤーを表示（Noneを除外）
            p_list = [g[f"player{i}_name"] for i in range(1, 5) if g[f"player{i}_name"]]
            # 参加者をタグ風に表示
            st.markdown(
                " ".join(
                    [
                        f'<span style="background-color:#e0e0e0;border-radius:8px;padding:4px 10px;margin-right:4px;display:inline-block;">{name}</span>'
                        for name in p_list
                    ]
                ),
                unsafe_allow_html=True,
            )

        with col_btn:
            if st.button("開く", key=f"open_{g['game_id']}"):
                st.session_state["game_id"] = g["game_id"]
                st.session_state["game_name"] = g["game_name"]
                for i in range(1, 5):
                    # sqlite3.Rowは辞書のように [] でアクセスします
                    st.session_state[f"player{i}_id"] = g[f"player{i}_id"]
                    st.session_state[f"player{i}_name"] = g[f"player{i}_name"]
                st.switch_page("pages/game_detail.py")

        # 簡易リザルト表示
        details = sqlite_db.fetch_game_detail(title_id, g["game_id"])
        if details:
            df_mini = pd.DataFrame(details)

            # プレイヤーごとに合計スコアを計算
            # 修正後のコード
            score_data = []
            for i in range(1, 5):
                # sqlite3.Row は辞書のように [] でアクセス可能ですが get() は使えません
                name = g[f"player{i}_name"]

                if name:  # 名前が None または空文字列でなければ処理
                    score_sum = df_mini[f"player{i}_score"].sum()
                    score_data.append({"プレイヤー": name, "合計スコア": score_sum})

            if score_data:
                # 1. まずスコアでソート
                score_data.sort(key=lambda x: x["合計スコア"], reverse=True)

                # 3. DataFrameに変換して表示
                df_score = pd.DataFrame(score_data)[["プレイヤー", "合計スコア"]]
                # indexカラム名を順位に変更
                df_score.index = range(1, len(df_score) + 1)
                df_score.index.name = "順位"
                st.table(df_score)
