"""開催日の詳細。その日に立った卓の一覧と、新しい卓の作成。

卓（対戦）を作るときは、その大会のルール人数ぶんだけ席を出す。
旧実装は常に4席出したうえ人数の照合もしていなかったため、
3人用ルールの大会に4人の卓を作れてしまい、
**スコアを1件も入力できない対戦**ができあがっていた。
"""

from __future__ import annotations

import streamlit as st

from mahjong import session, ui
from mahjong.errors import AppError
from mahjong.repo import SeatSpec
from mahjong.repo import games as games_repo
from mahjong.repo import groups as groups_repo
from mahjong.repo import tournaments as tournaments_repo
from mahjong.timeutil import format_time

ui.show_flashes()
group = session.require_group()

day_id = ui.require_param("day", "開催日が指定されていません。", "views/tournaments.py", "大会一覧へ戻る")
if not day_id:
    st.stop()

try:
    day = tournaments_repo.get_day(day_id)
except AppError as exc:
    st.error(str(exc))
    st.stop()

if not day or day["group_id"] != group["group_id"]:
    st.warning("この開催日は見つかりません。")
    ui.link_button("大会一覧へ戻る", "views/tournaments.py", key="d_back_missing",
                   group=group["group_id"])
    st.stop()

tournament_id = day["tournament_id"]
try:
    tournament = tournaments_repo.get_tournament(tournament_id)
    rules, _ = tournaments_repo.get_ruleset(tournament_id)
    players = groups_repo.list_players(group["group_id"])
    day_games = games_repo.list_games(day_id)
except AppError as exc:
    st.error(str(exc))
    st.stop()

ui.link_button(
    "← 大会へ戻る", "views/tournament.py", key="d_back",
    group=group["group_id"], tournament=tournament_id,
)

heading = str(day["held_on"])
if day.get("label"):
    heading += f"（{day['label']}）"
st.title(f"📅 {heading}")
st.caption(f"{tournament['name'] if tournament else ''} ／ {ui.ruleset_summary(rules)}")
if day.get("note"):
    st.caption(day["note"])


# --- 新しい卓 ---------------------------------------------------------------

NEW = "__new__"
NONE = "__none__"


def label_for(value: str) -> str:
    if value == NEW:
        return "＋ 新しい名前を入力"
    if value == NONE:
        return "（空席）"
    return next((p["name"] for p in players if p["id"] == value), value)


with st.expander("🆕 新しい卓をはじめる", expanded=not day_games):
    st.caption(f"{rules.player_count}人ぶんの席を埋めてください。")

    options = [p["id"] for p in players] + [NEW]
    specs: list[SeatSpec] = []
    cols = st.columns(2)
    for seat in range(rules.player_count):
        with cols[seat % 2]:
            # 既存プレイヤーが足りていれば順に埋め、足りない分は新規入力にする
            default_index = seat if seat < len(players) else options.index(NEW)
            selected = st.selectbox(
                f"席{seat + 1}",
                options,
                index=default_index,
                format_func=label_for,
                key=f"seat_{day_id}_{seat}",
            )
            if selected == NEW:
                new_name = st.text_input(
                    f"席{seat + 1}の名前",
                    key=f"seat_name_{day_id}_{seat}",
                    label_visibility="collapsed",
                    placeholder="名前",
                )
                specs.append(SeatSpec(new_name=new_name))
            else:
                specs.append(SeatSpec(player_id=selected))

    game_name = st.text_input(
        "卓の名前（任意）", key=f"game_name_{day_id}", placeholder=f"卓{len(day_games) + 1}"
    )

    if st.button("この面子で開始", type="primary", width="stretch"):
        try:
            game_id = games_repo.create_game(
                day_id,
                game_name or f"卓{len(day_games) + 1}",
                specs,
                player_count=rules.player_count,
            )
        except AppError as exc:
            st.error(str(exc))
        else:
            # 席の選択と卓名を初期化する。残すと連打で同じ面子の卓が増える。
            for seat in range(4):
                st.session_state.pop(f"seat_{day_id}_{seat}", None)
                st.session_state.pop(f"seat_name_{day_id}_{seat}", None)
            st.session_state.pop(f"game_name_{day_id}", None)
            ui.flash("卓をつくりました。スコアを入力してください。")
            ui.nav("views/game.py", group=group["group_id"], game=game_id)


# --- 卓の一覧 ---------------------------------------------------------------

if not day_games:
    st.info("この日はまだ卓がありません。")
    st.stop()

st.markdown("### 🀄 この日の卓")

for game in day_games:
    with st.container(border=True):
        st.markdown(f"**{game['name']}** ・ {format_time(game['created_at'])}")
        st.caption(f"{game['round_count']}半荘")

        ordered = sorted(game["seats"], key=lambda s: -s["total_point"])
        for index, seat in enumerate(ordered):
            col1, col2 = st.columns([3, 1])
            col1.write(f"{ui.rank_medal(index)} {seat['player_name']}")
            col2.write(ui.format_point(seat["total_point"]))

        ui.link_button(
            "入力・編集", "views/game.py", key=f"game_{game['id']}", primary=True,
            group=group["group_id"], game=game["id"],
        )
