"""エラー正規化のテスト。

通信断で生のトレースバックが出ていた問題（卓上のスマホで最も起きやすい失敗）の回帰テスト。
"""

from __future__ import annotations

import pytest

from mahjong.errors import (
    AppError,
    AuthExpired,
    NetworkError,
    PermissionDenied,
    SchemaOutOfDate,
    call,
    describe,
)


class FakeAPIError(Exception):
    """postgrest.exceptions.APIError の形だけ真似たもの。"""

    def __init__(self, code="", message="", details="", hint=""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.hint = hint


def test_connection_error_becomes_network_error():
    assert isinstance(describe(ConnectionError("boom")), NetworkError)


def test_timeout_becomes_network_error():
    assert isinstance(describe(TimeoutError()), NetworkError)


def test_httpx_style_error_is_detected_by_module():
    """httpx を import せずにモジュール名で判定している。"""

    class ConnectError(Exception):
        pass

    ConnectError.__module__ = "httpx"
    assert isinstance(describe(ConnectError("failed")), NetworkError)


def test_permission_denied_is_explained():
    err = describe(FakeAPIError(code="42501", message="permission denied for column role"))
    assert isinstance(err, PermissionDenied)
    assert "権限" in str(err)


def test_stale_schema_cache_is_explained():
    err = describe(FakeAPIError(code="PGRST205", message="Could not find the table"))
    assert isinstance(err, SchemaOutOfDate)


def test_expired_jwt_asks_for_relogin():
    err = describe(FakeAPIError(code="PGRST301", message="JWT expired"))
    assert isinstance(err, AuthExpired)


def test_plpgsql_message_is_passed_through():
    """RPC 側で用意した日本語メッセージはそのまま見せる。"""
    err = describe(FakeAPIError(code="P0001", message="3人以上のプレイヤーを選択してください。"))
    assert str(err) == "3人以上のプレイヤーを選択してください。"


@pytest.mark.parametrize(
    "constraint, expected",
    [
        ("players_group_name_uniq", "同じ名前の参加者"),
        ("players_group_user_uniq", "すでにこのグループに参加"),
        ("tournament_days_uniq", "同じ日付の開催日"),
    ],
)
def test_unique_violations_get_specific_messages(constraint, expected):
    err = describe(
        FakeAPIError(code="23505", message="duplicate key", details=f'constraint "{constraint}"')
    )
    assert expected in str(err)


def test_foreign_key_violation_is_explained():
    err = describe(FakeAPIError(code="23503", message="violates foreign key constraint"))
    assert "参照されている" in str(err)


def test_unknown_error_still_returns_app_error():
    err = describe(ValueError("なにか変なこと"))
    assert isinstance(err, AppError)
    assert "なにか変なこと" in str(err)


def test_app_error_passes_through_unchanged():
    original = PermissionDenied("だめです")
    assert describe(original) is original


def test_call_returns_value_on_success():
    assert call(lambda x: x * 2, 21) == 42


def test_call_translates_exception():
    def boom():
        raise FakeAPIError(code="42501", message="permission denied")

    with pytest.raises(PermissionDenied):
        call(boom)
