"""麻雀管理アプリのエントリポイント。

階層: グループ → 大会 → 開催日 → 対戦（卓）→ 半荘

ログインしないとデータを一切扱えない（RLS がグループ所属で判定するため、
どのグループにも属さないアカウントには何も見えない）。
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="麻雀管理アプリ",
    page_icon="🀄",
    layout="centered",
)

from mahjong import auth, session  # noqa: E402 - set_page_config より後に読む必要がある

auth.require_login()

groups = session.my_groups()
current = session.active_group(groups)
has_group = current is not None

# ★ページは常に全部登録する★
# st.switch_page は「いま st.navigation に渡されているページ」にしか遷移できない。
# グループ未所属のときに onboarding だけを登録していると、
# グループを作った直後の switch_page("views/home.py") が
# 「Could not find page」で落ちる。
# 表示・非表示と既定ページの切り替えだけで出し分ける。
main = "visible" if has_group else "hidden"

if has_group:
    session.sidebar_group_picker(groups, current)

pages = [
    st.Page("views/home.py", title="ホーム", icon="🏠", default=has_group, visibility=main),
    st.Page("views/tournaments.py", title="大会", icon="🏆", visibility=main),
    st.Page("views/stats.py", title="成績", icon="📊", visibility=main),
    st.Page("views/members.py", title="メンバー", icon="👥", visibility=main),
    st.Page("views/settings.py", title="設定", icon="⚙️", visibility=main),
    st.Page(
        "views/onboarding.py",
        title="はじめに" if not has_group else "グループを追加",
        icon="👋",
        default=not has_group,
        visibility="visible" if not has_group else "hidden",
    ),
    # 一覧から辿る画面。サイドバーには出さないが URL では開ける。
    st.Page("views/tournament.py", title="大会の詳細", icon="🗓️", visibility="hidden"),
    st.Page("views/day.py", title="開催日", icon="📅", visibility="hidden"),
    st.Page("views/game.py", title="スコア入力", icon="✏️", visibility="hidden"),
]

auth.sidebar_account()
st.navigation(pages).run()
