"""ログイン失敗時のメッセージ。

卓上でスマホの電波が切れたときに `[Errno 11001] getaddrinfo failed` のような
生のエラーを見せないこと。
"""

from __future__ import annotations

import pytest

from mahjong.auth import _friendly


class FakeAuthError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def test_dns_failure_is_translated():
    exc = OSError("[Errno 11001] getaddrinfo failed")
    message = _friendly(exc)
    assert "getaddrinfo" not in message
    assert "接続" in message


def test_timeout_is_translated():
    assert "接続" in _friendly(TimeoutError())


def test_httpx_connect_error_is_translated():
    class ConnectError(Exception):
        pass

    ConnectError.__module__ = "httpx"
    assert "接続" in _friendly(ConnectError("nope"))


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Invalid login credentials", "メールアドレスまたはパスワードが違います。"),
        ("User already registered", "このメールアドレスはすでに登録されています。"),
        ("Email not confirmed", "メール確認が完了していません。確認メールのリンクを開いてください。"),
        ("Rate limit exceeded", "試行回数が多すぎます。しばらく待ってからお試しください。"),
    ],
)
def test_known_supabase_errors_are_japanese(raw, expected):
    assert _friendly(FakeAuthError(raw)) == expected


@pytest.mark.parametrize(
    "raw",
    ["Email signups are disabled", "Email logins are disabled"],
)
def test_disabled_email_provider_explains_where_to_fix_it(raw):
    """アプリの不具合ではなく Supabase 側の設定なので、直す場所まで案内する。"""
    message = _friendly(FakeAuthError(raw))
    assert "Email" in message and "Providers" in message


def test_unknown_error_falls_back_to_the_original_message():
    assert _friendly(FakeAuthError("something odd")) == "something odd"
