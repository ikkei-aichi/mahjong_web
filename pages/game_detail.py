import datetime
import streamlit as st
import sqlite_db
import pandas as pd

st.set_page_config(
    page_title="対戦詳細 - 麻雀管理アプリ",
    page_icon="🀄",
    layout="centered",
)

# --- 1. 戻るボタンを最上部に配置 ---
if st.button("← 対戦一覧へ戻る"):
    st.switch_page("pages/game_list.py")
st.divider()

# セッションチェック
if "title_id" not in st.session_state:
    st.error("タイトルが選択されていません。")
    st.stop()

# データの取得
title_id = st.session_state["title_id"]
title_name = st.session_state["title_name"]
game_id = st.session_state.get("game_id")
game_details = sqlite_db.fetch_game_detail(title_id, game_id)

# プレイヤー名の取得
p_names = [
    st.session_state.get("player1_name"),
    st.session_state.get("player2_name"),
    st.session_state.get("player3_name"),
    st.session_state.get("player4_name"),
]

# 参加しているプレイヤー（名前がある人）のインデックスを特定
active_idxs = [i for i, name in enumerate(p_names) if name]
num_players = len(active_idxs)

st.title(f"🀄 {title_name}")
st.caption(f"対戦ID:{game_id} / {num_players}人麻雀モード")

# --- 2. スコア入力フォーム ---
with st.form("game_detail_form"):
    st.markdown("### 今回のスコア（持ち点）を入力")

    # 4人なら2列、3人なら3列で表示
    cols = st.columns(num_players)
    kaze_inputs = []
    score_inputs = []

    # デフォルトの持ち点設定
    default_score = 25000 if num_players == 4 else 35000

    for i, idx in enumerate(active_idxs):
        with cols[i]:
            name = p_names[idx]
            kaze = st.selectbox(
                f"{name} 風", ["東", "南", "西", "北"], key=f"kaze_{idx}", index=i
            )
            score = st.number_input(
                f"{name}", value=default_score, step=1000, key=f"score_{idx}"
            )
            kaze_inputs.append(kaze)
            score_inputs.append(score)

    submitted = st.form_submit_button("この結果を登録する")

    if submitted:
        # 風の重複チェック
        if len(set(kaze_inputs)) != num_players:
            st.error("風が重複しています。")
        elif score_inputs.count(max(score_inputs)) > 1:
            st.error("同点1位がいます。順位を確定させてください。")
        else:
            # --- 🧮 計算ロジック (4麻/3麻で整合性を取る) ---
            # 4麻: 25k持ち30k返し (オカ20k) -> 合計-20kからトップに+20kで合計0
            # 3麻: 35k持ち40k返し (オカ15k) -> 合計-15kからトップに+15kで合計0
            if num_players == 4:
                kaeshi = 30000
                oka = 20000
            else:
                kaeshi = 40000  # 35k持ち40k返しを想定（合計を0にするため）
                oka = 15000  # (40k-35k)*3 = 15kがトップに集まる

            top_idx_in_active = score_inputs.index(max(score_inputs))

            final_scores = [0, 0, 0, 0]  # 初期化
            final_kazes = [None, None, None, None]

            for i, idx in enumerate(active_idxs):
                # 収支 = 持ち点 - 返し点
                diff = score_inputs[i] - kaeshi
                # 1位ならオカ（余り点）を加算
                if i == top_idx_in_active:
                    diff += oka

                final_scores[idx] = diff
                final_kazes[idx] = kaze_inputs[i]

            # DBへ登録
            renban = max([d["renban"] for d in game_details]) + 1 if game_details else 1
            sqlite_db.insert_game_detail(
                title_id,
                game_id,
                renban,
                final_scores[0],
                final_scores[1],
                final_scores[2],
                final_scores[3],
                final_kazes[0],
                final_kazes[1],
                final_kazes[2],
                final_kazes[3],
            )
            st.success("対戦結果を登録しました。")
            st.rerun()

# --- 3. 結果表示エリア ---
st.markdown("---")
if not game_details:
    st.info("まだ対戦結果が登録されていません。")
else:
    # 削除機能
    with st.expander("🗑️ データの削除"):
        renbans = [d["renban"] for d in game_details]
        target = st.selectbox("削除する回数を選択", renbans, index=len(renbans) - 1)
        if st.button("選択した行を削除", type="primary"):
            sqlite_db.delete_game_detail(title_id, game_id, target)
            st.rerun()

    # 表示用テーブル作成
    table_rows = []
    for d in game_details:
        dt = datetime.datetime.strptime(d["create_date"], "%Y-%m-%d %H:%M:%S")
        row = {"回": d["renban"], "時刻": dt.strftime("%H:%M")}
        for idx in active_idxs:
            name = p_names[idx]
            row[name] = d[f"player{idx+1}_score"]
        table_rows.append(row)

    df = pd.DataFrame(table_rows)

    # 合計行の追加
    total_row = {"回": "合計", "時刻": ""}
    for idx in active_idxs:
        name = p_names[idx]
        total_row[name] = df[name].sum()
    df = pd.concat([pd.DataFrame([total_row]), df], ignore_index=True)

    # 1位の強調スタイル
    def highlight_results(row):
        if row["回"] == "合計":
            return ["font-weight: bold"] * len(row)
        # 参加プレイヤーの列名リスト
        p_cols = [p_names[i] for i in active_idxs]
        max_val = row[p_cols].max()
        return [
            (
                "color: #ff4b4b; font-weight: bold"
                if (col in p_cols and row[col] == max_val)
                else ""
            )
            for col in row.index
        ]

    st.markdown("### 対戦結果（収支）")
    st.dataframe(
        df.style.apply(highlight_results, axis=1),
        use_container_width=True,
        hide_index=True,
    )
