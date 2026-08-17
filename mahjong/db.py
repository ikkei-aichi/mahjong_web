"""Supabase への接続（REST API / PostgREST 経由）。

Postgres へ直結（5432）する構成も検討したが、利用環境のネットワークが
HTTP/HTTPS 以外の外向き通信を遮断していたため REST API 方式に変更した。
すべての通信が 443 番で完結するので、社内ネットワークや公衆Wi-Fiでも動く。

副次的な効果として RLS が実際に適用されるようになった。そのため
**ログインしていないとデータを読み書きできない**。

## クライアントは必ずブラウザセッションごとに作る

旧実装は `st.cache_resource` とモジュールグローバルで
Supabase クライアントを1個だけ作り、プロセス全体で共有していた。
supabase-py はログインセッション(JWT)をクライアント内部に保持するため、
これは「サーバー全体で JWT が1個」という意味になり、次の事故が起きていた:

    * B さんがログインすると、A さんのリクエストまで B の JWT で飛ぶ
    * B さんがログアウトすると A さんも巻き添えでログアウトする
    * Cookie も session_state も持たない新規訪問者が、
      直前にログインした人として自動ログインされる

その結果「同じログイン情報を共有しないと運用できない」状態になっていた。
グループ単位の RLS を入れると、これは「他人のデータを他人の権限で
読み書きする」に直結するため、**セッション分離は RLS より先に必要**。

設定の解決順:
    1. 環境変数 SUPABASE_URL / SUPABASE_KEY
    2. .streamlit/secrets.toml の [supabase] url / publishable_key
"""

from __future__ import annotations

import os

from supabase import Client, ClientOptions, create_client

ENV_URL = "SUPABASE_URL"
ENV_KEY = "SUPABASE_KEY"

# st.session_state に置くキー。ブラウザセッション1つにつきクライアント1つ。
SESSION_CLIENT_KEY = "_supabase_client"

# Streamlit の外（pytest / CLI）で使うときだけのフォールバック
_fallback_client: Client | None = None


class ConfigError(RuntimeError):
    """接続設定が見つからない・不正なときに送出する。"""


def _session_state():
    """Streamlit の session_state。スクリプト実行文脈の外では None。"""
    try:
        import streamlit as st
    except ModuleNotFoundError:
        return None
    try:
        # ScriptRunContext が無いと session_state の参照は例外になる
        st.session_state  # noqa: B018 - 存在確認のための参照
    except Exception:
        return None
    return st.session_state


def _from_streamlit_secrets() -> tuple[str | None, str | None]:
    try:
        import streamlit as st
    except ModuleNotFoundError:
        return None, None
    try:
        section = st.secrets["supabase"]
    except Exception:
        return None, None
    # publishable_key が正式名。古い設定との互換のため anon_key も見る
    key = section.get("publishable_key") or section.get("anon_key")
    return section.get("url"), key


def get_config() -> tuple[str, str]:
    url = os.environ.get(ENV_URL)
    key = os.environ.get(ENV_KEY)
    if not (url and key):
        secret_url, secret_key = _from_streamlit_secrets()
        url = url or secret_url
        key = key or secret_key

    if not url or not key:
        raise ConfigError(
            "Supabase の接続設定が見つかりません。\n"
            ".streamlit/secrets.toml の [supabase] に url と publishable_key を\n"
            "設定してください。雛形は secrets.toml.example にあります。"
        )
    if "<" in url or "<" in key:
        raise ConfigError(
            ".streamlit/secrets.toml がテンプレートのままです。\n"
            "<project-ref> などのプレースホルダを実際の値に置き換えてください。"
        )
    if key.startswith("sb_secret_") or key.startswith("service_role"):
        raise ConfigError(
            "Secret key (sb_secret_...) が設定されています。\n"
            "これは RLS を無視して全データを操作できる管理用キーです。\n"
            "Publishable key (sb_publishable_...) に変更してください。"
        )
    return url, key


def _new_client() -> Client:
    url, key = get_config()
    return create_client(
        url,
        key,
        options=ClientOptions(
            # 自動更新スレッドはブラウザセッションごとに増えてしまうので使わない。
            # 代わりに auth.current_user() が期限切れ前に明示的に更新する。
            auto_refresh_token=False,
            # 保存先は既定のインメモリ（クライアントインスタンス固有）。
            # これによりセッション間でトークンが混ざらない。
            persist_session=True,
        ),
    )


def get_client() -> Client:
    """このブラウザセッション専用の Supabase クライアントを返す。

    Streamlit の外（テストや CLI）ではプロセス内で1つを使い回す。
    """
    state = _session_state()
    if state is None:
        global _fallback_client
        if _fallback_client is None:
            _fallback_client = _new_client()
        return _fallback_client

    client = state.get(SESSION_CLIENT_KEY)
    if client is None:
        client = _new_client()
        state[SESSION_CLIENT_KEY] = client
    return client


def reset_client() -> None:
    """このセッションのクライアントを破棄する（設定変更後やテスト用）。"""
    global _fallback_client
    _fallback_client = None
    state = _session_state()
    if state is not None:
        state.pop(SESSION_CLIENT_KEY, None)
