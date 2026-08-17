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

    どの方式も**絶対値に対して**丸めて符号を戻す。麻雀の「切り上げ／切り捨て」は
    数学的な ceil/floor（±∞方向）ではなく「点数の大きさを繰り上げる／捨てる」意味であり、
    ±∞方向で丸めると -25.6 が -25 になるなど、五捨六入と符号ごとに挙動が食い違う。

    Examples:
        >>> round_to_point(600)      # +0.6 → 五捨六入 → +1
        1
        >>> round_to_point(500)      # +0.5 → 切り捨て
        0
        >>> round_to_point(-25600)   # -25.6 → -26
        -26
        >>> round_to_point(4600)     # float だと 4 になりがちな境界
        5
        >>> round_to_point(-400, ROUND_CEIL)   # 絶対値を繰り上げる
        -1
    """
    if mode == ROUND_GOSHA_ROKUNYU:
        bias = 400  # .6 以上で繰り上がる
    elif mode == ROUND_SHISHA_GONYU:
        bias = 500  # .5 以上で繰り上がる
    elif mode == ROUND_CEIL:
        bias = 999  # 端数があれば必ず繰り上がる（ちょうどの倍数は動かない）
    elif mode == ROUND_FLOOR:
        bias = 0  # 端数は常に捨てる
    else:
        raise ScoringError(f"未知の端数処理方式です: {mode}")

    # 0から遠ざかる向きに丸めるため、絶対値で計算して符号を戻す
    sign = -1 if diff < 0 else 1
    return sign * ((abs(diff) + bias) // 1000)


def effective_oka(rules: RuleSet) -> int:
    """端数処理を通した実効オカ（pt）。

    `RuleSet.oka` は (返し点-配給原点)×人数÷1000 の名目値で、
    返し点差が1000の倍数でないと実際にトップへ渡る額とずれる。
    表示にはこちらを使うこと。

    Examples:
        >>> from .rules import RuleSet
        >>> effective_oka(RuleSet())                      # 25000 / 30000
        20
        >>> effective_oka(RuleSet(return_score=30250))    # 名目21だが実効は20
        20
    """
    per_seat = round_to_point(rules.start_score - rules.return_score, rules.round_mode)
    return -rules.player_count * per_seat


def validate_total(raw_scores: list[int], rules: RuleSet) -> tuple[bool, int, int]:
    """点棒の合計が卓上の総数と一致するか検証する。

    不一致のまま計算すると、差分がまるごとトップの得点になる
    （トップは2位以下の合計の反転で求めるため）。入力ミスが
    静かに1位への加点に化けるので、既定では `calc_round` が保存を止める。
    供託（リーチ棒の残り）で正当に不一致となる場合のみ、
    呼び出し側が明示的に `strict=False` を渡すこと。

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
    strict: bool = True,
) -> list[SeatResult]:
    """半荘1回分のポイントを計算する。

    Args:
        raw_scores: 各プレイヤーの持ち点。並びは kazes と対応させる。
        kazes: 各プレイヤーの風。重複不可。
        rules: 適用するルール。
        seats: 各要素が対戦内のどの席かを示すインデックス。
            省略時は 0,1,2,... とみなす。3人麻雀で player1/2/4 を使う場合などに指定する。
        strict: 持ち点の合計が卓上の総数と一致しない場合に例外を投げるか。
            供託が残る場合や、保存済みデータの再計算では False にする。

    Returns:
        入力と同じ並びの SeatResult。

    Raises:
        ScoringError: 人数がルールと合わない、風が重複している、
            strict かつ持ち点の合計が合わない。
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

    if strict:
        ok, expected, actual = validate_total(raw_scores, rules)
        if not ok:
            raise ScoringError(
                f"持ち点の合計が{actual:,}点で、{expected:,}点と一致しません"
                f"（差{actual - expected:+,}点）。"
                "このまま計算すると差額がすべてトップの得点になります。"
            )

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

    # 飛び賞はトップとの移動として扱い、合計0を維持する。
    # 持ち点ちょうど0をハコとみなすかはルール次第なので設定で切り替える。
    if rules.tobi_includes_zero:
        tobi_flags = [score <= 0 for score in raw_scores]
    else:
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

    保存済みの記録には供託などで合計が合わないものが混ざりうるため、
    合計の検証は行わない（strict=False）。ここで弾くと過去データを
    まるごと再計算できなくなってしまう。

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
            strict=False,
        )
        for r in stored_rounds
    ]
