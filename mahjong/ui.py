"""画面をまたいで使う小さな部品。

ここには「表示の都合」だけを置く。計算は scoring / stats、
データ取得は repo に置くこと。

## Streamlit のウィジェット状態について（このファイルの設計の前提）

`key=` を付けたウィジェットは、**2回目以降の描画で `value=` / `index=` を無視する**。
セッション状態に既に値があるからで、これは仕様。旧実装はこれを踏まえておらず、

    * プリセットを「ウマなし」→「ウマ大」に変えても何も変わらない
    * 人数を4→3に変えると、残った4人用プリセット名で KeyError を出して落ちる
    * 保存していない編集が、保存済みのように見え続ける

という不具合が出ていた。対策は「初期値が変わるときはキーも変える」こと。
下の `_generation()` がそれをやっている。
"""

from __future__ import annotations

from typing import Any, Iterable

import streamlit as st

from .rules import ROUND_MODES, RuleError, RuleSet, presets_for
from .scoring import effective_oka
from .stats import PlayerStats


# --- 表示のヘルパー ---------------------------------------------------------


def format_point(value: int) -> str:
    """ポイントを符号付きで表示する（±0 も明示する）。"""
    return f"{value:+d}" if value else "±0"


def format_money(value: int) -> str:
    return f"{value:+,}円" if value else "±0円"


def rank_medal(index: int) -> str:
    """順位の見出し。上位3つはメダルにする。"""
    return {0: "🥇", 1: "🥈", 2: "🥉"}.get(index, f"{index + 1}位")


def kaze_rotated(kazes: Iterable[str], by: int = 1) -> list[str]:
    """風を1つずらす。次の半荘の初期値に使う（親が移るため）。"""
    values = list(kazes)
    if not values:
        return values
    shift = by % len(values)
    return values[shift:] + values[:shift]


# --- ページ遷移 -------------------------------------------------------------
# Streamlit 1.52 の st.switch_page はクエリパラメータを直接渡せる。
# 旧実装は session_state に積んで遷移先で URL へ書き戻す自前の仕組みを
# 持っていたが、もう不要。


def nav(page: str, **params: Any) -> None:
    """クエリパラメータを付けてページ遷移する。

    URL に状態を載せるので、リロードやブックマークでも復元できる。
    """
    st.switch_page(page, query_params={k: str(v) for k, v in params.items() if v})


def link_button(
    label: str, page: str, *, key: str, primary: bool = False, **params: Any
) -> None:
    """押すと指定ページへ遷移するボタン。"""
    if st.button(
        label, key=key, width="stretch", type="primary" if primary else "secondary"
    ):
        nav(page, **params)


def param(name: str) -> str | None:
    value = st.query_params.get(name)
    return value or None


def require_param(name: str, message: str, back_page: str, back_label: str) -> str | None:
    """必須のパラメータを取り出す。無ければ案内を出して None を返す。

    呼び出し側は None のときに `st.stop()` すること。例外を投げて
    トレースバックを見せる代わりに、戻る導線を出す。
    """
    value = param(name)
    if value:
        return value
    st.warning(message)
    if st.button(back_label, key=f"_back_{name}"):
        st.switch_page(back_page)
    return None


# --- フラッシュメッセージ ---------------------------------------------------
# st.success() の直後に st.rerun() すると、その実行の描画はすべて破棄されるため
# メッセージが**一瞬も表示されない**。旧実装は保存のたびにこれをやっていて、
# 「保存された確証が無い → もう一度押す → 無言で二重登録」を招いていた。

_FLASH = "_flash_messages"


def flash(message: str, kind: str = "success") -> None:
    """次の描画で表示するメッセージを積む。"""
    st.session_state.setdefault(_FLASH, []).append((kind, message))


def show_flashes() -> None:
    """積まれたメッセージを表示して消す。各ページの先頭で呼ぶ。"""
    for kind, message in st.session_state.pop(_FLASH, []):
        {"success": st.success, "info": st.info, "warning": st.warning, "error": st.error}.get(
            kind, st.info
        )(message)


# --- 確認つきの削除 ---------------------------------------------------------


def confirm_delete(
    token: str, label: str, warning: str, *, danger: bool = False
) -> bool:
    """2段階の削除確認。押し切られたときだけ True を返す。

    確認待ちの状態は「押した対象そのもの」を持つ。ページを移動しても
    別のページのトークンと一致することはないので、勝手に確認画面が出ることはない。
    逆に、ページ側でこの状態を毎回消してはいけない。1回目の押下は
    `st.rerun()` を挟むため、消すと確認が永久に出なくなる。

    Args:
        token: 対象を一意に識別する文字列。他ページと衝突しない接頭辞を付けること。
    """
    state_key = "_pending_delete"
    if st.button(label, key=f"del_{token}", width="stretch"):
        st.session_state[state_key] = token
        st.rerun()

    if st.session_state.get(state_key) != token:
        return False

    (st.error if danger else st.warning)(warning)
    col1, col2 = st.columns(2)
    with col1:
        if st.button("削除する", key=f"yes_{token}", type="primary", width="stretch"):
            st.session_state.pop(state_key, None)
            return True
    with col2:
        if st.button("やめる", key=f"no_{token}", width="stretch"):
            st.session_state.pop(state_key, None)
            st.rerun()
    return False


# --- ルール編集 -------------------------------------------------------------


def _generation(key_prefix: str, *parts: Any) -> str:
    """初期値が変わったらウィジェットのキーごと変える。

    キーが同じままだと Streamlit は `value=` を無視するため、
    プリセットを切り替えても入力欄が更新されない。
    """
    return f"{key_prefix}_" + "_".join(str(p) for p in parts)


def ruleset_editor(rules: RuleSet, key_prefix: str) -> RuleSet | None:
    """ルール設定の編集フォーム（送信ボタンは呼び出し側で用意する）。

    Returns:
        入力値から組み立てた RuleSet。矛盾していて組み立てられない場合は None。
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
    # 人数を変えるとプリセットの顔ぶれが変わる。キーに人数を含めないと
    # 4人用の選択が3人用のリストに残り、presets[choice] が KeyError で落ちる。
    choice = st.selectbox(
        "プリセット", preset_names, key=f"{key_prefix}_{player_count}_preset"
    )
    base = presets.get(choice) if choice != "カスタム" else None
    if base is None:
        base = rules if rules.player_count == player_count else presets[preset_names[1]]

    gen = _generation(key_prefix, player_count, choice)

    col1, col2 = st.columns(2)
    with col1:
        start_score = int(
            st.number_input(
                "配給原点", value=base.start_score, min_value=0, step=1000, key=f"{gen}_start"
            )
        )
    with col2:
        return_score = int(
            st.number_input(
                "返し点", value=base.return_score, min_value=0, step=1000, key=f"{gen}_return"
            )
        )

    if return_score < start_score:
        st.error("返し点は配給原点以上にしてください。")
        return None

    st.markdown("**ウマ（順位点）**")
    st.caption(
        "1位のウマは自動で決まります（2位以下の合計を反転した値）。"
        "計算方式の都合で1位のウマは常にこの値になるため、手入力しても反映されません。"
    )
    uma_cols = st.columns(player_count)
    lower: list[int] = []
    for i in range(1, player_count):
        with uma_cols[i]:
            default = base.uma[i] if i < len(base.uma) else 0
            lower.append(
                int(
                    st.number_input(
                        f"{i + 1}位", value=int(default), step=5, key=f"{gen}_uma{i}"
                    )
                )
            )
    uma = (-sum(lower), *lower)
    with uma_cols[0]:
        st.metric("1位", f"{uma[0]:+d}")

    col3, col4 = st.columns(2)
    with col3:
        round_modes = list(ROUND_MODES.keys())
        round_mode = st.selectbox(
            "端数処理",
            round_modes,
            index=round_modes.index(base.round_mode),
            format_func=lambda m: ROUND_MODES[m],
            key=f"{gen}_round",
        )
    with col4:
        tobi_bonus = int(
            st.number_input(
                "飛び賞",
                value=base.tobi_bonus,
                min_value=0,
                step=5,
                help="飛んだ人からトップへ移動するポイント。0で無効。",
                key=f"{gen}_tobi",
            )
        )

    tobi_includes_zero = st.checkbox(
        "持ち点ちょうど0も飛びに含める",
        value=base.tobi_includes_zero,
        key=f"{gen}_tobi0",
        disabled=tobi_bonus == 0,
    )

    rate = int(
        st.number_input(
            "レート（1ptあたりの円）",
            value=base.rate,
            step=10,
            help="0にすると金額を表示しません。",
            key=f"{gen}_rate",
        )
    )

    try:
        built = RuleSet(
            player_count=player_count,
            start_score=start_score,
            return_score=return_score,
            uma=uma,
            tobi_bonus=tobi_bonus,
            tobi_includes_zero=tobi_includes_zero,
            round_mode=round_mode,
            rate=rate,
        )
    except RuleError as exc:
        st.error(str(exc))
        return None

    st.caption(
        f"オカ: {effective_oka(built):+d}pt（トップの取り分に加算）"
        f" ／ 場に出る点棒の合計: {built.total_score:,}点"
    )
    return built


def ruleset_summary(rules: RuleSet) -> str:
    """一覧で1行表示するための要約。"""
    uma = "/".join(f"{u:+d}" for u in rules.uma)
    parts = [
        f"{rules.player_count}人",
        f"{rules.start_score // 1000}-{rules.return_score // 1000}",
        f"ウマ {uma}",
    ]
    if rules.tobi_bonus:
        parts.append(f"飛び賞 {rules.tobi_bonus}")
    if rules.rate:
        parts.append(f"{rules.rate}円/pt")
    return " ・ ".join(parts)


# --- 成績表 -----------------------------------------------------------------


def stats_table_rows(
    stats: list[PlayerStats], rules: RuleSet, detailed: bool = True
) -> list[dict[str, Any]]:
    """成績表の行を作る。半荘数0のプレイヤーは除外する。

    合計がちょうど ±0 のプレイヤーを消してしまわないよう、
    ポイントではなく参加半荘数で判定する。

    Args:
        detailed: False にすると列を5つに絞る。スマホで横スクロールしないため。
    """
    rows = []
    for i, s in enumerate(x for x in stats if x.games > 0):
        row: dict[str, Any] = {
            "順位": rank_medal(i),
            "プレイヤー": s.name,
            "半荘": s.games,
            "合計": format_point(s.total_point),
            "平均順位": f"{s.avg_rank:.2f}",
        }
        if detailed:
            row["平均"] = f"{s.avg_point:+.1f}"
            for rank in range(1, len(s.rank_counts) + 1):
                row[f"{rank}位"] = s.rank_counts[rank - 1]
            row["トップ率"] = f"{s.top_rate:.0%}"
            row["ラス率"] = f"{s.last_rate:.0%}"
            if rules.tobi_bonus or s.tobi_count:
                row["飛び"] = s.tobi_count
        if rules.rate:
            row["収支"] = format_money(s.money)
        rows.append(row)
    return rows


def stats_table(stats: list[PlayerStats], rules: RuleSet, key: str) -> None:
    """成績表を表示する。スマホ向けに「かんたん／くわしい」を切り替えられる。"""
    played = [s for s in stats if s.games > 0]
    if not played:
        st.info("まだ記録がありません。")
        return

    detailed = st.toggle("くわしく表示", value=False, key=f"{key}_detail")
    st.dataframe(
        stats_table_rows(stats, rules, detailed=detailed),
        hide_index=True,
    )
