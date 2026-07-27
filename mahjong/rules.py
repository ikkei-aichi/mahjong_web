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
        round_mode: 素点の端数処理方式。
        rate: 1ptあたりの金額（円）。0なら金額計算を表示しない。
    """

    player_count: int = 4
    start_score: int = 25000
    return_score: int = 30000
    uma: tuple[int, ...] = (10, 5, -5, -10)
    tobi_bonus: int = 0
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

    @property
    def total_score(self) -> int:
        """卓上の点棒総数。入力検証に使う（4人=100,000 / 3人=105,000）。"""
        return self.start_score * self.player_count

    @property
    def oka(self) -> int:
        """トップが得るオカ（pt）。返し点と配給原点の差の合計。"""
        return (self.return_score - self.start_score) * self.player_count // 1000

    @property
    def uma_is_zero_sum(self) -> bool:
        """ウマの合計が0か。0でない分はトップが吸収する。"""
        return sum(self.uma) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_count": self.player_count,
            "start_score": self.start_score,
            "return_score": self.return_score,
            "uma": list(self.uma),
            "tobi_bonus": self.tobi_bonus,
            "round_mode": self.round_mode,
            "rate": self.rate,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RuleSet":
        """jsonb から復元する。未知のキーは無視し、欠けたキーは既定値で補う。

        古いレコードに新しい項目が無くても壊れないようにするための寛容な変換。
        """
        if not data:
            return cls()
        known = {
            "player_count",
            "start_score",
            "return_score",
            "uma",
            "tobi_bonus",
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
