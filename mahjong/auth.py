"""Supabase Auth によるログイン。

RLS を有効にしたため、ログインしないとデータを一切読み書きできない。
そのため全ページの先頭で `require_login()` を呼ぶ。

セッションの永続化について:
    st.session_state はブラウザをリロードすると消えるため、そのままだと
    F5 のたびに再ログインになる。これを避けるため refresh token を Cookie に
    保存し、起動時に set_session() でセッションを復元する。

    Cookie は JavaScript から読める（HttpOnly にできない）ので、
    共有PCで使う場合はログアウトを徹底すること。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import streamlit as st

from .db import ConfigError, get_client

# Cookie 名と保持期間。長すぎるとトークン流出時の危険が増すため2週間にする。
_COOKIE_NAME = "mahjong_refresh_token"
_COOKIE_DAYS = 14


class AuthError(RuntimeError):
    """ログイン・登録に失敗したときに送出する。"""


def _controller():
    """Cookie の書き込み用コンポーネント。取得できない環境では None。

    読み取りには使わない。このコンポーネントは JS との往復が必要で、
    スクリプト初回実行時にはまだ値を返せないため。
    """
    try:
        from streamlit_cookies_controller import CookieController
    except ModuleNotFoundError:
        return None
    # key を固定しないとページ遷移のたびに別コンポーネント扱いになる
    return CookieController(key="mahjong_cookies")


# Cookie の書き込みは JS コンポーネントとの往復が必要で、直後に st.rerun() すると
# 送信される前にスクリプトが打ち切られてしまう。そこで「次の実行で書く」よう予約し、
# flush_cookies() が実際の書き込みを行う。
_PENDING_SAVE = "_cookie_save"
_PENDING_CLEAR = "_cookie_clear"


def _save_token(refresh_token: str | None) -> None:
    """refresh token の保存を予約する。実際の書き込みは次の実行時。"""
    if refresh_token:
        st.session_state[_PENDING_SAVE] = refresh_token
        st.session_state.pop(_PENDING_CLEAR, None)


def _clear_token() -> None:
    """Cookie の削除を予約する。"""
    st.session_state[_PENDING_CLEAR] = True
    st.session_state.pop(_PENDING_SAVE, None)


def flush_cookies() -> None:
    """予約された Cookie 操作を実行する。

    require_login() の最後（＝この実行が最後まで走ると確定した時点）で呼ぶ。
    """
    token = st.session_state.pop(_PENDING_SAVE, None)
    should_clear = st.session_state.pop(_PENDING_CLEAR, False)
    if not token and not should_clear:
        return

    controller = _controller()
    if controller is None:
        return
    try:
        if token:
            controller.set(
                _COOKIE_NAME,
                token,
                expires=datetime.now(timezone.utc) + timedelta(days=_COOKIE_DAYS),
                same_site="strict",
            )
        else:
            controller.remove(_COOKIE_NAME)
    except Exception:
        # Cookie が扱えない環境でもログイン自体は継続させる
        # （その場合はリロードで再ログインが必要になる）
        pass


def _read_token() -> str | None:
    """リクエストヘッダから refresh token を読む。

    st.context.cookies はページ読み込み時のリクエストに含まれる Cookie を
    そのまま返すため、コンポーネントの往復を待たずに初回実行で使える。
    """
    try:
        return st.context.cookies.get(_COOKIE_NAME)
    except Exception:
        return None


def current_user() -> dict[str, Any] | None:
    """ログイン中のユーザーを返す。未ログインなら None。

    session_state にセッションが無い場合は Cookie の refresh token から
    復元を試みる（リロード対策）。
    """
    if st.session_state.get("auth_user"):
        return st.session_state["auth_user"]

    client = get_client()

    try:
        session = client.auth.get_session()
    except Exception:
        session = None

    if session is None:
        token = _read_token()
        if not token:
            return None
        try:
            # refresh token から新しいセッションを発行し直す
            session = client.auth.refresh_session(token).session
        except Exception:
            # 期限切れ・失効済み。残っていても意味がないので消す
            _clear_token()
            return None

    if session is None or session.user is None:
        return None

    user = {"id": session.user.id, "email": session.user.email}
    st.session_state["auth_user"] = user
    # 更新された refresh token を保存し直す（ローテーションに追従）
    _save_token(session.refresh_token)
    return user


def sign_in(email: str, password: str) -> dict[str, Any]:
    email = (email or "").strip()
    if not email or not password:
        raise AuthError("メールアドレスとパスワードを入力してください。")
    try:
        result = get_client().auth.sign_in_with_password(
            {"email": email, "password": password}
        )
    except Exception as exc:
        raise AuthError(_friendly(exc)) from exc

    if result.session is None or result.user is None:
        raise AuthError("ログインできませんでした。")

    user = {"id": result.user.id, "email": result.user.email}
    st.session_state["auth_user"] = user
    _save_token(result.session.refresh_token)
    return user


def sign_up(email: str, password: str) -> str:
    """アカウントを登録する。

    Returns:
        利用者に見せるメッセージ。メール確認が有効な場合は確認を促す文面になる。
    """
    email = (email or "").strip()
    if not email or not password:
        raise AuthError("メールアドレスとパスワードを入力してください。")
    if len(password) < 6:
        raise AuthError("パスワードは6文字以上にしてください。")

    try:
        result = get_client().auth.sign_up({"email": email, "password": password})
    except Exception as exc:
        raise AuthError(_friendly(exc)) from exc

    if result.session is not None and result.user is not None:
        # メール確認が無効な設定。そのままログイン状態にする
        st.session_state["auth_user"] = {"id": result.user.id, "email": result.user.email}
        _save_token(result.session.refresh_token)
        return "登録しました。ログインしています。"
    return "確認メールを送信しました。メール内のリンクを開いてから、ログインしてください。"


def sign_out() -> None:
    try:
        get_client().auth.sign_out()
    except Exception:
        # サーバ側で既に失効していても、ローカルの状態は必ず消す
        pass
    st.session_state.pop("auth_user", None)
    _clear_token()


def _friendly(exc: Exception) -> str:
    """Supabase のエラーを日本語の短い説明に変換する。"""
    message = str(getattr(exc, "message", None) or exc)
    lowered = message.lower()
    if "invalid login credentials" in lowered:
        return "メールアドレスまたはパスワードが違います。"
    if "already registered" in lowered or "already been registered" in lowered:
        return "このメールアドレスはすでに登録されています。"
    if "email not confirmed" in lowered:
        return "メール確認が完了していません。確認メールのリンクを開いてください。"
    if "password" in lowered and "short" in lowered:
        return "パスワードが短すぎます。"
    if "rate limit" in lowered or "too many" in lowered:
        return "試行回数が多すぎます。しばらく待ってからお試しください。"
    return message


def login_form() -> None:
    """ログイン／新規登録の画面を描画する。ログイン成功時は再描画する。"""
    st.title("🀄 麻雀管理アプリ")
    st.caption("仲間内でデータを共有するため、ログインが必要です。")

    tab_login, tab_signup = st.tabs(["ログイン", "新規登録"])

    with tab_login:
        with st.form("login_form"):
            email = st.text_input("メールアドレス", key="login_email")
            password = st.text_input("パスワード", type="password", key="login_password")
            if st.form_submit_button("ログイン", use_container_width=True, type="primary"):
                try:
                    sign_in(email, password)
                except AuthError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()

    with tab_signup:
        with st.form("signup_form"):
            email = st.text_input("メールアドレス", key="signup_email")
            password = st.text_input(
                "パスワード", type="password", key="signup_password",
                help="6文字以上",
            )
            if st.form_submit_button("登録", use_container_width=True):
                try:
                    message = sign_up(email, password)
                except AuthError as exc:
                    st.error(str(exc))
                else:
                    st.success(message)
                    if st.session_state.get("auth_user"):
                        st.rerun()


def require_login() -> dict[str, Any]:
    """ログイン必須のページの先頭で呼ぶ。

    未ログインならログイン画面を出して、そのページの処理を止める。

    Returns:
        ログイン中のユーザー情報。
    """
    try:
        user = current_user()
    except ConfigError as exc:
        st.error(str(exc))
        st.stop()

    # st.rerun() は描画をすべて破棄するが st.stop() は破棄しない。
    # ログアウト直後（user is None）の削除も確実に反映させるため、
    # 分岐より前に実行する。
    flush_cookies()

    if user is None:
        login_form()
        st.stop()
    return user


def sidebar_account() -> None:
    """サイドバーにログイン中のユーザーとログアウトボタンを出す。"""
    user = st.session_state.get("auth_user")
    if not user:
        return
    with st.sidebar:
        st.caption(f"👤 {user['email']}")
        if st.button("ログアウト", use_container_width=True):
            sign_out()
            st.rerun()
