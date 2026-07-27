"""画面をまたいで使う小さな部品。

ここには「表示の都合」だけを置く。計算は scoring / stats、
データ取得は repo に置くこと。
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from .rules import ROUND_MODES, RuleSet, presets_for
from .stats import PlayerStats


def format_point(value: int) -> str:
    """ポイントを符号付きで表示する（±0 も明示する）。"""
    return f"{value:+d}" if value else "±0"


def format_money(value: int) -> str:
    return f"{value:+,}円" if value else "±0円"


def rank_medal(index: int) -> str:
    """順位の見出し。上位3つはメダルにする。"""
    return {0: "🥇", 1: "🥈", 2: "🥉"}.get(index, f"{index + 1}位")


# st.switch_page() はクエリパラメータを捨ててしまうため、遷移先へは
# session_state で運び、到着後に URL へ書き戻す。
# URL に載せることで、リロードやURL直打ちでも状態を復元できる
# （旧実装は session_state だけに頼っていたため F5 で壊れていた）。
_NAV_KEY = "_nav_params"


def goto(page: str, **params: str) -> None:
    """パラメータを引き継いでページを切り替える。"""
    st.session_state[_NAV_KEY] = {k: v for k, v in params.items() if v}
    st.switch_page(page)


def sync_params() -> dict[str, str]:
    """遷移で持ち越したパラメータを URL に反映し、現在の値を返す。

    各ページの先頭で1回呼ぶ。すでに URL にある場合は何もしない。
    """
    pending = st.session_state.pop(_NAV_KEY, None)
    if pending:
        for key, value in pending.items():
            if st.query_params.get(key) != value:
                st.query_params[key] = value
    return dict(st.query_params)


def require_param(name: str, message: str, back_page: str, back_label: str) -> str | None:
    """必須のパラメータを取り出す。無ければ案内を出して None を返す。

    呼び出し側は None のときに `st.stop()` すること。例外を投げて
    トレースバックを見せる代わりに、戻る導線を出す。
    """
    value = sync_params().get(name)
    if value:
        return value
    st.warning(message)
    if st.button(back_label):
        goto(back_page)
    return None


def ruleset_editor(rules: RuleSet, key_prefix: str) -> RuleSet:
    """ルール設定の編集フォーム（送信ボタンは呼び出し側で用意する）。

    Returns:
        画面の入力値から組み立てた RuleSet。
    """
    player_count = st.radio(
        "人数",
        [4, 3],
        index=0 if rules.player_count == 4 else 1,
        format_func=lambda n: f"{n}人麻雀",
        horizontal=True,
        key=f"{key_prefix}_count",
    )

    presets = presets_for(player_count)
    preset_names = ["カスタム", *presets.keys()]
    choice = st.selectbox("プリセット", preset_names, key=f"{key_prefix}_preset")
    if choice != "カスタム":
        base = presets[choice]
    else:
        base = rules if rules.player_count == player_count else presets[preset_names[1]]

    col1, col2 = st.columns(2)
    with col1:
        start_score = st.number_input(
            "配給原点", value=base.start_score, step=1000, key=f"{key_prefix}_start"
        )
    with col2:
        return_score = st.number_input(
            "返し点", value=base.return_score, step=1000, key=f"{key_prefix}_return"
        )

    oka = (return_score - start_score) * player_count // 1000
    st.caption(f"オカ: {oka:+d}pt（トップの取り分に加算されます）")

    st.markdown("**ウマ（順位点）**")
    uma_cols = st.columns(player_count)
    uma = []
    for i in range(player_count):
        with uma_cols[i]:
            default = base.uma[i] if i < len(base.uma) else 0
            uma.append(
                st.number_input(
                    f"{i + 1}位", value=int(default), step=5, key=f"{key_prefix}_uma{i}"
                )
            )
    if sum(uma) != 0:
        st.caption(
            f"⚠️ ウマの合計が {sum(uma):+d} です。0でない差分はトップが受け取ります。"
        )

    col3, col4 = st.columns(2)
    with col3:
        round_mode = st.selectbox(
            "端数処理",
            list(ROUND_MODES.keys()),
            index=list(ROUND_MODES.keys()).index(base.round_mode),
            format_func=lambda m: ROUND_MODES[m],
            key=f"{key_prefix}_round",
        )
    with col4:
        tobi_bonus = st.number_input(
            "飛び賞", value=base.tobi_bonus, min_value=0, step=5,
            help="飛んだ人からトップへ移動するポイント。0で無効。",
            key=f"{key_prefix}_tobi",
        )

    rate = st.number_input(
        "レート（1ptあたりの円）",
        value=base.rate,
        step=10,
        help="0にすると金額を表示しません。",
        key=f"{key_prefix}_rate",
    )

    return RuleSet(
        player_count=player_count,
        start_score=int(start_score),
        return_score=int(return_score),
        uma=tuple(int(u) for u in uma),
        tobi_bonus=int(tobi_bonus),
        round_mode=round_mode,
        rate=int(rate),
    )


def ruleset_summary(rules: RuleSet) -> str:
    """一覧で1行表示するための要約。"""
    uma = "/".join(f"{u:+d}" for u in rules.uma)
    parts = [
        f"{rules.player_count}人",
        f"{rules.start_score // 1000}-{rules.return_score // 1000}",
        f"ウマ {uma}",
    ]
    if rules.rate:
        parts.append(f"{rules.rate}円/pt")
    return " ・ ".join(parts)


def stats_table_rows(stats: list[PlayerStats], rules: RuleSet) -> list[dict[str, Any]]:
    """成績表の行を作る。半荘数0のプレイヤーは除外する。

    合計がちょうど ±0 のプレイヤーを消してしまわないよう、
    ポイントではなく参加半荘数で判定する。
    """
    rows = []
    for i, s in enumerate(x for x in stats if x.games > 0):
        row: dict[str, Any] = {
            "順位": rank_medal(i),
            "プレイヤー": s.name,
            "半荘": s.games,
            "合計": format_point(s.total_point),
            "平均": f"{s.avg_point:+.1f}",
            "平均順位": f"{s.avg_rank:.2f}",
        }
        for rank in range(1, rules.player_count + 1):
            row[f"{rank}位"] = s.rank_counts[rank - 1]
        row["トップ率"] = f"{s.top_rate:.0%}"
        row["ラス率"] = f"{s.last_rate:.0%}"
        if rules.tobi_bonus or s.tobi_count:
            row["飛び"] = s.tobi_count
        if rules.rate:
            row["収支"] = format_money(s.money)
        rows.append(row)
    return rows
