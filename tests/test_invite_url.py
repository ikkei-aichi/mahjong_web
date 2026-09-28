"""招待リンク（?invite=CODE）の受け取り。

これまでは招待コードを口頭やチャットで伝えて、相手に貼ってもらう必要があった。
URL を開くだけで参加画面まで進めるようにする。

参加そのものはボタンを押したときだけ行う。join_group_by_code は先に使用回数を
加算するので、リンクのプレビューやプリフェッチで開かれると回数を食い潰す。
"""

from __future__ import annotations

import pathlib

import pytest
from streamlit.testing.v1 import AppTest

from tests.fake_backend import install
from tests.test_views import texts

ROOT = pathlib.Path(__file__).resolve().parent.parent
TIMEOUT = 30


@pytest.fixture
def invited(monkeypatch, backend):
    """招待コードが有効な状態にする。"""
    install(monkeypatch, backend)
    from mahjong.repo import groups as groups_repo

    monkeypatch.setattr(
        groups_repo,
        "preview_invite",
        lambda code: {
            "group_name": "テスト麻雀会",
            "group_id": backend.group_id,
            "already_member": False,
            "claimable": [],
        },
    )
    joined: list[str] = []
    monkeypatch.setattr(
        groups_repo,
        "join_group_by_code",
        lambda code, claim_player_id=None, new_name=None: (
            joined.append(code) or backend.group_id
        ),
    )
    return joined


def test_url_prefills_the_code(invited):
    """?invite=CODE で来たら、入力欄が埋まった状態で開く。"""
    app = AppTest.from_file(str(ROOT / "views/onboarding.py"), default_timeout=TIMEOUT)
    app.query_params["invite"] = "ABCD2345"
    app = app.run()

    assert not app.exception, app.exception
    field = next(t for t in app.text_input if t.label == "招待コード")
    assert field.value == "ABCD2345"
    assert "テスト麻雀会" in texts(app)


def test_lowercase_url_is_normalised(invited):
    app = AppTest.from_file(str(ROOT / "views/onboarding.py"), default_timeout=TIMEOUT)
    app.query_params["invite"] = "abcd2345"
    app = app.run()

    field = next(t for t in app.text_input if t.label == "招待コード")
    assert field.value == "ABCD2345"


def test_opening_the_link_does_not_join(invited):
    """開いただけでは参加しない。

    join_group_by_code は先に使用回数を加算するので、リンクのプレビューで
    開かれるだけで「20人まで」の枠を消費してしまう。
    """
    app = AppTest.from_file(str(ROOT / "views/onboarding.py"), default_timeout=TIMEOUT)
    app.query_params["invite"] = "ABCD2345"
    app.run()

    assert invited == [], "リンクを開いただけで参加している"


def test_button_joins(invited):
    app = AppTest.from_file(str(ROOT / "views/onboarding.py"), default_timeout=TIMEOUT)
    app.query_params["invite"] = "ABCD2345"
    app = app.run()

    join = next(b for b in app.button if "参加する" in b.label)
    join.click().run()

    assert invited == ["ABCD2345"]


def test_invite_link_opens_the_join_page_even_with_a_group(monkeypatch, backend):
    """すでに別のグループに所属していても、招待リンクなら参加画面を開く。

    home が既定ページのままだと、リンクを踏んでもホームに着地して
    ?invite= が読まれずに終わる。
    """
    install(monkeypatch, backend)
    from mahjong.repo import groups as groups_repo

    monkeypatch.setattr(
        groups_repo,
        "preview_invite",
        lambda code: {
            "group_name": "別の会",
            "group_id": "other-group",
            "already_member": False,
            "claimable": [],
        },
    )

    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=TIMEOUT)
    app.query_params["group"] = backend.group_id
    app.query_params["invite"] = "ABCD2345"
    app = app.run()

    assert not app.exception, app.exception
    assert "招待コードを入力" in texts(app), "招待リンクなのに参加画面が開いていない"
