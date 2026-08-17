"""スコア入力。このアプリで最も回数の多い画面。

旧実装の問題:
    * 1半荘の入力に9操作、そのたびに再実行＋3リクエスト（計27往復）
    * 保存後も入力欄に前回の点数が残る
    * `st.success` の直後に `st.rerun()` していたため、保存された表示が
      一瞬も出ない → 不安になってもう一度押す → 無言で二重登録
    * 風を毎回4つとも手で直す必要がある（親は毎半荘移るのに）
    * 合計が10万点でなくても警告だけで保存でき、差額がまるごとトップの得点になる

対策:
    * 入力部分を `st.fragment` にする。入力のたびに**この部分だけ**が再実行され、
      ページ全体（＝対局の読み込みなど通信を伴う処理）は走らない。
      おかげで往復を増やさずに、差額とポイントをその場で出せる。
    * 風は前の半荘から自動で1つずらす
    * 最後の1人の点数は合計から自動計算
    * 合計が合わないうちは登録ボタン自体を押せなくする
      （供託が残る卓だけ、明示的に許可すれば押せる）
    * 保存が成功したときだけ入力欄を初期化する
      （`clear_on_submit` は失敗時にも消してしまうので使わない）
"""

from __future__ import annotations

from typing import Callable, Sequence

import streamlit as st

from mahjong import session, ui
from mahjong.errors import AppError
from mahjong.repo import games as games_repo
from mahjong.repo import tournaments as tournaments_repo
from mahjong.rules import KAZE_NAMES
from mahjong.scoring import ScoringError, SeatResult, calc_round
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
seat_indexes = [s["seat"] for s in seats]
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


# --- 入力欄（ライブ計算つき） -----------------------------------------------


def preview(values: list[int], kazes: list[str]) -> list[SeatResult] | None:
    """入力途中の点数から順位とポイントを試算する。計算できなければ None。"""
    try:
        return calc_round(values, kazes, rules, seats=seat_indexes, strict=False)
    except ScoringError:
        return None


def score_entry(
    key: str,
    base_kazes: Sequence[str],
    base_scores: Sequence[int],
    submit_label: str,
    on_save: Callable[[list[SeatResult]], None],
    allow_auto: bool = True,
) -> None:
    """点数入力ひとかたまり。

    ★ここを個別に @st.fragment にしてはいけない★
    フラグメントを複数に分けると、片方だけが再実行されている間、
    もう片方のウィジェットは描画されない。Streamlit は描画されなかった
    ウィジェットの状態を「使われていない」とみなして破棄するため、
    修正欄をいじった直後に全体が再実行されると、**入力途中だった
    新規半荘の点数が初期値(配給原点)に戻ってしまう**。
    入力エリア全体を1つの `entry_area()` フラグメントに入れて、
    どの操作でも全ウィジェットが必ず描画されるようにしている。
    """
    options = list(KAZE_NAMES[: len(seats)])

    auto_last = False
    if allow_auto:
        auto_last = st.checkbox(
            f"最後の席（{seats[-1]['player_name']}）の点数を自動計算する",
            value=True,
            key=f"{key}_auto",
            help=f"残りの人の点数から、合計が{rules.total_score:,}点になるように埋めます。",
        )

    headers: list = []
    score_slots: list = []
    kazes: list[str] = []
    scores: list[int] = []

    for i, seat in enumerate(seats):
        is_auto = auto_last and i == len(seats) - 1
        # 名前とポイントは全員の入力が出そろってから書き込む。
        # 先に場所だけ取っておき、下で埋める。
        headers.append(st.empty())
        col1, col2 = st.columns([1, 2])
        with col1:
            kazes.append(
                st.selectbox(
                    "風",
                    options,
                    index=options.index(base_kazes[i]) if base_kazes[i] in options else i,
                    key=f"{key}_kaze_{i}",
                    label_visibility="collapsed",
                )
            )
        with col2:
            if is_auto:
                score_slots.append(st.empty())
                scores.append(0)
            else:
                score_slots.append(None)
                scores.append(
                    int(
                        st.number_input(
                            "持ち点",
                            value=base_scores[i],
                            step=1000,
                            key=f"{key}_score_{i}",
                            label_visibility="collapsed",
                        )
                    )
                )

    # --- ここから試算 ---
    values = list(scores)
    if auto_last:
        values[-1] = rules.total_score - sum(values[:-1])
        # 表示専用なのでウィジェットは使わない。
        # キー付きウィジェットは2回目以降 value= を無視するため、
        # 入力欄として置くと計算結果が更新されず 25,000 のまま固まる。
        score_slots[-1].markdown(f"### {values[-1]:,}")

    total = sum(values)
    diff = total - rules.total_score
    results = preview(values, kazes)

    for i, seat in enumerate(seats):
        label = f"**{seat['player_name']}**"
        if results is not None:
            r = results[i]
            badge = f"{r.rank}位　{ui.format_point(r.point)}"
            if r.tobi:
                badge += "　飛び"
            label += f"　:gray[{badge}]"
        headers[i].markdown(label)

    if len(set(kazes)) != len(kazes):
        st.warning("風が重複しています。同じ風は1人までです。")
        return

    if diff == 0:
        st.success(f"点棒合計 {total:,} 点（一致）")
        allow_mismatch = True
    else:
        st.error(
            f"点棒合計 {total:,} 点。{rules.total_score:,} 点との差が {diff:+,} 点あります。"
            "このまま登録すると差額がすべてトップの得点になります。"
        )
        allow_mismatch = st.checkbox(
            "供託が残っているので、このまま登録する",
            value=False,
            key=f"{key}_mismatch",
        )

    if st.button(
        submit_label,
        key=f"{key}_submit",
        type="primary",
        width="stretch",
        disabled=results is None or not allow_mismatch,
    ):
        if results is not None:
            on_save(results)


# --- 入力エリア（全体で1つのフラグメント） ---------------------------------
# 入力のたびにこの関数だけが再実行される。ページ本体（対局・半荘の読み込み＝通信）は
# 走らないので、1文字ごとに再計算しても往復は増えない。
#
# 新規入力と修正欄を別々のフラグメントに分けてはいけない。分けると、
# 片方だけが再実行されている間もう片方が描画されず、その状態が破棄される。
# 結果、修正欄をいじった直後に入力途中の新規半荘が配給原点に戻ってしまう。


def next_kazes() -> list[str]:
    """次の半荘の風。前の半荘から1つずらす（親が移るため）。"""
    base = list(KAZE_NAMES[: len(seats)])
    if not rounds:
        return base
    previous = [r["kaze"] for r in sorted(rounds[-1]["results"], key=lambda x: x["seat"])]
    if sorted(previous) != sorted(base):
        return base
    return ui.kaze_rotated(previous, 1)


def save_new(results: list[SeatResult]) -> None:
    try:
        games_repo.add_round(game_id, results, seat_to_player)
    except AppError as exc:
        st.error(str(exc))
        return
    ui.flash(f"{len(rounds) + 1}半荘目を登録しました。")
    st.rerun()


def make_saver(round_id: str, no: int) -> Callable[[list[SeatResult]], None]:
    def save_edit(results: list[SeatResult]) -> None:
        try:
            games_repo.update_round(round_id, results, seat_to_player)
        except AppError as exc:
            st.error(str(exc))
            return
        ui.flash(f"{no}回目を書き換えました。")
        st.rerun()

    return save_edit


@st.fragment
def entry_area() -> None:
    st.markdown("### 今回の持ち点を入力")

    # 半荘を1つ保存するたびにキーが変わるので、入力欄は自動的に初期値へ戻る。
    # 逆に、保存に失敗している間はキーが変わらないので打った値が消えない。
    score_entry(
        key=f"new_{game_id}_{len(rounds)}",
        base_kazes=next_kazes(),
        base_scores=[rules.start_score] * len(seats),
        submit_label=f"{len(rounds) + 1}半荘目を登録",
        on_save=save_new,
    )

    if not rounds:
        st.info("まだ半荘の記録がありません。")
        return

    # --- 記録 ---
    st.markdown("### 記録")

    totals: dict[str, int] = {s["player_id"]: 0 for s in seats}
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

    # --- 修正・削除 ---
    st.markdown("### 修正・削除")

    for rnd in rounds:
        summary = "  ".join(
            f"{player_names.get(r['player_id'], '?')} {ui.format_point(r['point'])}"
            for r in rnd["results"]
        )
        with st.expander(f"{rnd['no']}回目 ・ {summary}"):
            ordered = sorted(rnd["results"], key=lambda x: x["seat"])
            # 保存済みの内容をキーに混ぜる。書き換えが成功して中身が変われば
            # キーも変わるので、入力欄はDBの最新値に追従する。
            # 逆に保存に失敗した間はキーが変わらないので、打った値が消えない。
            # hash() はプロセスごとに値が変わるので使わない。素直に内容を並べる。
            stored = "-".join(f"{r['seat']}{r['kaze']}{r['raw_score']}" for r in ordered)

            score_entry(
                key=f"edit_{rnd['id']}_{stored}",
                base_kazes=[r["kaze"] for r in ordered],
                base_scores=[r["raw_score"] for r in ordered],
                submit_label="この内容に書き換える",
                on_save=make_saver(rnd["id"], rnd["no"]),
                allow_auto=False,
            )
            st.caption(
                "⚠️ 書き換えると元の持ち点は残りません。"
                "取り消せないので、内容を確かめてから押してください。"
            )

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


entry_area()
