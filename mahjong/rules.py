"""タイトルごとの対戦ルール（ウマ・オカ・返し点・レート等）の定義。

RuleSet は `titles.ruleset` に jsonb として保存する。列を増やさずに項目を追加できる。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

# 風の優先順位。同点時は起家に近い side が上位になる。
KAZE_NAMES: tuple[str, ...] = ("東", "南", "西", "北")
KAZE_ORDER: dict[str, int] = {k: i for i, k in enumerate(KAZE_NAMES)}

# 端数処理の方式
ROUND_GOSHA_ROKUNYU = "gosha_rokunyu"  # 五捨六入（麻雀で最も一般的）
ROUND_SHISHA_GONYU = "shisha_gonyu"  # 四捨五入
ROUND_CEIL = "ceil"  # 切り上げ
ROUND_FLOOR = "floor"  # 切り捨て

ROUND_MODES: dict[str, str] = {
    ROUND_GOSHA_ROKUNYU: "五捨六入",
    ROUND_SHISHA_GONYU: "四捨五入",
    ROUND_CEIL: "切り上げ",
    ROUND_FLOOR: "切り捨て",
}


class RuleError(ValueError):
    """ルール定義が矛盾しているときに送出する。"""


@dataclass(frozen=True)
class RuleSet:
    """1タイトル分の対戦ルール。

    Attributes:
        player_count: 3（三人麻雀）または 4。
        start_score: 配給原点。開始時の持ち点。
        return_score: 返し点（オカの計算基準）。start_score との差が
            人数分だけトップに加算される（＝オカ）。
        uma: 順位点。index 0 が1位。長さは player_count と一致させる。
        tobi_bonus: 飛び賞。飛んだ人からトップへ移動する点数（0で無効）。
        tobi_includes_zero: 持ち点ちょうど0を飛びに含めるか。
            多くのハウスルールでは0点はハコ（飛び）扱いだが、含めない流儀もある。
        round_mode: 素点の端数処理方式。
        rate: 1ptあたりの金額（円）。0なら金額計算を表示しない。
    """

    player_count: int = 4
    start_score: int = 25000
    return_score: int = 30000
    uma: tuple[int, ...] = (10, 5, -5, -10)
    tobi_bonus: int = 0
    tobi_includes_zero: bool = False
    round_mode: str = ROUND_GOSHA_ROKUNYU
    rate: int = 0

    def __post_init__(self) -> None:
        if self.player_count not in (3, 4):
            raise RuleError("人数は3人または4人のみ対応しています。")
        if len(self.uma) != self.player_count:
            raise RuleError(
                f"ウマは{self.player_count}人分必要です（現在{len(self.uma)}個）。"
            )
        if self.round_mode not in ROUND_MODES:
            raise RuleError(f"未知の端数処理方式です: {self.round_mode}")
        if self.return_score < self.start_score:
            raise RuleError("返し点は配給原点以上である必要があります。")
        if self.tobi_bonus < 0:
            raise RuleError("飛び賞に負の値は指定できません。")
        # ウマの合計が0でないと、1位のウマは計算に反映されない。
        # 計算方式（トップは2位以下の合計の反転）の都合で、トップの実効ウマは
        # 常に -(2位以下のウマの合計) になるため。ここで弾いて設定ミスを防ぐ。
        if sum(self.uma) != 0:
            raise RuleError(
                f"ウマの合計は0にしてください（現在{sum(self.uma):+d}）。"
                "合計が0でないと1位のウマが無視されます。"
            )

    @property
    def total_score(self) -> int:
        """卓上の点棒総数。入力検証に使う（4人=100,000 / 3人=105,000）。"""
        return self.start_score * self.player_count

    @property
    def oka(self) -> int:
        """トップが得るオカ（pt）の名目値。返し点と配給原点の差の合計。

        端数処理を通した**実効値**は `scoring.effective_oka()` を使うこと。
        返し点と配給原点の差が1000の倍数でないとき、この名目値と実効値はずれる。
        """
        return (self.return_score - self.start_score) * self.player_count // 1000

    @property
    def uma_is_zero_sum(self) -> bool:
        """ウマの合計が0か。__post_init__ で保証されるので常に True。"""
        return sum(self.uma) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_count": self.player_count,
            "start_score": self.start_score,
            "return_score": self.return_score,
            "uma": list(self.uma),
            "tobi_bonus": self.tobi_bonus,
            "tobi_includes_zero": self.tobi_includes_zero,
            "round_mode": self.round_mode,
            "rate": self.rate,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RuleSet":
        """jsonb から復元する。未知のキーは無視し、欠けたキーは既定値で補う。

        古いレコードに新しい項目が無くても壊れないようにするための寛容な変換。
        矛盾した値（ウマの数が人数と合わない等）に対しては RuleError を送出する。
        壊れた値でも画面を落としたくない場合は `load_ruleset()` を使うこと。

        `ruleset` は jsonb なので、SQL から直接書けば文字列や配列も入りうる。
        その場合も RuleError にする（素の AttributeError を漏らさない）。
        """
        if not data:
            return cls()
        if not isinstance(data, dict):
            raise RuleError(
                f"ルール設定は連想配列である必要があります（{type(data).__name__} が入っています）。"
            )
        known = {
            "player_count",
            "start_score",
            "return_score",
            "uma",
            "tobi_bonus",
            "tobi_includes_zero",
            "round_mode",
            "rate",
        }
        kwargs = {k: v for k, v in data.items() if k in known}
        if "uma" in kwargs:
            kwargs["uma"] = tuple(kwargs["uma"])
        return cls(**kwargs)

    def with_changes(self, **kwargs: Any) -> "RuleSet":
        if "uma" in kwargs:
            kwargs["uma"] = tuple(kwargs["uma"])
        return replace(self, **kwargs)


# --- プリセット -------------------------------------------------------------
# 呼び出し側が名前で選べるようにしておく。カスタムは RuleSet を直接組み立てる。

PRESETS_4P: dict[str, RuleSet] = {
    "ウマなし": RuleSet(uma=(0, 0, 0, 0)),
    "ゴットー (5-10)": RuleSet(uma=(10, 5, -5, -10)),
    "ワンツー (10-20)": RuleSet(uma=(20, 10, -10, -20)),
    "ウマ大 (10-30)": RuleSet(uma=(30, 10, -10, -30)),
}

PRESETS_3P: dict[str, RuleSet] = {
    "三人麻雀 ウマなし": RuleSet(
        player_count=3, start_score=35000, return_score=40000, uma=(0, 0, 0)
    ),
    "三人麻雀 (10-20)": RuleSet(
        player_count=3, start_score=35000, return_score=40000, uma=(20, 0, -20)
    ),
}


def presets_for(player_count: int) -> dict[str, RuleSet]:
    return PRESETS_4P if player_count == 4 else PRESETS_3P


DEFAULT_RULESET = PRESETS_4P["ゴットー (5-10)"]


# --- 壊れたレコードの安全な読み込み -----------------------------------------


def _coerce_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def load_ruleset(data: dict[str, Any] | None) -> tuple[RuleSet, list[str]]:
    """保存済みの ruleset を、何があっても例外を投げずに復元する。

    旧実装は `RuleSet.from_dict` の RuleError を誰も捕まえておらず、
    壊れたレコードが1件あるだけで大会一覧ページ全体が落ちていた。
    ここでは項目ごとに検証して既定値で補い、直した内容を警告として返す。

    ウマの合計が0でない場合は 1位のウマを `-(2位以下の合計)` に補正する。
    これは計算方式（トップは2位以下の合計の反転）が元々そう振る舞っていた値と
    同じなので、**過去の点数は1点も変わらない**。

    Returns:
        (復元した RuleSet, 修復した内容の説明。空なら無修正)
    """
    warnings: list[str] = []
    if not data:
        return RuleSet(), warnings
    if not isinstance(data, dict):
        # jsonb に文字列や配列が入っているケース。この下の修復処理は
        # dict であることを前提にしているので、ここで既定値に倒す。
        return RuleSet(), [
            f"ルール設定の形式が不正（{type(data).__name__}）だったため、既定のルールで表示しています。"
        ]
    try:
        return RuleSet.from_dict(data), warnings
    except (RuleError, TypeError, ValueError) as exc:
        warnings.append(str(exc))

    count = _coerce_int(data.get("player_count"), 4)
    if count not in (3, 4):
        warnings.append(f"人数「{data.get('player_count')}」は不正です。4人として扱います。")
        count = 4
    base = presets_for(count)[next(iter(presets_for(count)))]

    start = _coerce_int(data.get("start_score"), base.start_score)
    ret = _coerce_int(data.get("return_score"), base.return_score)
    if ret < start:
        warnings.append(f"返し点({ret})が配給原点({start})を下回っていたため、{start}に補正しました。")
        ret = start

    try:
        uma = tuple(int(v) for v in data.get("uma", base.uma))
    except (TypeError, ValueError):
        uma = base.uma
        warnings.append("ウマを読み取れなかったため既定値に戻しました。")
    if len(uma) != count:
        warnings.append(f"ウマが{len(uma)}個で{count}人と合わないため既定値に戻しました。")
        uma = base.uma
    if sum(uma) != 0:
        fixed = (-sum(uma[1:]),) + uma[1:]
        warnings.append(
            f"ウマの合計が{sum(uma):+d}だったため、1位を{fixed[0]:+d}に補正しました"
            "（従来の計算結果と同じ値です）。"
        )
        uma = fixed

    mode = data.get("round_mode", base.round_mode)
    if mode not in ROUND_MODES:
        warnings.append(f"端数処理「{mode}」は不明です。五捨六入として扱います。")
        mode = ROUND_GOSHA_ROKUNYU

    tobi = max(0, _coerce_int(data.get("tobi_bonus"), 0))

    return (
        RuleSet(
            player_count=count,
            start_score=start,
            return_score=ret,
            uma=uma,
            tobi_bonus=tobi,
            tobi_includes_zero=bool(data.get("tobi_includes_zero", False)),
            round_mode=mode,
            rate=_coerce_int(data.get("rate"), 0),
        ),
        warnings,
    )
