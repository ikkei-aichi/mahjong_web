"""点数計算のテスト。DB不要で高速に回る。"""

from __future__ import annotations

import math

import pytest

from mahjong.rules import (
    ROUND_CEIL,
    ROUND_FLOOR,
    ROUND_SHISHA_GONYU,
    PRESETS_3P,
    PRESETS_4P,
    RuleSet,
)
from mahjong.scoring import (
    ScoringError,
    calc_round,
    determine_ranks,
    recalculate,
    round_to_point,
    validate_total,
)

NO_UMA = PRESETS_4P["ウマなし"]
GOTTO = PRESETS_4P["ゴットー (5-10)"]
ONE_TWO = PRESETS_4P["ワンツー (10-20)"]


# --- 端数処理 ---------------------------------------------------------------


@pytest.mark.parametrize(
    "diff, expected",
    [
        (0, 0),
        (400, 0),  # +0.4 → 切り捨て
        (500, 0),  # +0.5 → 五捨（捨てる）
        (600, 1),  # +0.6 → 六入（繰り上げ）
        (-400, 0),
        (-500, 0),
        (-600, -1),
        (-25600, -26),
        (25400, 25),
        (25600, 26),
    ],
)
def test_gosha_rokunyu(diff, expected):
    assert round_to_point(diff) == expected


def test_ceil_is_not_gosha_rokunyu():
    """旧実装の誤り: math.ceil を五捨六入の代わりに使っていた。

    ceil は常に0から離れる向き（正は繰り上げ）に丸めるため、
    +0.4 を +1 に、-25.6 を -25 にしてしまう。
    """
    assert math.ceil(400 / 1000) == 1 and round_to_point(400) == 0
    assert math.ceil(-25600 / 1000) == -25 and round_to_point(-25600) == -26


def test_integer_rounding_matches_float_across_realistic_range():
    """整数演算は float 版と同じ結果を出しつつ、丸め誤差に依存しない。

    現実的な範囲では float でも一致するが、整数演算なら「たまたま一致している」
    のではなく定義上必ず正しい。
    """
    for diff in range(-100000, 100001, 100):
        sign = -1 if diff < 0 else 1
        assert round_to_point(diff) == sign * math.floor(abs(diff / 1000) + 0.4)


def test_gosha_rokunyu_is_symmetric_around_zero():
    for diff in range(-60000, 60001, 100):
        assert round_to_point(diff) == -round_to_point(-diff)


@pytest.mark.parametrize(
    "diff, expected",
    [(400, 0), (500, 1), (-500, -1), (499, 0)],
)
def test_shisha_gonyu(diff, expected):
    assert round_to_point(diff, ROUND_SHISHA_GONYU) == expected


def test_ceil_and_floor_modes():
    assert round_to_point(400, ROUND_CEIL) == 1
    assert round_to_point(-400, ROUND_CEIL) == 0
    assert round_to_point(400, ROUND_FLOOR) == 0
    assert round_to_point(-400, ROUND_FLOOR) == -1


def test_unknown_round_mode_is_rejected():
    with pytest.raises(ScoringError):
        round_to_point(1000, "nonsense")


# --- 順位判定 ---------------------------------------------------------------


def test_ranks_by_score():
    ranks = determine_ranks([40000, 30000, 20000, 10000], ["東", "南", "西", "北"])
    assert ranks == [1, 2, 3, 4]


def test_tie_is_broken_by_kaze_not_shared():
    """同点でも順位は一意。旧実装は両者に1位を数えていた。"""
    ranks = determine_ranks([25000, 25000, 25000, 25000], ["南", "東", "北", "西"])
    assert sorted(ranks) == [1, 2, 3, 4]
    # 東が最上位、北が最下位
    assert ranks[1] == 1
    assert ranks[2] == 4


def test_tie_at_top_prefers_east():
    ranks = determine_ranks([30000, 30000, 20000, 20000], ["西", "東", "北", "南"])
    assert ranks[1] == 1  # 東
    assert ranks[0] == 2  # 西
    assert ranks[3] == 3  # 南
    assert ranks[2] == 4  # 北


def test_duplicate_kaze_is_rejected():
    with pytest.raises(ScoringError):
        determine_ranks([25000] * 4, ["東", "東", "西", "北"])


def test_unknown_kaze_is_rejected():
    with pytest.raises(ScoringError):
        determine_ranks([25000] * 4, ["東", "南", "西", "中"])


# --- 半荘の計算 -------------------------------------------------------------


def test_known_case_without_uma():
    """旧実装は math.ceil のせいで +29/+1/-5/-25 を返していた。正解は +30/0/-5/-25。"""
    results = calc_round(
        [40000, 30400, 25000, 4600], ["東", "南", "西", "北"], NO_UMA
    )
    assert [r.point for r in results] == [30, 0, -5, -25]
    assert [r.rank for r in results] == [1, 2, 3, 4]


def test_known_case_with_one_two_uma():
    results = calc_round(
        [40000, 30000, 20000, 10000], ["東", "南", "西", "北"], ONE_TWO
    )
    # 素点 +10/0/-10/-20、ウマ +20/+10/-10/-20、オカ +20 はトップへ
    assert [r.point for r in results] == [50, 10, -20, -40]


def test_gotto_uma_applied():
    results = calc_round(
        [40000, 30000, 20000, 10000], ["東", "南", "西", "北"], GOTTO
    )
    assert [r.point for r in results] == [40, 5, -15, -30]


@pytest.mark.parametrize("rules", [NO_UMA, GOTTO, ONE_TWO])
@pytest.mark.parametrize(
    "scores",
    [
        [40000, 30400, 25000, 4600],
        [40000, 30000, 20000, 10000],
        [100000, 0, 0, 0],
        [25000, 25000, 25000, 25000],
        [55300, 24700, 12000, 8000],
        [31000, 30000, 29000, 10000],
    ],
)
def test_points_always_sum_to_zero(rules, scores):
    """ウマの有無にかかわらず合計は必ず0。誰かの得点は誰かの失点。"""
    results = calc_round(scores, ["東", "南", "西", "北"], rules)
    assert sum(r.point for r in results) == 0


def test_top_receives_oka():
    """返し点30000・配給原点25000ならオカ原資は20pt。

    全員が原点のまま終わると、2位以下は返し点との差 -5 を負い、
    トップはその合計を受け取って +15（= 素点 -5 + オカ 20）になる。
    """
    assert NO_UMA.oka == 20
    results = calc_round([25000] * 4, ["東", "南", "西", "北"], NO_UMA)
    assert [r.point for r in results] == [15, -5, -5, -5]
    # トップの取り分はオカから自分の素点マイナス分を引いた額
    assert results[0].point == NO_UMA.oka - 5


def test_raw_score_is_preserved():
    """ルール変更時に再計算できるよう、入力された持ち点がそのまま残る。"""
    scores = [40000, 30400, 25000, 4600]
    results = calc_round(scores, ["東", "南", "西", "北"], NO_UMA)
    assert [r.raw_score for r in results] == scores


def test_seats_are_passed_through():
    """3人麻雀で player1/2/4 の席を使う場合など、席位置を保持する。"""
    rules = PRESETS_3P["三人麻雀 ウマなし"]
    results = calc_round(
        [40000, 35000, 30000], ["東", "南", "西"], rules, seats=[0, 1, 3]
    )
    assert [r.seat for r in results] == [0, 1, 3]


def test_player_count_mismatch_is_rejected():
    with pytest.raises(ScoringError):
        calc_round([25000, 25000, 25000], ["東", "南", "西"], NO_UMA)


# --- 三人麻雀 ---------------------------------------------------------------


def test_three_player_game():
    rules = PRESETS_3P["三人麻雀 ウマなし"]
    assert rules.total_score == 105000
    results = calc_round([50000, 35000, 20000], ["東", "南", "西"], rules)
    assert sum(r.point for r in results) == 0
    # 素点 -5/-20 の反転でトップ +25（オカ15込み）
    assert [r.point for r in results] == [25, -5, -20]


def test_three_player_with_uma():
    rules = PRESETS_3P["三人麻雀 (10-20)"]
    results = calc_round([50000, 35000, 20000], ["東", "南", "西"], rules)
    assert sum(r.point for r in results) == 0
    assert [r.point for r in results] == [45, -5, -40]


# --- 飛び賞 -----------------------------------------------------------------


def test_tobi_bonus_transfers_to_top():
    rules = RuleSet(uma=(0, 0, 0, 0), tobi_bonus=10)
    results = calc_round(
        [60000, 30000, 15000, -5000], ["東", "南", "西", "北"], rules
    )
    assert results[3].tobi is True
    assert results[0].tobi is False
    assert sum(r.point for r in results) == 0
    # 飛び賞なしの場合と比べてトップ +10 / 飛んだ人 -10
    plain = calc_round(
        [60000, 30000, 15000, -5000],
        ["東", "南", "西", "北"],
        RuleSet(uma=(0, 0, 0, 0)),
    )
    assert results[0].point == plain[0].point + 10
    assert results[3].point == plain[3].point - 10


def test_no_tobi_flag_when_score_positive():
    results = calc_round([40000, 30000, 20000, 10000], ["東", "南", "西", "北"], NO_UMA)
    assert not any(r.tobi for r in results)


# --- 点棒合計の検証 ---------------------------------------------------------


def test_validate_total_accepts_correct_sum():
    ok, expected, actual = validate_total([40000, 30000, 20000, 10000], NO_UMA)
    assert ok and expected == 100000 and actual == 100000


def test_validate_total_flags_mismatch():
    ok, expected, actual = validate_total([40000, 30000, 20000, 9000], NO_UMA)
    assert not ok and expected == 100000 and actual == 99000


def test_validate_total_for_three_player():
    ok, expected, _ = validate_total([40000, 35000, 30000], PRESETS_3P["三人麻雀 ウマなし"])
    assert ok and expected == 105000


# --- 再計算 -----------------------------------------------------------------


def test_recalculate_follows_rule_change():
    """持ち点を保存しているので、後からウマを変えても過去データを作り直せる。"""
    stored = [
        {"seats": [0, 1, 2, 3], "raw_scores": [40000, 30000, 20000, 10000],
         "kazes": ["東", "南", "西", "北"]},
        {"seats": [0, 1, 2, 3], "raw_scores": [10000, 20000, 30000, 40000],
         "kazes": ["東", "南", "西", "北"]},
    ]
    before = recalculate(stored, NO_UMA)
    after = recalculate(stored, ONE_TWO)

    assert [r.point for r in before[0]] == [30, 0, -10, -20]
    assert [r.point for r in after[0]] == [50, 10, -20, -40]
    # 順位は持ち点だけで決まるのでルールを変えても不変
    assert [r.rank for r in before[1]] == [r.rank for r in after[1]]
    for rnd in after:
        assert sum(r.point for r in rnd) == 0
