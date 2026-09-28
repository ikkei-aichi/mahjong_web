"""テスト共通のフィクスチャ。

`backend` を各テストファイルから import し合うと、pytest では動くものの
「使っていない import」として静的検査に引っかかる。共有するものはここに置く。
"""

from __future__ import annotations

import pytest

from tests.fake_backend import FakeBackend


@pytest.fixture
def backend():
    return FakeBackend()
