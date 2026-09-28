"""自動更新（mahjong/live.py）。

他の人がスコアを入れたのに画面が古いまま、という状態をなくすための仕組み。
ここで押さえたいのは2点。

    * 取得ごとフラグメントの中に入っていること
      （外に置いたままだと、再実行しても古い変数を描き直すだけで意味がない）
    * フラグメントにしたことで、その部分が描画されなくなっていないこと
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from mahjong import live
from tests.test_views import run, texts

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_off_returns_the_function_itself(monkeypatch):
    """「オフ」のときはフラグメントにしない。

    フラグメントにすると中のボタンの再実行範囲が変わる。自動更新しないなら
    元の挙動のままにしておきたい。
    """
    import streamlit as st

    monkeypatch.setitem(st.session_state, "_auto_refresh_interval", "オフ")

    def body() -> None:
        pass

    assert live.auto(body) is body


def test_interval_seconds_matches_the_label():
    assert live.INTERVALS[live.DEFAULT_INTERVAL] is not None
    assert live.INTERVALS["オフ"] is None


@pytest.mark.parametrize(
    "path,params,expected",
    [
        ("views/home.py", {}, "このグループの通算成績"),
        ("views/day.py", {"day": True, "tournament": True}, "この日の卓"),
        ("views/game.py", {"game": True}, "記録"),
    ],
)
def test_auto_refreshed_blocks_are_actually_rendered(
    monkeypatch, backend, path, params, expected
):
    """自動更新の対象にした部分が、ちゃんと描画されていること。

    取得をフラグメントの中へ移したときに例外を握り潰す書き方をしたので、
    通信に失敗した扱いになって中身が消えていても、
    「例外が出ない」だけのテストでは気づけない。
    """
    resolved = {"group": backend.group_id}
    if params.get("tournament"):
        resolved["tournament"] = backend.tournament_id
    if params.get("day"):
        resolved["day"] = backend.day_id
    if params.get("game"):
        resolved["game"] = backend.game_id

    app = run(path, monkeypatch, backend, **resolved)
    assert not app.exception
    body = texts(app)
    assert expected in body
    assert "取得できませんでした" not in body


def test_views_fetch_inside_the_refreshed_function():
    """自動更新する関数の中で取得していること。

    `@live.auto` を貼っただけで取得がモジュール直下に残っていると、
    タイマーで再実行されても古い内容を描き直すだけになる。
    見た目では絶対に分からないので静的に見る。
    """
    targets = {
        "views/home.py": "overall_stats",
        "views/day.py": "game_list",
        "views/game.py": "entry_area",
        "views/tournament.py": "tournament_stats",
    }
    for path, func_name in targets.items():
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        func = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == func_name
        )
        decorated = any(
            isinstance(deco, ast.Attribute) and deco.attr == "auto"
            for deco in func.decorator_list
        )
        assert decorated, f"{path} の {func_name} に @live.auto が付いていない"

        calls = [
            node.func.attr
            for node in ast.walk(func)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        fetches = [
            name
            for name in calls
            if name.startswith(("fetch_", "list_", "player_names", "count_"))
        ]
        assert fetches, (
            f"{path} の {func_name} の中に取得処理が無い。"
            "取得を外に置いたままでは自動更新しても内容が変わらない。"
        )
