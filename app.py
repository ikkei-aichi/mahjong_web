import streamlit as st
import sqlite_db
from datetime import datetime
from pathlib import Path
import os
import pytz

# ===== 1. ページ設定（必ず最初に記述） =====
st.set_page_config(
    page_title="麻雀管理アプリ",
    page_icon="🀄",
    layout="centered",
)

# ===== 2. CSS & JS設定 =====
st.markdown(
    """
    <style>
    /* デプロイボタン非表示 */
    button[title="Deploy"] { display: none; }
    /* カードのデザイン調整 */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        background-color: #f8f9fa;
        transition: 0.3s;
    }
    div[data-testid="stVerticalBlockBorderWrapper"]:hover {
        border-color: #ff4b4b;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# モバイル判定のスクリプト（セッションに保存）
# ※ 簡易的な判定として、Streamlitのネイティブ機能に近い形で処理
if "is_mobile" not in st.session_state:
    st.session_state["is_mobile"] = False

# ===== 3. サイドバー（バックアップ関連） =====
st.sidebar.title("⚙️ 設定・管理")
db_path = Path("app.db")

if db_path.exists():
    with open(db_path, "rb") as f:
        st.sidebar.download_button(
            label="📥 DBをバックアップ",
            data=f,
            file_name=f"mahjong_backup_{datetime.now().strftime('%Y%m%d')}.db",
            mime="application/octet-stream",
            use_container_width=True,
        )

with st.sidebar.expander("📤 データを復元"):
    uploaded_file = st.file_uploader("バックアップを選択", type=["db", "sqlite3"])
    if uploaded_file:
        with open(db_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        st.success("復元完了！ページをリロードしてください。")

# ===== 4. メインコンテンツ =====
st.title("🀄 麻雀管理アプリ")
st.markdown("対戦グループや大会ごとにタイトルを作成して管理できます。")

# タイトル新規作成
with st.container(border=True):
    st.markdown("### 🆕 新規タイトル作成")
    col_in, col_btn = st.columns([3, 1])
    with col_in:
        title_name = st.text_input(
            "タイトル名",
            placeholder="例：2024年 正月大会",
            label_visibility="collapsed",
        )
    with col_btn:
        submitted = st.button("登録", use_container_width=True, type="primary")

    if submitted:
        if title_name.strip():
            sqlite_db.insert_title(title_name)
            st.success("登録しました！")
            st.rerun()
        else:
            st.error("入力してください")

st.divider()

# ===== 5. 麻雀タイトル一覧 =====
st.markdown("### 🀄 登録済みタイトル")
rows = sqlite_db.fetch_titles()

if not rows:
    st.info("タイトルがありません。上のフォームから作成してください。")
else:
    # 2列で見やすく表示
    cols = st.columns(2)
    for i, row in enumerate(rows):
        # 偶数奇数で列を振り分け
        with cols[i % 2]:
            with st.container(border=True):
                st.subheader(row["title_name"])

                # 時刻をJSTに変換
                dt_utc = datetime.strptime(row["create_date"], "%Y-%m-%d %H:%M:%S")
                dt_jst = dt_utc.replace(tzinfo=pytz.UTC).astimezone(
                    pytz.timezone("Asia/Tokyo")
                )
                st.caption(f"📅 {dt_jst.strftime('%Y/%m/%d %H:%M')}")

                if st.button(
                    "▶ 開く", key=f"btn_{row['title_id']}", use_container_width=True
                ):
                    st.session_state["title_id"] = row["title_id"]
                    st.session_state["title_name"] = row["title_name"]
                    st.switch_page("pages/game_list.py")
