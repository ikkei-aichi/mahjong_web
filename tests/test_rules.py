"""ルール定義のテスト。旧実装ではこのモジュールのテストが1本も無かった。"""

from __future__ import annotations

import pytest

from mahjong.rules import (
    DEFAULT_RULESET,
    PRESETS_3P,
    PRESETS_4P,
    ROUND_GOSHA_ROKUNYU,
    RuleError,
    RuleSet,
    load_ruleset,
    presets_for,
)


# --- 検証 -------------------------------------------------------------------


def test_defaults_are_valid():
    rules = RuleSet()
    assert rules.player_count == 4
    assert rules.total_score == 100000
    assert rules.uma_is_zero_sum


@pytest.mark.parametrize("count", [0, 1, 2, 5])
def test_player_count_must_be_three_or_four(count):
    with pytest.raises(RuleError):
        RuleSet(player_count=count, uma=(0,) * count)


def test_uma_length_must_match_player_count():
    with pytest.raises(RuleError, match="ウマは3人分"):
        RuleSet(player_count=3, start_score=35000, return_score=40000, uma=(10, 5, -5, -10))


def test_return_score_below_start_is_rejected():
    with pytest.raises(RuleError, match="返し点"):
        RuleSet(start_score=30000, return_score=25000)


def test_negative_tobi_bonus_is_rejected():
    with pytest.raises(RuleError):
        RuleSet(tobi_bonus=-10)


def test_unknown_round_mode_is_rejected():
    with pytest.raises(RuleError):
        RuleSet(round_mode="nonsense")


def test_non_zero_sum_uma_is_rejected():
    """ウマの合計が0でないと1位のウマが無視される（トップは2位以下の反転のため）。

    旧実装は `uma_is_zero_sum` を用意しながら一度も呼んでおらず、
    設定画面から (30,10,-10,-10) のような値を保存できてしまっていた。
    """
    with pytest.raises(RuleError, match="ウマの合計"):
        RuleSet(uma=(30, 10, -10, -10))


# --- プリセット -------------------------------------------------------------


@pytest.mark.parametrize("name,rules", list(PRESETS_4P.items()) + list(PRESETS_3P.items()))
def test_all_presets_are_valid_and_zero_sum(name, rules):
    assert rules.uma_is_zero_sum, name
    assert len(rules.uma) == rules.player_count, name


def test_presets_for_selects_by_count():
    assert presets_for(4) is PRESETS_4P
    assert presets_for(3) is PRESETS_3P


def test_three_player_presets_use_35000_start():
    for rules in PRESETS_3P.values():
        assert rules.total_score == 105000


# --- 変換 -------------------------------------------------------------------


def test_to_dict_from_dict_round_trip():
    original = RuleSet(
        player_count=3,
        start_score=35000,
        return_score=40000,
        uma=(20, 0, -20),
        tobi_bonus=10,
        tobi_includes_zero=True,
        rate=50,
    )
    assert RuleSet.from_dict(original.to_dict()) == original


def test_from_dict_ignores_unknown_keys():
    rules = RuleSet.from_dict({"uma": [10, 5, -5, -10], "未知の項目": 1})
    assert rules.uma == (10, 5, -5, -10)


def test_from_dict_fills_missing_keys_with_defaults():
    """古いレコードに新しい項目が無くても壊れない。"""
    rules = RuleSet.from_dict({"start_score": 25000})
    assert rules == RuleSet(start_score=25000)


def test_from_dict_of_empty_returns_defaults():
    assert RuleSet.from_dict(None) == RuleSet()
    assert RuleSet.from_dict({}) == RuleSet()


def test_with_changes_keeps_other_fields():
    changed = DEFAULT_RULESET.with_changes(rate=100)
    assert changed.rate == 100
    assert changed.uma == DEFAULT_RULESET.uma


# --- 壊れたレコードの読み込み -----------------------------------------------


def test_load_ruleset_returns_no_warnings_for_valid_data():
    rules, warnings = load_ruleset(DEFAULT_RULESET.to_dict())
    assert rules == DEFAULT_RULESET
    assert warnings == []


def test_load_ruleset_repairs_uma_count_mismatch():
    """人数とウマの数が食い違う壊れたレコードでも例外を投げない。

    旧実装は from_dict の RuleError が捕捉されておらず、
    こういうレコードが1件あるだけで大会一覧ページ全体が落ちていた。
    """
    rules, warnings = load_ruleset({"player_count": 3, "uma": [10, 5, -5, -10]})
    assert rules.player_count == 3
    assert len(rules.uma) == 3
    assert warnings


def test_load_ruleset_normalises_non_zero_sum_uma_without_changing_points():
    """1位のウマを -(2位以下の合計) に直す。過去の点数は1点も変わらない。"""
    rules, warnings = load_ruleset({"uma": [30, 10, -10, -10]})
    assert rules.uma == (10, 10, -10, -10)
    assert rules.uma_is_zero_sum
    assert any("ウマの合計" in w for w in warnings)


def test_load_ruleset_repairs_return_below_start():
    rules, warnings = load_ruleset({"start_score": 30000, "return_score": 25000})
    assert rules.return_score == rules.start_score == 30000
    assert warnings


def test_load_ruleset_repairs_unknown_round_mode():
    rules, warnings = load_ruleset({"round_mode": "nonsense"})
    assert rules.round_mode == ROUND_GOSHA_ROKUNYU
    assert warnings


def test_load_ruleset_repairs_garbage_types():
    rules, warnings = load_ruleset(
        {"player_count": "よん", "start_score": None, "rate": "たかい"}
    )
    assert rules.player_count == 4
    assert rules.rate == 0
    assert warnings


def test_load_ruleset_never_raises_on_arbitrary_junk():
    for junk in ({"uma": "abc"}, {"uma": [1, 2]}, {"tobi_bonus": -5}, {"player_count": 99}):
        rules, _ = load_ruleset(junk)
        assert isinstance(rules, RuleSet)
