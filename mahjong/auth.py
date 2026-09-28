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

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import streamlit as st

from .db import ConfigError, app_base_url, get_client, reset_client
from .errors import is_network_error, describe

# Cookie 名と保持期間。長すぎるとトークン流出時の危険が増すため2週間にする。
_COOKIE_NAME = "mahjong_refresh_token"
_COOKIE_DAYS = 14

# アクセストークンの残り寿命がこれを切ったら先回りして更新する。
# supabase-py の自動更新スレッドはブラウザセッションごとに増えてしまうので
# db.py で無効化しており、代わりにここで更新する。
_REFRESH_MARGIN_SECONDS = 120


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
                # Strict にすると「外部リンクから飛んできた最初の1回」で
                # Cookie が送られない。確認メールのリンクも招待URLも
                # 外部からの遷移なので、その1回でログイン画面に落ちてしまう。
                same_site="lax",
                secure=_is_https() or None,
            )
        else:
            controller.remove(_COOKIE_NAME, same_site="lax")
    except Exception:
        # Cookie が扱えない環境でもログイン自体は継続させる
        # （その場合はリロードで再ログインが必要になる）
        pass


def _is_https() -> bool:
    """いま https で配信されているか。Cookie の Secure 属性に使う。

    localhost（http）で Secure を付けるとブラウザに捨てられるため、
    実際の配信方式を見て決める。
    """
    base = app_base_url()
    return bool(base and base.startswith("https://"))


def _read_token() -> str | None:
    """リクエストヘッダから refresh token を読む。

    st.context.cookies はページ読み込み時のリクエストに含まれる Cookie を
    そのまま返すため、コンポーネントの往復を待たずに初回実行で使える。
    """
    try:
        return st.context.cookies.get(_COOKIE_NAME)
    except Exception:
        return None


def _is_dead_token(exc: Exception) -> bool:
    """この refresh token がもう使えないと断定できるエラーか。

    判定はトークンそのものの話に限る。通信断や APIキーの取り違えまで
    「失効」とみなすと、設定を直せば戻れたはずのログイン状態まで
    Cookie ごと捨ててしまう（例: "Invalid API key" は対象外）。
    """
    if is_network_error(exc):
        return False
    code = str(getattr(exc, "code", "") or "").lower()
    if code:
        return "refresh_token" in code or code in ("session_not_found", "session_expired")
    message = str(getattr(exc, "message", None) or exc).lower()
    return "refresh token" in message


def _refresh_if_expiring(client) -> bool:
    """アクセストークンの期限が近ければ更新する。

    db.py で自動更新を切っているため、ここが唯一の更新経路。
    更新しないまま1時間ほど使い続けると PostgREST が 401 を返し始める。

    Returns:
        セッションが有効（更新済みを含む）なら True。
    """
    try:
        session = client.auth.get_session()
    except Exception:
        return False
    if session is None:
        return False

    expires_at = getattr(session, "expires_at", None)
    if expires_at is None or expires_at - time.time() > _REFRESH_MARGIN_SECONDS:
        return True

    try:
        refreshed = client.auth.refresh_session().session
    except Exception as exc:
        # 通信が一瞬切れただけでログアウト扱いにしない。
        # 期限まではまだ _REFRESH_MARGIN_SECONDS の猶予があるので、
        # 今回の実行はそのまま続け、次の実行で取り直す。
        if is_network_error(exc):
            return True
        return False
    if refreshed is None:
        return False
    # ローテーションされた refresh token を Cookie に反映する
    _save_token(refreshed.refresh_token)
    return True


def current_user() -> dict[str, Any] | None:
    """ログイン中のユーザーを返す。未ログインなら None。

    session_state にセッションが無い場合は Cookie の refresh token から
    復元を試みる（リロード対策）。

    クライアントはブラウザセッション専用（db.get_client 参照）なので、
    ここで得られるセッションは必ず「この画面を見ている本人」のもの。
    """
    client = get_client()

    if st.session_state.get("auth_user"):
        if _refresh_if_expiring(client):
            return st.session_state["auth_user"]
        # セッションが失効した。Cookie からの復元をこの下で試みる。
        st.session_state.pop("auth_user", None)

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
        except Exception as exc:
            # 消してよいのは「このトークンはもう使えない」と断定できるときだけ。
            # 圏外やサーバの一時障害で消すと、復旧してもログインし直しになる。
            if _is_dead_token(exc):
                _clear_token()
            return None

    if session is None or session.user is None:
        return None

    user = {"id": session.user.id, "email": session.user.email}
    st.session_state["auth_user"] = user
    # 更新された refresh token を保存し直す（ローテーションに追従）
    _save_token(session.refresh_token)
    return user


# --- メールのリンクからの戻り -------------------------------------------------
# Supabase は既定で "#access_token=..." の形で戻してくる。"#" 以降はサーバへ
# 送られず、さらに Streamlit は読み込み直後にそれを URL から消してしまうので、
# Streamlit のページで直接受け取ることはできない。
#
# そこで戻り先を static/auth_callback.html（Streamlit を通らない静的HTML）にして、
# そこで refresh token を Cookie に入れてからアプリへ送る。アプリは起動時に
# Cookie からセッションを復元するので、リンクを開くだけでログインまで終わる。
# メールのテンプレートを書き換える必要は無い（編集にはカスタムSMTPが要るため）。

CALLBACK_PATH = "/app/static/auth_callback.html"

# 受け取り口が使うクエリパラメータ。処理したら URL から消す。
# ?token_hash= と ?code= は、テンプレートを変えた場合や OAuth を足した場合に届く形。
_CALLBACK_PARAMS = ("confirmed", "auth_error", "token_hash", "type", "code")

_CONFIRMED = "_auth_just_confirmed"
_MESSAGE = "_auth_callback_message"


def callback_url(invite_code: str | None = None) -> str | None:
    """確認メールのリンクの戻り先。公開URLが分からなければ None。"""
    base = app_base_url()
    if not base:
        return None
    url = base + CALLBACK_PATH
    return f"{url}?invite={invite_code}" if invite_code else url


def _drop_callback_params() -> None:
    # clear() だと session.py が使う ?group= まで消えるので、個別に消す
    for name in _CALLBACK_PARAMS:
        if name in st.query_params:
            del st.query_params[name]


def consume_auth_callback() -> None:
    """メールのリンクから戻ってきた合図を処理する。

    app.py で require_login() より前に呼ぶ。ここで例外を投げると
    ログイン画面にすら到達できなくなるため、何があっても握り潰して
    通常のログイン画面に落とす。
    """
    try:
        _consume_auth_callback()
    except ConfigError:
        raise
    except Exception:
        _drop_callback_params()


def _consume_auth_callback() -> None:
    params = st.query_params

    confirmed = params.get("confirmed")
    if confirmed is not None:
        _drop_callback_params()
        if confirmed == "1":
            # refresh token は static/auth_callback.html が既に Cookie に入れている。
            # ログインできたかは current_user() の結果を見て決まるので、
            # ここでは印だけ付けて文面は require_login() で決める。
            st.session_state[_CONFIRMED] = True
        st.rerun()

    failed = params.get("auth_error")
    if failed:
        _drop_callback_params()
        st.session_state[_MESSAGE] = (
            "error",
            "確認リンクを処理できませんでした（"
            + str(failed).replace("+", " ")
            + "）。期限切れの可能性があります。確認メールを再送してください。",
        )
        st.rerun()

    # テンプレートを token_hash 方式に変えた場合、または OAuth を足した場合。
    token_hash, code = params.get("token_hash"), params.get("code")
    if not token_hash and not code:
        return

    client = get_client()
    try:
        if token_hash:
            session = client.auth.verify_otp(
                {"token_hash": token_hash, "type": params.get("type") or "email"}
            ).session
        else:
            session = client.auth.exchange_code_for_session({"auth_code": code}).session
    except Exception as exc:
        _drop_callback_params()
        st.session_state[_MESSAGE] = ("error", _friendly(exc))
        st.rerun()

    if session is not None and session.user is not None:
        st.session_state["auth_user"] = {
            "id": session.user.id,
            "email": session.user.email,
        }
        _save_token(session.refresh_token)
        st.session_state[_CONFIRMED] = True

    _drop_callback_params()
    st.rerun()


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


def sign_up(email: str, password: str, invite_code: str | None = None) -> str:
    """アカウントを登録する。

    確認メールのリンクの戻り先を明示的に渡す。渡さないと Supabase は
    ダッシュボードの Site URL を使い、既定のままだと localhost に飛ぶ。

    Args:
        invite_code: 招待リンク経由なら、確認後もそのまま参加できるよう引き継ぐ。

    Returns:
        利用者に見せるメッセージ。メール確認が有効な場合は確認を促す文面になる。
    """
    email = (email or "").strip()
    if not email or not password:
        raise AuthError("メールアドレスとパスワードを入力してください。")
    if len(password) < 6:
        raise AuthError("パスワードは6文字以上にしてください。")

    credentials: dict[str, Any] = {"email": email, "password": password}
    redirect = callback_url(invite_code)
    if redirect:
        credentials["options"] = {"email_redirect_to": redirect}

    try:
        result = get_client().auth.sign_up(credentials)
    except Exception as exc:
        raise AuthError(_friendly(exc)) from exc

    if result.session is not None and result.user is not None:
        # メール確認が無効な設定。そのままログイン状態にする
        st.session_state["auth_user"] = {"id": result.user.id, "email": result.user.email}
        _save_token(result.session.refresh_token)
        return "登録しました。ログインしています。"
    return (
        f"{email} に確認メールを送信しました。\n\n"
        "メール内のリンクを開くと、この画面に戻ってそのままログインできます。"
    )


def resend_confirmation(email: str, invite_code: str | None = None) -> str:
    """確認メールを再送する。リンクの期限切れ用。"""
    email = (email or "").strip()
    if not email:
        raise AuthError("メールアドレスを入力してください。")

    credentials: dict[str, Any] = {"type": "signup", "email": email}
    redirect = callback_url(invite_code)
    if redirect:
        credentials["options"] = {"email_redirect_to": redirect}

    try:
        get_client().auth.resend(credentials)
    except Exception as exc:
        raise AuthError(_friendly(exc)) from exc
    return f"{email} に確認メールを再送しました。"


def sign_out() -> None:
    try:
        get_client().auth.sign_out()
    except Exception:
        # サーバ側で既に失効していても、ローカルの状態は必ず消す
        pass
    st.session_state.pop("auth_user", None)
    _clear_token()
    # クライアントごと捨てて、内部に残ったトークンを確実に消す。
    # このクライアントはこのブラウザセッション専用なので、他の利用者に影響しない。
    reset_client()


def _friendly(exc: Exception) -> str:
    """Supabase のエラーを日本語の短い説明に変換する。"""
    # 圏外・DNS失敗・タイムアウトは errors 側の判定に任せる。
    # ここを通さないと「[Errno 11001] getaddrinfo failed」がそのまま画面に出る。
    if is_network_error(exc):
        return str(describe(exc))

    message = str(getattr(exc, "message", None) or exc)
    lowered = message.lower()
    if "signups are disabled" in lowered or "signup is disabled" in lowered:
        return (
            "このプロジェクトではメールアドレスでの登録が無効になっています。\n"
            "Supabase ダッシュボード → Authentication → Sign In / Providers から "
            "「Email」を有効にしてください。"
        )
    if "email logins are disabled" in lowered or "email provider" in lowered:
        return (
            "このプロジェクトではメールアドレスでのログインが無効になっています。\n"
            "Supabase ダッシュボード → Authentication → Sign In / Providers から "
            "「Email」を有効にしてください。"
        )
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


def _show_callback_message() -> None:
    """メールのリンクからの戻りで出す文面を1回だけ表示する。"""
    message = st.session_state.pop(_MESSAGE, None)
    if not message:
        return
    kind, text = message
    (st.success if kind == "success" else st.error)(text)


def login_form() -> None:
    """ログイン／新規登録の画面を描画する。ログイン成功時は再描画する。"""
    st.title("🀄 麻雀管理アプリ")
    st.caption("仲間内でデータを共有するため、ログインが必要です。")

    _show_callback_message()

    # 招待リンク経由なら、確認メールの戻り先にも引き継いで参加まで繋げる
    invite_code = st.query_params.get("invite") or None
    if invite_code:
        st.info("🎟️ 招待リンクから来ています。登録／ログインすると参加画面に進みます。")

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
                    message = sign_up(email, password, invite_code)
                except AuthError as exc:
                    st.error(str(exc))
                else:
                    st.success(message)
                    st.session_state["_signup_email"] = email.strip()
                    if st.session_state.get("auth_user"):
                        st.rerun()

    with st.expander("確認メールが届かない / リンクが期限切れのとき"):
        st.caption(
            "確認メールのリンクは時間が経つと使えなくなります。"
            "同じメールアドレス宛に送り直せます。"
        )
        resend_email = st.text_input(
            "メールアドレス",
            value=st.session_state.get("_signup_email", ""),
            key="resend_email",
        )
        if st.button("確認メールを再送", width="stretch"):
            try:
                st.success(resend_confirmation(resend_email, invite_code))
            except AuthError as exc:
                st.error(str(exc))


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
        if st.session_state.pop(_CONFIRMED, False):
            # Cookie からの復元に失敗した場合。確認自体は終わっているので、
            # 「もう一度登録して」ではなく「そのままログインして」と案内する。
            st.session_state[_MESSAGE] = (
                "error",
                "メールアドレスの確認は完了しました。"
                "お手数ですが、下からログインしてください。",
            )
        login_form()
        st.stop()

    if st.session_state.pop(_CONFIRMED, False):
        from . import ui

        ui.flash("メールアドレスを確認しました。ログインしました。")
    pending = st.session_state.pop(_MESSAGE, None)
    if pending:
        from . import ui

        ui.flash(pending[1], "error" if pending[0] == "error" else "success")
    return user


def keep_session_fresh() -> None:
    """アクセストークンの期限切れを避ける。自動更新の実行前に呼ぶ。

    フラグメントの再実行では app.py の require_login() が走らないため、
    そのままだと1時間ほどでアクセストークンが切れ、自動更新だけが
    401 で失敗し続けるようになる。
    """
    if not st.session_state.get("auth_user"):
        return
    try:
        _refresh_if_expiring(get_client())
    except Exception:
        return
    # ローテーションされた refresh token を Cookie に書き戻す。
    # ここで書かないと、次にリロードしたときに消費済みのトークンを使ってしまう。
    flush_cookies()


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
