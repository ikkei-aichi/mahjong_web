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
        kaze: その半荘での風。風別成績の集計に使う。
    """

    player_id: str
    rank: int
    point: int
    tobi: bool = False
    table_size: int = 0
    kaze: str = ""


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


# --- 個人の掘り下げ ---------------------------------------------------------
# ここから下は「1人のプレイヤーを主語にした」分析。
# 入力はどれも `rounds`（時系列に並んだ半荘のリスト）で、
# 半荘の中に誰が同卓していたかが分かる形になっている。


@dataclass(frozen=True)
class Opponent:
    """ある相手と同卓したときの自分の成績。"""

    player_id: str
    games: int
    my_avg_rank: float
    my_total_point: int
    beat: int  # 相手より上の着順で終えた回数

    @property
    def beat_rate(self) -> float:
        return self.beat / self.games if self.games else 0.0


@dataclass(frozen=True)
class KazeStats:
    """風（席）別の成績。東家が有利かどうかを見るために使う。"""

    kaze: str
    games: int
    avg_rank: float
    avg_point: float
    total_point: int
    top_rate: float


@dataclass(frozen=True)
class Streaks:
    """連続記録。「今3連続トップ中」のような話ができるようにする。"""

    longest_top: int
    longest_last: int
    longest_rentai: int
    current_top: int
    current_last: int


def player_rounds(rounds: list[list[RoundEntry]], player_id: str) -> list[RoundEntry]:
    """そのプレイヤーが参加した半荘の記録だけを時系列で返す。"""
    found = []
    for entries in rounds:
        for entry in entries:
            if entry.player_id == player_id:
                found.append(entry)
                break
    return found


def head_to_head(rounds: list[list[RoundEntry]], player_id: str) -> list[Opponent]:
    """相手別の成績。同卓数の多い順に返す。

    「この人と打つと勝てない」が見えるようにするための集計。
    """
    games: dict[str, int] = defaultdict(int)
    rank_sum: dict[str, int] = defaultdict(int)
    point_sum: dict[str, int] = defaultdict(int)
    beat: dict[str, int] = defaultdict(int)

    for entries in rounds:
        me = next((e for e in entries if e.player_id == player_id), None)
        if me is None:
            continue
        for other in entries:
            if other.player_id == player_id:
                continue
            games[other.player_id] += 1
            rank_sum[other.player_id] += me.rank
            point_sum[other.player_id] += me.point
            if me.rank < other.rank:
                beat[other.player_id] += 1

    result = [
        Opponent(
            player_id=pid,
            games=count,
            my_avg_rank=rank_sum[pid] / count,
            my_total_point=point_sum[pid],
            beat=beat[pid],
        )
        for pid, count in games.items()
    ]
    result.sort(key=lambda o: (-o.games, o.my_avg_rank))
    return result


def kaze_breakdown(rounds: list[list[RoundEntry]], player_id: str) -> list[KazeStats]:
    """風別の成績。記録に風が入っていない場合は空を返す。"""
    from .rules import KAZE_NAMES

    buckets: dict[str, list[RoundEntry]] = defaultdict(list)
    for entry in player_rounds(rounds, player_id):
        if entry.kaze:
            buckets[entry.kaze].append(entry)

    stats = []
    for kaze in KAZE_NAMES:
        rows = buckets.get(kaze)
        if not rows:
            continue
        games = len(rows)
        total = sum(r.point for r in rows)
        stats.append(
            KazeStats(
                kaze=kaze,
                games=games,
                avg_rank=sum(r.rank for r in rows) / games,
                avg_point=total / games,
                total_point=total,
                top_rate=sum(1 for r in rows if r.rank == 1) / games,
            )
        )
    return stats


def streaks(rounds: list[list[RoundEntry]], player_id: str) -> Streaks:
    """連続トップ・連続ラス・連続連対の最長と、いま継続中の記録。

    ラスの判定には卓の人数が要る。`table_size` が入っていない記録では
    その半荘の参加人数で代用する。ここを `entry.rank` で代用してはいけない
    （`rank == rank` になって**全半荘がラス扱い**になる）。
    """
    longest_top = longest_last = longest_rentai = 0
    run_top = run_last = run_rentai = 0

    for entries in rounds:
        entry = next((e for e in entries if e.player_id == player_id), None)
        if entry is None:
            continue
        size = entry.table_size or len(entries)
        last_place = entry.rank == size

        run_top = run_top + 1 if entry.rank == 1 else 0
        run_last = run_last + 1 if last_place else 0
        run_rentai = run_rentai + 1 if entry.rank <= 2 else 0

        longest_top = max(longest_top, run_top)
        longest_last = max(longest_last, run_last)
        longest_rentai = max(longest_rentai, run_rentai)

    return Streaks(
        longest_top=longest_top,
        longest_last=longest_last,
        longest_rentai=longest_rentai,
        current_top=run_top,
        current_last=run_last,
    )


def rank_trend(
    rounds: list[list[RoundEntry]], player_id: str, window: int = 10
) -> list[float]:
    """直近 window 半荘の平均着順の推移。調子の波を見るために使う。

    半荘数が window に満たない間は、そこまでの全半荘の平均を返す
    （最初だけ極端な値になって読めなくなるのを避ける）。
    """
    ranks = [e.rank for e in player_rounds(rounds, player_id)]
    trend = []
    for i in range(len(ranks)):
        chunk = ranks[max(0, i - window + 1) : i + 1]
        trend.append(sum(chunk) / len(chunk))
    return trend


def point_by_rank(rounds: list[list[RoundEntry]], player_id: str) -> dict[int, float]:
    """着順ごとの平均ポイント。

    「トップは取れているが、ラスの沈み方が大きい」のような偏りが見える。
    """
    buckets: dict[int, list[int]] = defaultdict(list)
    for entry in player_rounds(rounds, player_id):
        buckets[entry.rank].append(entry.point)
    return {rank: sum(points) / len(points) for rank, points in sorted(buckets.items())}
