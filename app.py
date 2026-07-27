"""麻雀管理アプリのエントリポイント。

ページ遷移は st.navigation で管理する。詳細ページは visibility="hidden" に
してサイドバーから隠す（旧実装は pages/ の自動登録により、状態を持たない
まま詳細ページへ直接飛べてしまい、そこで落ちていた）。

RLS を有効にしているため、ログインしないとデータを一切扱えない。
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="麻雀管理アプリ",
    page_icon="🀄",
    layout="centered",
)

from mahjong import auth  # noqa: E402 - set_page_config より後に読む必要がある

auth.require_login()
auth.sidebar_account()

pages = [
    st.Page("views/home.py", title="タイトル一覧", icon="🀄", default=True),
    st.Page("views/game_list.py", title="対戦一覧", icon="📋", visibility="hidden"),
    st.Page("views/game_detail.py", title="スコア入力", icon="✏️", visibility="hidden"),
    st.Page("views/stats.py", title="成績", icon="📊", visibility="hidden"),
    st.Page("views/settings.py", title="設定", icon="⚙️", visibility="hidden"),
]

st.navigation(pages).run()
