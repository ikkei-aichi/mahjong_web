"""個人成績。1人のプレイヤーを主語にした掘り下げ。

「自分は勝てているのか」「誰と打つと沈むのか」「東家だと強いのか」
「いま調子はいいのか」に答えるための画面。

総合成績（views/stats.py）が全員を横に並べるのに対して、
こちらは1人を縦に深く見る。
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from mahjong import session, ui
from mahjong.errors import AppError
from mahjong.repo import groups as groups_repo
from mahjong.repo import queries, tournaments as tournaments_repo
from mahjong.rules import DEFAULT_RULESET
from mahjong.stats import (
    aggregate,
    head_to_head,
    kaze_breakdown,
    player_rounds,
    point_by_rank,
    rank_trend,
    streaks,
)

ui.show_flashes()
group = session.require_group()

st.title("🧑 個人成績")

try:
    names = groups_repo.player_names(group["group_id"])
    tournaments = tournaments_repo.list_tournaments(group["group_id"])
except AppError as exc:
    st.error(str(exc))
    st.stop()

if not names:
    st.info("まだ参加者がいません。")
    st.stop()


# --- 対象と範囲 -------------------------------------------------------------

col1, col2 = st.columns(2)

with col1:
    ids = list(names)
    labels = [names[pid] for pid in ids]
    # 既定は自分。URL で ?player= を指定すればその人を開く。
    wanted = ui.param("player") or group.get("my_player_id")
    index = ids.index(wanted) if wanted in ids else 0
    # 選択の実体は player_id。退会者は表示名が重複しうる（同名の一意制約は
    # 在籍中の行にしか効かない）ので、名前で引き直すと別人を開いてしまう。
    player_id = ui.select_one(
        "プレイヤー", ids, labels, index=index, key="player_pick"
    )

with col2:
    scope_choices: list[tuple[str, str, str]] = [("", "group_id", "グループ通算")]
    for t in tournaments:
        scope_choices.append((t["id"], "tournament_id", t["name"]))
    # 同名の大会があっても取り違えないよう、選択の実体は大会IDにする。
    chosen_scope_id = ui.select_one(
        "範囲",
        [cid for cid, _, _ in scope_choices],
        [label for _, _, label in scope_choices],
        key="player_scope",
    )
    target_id, scope, _ = next(c for c in scope_choices if c[0] == chosen_scope_id)

value = group["group_id"] if scope == "group_id" else target_id
if scope == "group_id":
    rules = DEFAULT_RULESET
    if tournaments:
        rules, _ = tournaments_repo.get_ruleset(tournaments[0]["id"])
else:
    rules, _ = tournaments_repo.get_ruleset(target_id)

try:
    rounds = queries.fetch_rounds_in_order(scope, value)
except AppError as exc:
    st.error(str(exc))
    st.stop()

mine = player_rounds(rounds, player_id)
if not mine:
    st.info(f"{names[player_id]} さんの記録はこの範囲にはまだありません。")
    st.stop()

entries = [e for rnd in rounds for e in rnd]
me = next(s for s in aggregate(entries, names, rules) if s.player_id == player_id)


# --- サマリー ---------------------------------------------------------------

col1, col2, col3, col4 = st.columns(4)
col1.metric("半荘", me.games)
col2.metric("合計", ui.format_point(me.total_point))
col3.metric("平均", f"{me.avg_point:+.1f}")
col4.metric("平均順位", f"{me.avg_rank:.2f}")

col1, col2, col3, col4 = st.columns(4)
col1.metric("トップ率", f"{me.top_rate:.0%}")
col2.metric("連対率", f"{me.rentai_rate:.0%}")
col3.metric("ラス率", f"{me.last_rate:.0%}")
col4.metric("飛び", me.tobi_count)

col1, col2, col3 = st.columns(3)
col1.metric("最高", ui.format_point(me.best_point))
col2.metric("最低", ui.format_point(me.worst_point))
if rules.rate:
    col3.metric("収支", ui.format_money(me.money))

run = streaks(rounds, player_id)
col1, col2, col3 = st.columns(3)
col1.metric("最長連続トップ", f"{run.longest_top}")
col2.metric("最長連続ラス", f"{run.longest_last}")
col3.metric("最長連対", f"{run.longest_rentai}")
if run.current_top >= 2:
    st.success(f"🔥 {run.current_top}連続トップ中")
elif run.current_last >= 2:
    st.warning(f"❄️ {run.current_last}連続ラス中")


tab_trend, tab_rank, tab_vs, tab_kaze, tab_log = st.tabs(
    ["調子", "着順", "対戦相手", "風", "履歴"]
)


# --- 調子 -------------------------------------------------------------------

with tab_trend:
    st.caption("直近の平均着順の推移。下にあるほど good（1位に近い）。")
    window = st.slider("移動平均の窓（半荘）", 3, 30, 10, key="player_window")
    trend = rank_trend(rounds, player_id, window=window)
    frame = pd.DataFrame({"半荘": range(1, len(trend) + 1), "平均着順": trend})
    line = (
        alt.Chart(frame)
        .mark_line(point=True)
        .encode(
            x=alt.X("半荘:Q"),
            # 着順は小さいほど良いので軸を反転させる
            y=alt.Y("平均着順:Q", scale=alt.Scale(reverse=True, zero=False)),
            tooltip=["半荘", "平均着順"],
        )
    )
    st.altair_chart(line, width="stretch")

    st.caption("累積ポイントの推移")
    cumulative = []
    running = 0
    for i, entry in enumerate(mine, start=1):
        running += entry.point
        cumulative.append({"半荘": i, "累積": running})
    area = (
        alt.Chart(pd.DataFrame(cumulative))
        .mark_area(opacity=0.3, line=True)
        .encode(x="半荘:Q", y="累積:Q", tooltip=["半荘", "累積"])
    )
    st.altair_chart(area, width="stretch")


# --- 着順 -------------------------------------------------------------------

with tab_rank:
    dist = pd.DataFrame(
        [
            {"着順": f"{rank}位", "回数": me.rank_counts[rank - 1]}
            for rank in range(1, len(me.rank_counts) + 1)
        ]
    )
    bars = (
        alt.Chart(dist)
        .mark_bar()
        .encode(
            x=alt.X("着順:N", sort=None),
            y="回数:Q",
            color=alt.Color("着順:N", legend=None),
            tooltip=["着順", "回数"],
        )
    )
    st.altair_chart(bars, width="stretch")

    st.caption("着順ごとの平均ポイント。トップは取れているのにラスが重い、などが見える。")
    by_rank = point_by_rank(rounds, player_id)
    st.dataframe(
        [
            {
                "着順": f"{rank}位",
                "回数": me.rank_counts[rank - 1] if rank <= len(me.rank_counts) else 0,
                "平均ポイント": f"{avg:+.1f}",
            }
            for rank, avg in by_rank.items()
        ],
        hide_index=True,
    )


# --- 対戦相手 ---------------------------------------------------------------

with tab_vs:
    st.caption(
        "その人と同卓したときの自分の成績。"
        "「勝率」は相手より上の着順で終えた割合。"
    )
    rows = []
    for opponent in head_to_head(rounds, player_id):
        rows.append(
            {
                "相手": names.get(opponent.player_id, "(不明)"),
                "同卓": opponent.games,
                "自分の平均着順": f"{opponent.my_avg_rank:.2f}",
                "自分の合計": ui.format_point(opponent.my_total_point),
                "勝率": f"{opponent.beat_rate:.0%}",
            }
        )
    if rows:
        st.dataframe(rows, hide_index=True)
    else:
        st.info("同卓した相手がいません。")


# --- 風 ---------------------------------------------------------------------

with tab_kaze:
    st.caption("風（席）別の成績。起家に近いほど有利、といった偏りが見える。")
    kaze_rows = kaze_breakdown(rounds, player_id)
    if not kaze_rows:
        st.info("風の記録がありません。")
    else:
        st.dataframe(
            [
                {
                    "風": k.kaze,
                    "半荘": k.games,
                    "平均着順": f"{k.avg_rank:.2f}",
                    "平均ポイント": f"{k.avg_point:+.1f}",
                    "合計": ui.format_point(k.total_point),
                    "トップ率": f"{k.top_rate:.0%}",
                }
                for k in kaze_rows
            ],
            hide_index=True,
        )
        chart = (
            alt.Chart(
                pd.DataFrame(
                    [{"風": k.kaze, "平均着順": k.avg_rank} for k in kaze_rows]
                )
            )
            .mark_bar()
            .encode(
                x=alt.X("風:N", sort=["東", "南", "西", "北"]),
                y=alt.Y("平均着順:Q", scale=alt.Scale(reverse=True, zero=False)),
                tooltip=["風", "平均着順"],
            )
        )
        st.altair_chart(chart, width="stretch")


# --- 履歴 -------------------------------------------------------------------

with tab_log:
    st.caption("新しい順。")
    log = []
    running = me.total_point
    for i, entry in reversed(list(enumerate(mine, start=1))):
        log.append(
            {
                "#": i,
                "着順": f"{entry.rank}位",
                "ポイント": ui.format_point(entry.point),
                "累積": ui.format_point(running),
                "風": entry.kaze or "-",
                "飛び": "○" if entry.tobi else "",
            }
        )
        running -= entry.point
    st.dataframe(log, hide_index=True)
