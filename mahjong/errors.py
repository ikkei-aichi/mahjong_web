"""データアクセスで起きる例外を、画面に出せる日本語に正規化する。

旧実装は `postgrest.exceptions.APIError` しか捕まえておらず、
`httpx` の接続エラー・タイムアウトがそのまま画面まで飛んで
生のトレースバックが表示されていた。卓上でスマホの電波が切れる、
というこのアプリで最も起きやすい失敗がそれに当たる。

ここを1か所に集約し、repo 層のすべての呼び出しを `call()` で包む。
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar

T = TypeVar("T")


class AppError(RuntimeError):
    """利用者にそのまま見せてよいエラー。"""


class NetworkError(AppError):
    """通信できなかった。再試行すれば直る可能性がある。"""


class AuthExpired(AppError):
    """ログインセッションが切れた。再ログインが必要。"""


class PermissionDenied(AppError):
    """RLS または列権限で拒否された。"""


class SchemaOutOfDate(AppError):
    """PostgREST のスキーマキャッシュが古い。"""


# PostgREST / Postgres のエラーコード
_PGRST_MISSING = ("PGRST202", "PGRST205")  # 関数・テーブルが見つからない
_PG_UNIQUE = "23505"
_PG_FK = "23503"
_PG_NOT_NULL = "23502"
_PG_CHECK = "23514"
_PG_PERMISSION = "42501"
_PG_RAISE = "P0001"  # plpgsql の RAISE EXCEPTION（自前の日本語メッセージ）


def _attr(exc: Exception, name: str) -> str:
    value = getattr(exc, name, None)
    if value is None and isinstance(exc, dict):
        value = exc.get(name)
    return str(value) if value else ""


def is_network_error(exc: Exception) -> bool:
    """httpx を import せずに判定する（未導入環境でも壊れないように）。"""
    for klass in type(exc).__mro__:
        module = getattr(klass, "__module__", "") or ""
        if module.startswith(("httpx", "httpcore", "socket", "ssl")):
            return True
    return isinstance(exc, (ConnectionError, TimeoutError, OSError))


def describe(exc: Exception) -> AppError:
    """例外を、画面に出せる AppError に変換する。"""
    if isinstance(exc, AppError):
        return exc

    if is_network_error(exc):
        return NetworkError(
            "サーバーに接続できませんでした。電波の状態を確認して、もう一度お試しください。"
        )

    code = _attr(exc, "code")
    message = _attr(exc, "message") or str(exc)
    details = _attr(exc, "details")
    hint = _attr(exc, "hint")
    lowered = f"{code} {message}".lower()

    if code in _PGRST_MISSING:
        return SchemaOutOfDate(
            "データベースの定義が最新ではありません。"
            "マイグレーションを適用したあと、少し待ってから再読み込みしてください。"
        )
    if code == _PG_PERMISSION or "permission denied" in lowered:
        return PermissionDenied(
            "この操作を行う権限がありません。グループの管理者に依頼してください。"
        )
    if code.startswith("PGRST301") or "jwt" in lowered or "401" in code:
        return AuthExpired("ログインの有効期限が切れました。もう一度ログインしてください。")
    if code == _PG_RAISE:
        # plpgsql 側で用意した日本語メッセージをそのまま見せる
        return AppError(message)
    if code == _PG_UNIQUE:
        return AppError(_unique_message(message, details))
    if code == _PG_FK:
        return AppError(
            "他のデータから参照されているため、この操作はできません。"
            "先に関連する記録を整理してください。"
        )
    if code == _PG_NOT_NULL:
        return AppError("必須項目が入力されていません。")
    if code == _PG_CHECK:
        return AppError(f"入力値が条件を満たしていません。{details or message}")

    parts = [p for p in (message, details, hint) if p]
    return AppError(" / ".join(parts) if parts else "不明なエラーが発生しました。")


def _unique_message(message: str, details: str) -> str:
    text = f"{message} {details}"
    if "players_group_name_uniq" in text:
        return "同じ名前の参加者がすでにいます。別の名前にしてください。"
    if "players_group_user_uniq" in text:
        return "このアカウントはすでにこのグループに参加しています。"
    if "tournament_days_uniq" in text:
        return "同じ日付の開催日がすでに登録されています。"
    if "group_invites_code_key" in text:
        return "招待コードが重複しました。もう一度お試しください。"
    return "すでに同じデータが登録されています。"


def call(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """データアクセス呼び出しを包み、例外を AppError に正規化する。"""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - ここで一元的に翻訳する
        raise describe(exc) from exc
