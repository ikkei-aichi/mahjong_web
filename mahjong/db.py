"""Supabase への接続（REST API / PostgREST 経由）。

Postgres へ直結（5432）する構成も検討したが、利用環境のネットワークが
HTTP/HTTPS 以外の外向き通信を遮断していたため REST API 方式に変更した。
すべての通信が 443 番で完結するので、社内ネットワークや公衆Wi-Fiでも動く。

副次的な効果として RLS が実際に適用されるようになった。そのため
**ログインしていないとデータを読み書きできない**（002_views_rls_rpc.sql 参照）。

設定の解決順:
    1. 環境変数 SUPABASE_URL / SUPABASE_KEY
    2. .streamlit/secrets.toml の [supabase] url / publishable_key
"""

from __future__ import annotations

import os
from typing import Any

from supabase import Client, create_client

ENV_URL = "SUPABASE_URL"
ENV_KEY = "SUPABASE_KEY"

_client: Client | None = None


class ConfigError(RuntimeError):
    """接続設定が見つからない・不正なときに送出する。"""


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


def get_client() -> Client:
    """プロセス内で共有する Supabase クライアントを返す。

    Streamlit 上では st.cache_resource に載せ、再描画のたびに
    クライアントを作り直さないようにする。
    """
    global _client
    if _client is not None:
        return _client

    try:
        import streamlit as st
    except ModuleNotFoundError:
        url, key = get_config()
        _client = create_client(url, key)
        return _client

    @st.cache_resource(show_spinner=False)
    def _cached() -> Client:
        url, key = get_config()
        return create_client(url, key)

    _client = _cached()
    return _client


def reset_client() -> None:
    """キャッシュしたクライアントを破棄する（設定変更後やテスト用）。"""
    global _client
    _client = None


def check_connection() -> dict[str, Any]:
    """接続確認。認証不要のヘルスチェックとして auth 設定を叩く。

    Returns:
        {"reachable": bool, "authenticated": bool, "detail": str}
    """
    client = get_client()
    result: dict[str, Any] = {"reachable": False, "authenticated": False, "detail": ""}
    try:
        session = client.auth.get_session()
        result["reachable"] = True
        result["authenticated"] = session is not None
        result["detail"] = "ログイン済み" if session else "未ログイン"
    except Exception as exc:  # noqa: BLE001 - 呼び出し側に理由を見せたい
        result["detail"] = f"{type(exc).__name__}: {exc}"
    return result
