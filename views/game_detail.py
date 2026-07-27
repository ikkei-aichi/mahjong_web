"""1対戦のスコア入力・編集・削除。

旧実装との違い:
    * 対戦の情報を session_state ではなく DB から引く（リロードで壊れない）
    * 表の列キーをプレイヤーIDにする（同名プレイヤーでデータが消えない）
    * 入力された持ち点を保存する（ルール変更後に再計算できる）
    * 入力済みの半荘を編集できる（旧実装は関数だけあってUIが無かった）
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from mahjong import repo, ui
from mahjong.rules import KAZE_NAMES, RuleSet
from mahjong.scoring import ScoringError, calc_round, validate_total
from mahjong.timeutil import format_time

title_id = ui.require_param(
    "title", "タイトルが選択されていません。", "views/home.py", "タイトル一覧へ"
)
if not title_id:
    st.stop()

game_id = ui.require_param(
    "game", "対戦が選択されていません。", "views/home.py", "タイトル一覧へ"
)
if not game_id:
    st.stop()

game = repo.get_game(game_id)
if not game:
    st.error("対戦が見つかりません。削除された可能性があります。")
    if st.button("対戦一覧へ"):
        ui.goto("views/game_list.py", title=title_id)
    st.stop()

title = repo.get_title(title_id)
rules = RuleSet.from_dict(title.get("ruleset")) if title else RuleSet()

seats = game["seats"]
seat_to_player = {s["seat"]: s["player_id"] for s in seats}
player_names = {s["player_id"]: s["player_name"] for s in seats}

# --- ヘッダー ---------------------------------------------------------------

if st.button("← 対戦一覧へ戻る"):
    ui.goto("views/game_list.py", title=title_id)

st.title(f"🀄 {game['name']}")
st.caption(f"{len(seats)}人麻雀　|　{ui.ruleset_summary(rules)}")

if len(seats) != rules.player_count:
    st.error(
        f"この対戦は{len(seats)}人ですが、ルールは{rules.player_count}人用です。"
        "設定画面でルールの人数を合わせてください。"
    )
    if st.button("⚙️ 設定を開く"):
        ui.goto("views/settings.py", title=title_id)
    st.stop()

st.divider()


def score_inputs(key_prefix: str, defaults: dict[int, dict] | None = None):
    """席ごとの風と持ち点の入力欄を描画し、(kazes, scores) を返す。

    key_prefix に対戦IDや半荘IDを含めることで、別の対戦・別の回に移ったときに
    前の入力値が残らないようにする。
    """
    cols = st.columns(len(seats))
    kazes: list[str] = []
    scores: list[int] = []
    for i, seat in enumerate(seats):
        default = (defaults or {}).get(seat["seat"], {})
        with cols[i]:
            st.markdown(f"**{seat['player_name']}**")
            kaze = st.selectbox(
                "風",
                KAZE_NAMES[: len(seats)],
                index=KAZE_NAMES.index(default.get("kaze", KAZE_NAMES[i])),
                key=f"{key_prefix}_kaze_{seat['seat']}",
                label_visibility="collapsed",
            )
            score = st.number_input(
                "持ち点",
                value=int(default.get("raw_score", rules.start_score)),
                step=100,
                key=f"{key_prefix}_score_{seat['seat']}",
                label_visibility="collapsed",
            )
        kazes.append(kaze)
        scores.append(int(score))
    return kazes, scores


def show_total_check(scores: list[int]) -> None:
    """点棒合計を検証して表示する。

    供託（リーチ棒の残り）があると正当に不一致になるため、
    警告にとどめて保存は止めない。
    """
    ok, expected, actual = validate_total(scores, rules)
    if ok:
        st.success(f"点棒合計 {actual:,} 点（一致）")
    else:
        st.warning(
            f"点棒合計 {actual:,} 点。想定は {expected:,} 点で "
            f"{actual - expected:+,} 点ずれています。"
            "供託が残っている場合を除き、入力を確認してください。"
        )


# --- 新しい半荘の入力 -------------------------------------------------------

st.markdown("### 今回の持ち点を入力")

kazes, scores = score_inputs(f"new_{game_id}")
show_total_check(scores)

if st.button("この結果を登録", type="primary", use_container_width=True):
    try:
        results = calc_round(scores, kazes, rules, seats=[s["seat"] for s in seats])
    except ScoringError as exc:
        st.error(str(exc))
    else:
        try:
            repo.add_round(game_id, results, seat_to_player)
        except repo.RepoError as exc:
            st.error(str(exc))
        else:
            st.success("登録しました。")
            st.rerun()

st.divider()

# --- 結果一覧 ---------------------------------------------------------------

st.markdown("### 対戦結果")

rounds = repo.list_rounds(game_id)
if not rounds:
    st.info("まだ結果が登録されていません。")
    st.stop()

# 表の列はプレイヤーIDで管理し、名前は見出しにだけ使う。
# 旧実装は名前を辞書キーにしていたため、同名が2人いると片方が消えていた。
table_rows = []
totals = {s["player_id"]: 0 for s in seats}
for rnd in rounds:
    row = {"回": str(rnd["no"]), "時刻": format_time(rnd["created_at"])}
    for result in rnd["results"]:
        pid = result["player_id"]
        totals[pid] += result["point"]
        mark = "🈳" if result["tobi"] else ""
        row[pid] = f"{ui.format_point(result['point'])}({result['kaze']}){mark}"
    table_rows.append(row)

total_row = {"回": "合計", "時刻": ""}
for pid in totals:
    total_row[pid] = ui.format_point(totals[pid])

df = pd.DataFrame([total_row, *table_rows])
df = df.rename(columns={pid: name for pid, name in player_names.items()})
st.dataframe(df, hide_index=True, use_container_width=True)

# --- 各回の編集・削除 -------------------------------------------------------

st.markdown("### 回ごとの編集・削除")
st.caption("持ち点を保存しているので、入力し直せばポイントは自動で計算し直されます。")

for rnd in rounds:
    defaults = {r["seat"]: r for r in rnd["results"]}
    label = "　".join(
        f"{player_names[r['player_id']]} {ui.format_point(r['point'])}"
        for r in rnd["results"]
    )
    with st.expander(f"{rnd['no']}回目　{label}"):
        edit_kazes, edit_scores = score_inputs(f"edit_{rnd['id']}", defaults)
        show_total_check(edit_scores)

        col_save, col_delete = st.columns(2)
        with col_save:
            if st.button("この回を更新", key=f"save_{rnd['id']}", use_container_width=True):
                try:
                    results = calc_round(
                        edit_scores, edit_kazes, rules, seats=[s["seat"] for s in seats]
                    )
                except ScoringError as exc:
                    st.error(str(exc))
                else:
                    try:
                        repo.update_round(rnd["id"], results, seat_to_player)
                    except repo.RepoError as exc:
                        st.error(str(exc))
                    else:
                        st.success(f"{rnd['no']}回目を更新しました。")
                        st.rerun()

        with col_delete:
            if st.button(
                "🗑️ この回を削除",
                key=f"del_{rnd['id']}",
                use_container_width=True,
            ):
                st.session_state["pending_delete_round"] = rnd["id"]

        if st.session_state.get("pending_delete_round") == rnd["id"]:
            st.warning(f"{rnd['no']}回目を削除します。よろしいですか？")
            col_yes, col_no = st.columns(2)
            with col_yes:
                if st.button(
                    "削除する", key=f"yes_{rnd['id']}", type="primary",
                    use_container_width=True,
                ):
                    try:
                        repo.delete_round(rnd["id"])
                    except repo.RepoError as exc:
                        st.error(str(exc))
                    else:
                        st.session_state.pop("pending_delete_round", None)
                        st.rerun()
            with col_no:
                if st.button("やめる", key=f"no_{rnd['id']}", use_container_width=True):
                    st.session_state.pop("pending_delete_round", None)
                    st.rerun()
