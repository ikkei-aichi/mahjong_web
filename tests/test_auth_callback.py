"""確認メールの戻り先と、招待リンクの組み立て。

ここが効いていないと、確認メールのリンクが Supabase の Site URL
（既定は localhost）に飛んでエラーになる。実際にそれで詰まった。
"""

from __future__ import annotations

import pytest

from mahjong import auth, db, ui


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(db.ENV_APP_URL, raising=False)


class FakeAuth:
    def __init__(self):
        self.credentials = None

    def sign_up(self, credentials):
        self.credentials = credentials
        return type("R", (), {"session": None, "user": None})()

    def resend(self, credentials):
        self.credentials = credentials
        return None


class FakeClient:
    def __init__(self):
        self.auth = FakeAuth()


@pytest.fixture
def client(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(auth, "get_client", lambda: fake)
    return fake


# --- 公開URLの解決 -----------------------------------------------------------


def test_env_overrides_everything(monkeypatch):
    monkeypatch.setenv(db.ENV_APP_URL, "https://mahjong.example.app/")
    assert db.app_base_url() == "https://mahjong.example.app"


def test_unknown_base_url_is_none():
    # Streamlit の実行文脈の外では st.context.url を読めない。
    # 例外にせず None を返すこと（呼び出し側は options を付けずに続ける）。
    assert db.app_base_url() is None


def test_callback_url_points_at_the_static_receiver(monkeypatch):
    monkeypatch.setenv(db.ENV_APP_URL, "https://mahjong.example.app")
    assert auth.callback_url() == (
        "https://mahjong.example.app/app/static/auth_callback.html"
    )


def test_callback_url_carries_the_invite(monkeypatch):
    monkeypatch.setenv(db.ENV_APP_URL, "https://mahjong.example.app")
    assert auth.callback_url("ABCD2345").endswith("auth_callback.html?invite=ABCD2345")


# --- 登録時に戻り先を渡しているか ---------------------------------------------


def test_sign_up_sends_the_redirect(monkeypatch, client):
    monkeypatch.setenv(db.ENV_APP_URL, "https://mahjong.example.app")
    auth.sign_up("a@example.com", "password")

    options = client.auth.credentials.get("options")
    assert options, "email_redirect_to を渡していない（Site URL が使われて localhost に飛ぶ）"
    assert options["email_redirect_to"].startswith("https://mahjong.example.app/")


def test_sign_up_without_a_known_url_omits_options(client):
    auth.sign_up("a@example.com", "password")
    # 公開URLが分からないときに誤ったURLを渡すと、Supabase 側で弾かれて
    # かえって Site URL に差し戻される。渡さないほうが素直に動く。
    assert "options" not in client.auth.credentials


def test_resend_sends_the_redirect(monkeypatch, client):
    monkeypatch.setenv(db.ENV_APP_URL, "https://mahjong.example.app")
    auth.resend_confirmation("a@example.com")
    assert client.auth.credentials["type"] == "signup"
    assert "email_redirect_to" in client.auth.credentials["options"]


# --- 失効判定（Cookie を消してよいか） ----------------------------------------


def test_network_errors_do_not_count_as_dead():
    """圏外や一時障害で Cookie を消すと、復旧してもログインし直しになる。"""
    assert auth._is_dead_token(OSError("[Errno 11001] getaddrinfo failed")) is False
    assert auth._is_dead_token(TimeoutError()) is False


def test_wrong_api_key_does_not_count_as_dead():
    class ApiError(Exception):
        message = "Invalid API key"

    assert auth._is_dead_token(ApiError()) is False


def test_used_refresh_token_counts_as_dead():
    class ApiError(Exception):
        message = "Invalid Refresh Token: Already Used"

    assert auth._is_dead_token(ApiError()) is True


def test_refresh_token_error_code_counts_as_dead():
    class ApiError(Exception):
        message = "something"
        code = "refresh_token_not_found"

    assert auth._is_dead_token(ApiError()) is True


# --- 招待リンク ---------------------------------------------------------------


def test_invite_link_is_the_app_root(monkeypatch):
    monkeypatch.setenv(db.ENV_APP_URL, "https://mahjong.example.app")
    assert ui.invite_link("ABCD2345") == "https://mahjong.example.app/?invite=ABCD2345"


def test_invite_link_is_none_without_a_known_url():
    # URLが分からないときはコードだけ見せる。壊れたリンクは出さない。
    assert ui.invite_link("ABCD2345") is None
