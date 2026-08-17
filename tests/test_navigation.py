"""ページ遷移先が app.py に登録されているかを静的に検査する。

`st.switch_page` は「そのとき st.navigation に渡されているページ」にしか遷移できない。
登録漏れがあると実行時に

    StreamlitAPIException: Could not find page: `views/home.py`

で落ちる。画面テスト（AppTest）はビューを直接実行するので、
app.py のページ登録との整合はそちらでは検出できない。ここで押さえる。
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = ROOT / "app.py"
SOURCES = [APP, *sorted((ROOT / "views").glob("*.py")), *sorted((ROOT / "mahjong").glob("*.py"))]

_PAGE_REF = re.compile(r'["\'](views/[A-Za-z0-9_]+\.py)["\']')
_ST_PAGE = re.compile(r'st\.Page\(\s*["\'](views/[A-Za-z0-9_]+\.py)["\']')


def registered_pages() -> set[str]:
    return set(_ST_PAGE.findall(APP.read_text(encoding="utf-8")))


def referenced_pages() -> dict[str, set[str]]:
    """ファイル名 -> そこから参照しているページの集合。"""
    found: dict[str, set[str]] = {}
    for path in SOURCES:
        text = path.read_text(encoding="utf-8")
        targets = set(_PAGE_REF.findall(text))
        if path == APP:
            targets -= set(_ST_PAGE.findall(text))  # 登録そのものは参照ではない
        if targets:
            found[path.name] = targets
    return found


def test_app_registers_pages():
    pages = registered_pages()
    assert pages, "app.py に st.Page(...) が1つも見つからない"


def test_every_registered_page_file_exists():
    for page in registered_pages():
        assert (ROOT / page).is_file(), f"{page} が存在しない"


def test_every_view_file_is_registered():
    """views/ に置いたのに登録し忘れた画面が無いこと。"""
    on_disk = {f"views/{p.name}" for p in (ROOT / "views").glob("*.py")}
    missing = on_disk - registered_pages()
    assert not missing, f"app.py に登録されていない画面がある: {sorted(missing)}"


def test_every_navigation_target_is_registered():
    """遷移先が未登録だと実行時に Could not find page で落ちる。

    グループ未所属のときに onboarding だけを登録していた実装で、
    グループ作成直後の views/home.py への遷移が落ちていた。
    """
    pages = registered_pages()
    problems = []
    for filename, targets in referenced_pages().items():
        for target in sorted(targets - pages):
            problems.append(f"{filename} → {target}")
    assert not problems, "app.py に未登録のページへ遷移しようとしている: " + ", ".join(problems)


def test_pages_are_registered_unconditionally():
    """ページ一覧を条件分岐で作り分けないこと。

    「グループがあるときだけ home を登録する」と書くと、無いときに
    home へ遷移できず `Could not find page` で落ちる。
    表示・非表示は visibility で出し分け、登録自体は常に全部行う。
    """
    import ast

    tree = ast.parse(APP.read_text(encoding="utf-8"))
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "pages" for t in node.targets)
    ]
    assert len(assignments) == 1, (
        f"pages への代入が {len(assignments)} 箇所ある。"
        "状態ごとに別のページ一覧を作ると、登録されていないページへ遷移して落ちる。"
    )
    # 唯一の代入がモジュール直下にあること（if の中に隠れていないこと）
    assert assignments[0] in tree.body, "pages の代入が条件分岐の中にある"


@pytest.mark.parametrize("page", sorted(registered_pages()))
def test_registered_pages_are_importable_syntax(page):
    """登録された画面が構文エラーを持っていないこと。"""
    compile((ROOT / page).read_text(encoding="utf-8"), page, "exec")
