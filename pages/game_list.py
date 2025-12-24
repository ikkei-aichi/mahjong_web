import datetime
import streamlit as st
import sqlite_db
import pandas as pd

st.set_page_config(page_title="対戦（一覧）", page_icon="🎮")

# セッションチェック
if "title_id" not in st.session_state:
    st.error("タイトルが選択されていません。")
    st.stop()

# game一覧を取得
title_id = st.session_state["title_id"]
games = sqlite_db.fetch_games(title_id)

# ===== 戻るボタン =====
if st.button("← タイトル一覧へ戻る"):
    st.switch_page("app.py")
with st.sidebar:
    if st.button("← タイトル一覧へ戻る"):
        st.switch_page("app.py")

title_name = st.session_state["title_name"]

st.title(f"🀄 {title_name}")
st.caption(f"タイトルID:{title_id}")

# --- ここから新規作成エリア ---
st.markdown("### 🆕 新規対戦作成")

# 1. プレイヤー選択 (フォームの外に出すことで、選択時に即座に画面が更新されるようにする)
players = sqlite_db.fetch_players(st.session_state["title_id"])
player_map = {p["player_id"]: p["player_name"] for p in players}
options = list(player_map.keys()) + [-1]

st.markdown("###### プレイヤー選択")


def player_selector(label):
    selected_id = st.selectbox(
        label,
        options,
        format_func=lambda x: (player_map[x] if x != -1 else "＋ 新規プレイヤーを追加"),
        key=f"sel_{label}",  # keyを一意にする
    )

    new_name = None
    if selected_id == -1:
        # ここがポイント：フォームの外なら即座に表示されます
        new_name = st.text_input(f"{label} 新規プレイヤー名", key=f"new_{label}")

    return selected_id, new_name


# カラムを分けてスッキリ表示
col1, col2 = st.columns(2)
with col1:
    p1_id, p1_new = player_selector("プレイヤー1")
    p2_id, p2_new = player_selector("プレイヤー2")
with col2:
    p3_id, p3_new = player_selector("プレイヤー3")
    p4_id, p4_new = player_selector("プレイヤー4")

# 2. 対戦名と送信ボタン (確定操作のみフォームにする)
with st.form("create_game_form"):
    st.markdown("###### 対戦名")
    default_game_name = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    game_name = st.text_input(
        "対戦名", value=default_game_name, label_visibility="collapsed"
    )

    submitted = st.form_submit_button("新規作成")

    if submitted:
        if game_name.strip() == "":
            st.error("対戦名を入力してください。")
            st.stop()

        final_player_ids = []
        player_choices = [
            (p1_id, p1_new),
            (p2_id, p2_new),
            (p3_id, p3_new),
            (p4_id, p4_new),
        ]

        for pid, new_name in player_choices:
            if pid == -1:
                if not new_name or new_name.strip() == "":
                    st.error("新規プレイヤー名を入力してください。")
                    st.stop()

                # 新規プレイヤー登録
                new_player_id = sqlite_db.insert_player(
                    st.session_state["title_id"], new_name.strip()
                )
                final_player_ids.append(new_player_id)
            else:
                final_player_ids.append(pid)

        # 重複チェック
        if len(set(final_player_ids)) != 4:
            st.error("同じプレイヤーが複数選択されています。")
            st.stop()

        # 対戦登録
        sqlite_db.insert_game(
            title_id=st.session_state["title_id"],
            game_name=game_name,
            player_ids=final_player_ids,
        )

        st.success("対戦を登録しました。")
        st.rerun()

st.markdown("---")

# ===== 対戦summary表示 =====
# (以下、元のコードと同じ)
st.markdown("### 🧮 対戦サマリー")

if not games:
    st.info("対戦がありません。新規作成してください。")

summary_rows = sqlite_db.fetch_game_summary(title_id)

if not summary_rows:
    st.info("まだ集計データがありません。")
else:
    df_summary = pd.DataFrame(
        summary_rows,
        columns=["player_id", "player_name", "win_count", "total_score"],
    )
    df_summary = df_summary[df_summary["total_score"] != 0]
    df_summary = df_summary.rename(
        columns={
            "player_name": "プレイヤー",
            "win_count": "1位回数",
            "total_score": "合計スコア",
        }
    )
    df_summary = df_summary.sort_values("合計スコア", ascending=False)

    st.dataframe(df_summary, use_container_width=True, hide_index=True)

    # ===== 対戦一覧表示 =====
    for game in games:
        with st.container(border=True):
            st.subheader(game["game_name"])
            st.caption(
                "作成日時："
                + datetime.datetime.strptime(
                    game["create_date"], "%Y-%m-%d %H:%M:%S"
                ).strftime("%Y-%m-%d %H:%M:%S")
            )
            players_list = []
            for i in range(1, 5):
                p_name = game[f"player{i}_name"]
                if p_name:
                    players_list.append(p_name)
            # selection_mode="single" とし、default=None にすることで初期状態で何も選択されないようにします
            st.pills(
                "参加プレイヤー",
                players_list,
                selection_mode="single",
                default=None,
                disabled=True,
                key=f"pills_{game['game_id']}",
            )

            # ===== 対戦の結果をリスト表示 =====
            # ===== 明細行作成 =====
            table_rows = []

            game_details = sqlite_db.fetch_game_detail(title_id, game["game_id"])
            for detail in game_details:
                dt = datetime.datetime.strptime(
                    detail["create_date"], "%Y-%m-%d %H:%M:%S"
                )

                table_rows.append(
                    {
                        "回数": detail["renban"],
                        "時刻": dt.strftime("%H:%M"),
                        game["player1_name"]: detail["player1_score"],
                        game["player2_name"]: detail["player2_score"],
                        game["player3_name"]: detail["player3_score"],
                        game["player4_name"]: detail["player4_score"],
                    }
                )

            # table_rowsが空の場合の処理
            if not table_rows:
                st.info("対戦詳細がありません。")

            else:
                df = pd.DataFrame(table_rows)

                # ===== 合計行作成 =====
                total_row = {
                    "回数": "合計",
                    "時刻": "",
                    game["player1_name"]: df[game["player1_name"]].sum(),
                    game["player2_name"]: df[game["player2_name"]].sum(),
                    game["player3_name"]: df[game["player3_name"]].sum(),
                    game["player4_name"]: df[game["player4_name"]].sum(),
                }

                total_df = pd.DataFrame([total_row])

                # ===== 合計行を先頭に追加 =====
                df = pd.concat([total_df, df], ignore_index=True)

                # ===== 最大スコア強調（合計行は除外） =====
                def highlight_max(row):
                    # 合計行
                    if row["回数"] == "合計":
                        return ["font-weight: bold"] * len(row)

                    score_cols = row.index[2:]  # 回数・時刻を除外
                    max_val = row[score_cols].max()

                    return [
                        (
                            "background-color: #ffd966"
                            if col in score_cols and row[col] == max_val
                            else ""
                        )
                        for col in row.index
                    ]

                # ===== 表示 =====
                st.markdown("### 対戦結果")

                st.dataframe(
                    df.style.apply(highlight_max, axis=1),
                    use_container_width=True,
                    hide_index=True,
                )

            if st.button("▶ この対戦を開く", key=f"btn_{game['game_id']}"):
                st.session_state["game_id"] = game["game_id"]
                st.session_state["game_name"] = game["game_name"]
                for i in range(1, 5):
                    st.session_state[f"player{i}_id"] = game[f"player{i}_id"]
                    st.session_state[f"player{i}_name"] = game[f"player{i}_name"]
                st.switch_page("pages/game_detail.py")
