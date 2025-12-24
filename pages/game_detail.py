import datetime
import streamlit as st
import sqlite_db
import pandas as pd

st.set_page_config(page_title="対戦（詳細）", page_icon="🎮")


# セッションチェック
if "title_id" not in st.session_state:
    st.error("タイトルが選択されていません。")
    st.stop()


# game一覧を取得
title_id = st.session_state["title_id"]
game_id = st.session_state.get("game_id")
game_details = sqlite_db.fetch_game_detail(title_id, game_id)

# ===== 戻るボタン =====
if st.button("← 対戦一覧へ戻る"):
    st.switch_page("pages/game_list.py")
with st.sidebar:
    if st.button("← 対戦一覧へ戻る"):
        st.switch_page("pages/game_list.py")

title_id = st.session_state["title_id"]
title_name = st.session_state["title_name"]
player1_id = st.session_state["player1_id"]
player2_id = st.session_state["player2_id"]
player3_id = st.session_state["player3_id"]
player4_id = st.session_state["player4_id"]
player1_name = st.session_state["player1_name"]
player2_name = st.session_state["player2_name"]
player3_name = st.session_state["player3_name"]
player4_name = st.session_state["player4_name"]

st.title(f"🀄 {title_name}")
st.caption(f"タイトルID:{title_id} / 対戦ID:{game_id}")


# ===== 対戦詳細新規作成フォーム =====
with st.form("game_detail_form"):
    st.markdown("### 対戦結果を入力")

    # 入力欄 --- 東・南・西・北
    player1_kaze = st.selectbox(f"{player1_name} 風", ["東", "南", "西", "北"])
    player2_kaze = st.selectbox(f"{player2_name} 風", ["東", "南", "西", "北"])
    player3_kaze = st.selectbox(f"{player3_name} 風", ["東", "南", "西", "北"])
    player4_kaze = st.selectbox(f"{player4_name} 風", ["東", "南", "西", "北"])

    # 入力欄 --- スコア入力
    player1_score = st.number_input(f"{player1_name} スコア", value=25000, step=1000)
    player2_score = st.number_input(f"{player2_name} スコア", value=25000, step=1000)
    player3_score = st.number_input(f"{player3_name} スコア", value=25000, step=1000)
    player4_score = st.number_input(f"{player4_name} スコア", value=25000, step=1000)

    scores = [player1_score, player2_score, player3_score, player4_score]
    # トップのインデックス
    top_index = scores.index(max(scores))
    # 30000返しの収支
    diffs = [s - 30000 for s in scores]
    # トップに残り（+20000）を加算
    diffs[top_index] += 20000
    player1_score, player2_score, player3_score, player4_score = diffs

    submitted = st.form_submit_button("登録")

    if submitted:
        title_id = st.session_state["title_id"]
        game_id = st.session_state.get("game_id")

        # バリデーションチェック
        if not game_id:
            st.error("対戦が選択されていません。")
            st.stop()

        # 風の重複チェック
        if (
            player1_kaze == player2_kaze
            or player1_kaze == player3_kaze
            or player1_kaze == player4_kaze
            or player2_kaze == player3_kaze
            or player2_kaze == player4_kaze
            or player3_kaze == player4_kaze
        ):
            st.error("各プレイヤーの風は重複しないように選択してください。")
            st.stop()

        # 一位が複数いる場合エラー
        if scores.count(max(scores)) > 1:
            st.error("スコアの一位が複数います。正しいスコアを入力してください。")
            st.stop()

        # renbanは現在の最大値+1
        game_details = sqlite_db.fetch_game_detail(title_id, game_id)
        if game_details:
            renban = max([detail["renban"] for detail in game_details]) + 1
        else:
            renban = 1

        #

        sqlite_db.insert_game_detail(
            title_id,
            game_id,
            renban,
            player1_score,
            player2_score,
            player3_score,
            player4_score,
            player1_kaze,
            player2_kaze,
            player3_kaze,
            player4_kaze,
        )

        st.success("対戦結果を登録しました。")
        st.rerun()


if not game_details:
    st.info("対戦詳細がありません。新規作成してください。")

else:
    # ===== 明細行作成 =====
    table_rows = []

    for detail in game_details:
        dt = datetime.datetime.strptime(detail["create_date"], "%Y-%m-%d %H:%M:%S")

        table_rows.append(
            {
                "回数": detail["renban"],
                "時刻": dt.strftime("%H:%M"),
                player1_name: detail["player1_score"],
                player2_name: detail["player2_score"],
                player3_name: detail["player3_score"],
                player4_name: detail["player4_score"],
            }
        )

    df = pd.DataFrame(table_rows)

    # ===== 合計行作成 =====
    total_row = {
        "回数": "合計",
        "時刻": "",
        player1_name: df[player1_name].sum(),
        player2_name: df[player2_name].sum(),
        player3_name: df[player3_name].sum(),
        player4_name: df[player4_name].sum(),
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
