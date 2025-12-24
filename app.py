import streamlit as st
import sqlite_db
from datetime import datetime
import os

st.set_page_config(
    page_title="宮田一慶作成！麻雀管理アプリ",
    page_icon="🀄",
    layout="centered",
)

st.title("宮田一慶作成！麻雀管理アプリ")

# ===== タイトル新規作成フォーム =====
st.markdown("### 🆕 新規麻雀タイトル作成")
with st.form("create_title_form"):

    st.markdown("###### 麻雀タイトル名")
    title_name = st.text_input("", label_visibility="collapsed")
    submitted = st.form_submit_button("登録")

    if submitted:
        if title_name.strip() == "":
            st.error("タイトル名を入力してください。")
        else:
            sqlite_db.insert_title(title_name)
            st.success("麻雀タイトルを登録しました。")
            st.rerun()

st.divider()

# ===== 麻雀タイトル一覧 =====
rows = sqlite_db.fetch_titles()

st.markdown("### 🀄 麻雀タイトル一覧")

if not rows:
    st.info("麻雀のタイトルがありません。新規作成してください。")
else:
    for row in rows:
        with st.container(border=True):
            st.subheader(row["title_name"])
            st.caption(
                datetime.strptime(row["create_date"], "%Y-%m-%d %H:%M:%S").strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            )

            if st.button("▶ このタイトルを開く", key=row["title_id"]):
                st.session_state["title_id"] = row["title_id"]
                st.session_state["title_name"] = row["title_name"]
                # ★ 戻り先を保存
                st.switch_page("pages/game_list.py")
