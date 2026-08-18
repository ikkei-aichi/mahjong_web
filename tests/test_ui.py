"""表示ヘルパーのテスト。

`select_one` そのものは Streamlit のウィジェットを描くので画面テスト
（test_views.py）で押さえる。ここでは中で使う純粋関数だけを見る。
"""

from __future__ import annotations

import pytest

from mahjong.rules import KAZE_NAMES
from mahjong.ui import format_money, format_point, kaze_rotated, rank_medal, unique_labels


# --- 風の回転 ---------------------------------------------------------------


def test_kaze_rotated_by_minus_one_moves_the_dealer_to_the_next_seat():
    """次の半荘の初期値。前回の南家が次の東家になる。

    向きを間違えると前回の北家が親になり、記録される風が実態とずれる。
    """
    assert kaze_rotated(["東", "南", "西", "北"], -1) == ["北", "東", "南", "西"]


def test_kaze_rotated_works_for_three_players():
    assert kaze_rotated(["東", "南", "西"], -1) == ["西", "東", "南"]


@pytest.mark.parametrize("count", [3, 4])
def test_kaze_rotation_returns_to_the_start_after_one_lap(count):
    """人数ぶん回すと元に戻る＝全員がちょうど1回ずつ親をやる。"""
    kazes = list(KAZE_NAMES[:count])
    rotated = kazes
    for _ in range(count):
        rotated = kaze_rotated(rotated, -1)
    assert rotated == kazes


def test_kaze_rotated_handles_empty():
    assert kaze_rotated([]) == []


# --- 同名の項目 -------------------------------------------------------------


def test_unique_labels_leaves_distinct_names_alone():
    assert unique_labels(["春大会", "夏大会"]) == ["春大会", "夏大会"]


def test_unique_labels_numbers_only_the_duplicates():
    """重複した名前だけに連番を付ける。関係ない項目に (1) が付くと読みにくい。"""
    assert unique_labels(["春大会", "夏大会", "春大会"]) == [
        "春大会 (1)",
        "夏大会",
        "春大会 (2)",
    ]


def test_unique_labels_keeps_the_input_order():
    assert unique_labels(["A", "A", "A"]) == ["A (1)", "A (2)", "A (3)"]


def test_unique_labels_of_empty():
    assert unique_labels([]) == []


# --- 数値の表示 -------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected", [(5, "+5"), (-5, "-5"), (0, "±0")]
)
def test_format_point(value, expected):
    assert format_point(value) == expected


@pytest.mark.parametrize(
    "value,expected", [(1500, "+1,500円"), (-1500, "-1,500円"), (0, "±0円")]
)
def test_format_money(value, expected):
    assert format_money(value) == expected


def test_rank_medal():
    assert [rank_medal(i) for i in range(4)] == ["🥇", "🥈", "🥉", "4位"]
