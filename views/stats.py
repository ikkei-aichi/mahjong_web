"""タイトル配下の成績とグラフ。"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from mahjong import repo, ui
from mahjong.rules import RuleSet
from mahjong.stats import aggregate, cumulative_series

title_id = ui.require_param(
    "title", "タイトルが選択されていません。", "views/home.py", "タイトル一覧へ"
)
if not title_id:
    st.stop()

title = repo.get_title(title_id)
if not title:
    st.error("タイトルが見つかりません。")
    if st.button("タイトル一覧へ"):
        ui.goto("views/home.py")
    st.stop()

rules = RuleSet.from_dict(title.get("ruleset"))

if st.button("← 対戦一覧へ戻る"):
    ui.goto("views/game_list.py", title=title_id)

st.title(f"📊 {title['name']} の成績")
st.caption(ui.ruleset_summary(rules))

players = {p["id"]: p["name"] for p in repo.list_players(title_id)}
entries = repo.fetch_round_entries(title_id)
stats = aggregate(entries, players, rules)
played = [s for s in stats if s.games > 0]

if not played:
    st.info("まだ記録がありません。")
    st.stop()

# --- サマリー ---------------------------------------------------------------

total_rounds = sum(s.games for s in played) // max(rules.player_count, 1)
col1, col2, col3 = st.columns(3)
col1.metric("参加人数", f"{len(played)}人")
col2.metric("総半荘数", f"{total_rounds}")
col3.metric("トップ", played[0].name if played else "-")

st.divider()

# --- 成績表 -----------------------------------------------------------------

st.markdown("### 成績一覧")
st.dataframe(
    pd.DataFrame(ui.stats_table_rows(stats, rules)),
    hide_index=True,
    use_container_width=True,
)

csv = pd.DataFrame(ui.stats_table_rows(stats, rules)).to_csv(index=False).encode("utf-8-sig")
st.download_button(
    "📥 成績をCSVでダウンロード",
    data=csv,
    file_name=f"{title['name']}_成績.csv",
    mime="text/csv",
    use_container_width=True,
)

st.divider()

# --- 累積ポイント推移 -------------------------------------------------------

st.markdown("### 累積ポイントの推移")

rounds = repo.fetch_rounds_in_order(title_id)
series = cumulative_series(rounds, {s.player_id: s.name for s in played})

chart_rows = []
for player_id, values in series.items():
    name = players.get(player_id, player_id)
    for index, value in enumerate(values):
        chart_rows.append({"半荘": index, "累積ポイント": value, "プレイヤー": name})

if len(rounds) < 1:
    st.info("半荘が登録されるとグラフが表示されます。")
else:
    chart_df = pd.DataFrame(chart_rows)
    chart = (
        alt.Chart(chart_df)
        .mark_line(point=True)
        .encode(
            x=alt.X("半荘:Q", title="半荘数"),
            y=alt.Y("累積ポイント:Q", title="累積ポイント"),
            color=alt.Color("プレイヤー:N", title="プレイヤー"),
            tooltip=["プレイヤー", "半荘", "累積ポイント"],
        )
        .properties(height=360)
    )
    st.altair_chart(chart, use_container_width=True)

st.divider()

# --- 順位分布 ---------------------------------------------------------------

st.markdown("### 順位の分布")

dist_rows = []
for s in played:
    for rank in range(1, rules.player_count + 1):
        dist_rows.append(
            {
                "プレイヤー": s.name,
                "順位": f"{rank}位",
                "回数": s.rank_counts[rank - 1],
                "割合": s.rank_rate(rank),
            }
        )

dist_chart = (
    alt.Chart(pd.DataFrame(dist_rows))
    .mark_bar()
    .encode(
        x=alt.X("割合:Q", stack="normalize", axis=alt.Axis(format="%"), title="割合"),
        y=alt.Y("プレイヤー:N", title=None),
        color=alt.Color(
            "順位:N",
            title="順位",
            scale=alt.Scale(scheme="blueorange"),
        ),
        tooltip=["プレイヤー", "順位", "回数"],
    )
    .properties(height=max(120, 60 * len(played)))
)
st.altair_chart(dist_chart, use_container_width=True)

# --- 個人の詳細 -------------------------------------------------------------

st.divider()
st.markdown("### 個人の詳細")

target = st.selectbox("プレイヤー", [s.name for s in played], key="stats_player")
selected = next(s for s in played if s.name == target)

col1, col2, col3, col4 = st.columns(4)
col1.metric("平均順位", f"{selected.avg_rank:.2f}")
col2.metric("トップ率", f"{selected.top_rate:.0%}")
col3.metric("連対率", f"{selected.rentai_rate:.0%}")
col4.metric("ラス率", f"{selected.last_rate:.0%}")

col5, col6, col7, col8 = st.columns(4)
col5.metric("半荘数", selected.games)
col6.metric("合計", ui.format_point(selected.total_point))
col7.metric("最高", ui.format_point(selected.best_point))
col8.metric("最低", ui.format_point(selected.worst_point))

if rules.rate:
    st.metric("収支", ui.format_money(selected.money))
if selected.tobi_count:
    st.caption(f"飛び {selected.tobi_count} 回")
