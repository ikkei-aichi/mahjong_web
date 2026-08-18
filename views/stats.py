"""成績。グループ通算／大会別／開催日別を1画面で切り替えられる。

旧実装は大会単位の成績しか出せなかったうえ、プレイヤー選択のキーが
大会をまたいで残るため、別の大会を開くと StopIteration で落ちていた。
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
from mahjong.stats import aggregate, cumulative_series, head_to_head

ui.show_flashes()
group = session.require_group()

st.title("📊 成績")

try:
    tournaments = tournaments_repo.list_tournaments(group["group_id"])
    names = groups_repo.player_names(group["group_id"])
except AppError as exc:
    st.error(str(exc))
    st.stop()


# --- 集計範囲 ---------------------------------------------------------------

GROUP_TOTAL = "__group__"
choices: list[tuple[str, str, str]] = [(GROUP_TOTAL, "group_id", "グループ通算")]
for tournament in tournaments:
    choices.append((tournament["id"], "tournament_id", tournament["name"]))

# URL の ?tournament= で来たら、その大会を初期選択にする
wanted = ui.param("tournament")
default_index = next((i for i, (cid, _, _) in enumerate(choices) if cid == wanted), 0)

# 大会名に一意制約は無い。表示名で引き直すと、同名の大会が2つあるとき
# 2つ目を選んでも1つ目の成績が出てしまう。選択の実体は大会IDにする。
chosen_id = ui.select_one(
    "集計範囲",
    [cid for cid, _, _ in choices],
    [label for _, _, label in choices],
    index=default_index,
    key="stats_scope",
)
target_id, scope, _ = next(c for c in choices if c[0] == chosen_id)

if scope == "group_id":
    value = group["group_id"]
    # グループ通算にはグループ共通のルールが無いので、レート表示だけ直近の大会に合わせる
    rules = DEFAULT_RULESET
    if tournaments:
        rules, _ = tournaments_repo.get_ruleset(tournaments[0]["id"])
else:
    value = target_id
    rules, _ = tournaments_repo.get_ruleset(target_id)

    days = tournaments_repo.list_days(target_id)
    if days:
        ALL_DAYS = ""
        day_labels = ["すべての開催日"] + [
            f"{d['held_on']}" + (f"（{d['label']}）" if d.get("label") else "") for d in days
        ]
        # 大会が変わると選択肢の顔ぶれも変わる。キーに大会IDを含めないと
        # 前の大会の選択が残って別の日を指してしまう。
        chosen_day = ui.select_one(
            "開催日",
            [ALL_DAYS] + [d["id"] for d in days],
            day_labels,
            key=f"stats_day_{target_id}",
        )
        if chosen_day != ALL_DAYS:
            scope, value = "day_id", chosen_day

try:
    entries = queries.fetch_entries(scope, value)
    rounds = queries.fetch_rounds_in_order(scope, value)
except AppError as exc:
    st.error(str(exc))
    st.stop()

if not entries:
    st.info("この範囲にはまだ記録がありません。")
    st.stop()

stats = aggregate(entries, names, rules)
played = [s for s in stats if s.games > 0]

col1, col2, col3 = st.columns(3)
col1.metric("半荘", len(rounds))
col2.metric("参加者", len(played))
col3.metric("首位", played[0].name if played else "-")


# --- 一覧 -------------------------------------------------------------------

st.markdown("### 総合")
ui.stats_table(stats, rules, key="stats_main")

st.download_button(
    "CSVでダウンロード",
    pd.DataFrame(ui.stats_table_rows(stats, rules, detailed=True)).to_csv(index=False),
    file_name="mahjong_stats.csv",
    mime="text/csv",
    width="stretch",
)


# --- 推移 -------------------------------------------------------------------

st.markdown("### ポイントの推移")

series = cumulative_series(rounds, {s.player_id: s.name for s in played})
frame = pd.DataFrame(
    [
        {"半荘": i, "ポイント": value, "プレイヤー": names.get(pid, pid)}
        for pid, values in series.items()
        for i, value in enumerate(values)
    ]
)
chart = (
    alt.Chart(frame)
    .mark_line(point=True)
    .encode(
        x=alt.X("半荘:Q", title="半荘"),
        y=alt.Y("ポイント:Q", title="累積ポイント"),
        color=alt.Color("プレイヤー:N", title=None),
        tooltip=["プレイヤー", "半荘", "ポイント"],
    )
)
st.altair_chart(chart, width="stretch")


# --- 順位分布 ---------------------------------------------------------------

st.markdown("### 順位の分布")

distribution = pd.DataFrame(
    [
        {"プレイヤー": s.name, "順位": f"{rank}位", "回数": s.rank_counts[rank - 1]}
        for s in played
        for rank in range(1, len(s.rank_counts) + 1)
    ]
)
bars = (
    alt.Chart(distribution)
    .mark_bar()
    .encode(
        x=alt.X("回数:Q", stack="normalize", title="割合"),
        y=alt.Y("プレイヤー:N", title=None, sort=[s.name for s in played]),
        color=alt.Color("順位:N", title=None),
        tooltip=["プレイヤー", "順位", "回数"],
    )
)
st.altair_chart(bars, width="stretch")


# --- 対戦相手ごとの相性 -----------------------------------------------------

st.markdown("### 相性")
st.caption("行の人から見て、列の人と同卓したときの平均着順。低いほど勝てている。")

matrix = []
for me in played:
    row = {"": me.name}
    for opponent in head_to_head(rounds, me.player_id):
        row[names.get(opponent.player_id, "?")] = round(opponent.my_avg_rank, 2)
    matrix.append(row)
st.dataframe(matrix, hide_index=True)


# --- 個人別 -----------------------------------------------------------------

st.markdown("### 個人別")

# キーに集計範囲を含める。含めないと別の範囲へ移ったとき、
# そこに存在しない名前が選択されたままになって落ちる。
# 選択の実体は player_id。退会者は表示名が重複しうるので名前では引かない。
target = ui.select_one(
    "プレイヤー",
    [s.player_id for s in played],
    [s.name for s in played],
    key=f"stats_player_{scope}_{value}",
)
selected = next((s for s in played if s.player_id == target), None)

if selected is not None:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("半荘", selected.games)
    col2.metric("合計", ui.format_point(selected.total_point))
    col3.metric("平均", f"{selected.avg_point:+.1f}")
    col4.metric("平均順位", f"{selected.avg_rank:.2f}")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("トップ率", f"{selected.top_rate:.0%}")
    col2.metric("連対率", f"{selected.rentai_rate:.0%}")
    col3.metric("ラス率", f"{selected.last_rate:.0%}")
    col4.metric("最高／最低", f"{selected.best_point:+d} / {selected.worst_point:+d}")

    if rules.rate:
        st.metric("収支", ui.format_money(selected.money))

    ui.link_button(
        f"🧑 {selected.name} さんの詳しい成績", "views/player.py",
        key="stats_to_player", group=group["group_id"], player=selected.player_id,
    )
