"""画面の自動更新。

Streamlit は「誰かが操作したとき」しか再実行しない。そのため、同じ卓の別の人が
スコアを入力しても、こちらの画面は開いたままだと古い内容を表示し続ける。

`st.fragment(run_every=...)` で、表示している部分だけを定期的に取り直す。
ページ全体を再実行するわけではないので、往復も増えないし入力途中の値も飛ばない。

使い方:
    @live.auto
    def _body():
        ...取得と表示...
    _body()

安全性について（実機で確認した挙動）:
    * タイマー付きフラグメントが再実行されても、その中のウィジェットの値は
      session_state に残るので消えない
    * 開いている expander も閉じない
    * 別のフラグメントのウィジェット状態も破棄されない
    ただし「取得をフラグメントの外に置いたまま」だと、再実行しても古い変数を
    描き直すだけで意味がない。取得はフラグメントの中に入れること。

更新間隔はサイドバーで変えられる。既定は15秒。
通信量を気にする場合は間隔を延ばすか、オフにする。
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

import streamlit as st

from .timeutil import JST

# 表示名 -> 秒数（None は自動更新しない）
INTERVALS: dict[str, int | None] = {
    "5秒": 5,
    "15秒": 15,
    "30秒": 30,
    "1分": 60,
    "オフ": None,
}

# 短くしすぎると、誰も見ていない画面が延々と取得を続ける。
# 半荘1回の入力にかかる時間を考えると15秒で十分間に合う。
DEFAULT_INTERVAL = "15秒"

_KEY = "_auto_refresh_interval"


def interval_seconds() -> int | None:
    """現在の更新間隔（秒）。オフなら None。"""
    return INTERVALS.get(st.session_state.get(_KEY, DEFAULT_INTERVAL))


def sidebar_control() -> None:
    """サイドバーに更新間隔の切り替えと手動更新ボタンを出す。"""
    with st.sidebar:
        st.divider()
        st.caption("🔄 自動更新")
        # 初期値は session_state 側に置く。index= と key= を両方渡すと
        # 「既定値と Session State の両方で値を指定した」と警告が出る。
        st.session_state.setdefault(_KEY, DEFAULT_INTERVAL)
        st.selectbox(
            "更新間隔",
            list(INTERVALS.keys()),
            key=_KEY,
            label_visibility="collapsed",
        )
        if st.button("今すぐ更新", width="stretch", key="_manual_refresh"):
            st.rerun()
        if interval_seconds() is None:
            st.caption("他の人の入力は自動では反映されません。")


def auto(func: Callable[[], None]) -> Callable[[], None]:
    """表示処理を自動更新の対象にする。

    間隔が「オフ」のときはフラグメントにすらしない。フラグメントにすると
    中のボタンの再実行範囲が変わるため、自動更新しないなら元の挙動のままにする。

    フラグメントの再実行ではページ本体（＝app.py の require_login）が走らない。
    そのままだとアクセストークンの期限が切れたあと自動更新だけが 401 で
    失敗し続けるので、毎回ここでセッションの面倒を見る。
    """
    seconds = interval_seconds()
    if seconds is None:
        return func

    def refreshed() -> None:
        from . import auth

        auth.keep_session_fresh()
        func()

    refreshed.__name__ = func.__name__
    refreshed.__qualname__ = getattr(func, "__qualname__", func.__name__)
    return st.fragment(run_every=seconds)(refreshed)


def updated_caption() -> None:
    """「最終更新 HH:MM:SS」を出す。自動更新の対象内で呼ぶ。"""
    if interval_seconds() is None:
        return
    st.caption(f"🔄 最終更新 {datetime.now(JST).strftime('%H:%M:%S')}")
