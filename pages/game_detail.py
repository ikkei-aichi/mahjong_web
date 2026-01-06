import datetime
import streamlit as st
import sqlite_db
import pandas as pd
import math

st.set_page_config(
    page_title="対戦詳細 - 麻雀管理アプリ",
    page_icon="🀄",
    layout="centered",
)

# --- 1. 戻るボタン ---
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

p_names = [
    st.session_state.get("player1_name"),
    st.session_state.get("player2_name"),
    st.session_state.get("player3_name"),
    st.session_state.get("player4_name"),
]

active_idxs = [i for i, name in enumerate(p_names) if name]
num_players = len(active_idxs)

st.title(f"🀄 {title_name}")
st.caption(f"対戦ID:{game_id} / {num_players}人麻雀モード")

# 風の優先順位を定義（東 > 南 > 西 > 北）
KAZE_ORDER = {"東": 0, "南": 1, "西": 2, "北": 3}

# --- 2. スコア入力フォーム ---
with st.form("game_detail_form"):
    st.markdown("### 今回のスコア（持ち点）を入力")

    cols = st.columns(num_players)
    kaze_inputs = []
    score_inputs = []

    default_score = 25000 if num_players == 4 else 35000

    for i, idx in enumerate(active_idxs):
        with cols[i]:
            name = p_names[idx]
            kaze = st.selectbox(
                f"{name} 風", ["東", "南", "西", "北"], key=f"kaze_{idx}", index=i
            )
            score = st.number_input(
                f"{name}", value=default_score, step=100, key=f"score_{idx}"
            )
            kaze_inputs.append(kaze)
            score_inputs.append(score)

    submitted = st.form_submit_button("この結果を登録する", use_container_width=True)

    if submitted:
        if len(set(kaze_inputs)) != num_players:
            st.error("風が重複しています。正しく選択してください。")
        else:
            kaeshi = 30000 if num_players == 4 else 40000
            score_data = []
            for i, s in enumerate(score_inputs):
                score_data.append(
                    {
                        "active_idx": i,
                        "score": s,
                        "kaze_val": KAZE_ORDER[kaze_inputs[i]],
                    }
                )

            sorted_data = sorted(score_data, key=lambda x: (-x["score"], x["kaze_val"]))
            top_active_idx = sorted_data[0]["active_idx"]
            final_pts = [0] * num_players
            total_pts_except_top = 0

            for rank in range(1, num_players):
                idx = sorted_data[rank]["active_idx"]
                raw_pt = (score_inputs[idx] - kaeshi) / 1000
                rounded_pt = int(math.ceil(raw_pt))
                final_pts[idx] = rounded_pt
                total_pts_except_top += rounded_pt

            final_pts[top_active_idx] = -total_pts_except_top

            final_scores = [0, 0, 0, 0]
            final_kazes = [None, None, None, None]
            for i, idx in enumerate(active_idxs):
                final_scores[idx] = final_pts[i]
                final_kazes[idx] = kaze_inputs[i]

            sqlite_db.insert_game_detail(
                title_id,
                game_id,
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
st.markdown("### 対戦結果（ポイント）")

if not game_details:
    st.info("データ無し（まだ対戦結果が登録されていません）")
else:
    # --- データの削除機能 (復活) ---
    with st.expander("🗑️ 特定の回を削除する"):
        renbans = [d["renban"] for d in game_details]
        target = st.selectbox("削除する「回」を選択", renbans, index=len(renbans) - 1)
        if st.button("選択した行を削除", type="primary", use_container_width=True):
            sqlite_db.delete_game_detail(title_id, game_id, target)
            st.success(f"{target}回目を削除しました")
            st.rerun()

    table_rows = []
    for d in game_details:
        dt = datetime.datetime.strptime(
            d["create_date"], "%Y-%m-%d %H:%M:%S"
        ) + datetime.timedelta(hours=9)
        row = {"回": str(d["renban"]), "時刻": dt.strftime("%H:%M")}
        for idx in active_idxs:
            name = p_names[idx]
            val = d.get(f"player{idx+1}_score", 0)
            kaze = d.get(f"player{idx+1}_kaze", "-")
            row[name] = f"{val:+}({kaze})"
        table_rows.append(row)

    df = pd.DataFrame(table_rows)

    # 合計行の作成
    total_row = {"回": "合計", "時刻": ""}
    for idx in active_idxs:
        name = p_names[idx]
        total_pts = sum([d.get(f"player{idx+1}_score", 0) for d in game_details])
        total_row[name] = f"{total_pts:+}"

    # 合計を一番上に配置
    df = pd.concat([pd.DataFrame([total_row]), df], ignore_index=True)

    # --- スタイル適用 ---
    def make_pretty(styler):
        styler.hide(axis="index")  # インデックスを隠す
        styler.set_properties(
            **{"text-align": "right", "font-family": "monospace"}
        )  # 右寄せ&等幅
        styler.set_table_styles(
            [{"selector": "th", "props": [("text-align", "right")]}]
        )  # ヘッダー右寄せ
        return styler

    def style_row(row):
        styles = ["text-align: right"] * len(row)
        if row["回"] == "合計":
            return [
                s + "; font-weight: bold; background-color: #f0f2f6" for s in styles
            ]

        # 数値比較で1位を赤太字に
        valid_cols = [p_names[i] for i in active_idxs]
        vals = []
        for col in valid_cols:
            try:
                vals.append(int(row[col].split(" ")[0]))
            except:
                vals.append(-999999)

        if vals:
            max_v = max(vals)
            for i, col_name in enumerate(row.index):
                if col_name in valid_cols:
                    try:
                        v = int(row[col_name].split(" ")[0])
                        if v == max_v:
                            styles[i] += "; color: #ff4b4b; font-weight: bold"
                    except:
                        pass
        return styles

    # スタイルを適用して表示
    st.write(
        df.style.pipe(make_pretty).apply(style_row, axis=1).to_html(),
        unsafe_allow_html=True,
    )
