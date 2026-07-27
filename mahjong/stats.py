"""プレイヤー成績の集計。DB・UI に依存しない純粋関数のみ。

平均順位・トップ率・ラス率といった麻雀特有の指標を扱う。
順位は保存済みの `rank` をそのまま使うため、同点の解釈がここでブレることはない。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .rules import RuleSet


@dataclass(frozen=True)
class RoundEntry:
    """半荘1回における1人分の記録。集計の入力単位。"""

    player_id: str
    rank: int
    point: int
    tobi: bool = False


@dataclass(frozen=True)
class PlayerStats:
    """1プレイヤーの通算成績。

    `games` が 0 のプレイヤーも返す。表示側で `games > 0` を条件に絞ること
    （合計がちょうど ±0 のプレイヤーを消してしまわないようにするため）。
    """

    player_id: str
    name: str
    games: int
    total_point: int
    avg_point: float
    avg_rank: float
    rank_counts: tuple[int, ...]  # index 0 が1位の回数
    top_rate: float  # 1位率
    rentai_rate: float  # 連対率（1位または2位）
    last_rate: float  # ラス率（最下位）
    tobi_count: int
    best_point: int
    worst_point: int
    money: int  # レート未設定なら 0

    def rank_rate(self, rank: int) -> float:
        """指定順位の出現率（0.0〜1.0）。"""
        if self.games == 0 or not 1 <= rank <= len(self.rank_counts):
            return 0.0
        return self.rank_counts[rank - 1] / self.games


def aggregate(
    entries: list[RoundEntry],
    players: dict[str, str],
    rules: RuleSet,
) -> list[PlayerStats]:
    """半荘ごとの記録をプレイヤー単位の通算成績へ集計する。

    Args:
        entries: 全半荘・全プレイヤー分の記録。順序は問わない。
        players: player_id -> 表示名。ここに含まれる全員分を返す。
        rules: 人数（順位の段数）とレート（金額換算）に使う。

    Returns:
        合計ポイントの降順。同点は平均順位の良い方を上位にする。
    """
    n = rules.player_count
    by_player: dict[str, list[RoundEntry]] = defaultdict(list)
    for entry in entries:
        by_player[entry.player_id].append(entry)

    result: list[PlayerStats] = []
    for player_id, name in players.items():
        rows = by_player.get(player_id, [])
        games = len(rows)

        if games == 0:
            result.append(
                PlayerStats(
                    player_id=player_id,
                    name=name,
                    games=0,
                    total_point=0,
                    avg_point=0.0,
                    avg_rank=0.0,
                    rank_counts=tuple([0] * n),
                    top_rate=0.0,
                    rentai_rate=0.0,
                    last_rate=0.0,
                    tobi_count=0,
                    best_point=0,
                    worst_point=0,
                    money=0,
                )
            )
            continue

        points = [r.point for r in rows]
        total = sum(points)
        counts = [0] * n
        for r in rows:
            # 想定外の順位が紛れ込んでも集計全体を落とさない
            if 1 <= r.rank <= n:
                counts[r.rank - 1] += 1

        result.append(
            PlayerStats(
                player_id=player_id,
                name=name,
                games=games,
                total_point=total,
                avg_point=total / games,
                avg_rank=sum(r.rank for r in rows) / games,
                rank_counts=tuple(counts),
                top_rate=counts[0] / games,
                rentai_rate=(counts[0] + counts[1]) / games if n >= 2 else 0.0,
                last_rate=counts[n - 1] / games,
                tobi_count=sum(1 for r in rows if r.tobi),
                best_point=max(points),
                worst_point=min(points),
                money=total * rules.rate,
            )
        )

    result.sort(key=lambda s: (-s.total_point, s.avg_rank if s.games else 99))
    return result


def cumulative_series(
    rounds: list[list[RoundEntry]],
    players: dict[str, str],
) -> dict[str, list[int]]:
    """半荘ごとの累積ポイント推移を返す（折れ線グラフ用）。

    参加していない半荘では前回の値を維持するため、線が途切れず
    プレイヤーごとに半荘数が違っても比較できる。

    Args:
        rounds: 時系列順に並んだ半荘のリスト。各要素はその半荘の全員分の記録。
        players: player_id -> 表示名。

    Returns:
        player_id -> 各時点の累積ポイント。長さは常に len(rounds) + 1（先頭は0）。
    """
    series: dict[str, list[int]] = {pid: [0] for pid in players}
    for round_entries in rounds:
        gained = {e.player_id: e.point for e in round_entries}
        for pid, values in series.items():
            values.append(values[-1] + gained.get(pid, 0))
    return series
