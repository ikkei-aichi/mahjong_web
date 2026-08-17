"""スコア入力。このアプリで最も回数の多い画面。

旧実装の問題:
    * 1半荘の入力に9操作、そのたびに再実行＋3リクエスト（計27往復）
    * 保存後も入力欄に前回の点数が残る
    * `st.success` の直後に `st.rerun()` していたため、保存された表示が
      一瞬も出ない → 不安になってもう一度押す → 無言で二重登録
    * 風を毎回4つとも手で直す必要がある（親は毎半荘移るのに）
    * 合計が10万点でなくても警告だけで保存でき、差額がまるごとトップの得点になる

対策:
    * st.form で一括送信（往復1回）
    * 風は前の半荘から自動で1つずらす
    * 最後の1人の点数は合計から自動計算
    * 合計が合わないときは既定で保存を止める（供託がある場合だけ明示的に許可）
    * 保存後は入力欄をリセットし、次の描画でフラッシュメッセージを出す
"""

from __future__ import annotations

import streamlit as st

from mahjong import session, ui
from mahjong.errors import AppError
from mahjong.repo import games as games_repo
from mahjong.repo import tournaments as tournaments_repo
from mahjong.rules import KAZE_NAMES
from mahjong.scoring import ScoringError, calc_round
from mahjong.timeutil import format_time

ui.show_flashes()
group = session.require_group()

game_id = ui.require_param("game", "対戦が指定されていません。", "views/tournaments.py", "大会一覧へ戻る")
if not game_id:
    st.stop()

try:
    game = games_repo.get_game(game_id)
except AppError as exc:
    st.error(str(exc))
    st.stop()

# URL を書き換えて別グループの対戦を開こうとしても弾く。
# 旧実装はこの照合が無く、別大会のルールで別大会の対戦にスコアを書き込めた。
if not game or game["group_id"] != group["group_id"]:
    st.warning("この対戦は見つかりません。")
    ui.link_button("大会一覧へ戻る", "views/tournaments.py", key="g_back_missing",
                   group=group["group_id"])
    st.stop()

try:
    rules, warnings = tournaments_repo.get_ruleset(game["tournament_id"])
    rounds = games_repo.list_rounds(game_id)
except AppError as exc:
    st.error(str(exc))
    st.stop()

seats = game["seats"]
seat_to_player = {s["seat"]: s["player_id"] for s in seats}
player_names = {s["player_id"]: s["player_name"] for s in seats}

ui.link_button(
    "← 開催日へ戻る", "views/day.py", key="g_back",
    group=group["group_id"], tournament=game["tournament_id"], day=game["day_id"],
)

st.title(f"✏️ {game['name']}")
st.caption(f"{game.get('held_on') or ''} ／ {ui.ruleset_summary(rules)}")
if warnings:
    st.warning("ルール設定に不備があったため補正して表示しています: " + " / ".join(warnings))

if len(seats) != rules.player_count:
    st.error(
        f"この対戦は{len(seats)}人ですが、大会のルールは{rules.player_count}人用です。"
        "設定でルールの人数を直すか、この卓を作り直してください。"
    )
    st.stop()


# --- 入力欄 -----------------------------------------------------------------


def default_kazes() -> list[str]:
    """次の半荘の風。前の半荘から1つずらす（親が移るため）。"""
    base = KAZE_NAMES[: len(seats)]
    if not rounds:
        return list(base)
    previous = [r["kaze"] for r in sorted(rounds[-1]["results"], key=lambda x: x["seat"])]
    if sorted(previous) != sorted(base):
        return list(base)
    return ui.kaze_rotated(previous, 1)


def score_form(
    form_key: str, kazes: list[str], scores: list[int], submit_label: str, auto_last: bool
) -> tuple[bool, list[str], list[int]]:
    """点数入力フォーム。送信されたときだけ (True, 風, 持ち点) を返す。

    フォームにまとめることで、1半荘の入力がサーバーとの往復1回で済む。
    """
    options = list(KAZE_NAMES[: len(seats)])
    entered_kazes: list[str] = []
    entered_scores: list[int] = []

    with st.form(form_key, clear_on_submit=True):
        for i, seat in enumerate(seats):
            is_auto = auto_last and i == len(seats) - 1
            st.markdown(f"**{seat['player_name']}**")
            col1, col2 = st.columns([1, 2])
            with col1:
                entered_kazes.append(
                    st.selectbox(
                        "風",
                        options,
                        index=options.index(kazes[i]) if kazes[i] in options else i,
                        key=f"{form_key}_kaze_{i}",
                        label_visibility="collapsed",
                    )
                )
            with col2:
                if is_auto:
                    st.text_input(
                        "持ち点", value="自動計算", disabled=True,
                        key=f"{form_key}_auto_{i}", label_visibility="collapsed",
                    )
                    entered_scores.append(0)
                else:
                    entered_scores.append(
                        int(
                            st.number_input(
                                "持ち点",
                                value=scores[i],
                                step=1000,
                                key=f"{form_key}_score_{i}",
                                label_visibility="collapsed",
                            )
                        )
                    )

        submitted = st.form_submit_button(submit_label, type="primary", width="stretch")

    return submitted, entered_kazes, entered_scores


def finalize(kazes: list[str], scores: list[int], auto_last: bool, allow_mismatch: bool):
    """自動計算を埋めてから計算する。失敗時は None を返してエラーを表示する。"""
    values = list(scores)
    if auto_last:
        values[-1] = rules.total_score - sum(values[:-1])
    try:
        return calc_round(
            values,
            kazes,
            rules,
            seats=[s["seat"] for s in seats],
            strict=not allow_mismatch,
        )
    except ScoringError as exc:
        st.error(str(exc))
        return None


st.markdown("### 今回の持ち点を入力")

auto_last = st.checkbox(
    f"最後の席（{seats[-1]['player_name']}）の点数を自動計算する",
    value=True,
    key=f"auto_{game_id}",
    help=f"残り3人の点数から、合計が{rules.total_score:,}点になるように埋めます。",
)
allow_mismatch = st.checkbox(
    "供託が残っていて合計が合わない",
    value=False,
    key=f"mismatch_{game_id}",
    disabled=auto_last,
    help="通常はチェック不要です。合計が合わないまま保存すると、差額がすべてトップの得点になります。",
)

# 半荘を1つ保存するたびにキーが変わるので、入力欄は自動的に空に戻る。
form_key = f"new_{game_id}_{len(rounds)}"
submitted, kazes, scores = score_form(
    form_key,
    default_kazes(),
    [rules.start_score] * len(seats),
    f"{len(rounds) + 1}半荘目を登録",
    auto_last,
)

if submitted:
    results = finalize(kazes, scores, auto_last, allow_mismatch)
    if results is not None:
        try:
            games_repo.add_round(game_id, results, seat_to_player)
        except AppError as exc:
            st.error(str(exc))
        else:
            ui.flash(f"{len(rounds) + 1}半荘目を登録しました。")
            st.rerun()


# --- 記録済みの半荘 ---------------------------------------------------------

if not rounds:
    st.info("まだ半荘の記録がありません。")
    st.stop()

st.markdown("### 記録")

totals = {s["player_id"]: 0 for s in seats}
for rnd in rounds:
    for result in rnd["results"]:
        # 席から外れたプレイヤーの記録が混ざっていても落とさない
        totals[result["player_id"]] = totals.get(result["player_id"], 0) + result["point"]

table = []
for rnd in rounds:
    row = {"回": rnd["no"], "時刻": format_time(rnd["created_at"])}
    for result in rnd["results"]:
        name = player_names.get(result["player_id"], "(不明)")
        row[name] = f"{result['raw_score']:,} / {ui.format_point(result['point'])}"
    table.append(row)
st.dataframe(table, hide_index=True)

st.markdown("**合計**")
cols = st.columns(len(seats))
for col, seat in zip(cols, seats):
    col.metric(seat["player_name"], ui.format_point(totals.get(seat["player_id"], 0)))


# --- 修正・削除 -------------------------------------------------------------

st.markdown("### 修正・削除")

for rnd in rounds:
    summary = "  ".join(
        f"{player_names.get(r['player_id'], '?')} {ui.format_point(r['point'])}"
        for r in rnd["results"]
    )
    with st.expander(f"{rnd['no']}回目 ・ {summary}"):
        ordered = sorted(rnd["results"], key=lambda x: x["seat"])
        edit_key = f"edit_{rnd['id']}"

        edited, edit_kazes, edit_scores = score_form(
            edit_key,
            [r["kaze"] for r in ordered],
            [r["raw_score"] for r in ordered],
            "この内容に書き換える",
            auto_last=False,
        )
        st.caption(
            "⚠️ 書き換えると元の持ち点は残りません。"
            "取り消せないので、内容を確かめてから押してください。"
        )

        if edited:
            results = finalize(edit_kazes, edit_scores, False, allow_mismatch=True)
            if results is not None:
                try:
                    games_repo.update_round(rnd["id"], results, seat_to_player)
                except AppError as exc:
                    st.error(str(exc))
                else:
                    ui.flash(f"{rnd['no']}回目を書き換えました。")
                    st.rerun()

        if ui.confirm_delete(
            f"game_round_{rnd['id']}",
            f"🗑️ {rnd['no']}回目を削除",
            f"{rnd['no']}回目を削除します。よろしいですか？",
        ):
            try:
                games_repo.delete_round(rnd["id"])
            except AppError as exc:
                st.error(str(exc))
            else:
                ui.flash(f"{rnd['no']}回目を削除しました。")
                st.rerun()
