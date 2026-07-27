"""半荘1回分の点数計算。DB・UI に依存しない純粋関数のみ。

計算方式（一般的な「トップ調整」方式）:
    1. 持ち点の降順で順位を決める。同点は起家に近い風（東>南>西>北）が上位。
    2. 2位以下は 素点 = 端数処理((持ち点 - 返し点) / 1000) + ウマ。
    3. トップは 2位以下の合計の符号を反転した値。
       これにより合計が必ず0になり、オカが自動的にトップへ入る。
    4. 飛び賞があれば、飛んだ人からトップへ移動させる（ゼロサムを保つ）。

端数処理は**整数演算**で行う。float だと 4.6 が 4.5999... になり、
floor(abs(v) + 0.4) が 5 ではなく 4 を返して静かに壊れるため。
"""

from __future__ import annotations

from dataclasses import dataclass

from .rules import (
    KAZE_ORDER,
    ROUND_CEIL,
    ROUND_FLOOR,
    ROUND_GOSHA_ROKUNYU,
    ROUND_SHISHA_GONYU,
    RuleSet,
)


class ScoringError(ValueError):
    """入力が計算不能なときに送出する。"""


@dataclass(frozen=True)
class SeatResult:
    """半荘1回における1人分の結果。

    Attributes:
        seat: 対戦内の席インデックス（0始まり、player1_id なら 0）。
        raw_score: 入力された持ち点。ルール変更時の再計算の根拠になるため必ず保存する。
        kaze: 風。
        rank: 確定順位（1始まり）。同点でも風で必ず一意に決まる。
        point: 最終ポイント（ウマ・オカ・飛び賞込み）。
        tobi: 飛んだ（持ち点が負）か。
    """

    seat: int
    raw_score: int
    kaze: str
    rank: int
    point: int
    tobi: bool


def round_to_point(diff: int, mode: str = ROUND_GOSHA_ROKUNYU) -> int:
    """持ち点差（点）を千点単位のポイントへ丸める。

    整数演算のみを使うため、浮動小数の誤差で境界値が壊れることがない。

    Args:
        diff: 持ち点 - 返し点（点単位の整数）。
        mode: rules モジュールの ROUND_* 定数。

    Returns:
        丸め後のポイント。

    Examples:
        >>> round_to_point(600)      # +0.6 → 五捨六入 → +1
        1
        >>> round_to_point(500)      # +0.5 → 切り捨て
        0
        >>> round_to_point(-25600)   # -25.6 → -26
        -26
        >>> round_to_point(4600)     # float だと 4 になりがちな境界
        5
    """
    if mode == ROUND_CEIL:
        # -(-d // 1000) は整数のまま切り上げになる
        return -((-diff) // 1000)
    if mode == ROUND_FLOOR:
        return diff // 1000

    if mode == ROUND_GOSHA_ROKUNYU:
        bias = 400  # .6 以上で繰り上がる
    elif mode == ROUND_SHISHA_GONYU:
        bias = 500  # .5 以上で繰り上がる
    else:
        raise ScoringError(f"未知の端数処理方式です: {mode}")

    # 0から遠ざかる向きに丸めるため、絶対値で計算して符号を戻す
    sign = -1 if diff < 0 else 1
    return sign * ((abs(diff) + bias) // 1000)


def validate_total(raw_scores: list[int], rules: RuleSet) -> tuple[bool, int, int]:
    """点棒の合計が卓上の総数と一致するか検証する。

    供託（リーチ棒の残り）がある場合は正当に不一致となりうるため、
    呼び出し側は保存を止めず「警告」にとどめること。

    Returns:
        (一致しているか, 期待値, 実際の合計)
    """
    actual = sum(raw_scores)
    expected = rules.total_score
    return actual == expected, expected, actual


def determine_ranks(raw_scores: list[int], kazes: list[str]) -> list[int]:
    """持ち点と風から順位（1始まり）を決める。

    同点の場合は起家に近い風（東 > 南 > 西 > 北）を上位とするため、
    順位は必ず一意に定まる。同点で両者に1位が付く不具合を構造的に防ぐ。

    Returns:
        入力と同じ並びの順位リスト。
    """
    if len(raw_scores) != len(kazes):
        raise ScoringError("持ち点と風の数が一致しません。")
    for kaze in kazes:
        if kaze not in KAZE_ORDER:
            raise ScoringError(f"未知の風です: {kaze}")
    if len(set(kazes)) != len(kazes):
        raise ScoringError("風が重複しています。")

    order = sorted(
        range(len(raw_scores)),
        key=lambda i: (-raw_scores[i], KAZE_ORDER[kazes[i]]),
    )
    ranks = [0] * len(raw_scores)
    for rank, idx in enumerate(order, start=1):
        ranks[idx] = rank
    return ranks


def calc_round(
    raw_scores: list[int],
    kazes: list[str],
    rules: RuleSet,
    seats: list[int] | None = None,
) -> list[SeatResult]:
    """半荘1回分のポイントを計算する。

    Args:
        raw_scores: 各プレイヤーの持ち点。並びは kazes と対応させる。
        kazes: 各プレイヤーの風。重複不可。
        rules: 適用するルール。
        seats: 各要素が対戦内のどの席かを示すインデックス。
            省略時は 0,1,2,... とみなす。3人麻雀で player1/2/4 を使う場合などに指定する。

    Returns:
        入力と同じ並びの SeatResult。

    Raises:
        ScoringError: 人数がルールと合わない、風が重複しているなど。
    """
    n = len(raw_scores)
    if n != rules.player_count:
        raise ScoringError(
            f"このルールは{rules.player_count}人用ですが、{n}人分の入力が渡されました。"
        )
    if seats is None:
        seats = list(range(n))
    elif len(seats) != n:
        raise ScoringError("席インデックスの数が持ち点の数と一致しません。")

    ranks = determine_ranks(raw_scores, kazes)
    top_idx = ranks.index(1)

    # 2位以下を確定させ、トップはその合計の反転で求める（オカが自動的に乗る）
    points = [0] * n
    for i in range(n):
        if i == top_idx:
            continue
        base = round_to_point(raw_scores[i] - rules.return_score, rules.round_mode)
        points[i] = base + rules.uma[ranks[i] - 1]
    points[top_idx] = -sum(points[i] for i in range(n) if i != top_idx)

    # 飛び賞はトップとの移動として扱い、合計0を維持する
    tobi_flags = [score < 0 for score in raw_scores]
    if rules.tobi_bonus:
        for i in range(n):
            if i != top_idx and tobi_flags[i]:
                points[i] -= rules.tobi_bonus
                points[top_idx] += rules.tobi_bonus

    return [
        SeatResult(
            seat=seats[i],
            raw_score=raw_scores[i],
            kaze=kazes[i],
            rank=ranks[i],
            point=points[i],
            tobi=tobi_flags[i],
        )
        for i in range(n)
    ]


def recalculate(
    stored_rounds: list[dict],
    rules: RuleSet,
) -> list[list[SeatResult]]:
    """保存済みの持ち点から全半荘を再計算する。

    ルール（ウマ・返し点など）を後から変更したときに、過去のデータを追従させるために使う。
    持ち点を保存しているからこそ可能になる操作。

    Args:
        stored_rounds: 各要素が {"seats": [...], "raw_scores": [...], "kazes": [...]} の辞書。

    Returns:
        半荘ごとの SeatResult のリスト。
    """
    return [
        calc_round(
            raw_scores=r["raw_scores"],
            kazes=r["kazes"],
            rules=rules,
            seats=r.get("seats"),
        )
        for r in stored_rounds
    ]
