"""プレイヤー成績の集計。DB・UI に依存しない純粋関数のみ。

平均順位・トップ率・ラス率といった麻雀特有の指標を扱う。
順位は保存済みの `rank` をそのまま使うため、同点の解釈がここでブレることはない。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .rules import RuleSet

# 麻雀の1卓の上限。順位の段数がこれを超えることはない。
MAX_SEATS = 4


@dataclass(frozen=True)
class RoundEntry:
    """半荘1回における1人分の記録。集計の入力単位。

    Attributes:
        table_size: その半荘の人数。ラス率の判定に使う。
            0 のときは集計側のルール人数で代用する。3人卓と4人卓が
            混在する大会でも「最下位」を取り違えないために持たせている。
    """

    player_id: str
    rank: int
    point: int
    tobi: bool = False
    table_size: int = 0


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

    `entries` に含まれるのに `players` に無い player_id も必ず集計に含める。
    旧実装はこれを無言で捨てており、プレイヤーを削除すると
    その人の記録だけが順位表から消えて**合計がゼロサムでなくなっていた**
    （「過去の成績は残ります」という画面の説明とも矛盾していた）。

    Args:
        entries: 全半荘・全プレイヤー分の記録。順序は問わない。
        players: player_id -> 表示名。ここに含まれる全員分を返す。
        rules: 人数（順位の段数の下限）とレート（金額換算）に使う。

    Returns:
        合計ポイントの降順。同点は平均順位の良い方を上位にする。
    """
    by_player: dict[str, list[RoundEntry]] = defaultdict(list)
    for entry in entries:
        by_player[entry.player_id].append(entry)

    # 名簿に無い player_id を落とさない。落とすと合計が0にならなくなる。
    names = dict(players)
    for player_id in by_player:
        names.setdefault(player_id, f"(退会者 {player_id[:8]})")

    # 順位の段数は「ルールの人数」と「実際に現れた順位」の大きい方に合わせる。
    # 4人打ちの記録がある大会を3人設定に変えても、4着の記録が消えないようにする。
    # 麻雀の順位は最大4なので、壊れた値（rank=9 等）で段数が膨らまないよう上限を設ける。
    observed = [e.rank for e in entries if 1 <= e.rank <= MAX_SEATS]
    n = max([rules.player_count] + observed)

    result: list[PlayerStats] = []
    for player_id, name in names.items():
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

        # ラスは「その卓の人数と同じ順位」。3人卓と4人卓が混ざっても正しく数える。
        last_count = sum(
            1 for r in rows if r.rank == (r.table_size or rules.player_count)
        )

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
                last_rate=last_count / games,
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

    `aggregate` と同じく、名簿に無い player_id も落とさずに含める。

    Returns:
        player_id -> 各時点の累積ポイント。長さは常に len(rounds) + 1（先頭は0）。
    """
    # 先に全プレイヤーを確定させてから積み上げる。
    # 途中で追加すると系列の長さがずれるため、2周に分ける。
    series: dict[str, list[int]] = {pid: [0] for pid in players}
    for round_entries in rounds:
        for entry in round_entries:
            series.setdefault(entry.player_id, [0])

    for round_entries in rounds:
        gained = {e.player_id: e.point for e in round_entries}
        for pid, values in series.items():
            values.append(values[-1] + gained.get(pid, 0))
    return series
