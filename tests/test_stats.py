"""成績集計のテスト。手計算した値と照合する。"""

from __future__ import annotations

import pytest

from mahjong.rules import PRESETS_3P, PRESETS_4P, RuleSet
from mahjong.stats import PlayerStats, RoundEntry, aggregate, cumulative_series

NO_UMA = PRESETS_4P["ウマなし"]
PLAYERS = {"a": "アキラ", "b": "ボブ", "c": "チカ", "d": "ダイ"}


def make_round(ranks_and_points, tobi=()):
    """(player_id, rank, point) のリストから RoundEntry を作る。"""
    return [
        RoundEntry(player_id=pid, rank=rank, point=pt, tobi=pid in tobi)
        for pid, rank, pt in ranks_and_points
    ]


@pytest.fixture
def three_rounds():
    """3半荘分。アキラ 1,2,1位 / ボブ 2,1,4位 / チカ 3,3,2位 / ダイ 4,4,3位。"""
    return [
        make_round([("a", 1, 30), ("b", 2, 0), ("c", 3, -10), ("d", 4, -20)]),
        make_round([("b", 1, 40), ("a", 2, 10), ("c", 3, -20), ("d", 4, -30)]),
        make_round([("a", 1, 50), ("c", 2, 10), ("d", 3, -20), ("b", 4, -40)],
                   tobi={"b"}),
    ]


def by_id(stats: list[PlayerStats]) -> dict[str, PlayerStats]:
    return {s.player_id: s for s in stats}


# --- 基本集計 ---------------------------------------------------------------


def test_totals_and_game_counts(three_rounds):
    stats = by_id(aggregate([e for r in three_rounds for e in r], PLAYERS, NO_UMA))
    assert stats["a"].total_point == 90  # 30+10+50
    assert stats["b"].total_point == 0  # 0+40-40
    assert stats["c"].total_point == -20  # -10-20+10
    assert stats["d"].total_point == -70  # -20-30-20
    assert all(s.games == 3 for s in stats.values())
    assert sum(s.total_point for s in stats.values()) == 0


def test_average_rank(three_rounds):
    stats = by_id(aggregate([e for r in three_rounds for e in r], PLAYERS, NO_UMA))
    assert stats["a"].avg_rank == pytest.approx((1 + 2 + 1) / 3)
    assert stats["b"].avg_rank == pytest.approx((2 + 1 + 4) / 3)
    assert stats["d"].avg_rank == pytest.approx((4 + 4 + 3) / 3)


def test_rank_counts_and_rates(three_rounds):
    stats = by_id(aggregate([e for r in three_rounds for e in r], PLAYERS, NO_UMA))
    a = stats["a"]
    assert a.rank_counts == (2, 1, 0, 0)
    assert a.top_rate == pytest.approx(2 / 3)
    assert a.rentai_rate == pytest.approx(1.0)  # 1位2回 + 2位1回
    assert a.last_rate == 0.0

    b = stats["b"]
    assert b.rank_counts == (1, 1, 0, 1)
    assert b.last_rate == pytest.approx(1 / 3)
    assert b.rank_rate(2) == pytest.approx(1 / 3)


def test_average_point_best_worst(three_rounds):
    stats = by_id(aggregate([e for r in three_rounds for e in r], PLAYERS, NO_UMA))
    assert stats["a"].avg_point == pytest.approx(30.0)
    assert stats["b"].best_point == 40
    assert stats["b"].worst_point == -40


def test_tobi_count(three_rounds):
    stats = by_id(aggregate([e for r in three_rounds for e in r], PLAYERS, NO_UMA))
    assert stats["b"].tobi_count == 1
    assert stats["a"].tobi_count == 0


# --- 合計±0 のプレイヤーが消えないこと（A-3 の再発防止） -------------------


def test_player_with_zero_total_is_kept(three_rounds):
    """旧実装は合計が0のプレイヤーをランキングから除外していた。"""
    stats = aggregate([e for r in three_rounds for e in r], PLAYERS, NO_UMA)
    ids = [s.player_id for s in stats]
    assert "b" in ids
    bob = by_id(stats)["b"]
    assert bob.total_point == 0 and bob.games == 3


def test_never_played_player_is_returned_with_zero_games():
    """未参加者も返す。表示側が games > 0 で絞れるようにするため。"""
    entries = make_round([("a", 1, 30), ("b", 2, 0), ("c", 3, -10), ("d", 4, -20)])
    stats = by_id(aggregate(entries, {**PLAYERS, "e": "エリ"}, NO_UMA))
    assert stats["e"].games == 0
    assert stats["e"].total_point == 0
    assert stats["e"].avg_rank == 0.0
    # 実際に打ったプレイヤーは合計0でも games で区別できる
    assert stats["b"].games == 1 and stats["b"].total_point == 0


# --- 並び順 -----------------------------------------------------------------


def test_sorted_by_total_point_desc(three_rounds):
    stats = aggregate([e for r in three_rounds for e in r], PLAYERS, NO_UMA)
    assert [s.player_id for s in stats] == ["a", "b", "c", "d"]


def test_tie_on_points_broken_by_average_rank():
    entries = (
        make_round([("a", 1, 10), ("b", 2, 10), ("c", 3, -10), ("d", 4, -10)])
        + make_round([("b", 1, 10), ("a", 4, 10), ("c", 2, -10), ("d", 3, -10)])
    )
    stats = aggregate(entries, PLAYERS, NO_UMA)
    # a と b は同点。平均順位の良い b が上
    assert [s.player_id for s in stats[:2]] == ["b", "a"]


# --- 金額 -------------------------------------------------------------------


def test_money_uses_rate(three_rounds):
    rules = RuleSet(uma=(0, 0, 0, 0), rate=50)
    stats = by_id(aggregate([e for r in three_rounds for e in r], PLAYERS, rules))
    assert stats["a"].money == 90 * 50
    assert stats["d"].money == -70 * 50


def test_money_is_zero_without_rate(three_rounds):
    stats = by_id(aggregate([e for r in three_rounds for e in r], PLAYERS, NO_UMA))
    assert all(s.money == 0 for s in stats.values())


# --- 三人麻雀 ---------------------------------------------------------------


def test_three_player_last_rate_uses_third_place():
    rules = PRESETS_3P["三人麻雀 ウマなし"]
    players = {"a": "アキラ", "b": "ボブ", "c": "チカ"}
    entries = make_round([("a", 1, 20), ("b", 2, 0), ("c", 3, -20)])
    stats = by_id(aggregate(entries, players, rules))
    assert stats["c"].rank_counts == (0, 0, 1)
    assert stats["c"].last_rate == 1.0
    assert stats["a"].rentai_rate == 1.0


# --- 累積推移 ---------------------------------------------------------------


def test_cumulative_series_length_and_values(three_rounds):
    series = cumulative_series(three_rounds, PLAYERS)
    assert all(len(v) == len(three_rounds) + 1 for v in series.values())
    assert series["a"] == [0, 30, 40, 90]
    assert series["b"] == [0, 0, 40, 0]


def test_cumulative_series_holds_value_when_absent():
    """参加していない半荘では線が途切れず前回値を維持する。"""
    rounds = [
        make_round([("a", 1, 30), ("b", 2, 0), ("c", 3, -10), ("d", 4, -20)]),
        make_round([("a", 1, 20), ("b", 2, 10), ("c", 3, -30)]),  # d は不参加
    ]
    series = cumulative_series(rounds, PLAYERS)
    assert series["d"] == [0, -20, -20]
    assert series["a"] == [0, 30, 50]


def test_cumulative_series_with_no_rounds():
    series = cumulative_series([], PLAYERS)
    assert series == {pid: [0] for pid in PLAYERS}


# --- 端の条件 ---------------------------------------------------------------


def test_empty_entries_returns_all_players_with_zero():
    stats = aggregate([], PLAYERS, NO_UMA)
    assert len(stats) == 4
    assert all(s.games == 0 and s.total_point == 0 for s in stats)


def test_out_of_range_rank_does_not_crash_aggregation():
    """壊れたデータが1件混ざっても集計全体は落とさない。"""
    entries = [RoundEntry("a", rank=9, point=10), RoundEntry("a", rank=1, point=20)]
    stats = by_id(aggregate(entries, {"a": "アキラ"}, NO_UMA))
    assert stats["a"].games == 2
    assert stats["a"].total_point == 30
    assert stats["a"].rank_counts == (1, 0, 0, 0)


# --- 名簿に無いプレイヤー（削除済み） ---------------------------------------


def test_player_missing_from_roster_is_still_counted():
    """プレイヤーを削除しても順位表の合計はゼロサムのまま。

    旧実装は名簿(players)に無い player_id を無言で捨てていたため、
    削除したプレイヤーの記録だけが消えて合計が0にならなくなっていた。
    画面には「過去の成績は残ります」と書いてあり、説明とも矛盾していた。
    """
    entries = make_round(
        [("a", 1, 30), ("b", 2, 0), ("c", 3, -10), ("d", 4, -20)]
    )
    # d を名簿から外す（＝論理削除された状態）
    stats = aggregate(entries, {"a": "アキラ", "b": "ボブ", "c": "チカ"}, NO_UMA)

    assert sum(s.total_point for s in stats) == 0
    assert {s.player_id for s in stats} == {"a", "b", "c", "d"}
    assert by_id(stats)["d"].total_point == -20


def test_cumulative_series_includes_players_missing_from_roster():
    rounds = [make_round([("a", 1, 30), ("b", 2, 0), ("c", 3, -10), ("d", 4, -20)])]
    series = cumulative_series(rounds, {"a": "アキラ"})
    assert set(series) == {"a", "b", "c", "d"}
    assert all(len(v) == len(rounds) + 1 for v in series.values())
    assert series["d"] == [0, -20]


# --- 人数設定を変えても過去が壊れない ---------------------------------------


def test_switching_to_three_player_keeps_fourth_place_records():
    """4人打ちの大会を3人設定に変えても、4着の記録が消えない。

    旧実装は rank_counts の幅を現在のルール人数で決めていたため、
    4着が集計から抜け落ちて「ラス率0%」と表示されていた。
    """
    entries = [
        RoundEntry("a", rank=4, point=-20, table_size=4),
        RoundEntry("a", rank=1, point=30, table_size=4),
    ]
    three = PRESETS_3P["三人麻雀 ウマなし"]
    stats = by_id(aggregate(entries, {"a": "アキラ"}, three))["a"]

    assert stats.rank_counts == (1, 0, 0, 1)
    assert sum(stats.rank_counts) == stats.games
    assert stats.last_rate == 0.5


def test_last_rate_uses_table_size_for_mixed_arity():
    """3人卓と4人卓が混ざる大会でも「最下位」を取り違えない。"""
    entries = [
        RoundEntry("a", rank=3, point=-10, table_size=3),  # 3人卓のラス
        RoundEntry("a", rank=3, point=-10, table_size=4),  # 4人卓の3着（ラスではない）
    ]
    stats = by_id(aggregate(entries, {"a": "アキラ"}, NO_UMA))["a"]
    assert stats.last_rate == 0.5


# --- 個人の掘り下げ ---------------------------------------------------------


def rounds_fixture():
    """4半荘。アキラ視点で 1位/4位/1位/2位、風は 東南西北 を1つずつ。"""
    return [
        [
            RoundEntry("a", 1, 40, table_size=4, kaze="東"),
            RoundEntry("b", 2, 5, table_size=4, kaze="南"),
            RoundEntry("c", 3, -15, table_size=4, kaze="西"),
            RoundEntry("d", 4, -30, table_size=4, kaze="北"),
        ],
        [
            RoundEntry("a", 4, -30, table_size=4, kaze="南"),
            RoundEntry("b", 1, 40, table_size=4, kaze="西"),
            RoundEntry("c", 2, 5, table_size=4, kaze="北"),
            RoundEntry("d", 3, -15, table_size=4, kaze="東"),
        ],
        [
            RoundEntry("a", 1, 50, table_size=4, kaze="西"),
            RoundEntry("b", 3, -10, table_size=4, kaze="北"),
            RoundEntry("c", 4, -30, table_size=4, kaze="東"),
            RoundEntry("d", 2, -10, table_size=4, kaze="南"),
        ],
        # アキラは4半荘目に不参加（3人打ち）
        [
            RoundEntry("b", 1, 30, table_size=3, kaze="東"),
            RoundEntry("c", 2, 0, table_size=3, kaze="南"),
            RoundEntry("d", 3, -30, table_size=3, kaze="西"),
        ],
    ]


def test_player_rounds_only_includes_that_player():
    from mahjong.stats import player_rounds

    mine = player_rounds(rounds_fixture(), "a")
    assert len(mine) == 3, "不参加の半荘まで数えている"
    assert [e.rank for e in mine] == [1, 4, 1]


def test_head_to_head_counts_only_shared_rounds():
    from mahjong.stats import head_to_head

    by_id = {o.player_id: o for o in head_to_head(rounds_fixture(), "a")}
    # アキラが出た3半荘は全員同卓
    assert by_id["b"].games == 3
    assert by_id["b"].my_avg_rank == pytest.approx(2.0)
    assert by_id["b"].my_total_point == 60
    # bより上の着順だったのは1半荘目と3半荘目
    assert by_id["b"].beat == 2
    assert by_id["b"].beat_rate == pytest.approx(2 / 3)


def test_head_to_head_is_sorted_by_games():
    from mahjong.stats import head_to_head

    result = head_to_head(rounds_fixture(), "a")
    assert [o.games for o in result] == sorted((o.games for o in result), reverse=True)


def test_head_to_head_of_absent_player_is_empty():
    from mahjong.stats import head_to_head

    assert head_to_head(rounds_fixture(), "zzz") == []


def test_kaze_breakdown_groups_by_wind():
    from mahjong.stats import kaze_breakdown

    by_kaze = {k.kaze: k for k in kaze_breakdown(rounds_fixture(), "a")}
    assert set(by_kaze) == {"東", "南", "西"}
    assert by_kaze["東"].games == 1 and by_kaze["東"].avg_rank == 1.0
    assert by_kaze["南"].avg_point == -30.0
    assert by_kaze["西"].top_rate == 1.0


def test_kaze_breakdown_is_empty_without_wind_data():
    from mahjong.stats import kaze_breakdown

    rounds = [[RoundEntry("a", 1, 10, table_size=4)]]
    assert kaze_breakdown(rounds, "a") == []


def test_streaks_tracks_longest_and_current():
    from mahjong.stats import streaks

    # アキラ: 1位, 4位, 1位 → 最長連続トップ1、現在1
    s = streaks(rounds_fixture(), "a")
    assert s.longest_top == 1
    assert s.current_top == 1
    assert s.longest_last == 1
    assert s.current_last == 0


def test_streaks_counts_consecutive_tops():
    from mahjong.stats import streaks

    rounds = [
        [RoundEntry("a", 1, 40, table_size=4), RoundEntry("b", 2, -40, table_size=4)],
        [RoundEntry("a", 1, 40, table_size=4), RoundEntry("b", 2, -40, table_size=4)],
        [RoundEntry("a", 1, 40, table_size=4), RoundEntry("b", 2, -40, table_size=4)],
        [RoundEntry("a", 2, -40, table_size=4), RoundEntry("b", 1, 40, table_size=4)],
    ]
    s = streaks(rounds, "a")
    assert s.longest_top == 3
    assert s.current_top == 0, "最後がトップでないので継続中ではない"
    assert s.longest_rentai == 4, "1位も2位も連対"


def test_rank_trend_is_a_moving_average():
    from mahjong.stats import rank_trend

    trend = rank_trend(rounds_fixture(), "a", window=2)
    # 着順 1, 4, 1 → 窓2の移動平均は 1, 2.5, 2.5
    assert trend == pytest.approx([1.0, 2.5, 2.5])


def test_rank_trend_uses_all_games_until_the_window_fills():
    from mahjong.stats import rank_trend

    trend = rank_trend(rounds_fixture(), "a", window=10)
    assert trend == pytest.approx([1.0, 2.5, 2.0])


def test_point_by_rank_averages_each_place():
    from mahjong.stats import point_by_rank

    result = point_by_rank(rounds_fixture(), "a")
    assert result[1] == pytest.approx(45.0)  # +40 と +50
    assert result[4] == pytest.approx(-30.0)
    assert 2 not in result


def test_streaks_without_table_size_does_not_call_everything_a_last_place():
    """卓人数が未設定でも、ラス判定を自分の着順で代用してはいけない。

    `entry.table_size or entry.rank` と書くと rank == rank になり、
    **全半荘がラス**として数えられる（実際にラス率33%なのに
    「最長連続ラス12」と表示される不具合が出た）。
    """
    from mahjong.stats import streaks

    rounds = [
        [
            RoundEntry("a", 1, 40),  # table_size を渡さない
            RoundEntry("b", 2, 5),
            RoundEntry("c", 3, -15),
            RoundEntry("d", 4, -30),
        ],
        [
            RoundEntry("a", 2, 5),
            RoundEntry("b", 1, 40),
            RoundEntry("c", 4, -30),
            RoundEntry("d", 3, -15),
        ],
    ]
    s = streaks(rounds, "a")
    assert s.longest_last == 0, "1位と2位しか取っていないのにラス扱いされている"
    assert s.current_last == 0
    assert s.longest_top == 1

    # 実際にラスを引いた人は正しく数える
    assert streaks(rounds, "d").longest_last == 1


def test_streaks_ignores_rounds_the_player_missed():
    from mahjong.stats import streaks

    rounds = [
        [RoundEntry("a", 4, -30, table_size=4), RoundEntry("b", 1, 30, table_size=4)],
        [RoundEntry("b", 1, 30, table_size=4), RoundEntry("c", 2, -30, table_size=4)],
        [RoundEntry("a", 4, -30, table_size=4), RoundEntry("b", 1, 30, table_size=4)],
    ]
    # a は1半荘目と3半荘目でラス。間の半荘は不参加なので連続扱い。
    assert streaks(rounds, "a").longest_last == 2
