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

# --- 1. ヘッダーエリア ---
col_back, col_title = st.columns([1, 4])
with col_back:
    if st.button("← 戻る"):
        st.switch_page("app.py")
with col_title:
    st.title(f"🀄 {title_name}")

st.divider()

# --- 2. 🆕 新規対戦作成エリア ---
with st.expander("🆕 新規対戦を作成する", expanded=False):
    players = sqlite_db.fetch_players(title_id)
    player_map = {p["player_id"]: p["player_name"] for p in players}
    options = list(player_map.keys()) + [-1, -2]

    def player_selector(label, key, default_idx):
        return st.selectbox(
            label,
            options,
            index=default_idx,
            format_func=lambda x: (
                player_map[x]
                if x in player_map
                else "＋ 新規プレイヤーを追加" if x == -1 else "（なし）"
            ),
            key=key,
        )

    col1, col2 = st.columns(2)
    with col1:
        p1_id = player_selector(
            "プレイヤー1", "sel_p1", 0 if len(options) > 2 else len(options) - 2
        )
        p1_new = st.text_input("P1 新規名", key="n1") if p1_id == -1 else None
        p2_id = player_selector(
            "プレイヤー2", "sel_p2", 1 if len(options) > 3 else len(options) - 2
        )
        p2_new = st.text_input("P2 新規名", key="n2") if p2_id == -1 else None
    with col2:
        p3_id = player_selector(
            "プレイヤー3", "sel_p3", 2 if len(options) > 4 else len(options) - 2
        )
        p3_new = st.text_input("P3 新規名", key="n3") if p3_id == -1 else None
        p4_id = player_selector("プレイヤー4", "sel_p4", len(options) - 1)
        p4_new = st.text_input("P4 新規名", key="n4") if p4_id == -1 else None

    with st.form("create_game_form"):
        now_jst = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
        game_name = st.text_input("対戦名", value=now_jst.strftime("%Y-%m-%d %H:%M"))
        submitted = st.form_submit_button(
            "このメンバーで対戦開始！", use_container_width=True
        )

        if submitted:
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
                    final_ids.append(
                        sqlite_db.insert_player(title_id, new_name.strip())
                    )
                else:
                    final_ids.append(pid)

            active_ids = [i for i in final_ids if i is not None]
            if len(set(active_ids)) < 3:
                st.error("3人以上の異なるプレイヤーを選択してください。")
            elif len(set(active_ids)) != len(active_ids):
                st.error("プレイヤーが重複しています。")
            else:
                sqlite_db.insert_game(title_id, game_name, final_ids)
                st.success("対戦を作成しました！")
                st.rerun()

# --- 3. 🧮 プレイヤー別合計成績 (右寄せ・インデックスなし・順位カラム追加) ---
st.markdown("### 🏆 総合ランキング")
summary_rows = sqlite_db.fetch_game_summary(title_id)

if summary_rows:
    df_summary = pd.DataFrame(
        summary_rows, columns=["ID", "プレイヤー", "1位", "最下位", "合計スコア"]
    )
    # スコア順にソート
    df_summary = df_summary[df_summary["合計スコア"] != 0].sort_values(
        "合計スコア", ascending=False
    )

    # --- 順位文字列の生成 ---
    def get_rank_text(idx):
        if idx == 0:
            return "🥇"
        if idx == 1:
            return "🥈"
        if idx == 2:
            return "🥉"
        return f"{idx + 1}位"

    display_df = df_summary.copy()
    # 新しく「順位」カラムを作成
    display_df["順位"] = [get_rank_text(i) for i in range(len(display_df))]

    # カラムの並び替え（順位を左端に）
    display_df = display_df[["順位", "プレイヤー", "1位", "最下位", "合計スコア"]]

    # --- スタイル定義 ---
    def style_ranking(styler):
        styler.hide(axis="index")  # 左端のインデックス(0,1,2..)を隠す
        styler.set_properties(
            **{"text-align": "right", "font-family": "monospace"}
        )  # 全体を右寄せ
        styler.set_table_styles(
            [{"selector": "th", "props": [("text-align", "right")]}]  # ヘッダーも右寄せ
        )
        return styler

    def color_score_row(row):
        val = row["合計スコア"]
        color = "red" if val > 0 else "blue" if val < 0 else "black"
        # 合計スコア列と順位列にスタイル適用
        styles = []
        for col in row.index:
            if col == "合計スコア":
                styles.append(f"color: {color}; font-weight: bold")
            elif col == "順位":
                styles.append(
                    "text-align: center; font-weight: bold"
                )  # 順位は中央寄りの方が見やすい
            else:
                styles.append("")
        return styles

    # HTML形式で出力
    st.write(
        display_df.style.pipe(style_ranking).apply(color_score_row, axis=1).to_html(),
        unsafe_allow_html=True,
    )

# --- 4. 📋 対戦履歴 ---
st.markdown("### 📋 対戦履歴")
games = sqlite_db.fetch_games(title_id)

if not games:
    st.write("履歴はありません。")

for g in games:
    with st.container(border=True):
        col_main, col_btn = st.columns([3, 1])
        p_list = [g[f"player{i}_name"] for i in range(1, 5) if g[f"player{i}_name"]]

        # 簡易リザルト表示
        details = sqlite_db.fetch_game_detail(title_id, g["game_id"])

        with col_main:
            st.markdown(f"**{g['game_name']}**")
            st.caption("👤 " + " / ".join(p_list))

        with col_btn:
            if details:
                button_label = "詳細の確認・編集・入力を行う"
            else:
                button_label = "データの入力を行う"
            if st.button(
                button_label, key=f"open_{g['game_id']}", use_container_width=True
            ):
                st.session_state["game_id"] = g["game_id"]
                st.session_state["game_name"] = g["game_name"]
                for i in range(1, 5):
                    st.session_state[f"player{i}_id"] = g[f"player{i}_id"]
                    st.session_state[f"player{i}_name"] = g[f"player{i}_name"]
                st.switch_page("pages/game_detail.py")

        if details:
            with st.expander("この試合のスコアを表示"):
                df_mini = pd.DataFrame(details)
                score_data = []
                for i in range(1, 5):
                    name = g[f"player{i}_name"]
                    if name:
                        s_sum = df_mini[f"player{i}_score"].sum()
                        score_data.append({"プレイヤー": name, "スコア": s_sum})

                score_data.sort(key=lambda x: x["スコア"], reverse=True)
                cols = st.columns(len(score_data))
                for i, data in enumerate(score_data):
                    cols[i].metric(data["プレイヤー"], f"{data['スコア']:+}")
        else:
            st.info(
                "まだスコアが入力されていません。詳細・入力ボタンから登録してください。"
            )
