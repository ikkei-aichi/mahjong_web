"""画面のテスト。Supabase に繋がず Streamlit の AppTest で実行する。

旧実装の不具合はほとんどが Streamlit のウィジェット状態まわりで、
ドメイン層のテストでは絶対に捕まえられなかった。ここで押さえる。
"""

from __future__ import annotations

import pathlib

import pytest
from streamlit.testing.v1 import AppTest

from mahjong.rules import PRESETS_3P
from tests.fake_backend import FakeBackend, install

TIMEOUT = 30
ROOT = pathlib.Path(__file__).resolve().parent.parent


def run(path: str, monkeypatch, backend: FakeBackend, **params):
    install(monkeypatch, backend)
    # AppTest は相対パスを「呼び出したファイル」基準で解決するので絶対パスを渡す
    app = AppTest.from_file(str(ROOT / path), default_timeout=TIMEOUT)
    for key, value in params.items():
        app.query_params[key] = value
    return app.run()


def submit(app: AppTest, contains: str = ""):
    """フォームの送信ボタン。AppTest では st.button と同じ一覧に form_id 付きで出る。"""
    for button in app.button:
        if button.form_id and (not contains or contains in button.label):
            return button
    raise AssertionError(
        f"送信ボタンが見つからない（contains={contains!r}）: "
        + repr([(b.label, b.form_id) for b in app.button])
    )


def click(app: AppTest, contains: str):
    """ラベルにその文字を含むボタン。"""
    for button in app.button:
        if contains in button.label:
            return button
    raise AssertionError(
        f"ボタンが見つからない（{contains!r}）: " + repr([b.label for b in app.button])
    )


def new_round_inputs(app: AppTest):
    """新規入力の点数欄だけ。修正用の欄と混ざらないよう key で絞る。"""
    return [n for n in app.number_input if (n.key or "").startswith("new_")]


def new_round_winds(app: AppTest):
    return [s for s in app.selectbox if (s.key or "").startswith("new_")]


def texts(app: AppTest) -> str:
    """画面に出ている文字列をまとめて1本にする（含まれるか調べる用）。"""
    parts = []
    for collection in (
        app.markdown, app.title, app.caption, app.info, app.warning,
        app.error, app.success, app.metric, app.text,
    ):
        for element in collection:
            parts.append(str(getattr(element, "value", "")))
            parts.append(str(getattr(element, "label", "")))
    return "\n".join(parts)


@pytest.fixture
def backend():
    return FakeBackend()


# --- 起動（app.py 経由） ---------------------------------------------------
# ビューを直接実行するテストでは、app.py のページ登録との整合を検出できない。
# 実際、グループ未所属のときに onboarding だけを登録していたせいで、
# グループ作成直後の views/home.py への遷移が落ちていた。


def test_app_starts_for_a_user_with_a_group(monkeypatch, backend):
    install(monkeypatch, backend)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=TIMEOUT)
    app.query_params["group"] = backend.group_id
    app = app.run()
    assert not app.exception, app.exception
    assert "テスト麻雀会" in texts(app)


def test_app_starts_for_a_user_with_no_group(monkeypatch, backend):
    """所属ゼロなら「はじめに」が既定ページになる。"""
    install(monkeypatch, backend)
    import mahjong.session as session
    from mahjong.repo import groups as groups_repo

    monkeypatch.setattr(session, "my_groups", lambda: [])
    monkeypatch.setattr(groups_repo, "list_my_groups", lambda: [])

    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=TIMEOUT).run()
    assert not app.exception, app.exception
    assert "はじめに" in texts(app)


def test_creating_a_group_navigates_to_home(monkeypatch, backend):
    """グループ作成直後の遷移先が登録されていないと Could not find page で落ちる。"""
    install(monkeypatch, backend)
    import mahjong.session as session
    from mahjong.repo import groups as groups_repo

    created: list[str] = []
    monkeypatch.setattr(session, "my_groups", lambda: [] if not created else [backend.group])
    monkeypatch.setattr(groups_repo, "list_my_groups", lambda: [] if not created else [backend.group])

    def fake_create(name, display_name=None):
        created.append(name)
        return backend.group_id

    monkeypatch.setattr(groups_repo, "create_group", fake_create)

    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=TIMEOUT).run()
    assert not app.exception

    # 「招待コードで参加」タブの入力欄も同じ画面にあるので、ラベルで選ぶ
    name_field = next(t for t in app.text_input if t.label == "グループ名")
    name_field.set_value("新しい会")
    app = submit(app, "作成").click().run()

    assert created == ["新しい会"]
    assert not app.exception, f"グループ作成後の遷移で落ちた: {app.exception}"


# --- 起動（ビュー単体） -----------------------------------------------------


def test_home_renders(monkeypatch, backend):
    app = run("views/home.py", monkeypatch, backend, group=backend.group_id)
    assert not app.exception
    assert "テスト麻雀会" in texts(app)


@pytest.mark.parametrize(
    "path,params",
    [
        ("views/home.py", {}),
        ("views/tournaments.py", {}),
        ("views/tournament.py", {"tournament": True}),
        ("views/day.py", {"day": True, "tournament": True}),
        ("views/game.py", {"game": True}),
        ("views/stats.py", {}),
        ("views/player.py", {}),
        ("views/members.py", {}),
        ("views/settings.py", {}),
    ],
)
def test_every_page_renders_without_exception(monkeypatch, backend, path, params):
    """どの画面もトレースバックを出さずに描画できること。"""
    resolved = {"group": backend.group_id}
    if params.get("tournament"):
        resolved["tournament"] = backend.tournament_id
    if params.get("day"):
        resolved["day"] = backend.day_id
    if params.get("game"):
        resolved["game"] = backend.game_id

    app = run(path, monkeypatch, backend, **resolved)
    assert not app.exception, f"{path}: {app.exception}"


# --- スコア入力 -------------------------------------------------------------


def test_score_entry_saves_a_round(monkeypatch, backend):
    app = run("views/game.py", monkeypatch, backend, game=backend.game_id)
    assert not app.exception

    # 自動計算が既定でオン → 3人ぶん入れれば足りる
    assert app.checkbox[0].value is True
    assert len(new_round_inputs(app)) == 3, "自動計算の席には入力欄が出ないはず"

    for i, value in enumerate([40000, 30000, 20000]):
        new_round_inputs(app)[i].set_value(value)
    app = click(app, "半荘目を登録").click().run()

    assert not app.exception
    assert len(backend.rounds) == 1
    saved = backend.rounds[0]["results"]
    # 4人目は 100000 - 90000 = 10000 が自動で入る
    assert [r["raw_score"] for r in saved] == [40000, 30000, 20000, 10000]
    assert sum(r["point"] for r in saved) == 0


def test_score_inputs_reset_after_saving(monkeypatch, backend):
    """保存後に前回の点数が残っていると、連打で無言の二重登録になる。"""
    app = run("views/game.py", monkeypatch, backend, game=backend.game_id)
    for i, value in enumerate([40000, 30000, 20000]):
        new_round_inputs(app)[i].set_value(value)
    app = click(app, "半荘目を登録").click().run()

    assert len(backend.rounds) == 1
    assert [n.value for n in new_round_inputs(app)] == [25000, 25000, 25000]


def test_save_shows_confirmation_after_rerun(monkeypatch, backend):
    """st.success の直後に st.rerun() すると表示が破棄される。

    フラッシュメッセージにして、再描画後に必ず出るようにしてある。
    """
    app = run("views/game.py", monkeypatch, backend, game=backend.game_id)
    for i, value in enumerate([40000, 30000, 20000]):
        new_round_inputs(app)[i].set_value(value)
    app = click(app, "半荘目を登録").click().run()

    assert any("登録しました" in s.value for s in app.success)


def test_winds_rotate_for_the_next_round(monkeypatch, backend):
    """親は毎半荘移る。前の半荘の風を1つずらした値が初期値になる。

    ずれる向きは打順（東→南→西→北）と同じで、**前回の南家が次の東家**になる。
    逆回りにすると前回の北家が親になってしまい、風別成績が実態とずれる。
    """
    app = run("views/game.py", monkeypatch, backend, game=backend.game_id)
    assert [s.value for s in new_round_winds(app)] == ["東", "南", "西", "北"]

    for i, value in enumerate([40000, 30000, 20000]):
        new_round_inputs(app)[i].set_value(value)
    app = click(app, "半荘目を登録").click().run()

    # 席1（前回の南家）が次の東家になる
    assert [s.value for s in new_round_winds(app)] == ["北", "東", "南", "西"]


# --- ライブ計算 -------------------------------------------------------------


def test_points_are_shown_while_typing(monkeypatch, backend):
    """登録する前に、その場で順位とポイントが見えること。"""
    app = run("views/game.py", monkeypatch, backend, game=backend.game_id)
    for i, value in enumerate([40000, 30000, 20000]):
        new_round_inputs(app)[i].set_value(value)
    app = app.run()

    shown = texts(app)
    # ゴットー(10/5/-5/-10)・返し30000 → +40 / +5 / -15 / -30
    for expected in ("+40", "+5", "-15", "-30"):
        assert expected in shown, f"{expected} が表示されていない"
    assert "1位" in shown and "4位" in shown


def test_auto_calculated_seat_shows_its_value(monkeypatch, backend):
    """自動計算の席にも、実際に入る点数が出ること。"""
    app = run("views/game.py", monkeypatch, backend, game=backend.game_id)
    for i, value in enumerate([40000, 30000, 20000]):
        new_round_inputs(app)[i].set_value(value)
    app = app.run()

    # 100,000 - (40,000 + 30,000 + 20,000) = 10,000
    assert "10,000" in texts(app)


def test_total_difference_is_shown_live(monkeypatch, backend):
    """合計と、10万点との差額がその場で分かること。"""
    app = run("views/game.py", monkeypatch, backend, game=backend.game_id)
    app.checkbox[0].set_value(False)
    app = app.run()

    for i, value in enumerate([40000, 33000, 20000, 10000]):  # 合計103,000
        new_round_inputs(app)[i].set_value(value)
    app = app.run()

    assert any("103,000" in e.value and "+3,000" in e.value for e in app.error)


def test_matching_total_is_shown_as_ok(monkeypatch, backend):
    app = run("views/game.py", monkeypatch, backend, game=backend.game_id)
    app.checkbox[0].set_value(False)
    app = app.run()

    for i, value in enumerate([40000, 30000, 20000, 10000]):
        new_round_inputs(app)[i].set_value(value)
    app = app.run()

    assert any("一致" in s.value for s in app.success)


def test_duplicate_winds_are_flagged(monkeypatch, backend):
    app = run("views/game.py", monkeypatch, backend, game=backend.game_id)
    new_round_winds(app)[1].set_value("東")
    app = app.run()

    assert any("風が重複" in w.value for w in app.warning)


# --- 合計が合わないとき -----------------------------------------------------


def test_wrong_total_disables_the_save_button(monkeypatch, backend):
    """合計が合わないまま保存すると、差額がまるごとトップの得点になる。

    エラーを出すだけでなく、そもそも押せないようにする。
    """
    app = run("views/game.py", monkeypatch, backend, game=backend.game_id)
    app.checkbox[0].set_value(False)
    app = app.run()

    for i, value in enumerate([40000, 30000, 20000, 5000]):  # 合計95,000
        new_round_inputs(app)[i].set_value(value)
    app = app.run()

    assert click(app, "半荘目を登録").disabled, "合計が合わないのに押せてしまう"
    assert any("95,000" in e.value for e in app.error)
    assert not backend.rounds


def test_rejected_input_is_not_wiped(monkeypatch, backend):
    """合計が合わなくても、打った点数が消えないこと。

    st.form(clear_on_submit=True) は送信のたびに無条件でクリアするため、
    1か所打ち間違えただけで全部打ち直しになっていた。
    """
    app = run("views/game.py", monkeypatch, backend, game=backend.game_id)
    app.checkbox[0].set_value(False)
    app = app.run()

    typed = [40000, 33000, 20000, 10000]
    for i, value in enumerate(typed):
        new_round_inputs(app)[i].set_value(value)
    app = app.run()

    assert [n.value for n in new_round_inputs(app)] == typed
    assert [s.value for s in new_round_winds(app)] == ["東", "南", "西", "北"]


def test_wrong_total_is_allowed_when_explicitly_permitted(monkeypatch, backend):
    """供託が残る卓もあるので、明示的に許可すれば通す。"""
    app = run("views/game.py", monkeypatch, backend, game=backend.game_id)
    app.checkbox[0].set_value(False)
    app = app.run()

    for i, value in enumerate([40000, 30000, 20000, 5000]):
        new_round_inputs(app)[i].set_value(value)
    app = app.run()

    mismatch = next(c for c in app.checkbox if "供託" in c.label)
    app = mismatch.set_value(True).run()

    app = click(app, "半荘目を登録").click().run()
    assert len(backend.rounds) == 1
    assert sum(r["point"] for r in backend.rounds[0]["results"]) == 0


# --- ルール編集（旧実装で壊れていたところ） --------------------------------


def test_preset_change_actually_updates_the_inputs(monkeypatch, backend):
    """プリセットを変えても入力欄が変わらない不具合の回帰テスト。

    key 付きウィジェットは2回目以降 value= を無視するため、
    プリセットが変わったらキーごと変える必要がある。
    """
    app = run("views/tournaments.py", monkeypatch, backend, group=backend.group_id)
    assert not app.exception

    preset = next(s for s in app.selectbox if s.label == "プリセット")
    preset.set_value("ウマなし")
    app = app.run()
    uma_no = [n.value for n in app.number_input if n.label in ("2位", "3位", "4位")]

    preset = next(s for s in app.selectbox if s.label == "プリセット")
    preset.set_value("ウマ大 (10-30)")
    app = app.run()
    uma_big = [n.value for n in app.number_input if n.label in ("2位", "3位", "4位")]

    assert uma_no != uma_big, "プリセットを変えたのにウマが変わっていない"
    assert uma_big == [10, -10, -30]


def test_switching_player_count_does_not_crash(monkeypatch, backend):
    """人数を4→3に変えると KeyError で落ちていた不具合の回帰テスト。"""
    app = run("views/tournaments.py", monkeypatch, backend, group=backend.group_id)

    preset = next(s for s in app.selectbox if s.label == "プリセット")
    preset.set_value("ワンツー (10-20)")
    app = app.run()
    assert not app.exception

    count = next(r for r in app.radio if r.label == "人数")
    count.set_value(3)
    app = app.run()

    assert not app.exception, f"人数変更で落ちた: {app.exception}"


def test_first_place_uma_is_derived_and_zero_sum(monkeypatch, backend):
    """1位のウマは2位以下の反転。設定画面から零和でないウマを保存できない。"""
    app = run("views/tournaments.py", monkeypatch, backend, group=backend.group_id)
    labels = [n.label for n in app.number_input]
    assert "1位" not in labels, "1位は自動計算なので入力欄を出さない"
    assert {"2位", "3位", "4位"} <= set(labels)


# --- 3人麻雀 ----------------------------------------------------------------


def test_three_player_tournament_shows_three_seats(monkeypatch):
    """3人用ルールの大会で4席出すと、入力不能な卓ができてしまう。"""
    backend = FakeBackend(rules=PRESETS_3P["三人麻雀 ウマなし"])
    app = run("views/day.py", monkeypatch, backend, day=backend.day_id, group=backend.group_id)
    assert not app.exception

    # 席は2カラムに振り分けて描画するので、走査順は席1→席3→席2になる。並べ直して比べる。
    seat_labels = sorted(
        s.label for s in app.selectbox if s.label and s.label.startswith("席")
    )
    assert seat_labels == ["席1", "席2", "席3"]


def test_three_player_score_entry(monkeypatch):
    backend = FakeBackend(rules=PRESETS_3P["三人麻雀 ウマなし"])
    app = run("views/game.py", monkeypatch, backend, game=backend.game_id)
    assert not app.exception

    assert len(new_round_inputs(app)) == 2  # 3人 − 自動計算1人
    new_round_inputs(app)[0].set_value(50000)
    new_round_inputs(app)[1].set_value(35000)
    app = click(app, "半荘目を登録").click().run()

    assert len(backend.rounds) == 1
    saved = backend.rounds[0]["results"]
    assert [r["raw_score"] for r in saved] == [50000, 35000, 20000]  # 合計105,000
    assert sum(r["point"] for r in saved) == 0


# --- 別グループのデータを開けないこと ---------------------------------------


def test_game_from_another_group_is_rejected(monkeypatch, backend):
    """URL を書き換えて別グループの対戦を開けないこと。"""
    backend.games[0]["group_id"] = "other-group"
    app = run("views/game.py", monkeypatch, backend, game=backend.game_id)
    assert not app.exception
    assert any("見つかりません" in w.value for w in app.warning)


def test_missing_parameter_shows_guidance_not_traceback(monkeypatch, backend):
    app = run("views/game.py", monkeypatch, backend, group=backend.group_id)
    assert not app.exception
    assert any("指定されていません" in w.value for w in app.warning)


# --- 本人紐付け -------------------------------------------------------------


def test_provisional_member_is_prompted_to_link(monkeypatch):
    backend = FakeBackend(provisional=True)
    app = run("views/members.py", monkeypatch, backend, group=backend.group_id)
    assert not app.exception
    assert "あなたは誰ですか" in texts(app)


def test_home_shows_provisional_notice(monkeypatch):
    backend = FakeBackend(provisional=True)
    app = run("views/home.py", monkeypatch, backend, group=backend.group_id)
    assert not app.exception
    assert "自分の名前を選ぶ" in "\n".join(b.label for b in app.button)


# --- 成績 -------------------------------------------------------------------


def test_stats_page_with_records(monkeypatch, backend):
    from mahjong.scoring import calc_round

    seat_to_player = {s["seat"]: s["player_id"] for s in backend.games[0]["seats"]}
    results = calc_round(
        [40000, 30000, 20000, 10000], ["東", "南", "西", "北"], backend.rules
    )
    backend.add_round(backend.game_id, results, seat_to_player)

    app = run("views/stats.py", monkeypatch, backend, group=backend.group_id)
    assert not app.exception
    assert "総合" in texts(app)


def test_delete_needs_two_steps_and_then_deletes(monkeypatch, backend):
    """削除は2段階。1回目で確認を出し、2回目で実行する。

    1回目の押下は st.rerun() を挟むので、確認待ちの状態を
    ページ側で毎回消してしまうと確認画面が永久に出ない。
    """
    from mahjong.scoring import calc_round

    seat_to_player = {s["seat"]: s["player_id"] for s in backend.games[0]["seats"]}
    backend.add_round(
        backend.game_id,
        calc_round([40000, 30000, 20000, 10000], ["東", "南", "西", "北"], backend.rules),
        seat_to_player,
    )

    app = run("views/game.py", monkeypatch, backend, game=backend.game_id)
    delete = next(b for b in app.button if "回目を削除" in b.label)
    app = delete.click().run()

    assert len(backend.rounds) == 1, "1回押しただけで消えてはいけない"
    assert any("よろしいですか" in w.value for w in app.warning), "確認が出ていない"

    confirm = next(b for b in app.button if b.label == "削除する")
    app = confirm.click().run()

    assert not backend.rounds
    assert any("削除しました" in s.value for s in app.success)


def test_delete_can_be_cancelled(monkeypatch, backend):
    from mahjong.scoring import calc_round

    seat_to_player = {s["seat"]: s["player_id"] for s in backend.games[0]["seats"]}
    backend.add_round(
        backend.game_id,
        calc_round([40000, 30000, 20000, 10000], ["東", "南", "西", "北"], backend.rules),
        seat_to_player,
    )

    app = run("views/game.py", monkeypatch, backend, game=backend.game_id)
    app = next(b for b in app.button if "回目を削除" in b.label).click().run()
    app = next(b for b in app.button if b.label == "やめる").click().run()

    assert len(backend.rounds) == 1
    assert not any("よろしいですか" in w.value for w in app.warning)


def test_recalculation_is_applied_in_one_call(monkeypatch, backend):
    """再計算は半荘ごとのループではなく、1回でまとめて適用する。

    旧実装は半荘ごとにRPCを呼んでいたため、途中で通信が切れると
    新旧のルールが混在したまま判別できなくなっていた。
    """
    from mahjong.scoring import calc_round

    seat_to_player = {s["seat"]: s["player_id"] for s in backend.games[0]["seats"]}
    for scores in ([40000, 30000, 20000, 10000], [10000, 20000, 30000, 40000]):
        backend.add_round(
            backend.game_id,
            calc_round(scores, ["東", "南", "西", "北"], backend.rules),
            seat_to_player,
        )

    app = run(
        "views/settings.py", monkeypatch, backend,
        group=backend.group_id, tournament=backend.tournament_id,
    )
    assert not app.exception

    recalc = next(b for b in app.button if "再計算する" in b.label)
    app = recalc.click().run()

    assert not app.exception
    assert backend.calls.count("apply_recalculated_rounds") == 1
    assert any("2半荘を再計算しました" in s.value for s in app.success)


def test_stats_scope_selector_survives_switching(monkeypatch, backend):
    """集計範囲を切り替えても、存在しないプレイヤー名が選択されたままにならない。"""
    from mahjong.scoring import calc_round

    seat_to_player = {s["seat"]: s["player_id"] for s in backend.games[0]["seats"]}
    backend.add_round(
        backend.game_id,
        calc_round([40000, 30000, 20000, 10000], ["東", "南", "西", "北"], backend.rules),
        seat_to_player,
    )

    app = run("views/stats.py", monkeypatch, backend, group=backend.group_id)
    scope = next(s for s in app.selectbox if s.label == "集計範囲")
    # 選択肢の実体は大会ID（表示名ではない）
    scope.set_value(backend.tournament_id)
    app = app.run()
    assert not app.exception


# --- 同名の項目を取り違えないこと -------------------------------------------
# 大会名・グループ名には一意制約が無い。選択結果を表示名から引き直していると
# `labels.index(chosen)` が必ず1つ目を返すため、「2つ目を選んだのに
# 1つ目のルールを上書き・削除する」取り違えが起きていた。


def two_tournaments_with_the_same_name(backend: FakeBackend) -> str:
    """同じ名前の大会をもう1つ足して、その ID を返す。"""
    first = backend.tournaments[0]
    second = dict(first)
    second["id"] = "trn-second"
    second["name"] = first["name"]
    second["note"] = "2つ目の大会"
    backend.tournaments = [first, second]
    return second["id"]


def test_settings_edits_the_tournament_named_in_the_url(monkeypatch, backend):
    """同名の大会が2つあっても、URL で指定した方が編集対象になること。"""
    second_id = two_tournaments_with_the_same_name(backend)

    app = run("views/settings.py", monkeypatch, backend, tournament=second_id)
    assert not app.exception

    assert any("2つ目の大会" == t.value for t in app.text_area), (
        "URL で2つ目を指定したのに1つ目が編集対象になっている: "
        + repr([t.value for t in app.text_area])
    )


def test_settings_switches_to_the_chosen_tournament_when_names_collide(monkeypatch, backend):
    """同名の大会でも、選び直せば対象が入れ替わること。"""
    second_id = two_tournaments_with_the_same_name(backend)

    app = run("views/settings.py", monkeypatch, backend)
    picker = next(s for s in app.selectbox if s.label == "大会")
    # 選択肢の実体は大会ID。同名でも表示には連番が付いて見分けられる。
    assert picker.options == ["2026年春麻雀大会 (1)", "2026年春麻雀大会 (2)"]

    app = picker.set_value(second_id).run()
    assert not app.exception
    assert any("2つ目の大会" == t.value for t in app.text_area)


def test_stats_aggregates_the_tournament_named_in_the_url(monkeypatch, backend):
    """同名の大会が2つあっても、URL で指定した方が集計されること。"""
    second_id = two_tournaments_with_the_same_name(backend)

    install(monkeypatch, backend)
    from mahjong.repo import queries

    seen: list[tuple[str, str]] = []
    original = queries.fetch_entries
    monkeypatch.setattr(
        queries,
        "fetch_entries",
        lambda scope, value: (seen.append((scope, value)), original(scope, value))[1],
    )

    app = AppTest.from_file(str(ROOT / "views/stats.py"), default_timeout=TIMEOUT)
    app.query_params["tournament"] = second_id
    app.run()

    assert seen and seen[-1] == ("tournament_id", second_id), (
        f"URL で {second_id} を指定したのに {seen} で集計された"
    )


def test_group_picker_switches_between_groups_with_the_same_name(monkeypatch, backend):
    """同名のグループが2つあっても、2つ目に切り替えられること。"""
    other = dict(backend.group)
    other["group_id"] = "grp-second"  # 名前は1つ目と同じ

    # 本番と同じく「いま見ているグループ」は URL から解決する。
    # そうしないと切り替え後も current が1つ目のままになり、
    # サイドバーが毎回 st.rerun() を呼んで止まらなくなる。
    app = AppTest.from_string(
        "import streamlit as st\n"
        "from mahjong import session\n"
        "groups = st.session_state['_groups']\n"
        "session.sidebar_group_picker(groups, session.active_group(groups))\n",
        default_timeout=TIMEOUT,
    )
    app.session_state["_groups"] = [backend.group, other]
    app = app.run()

    picker = next(s for s in app.selectbox if s.label == "グループ")
    assert picker.options == ["テスト麻雀会 (1)", "テスト麻雀会 (2)"]

    app = picker.set_value("grp-second").run()

    # AppTest の query_params は値をリストで返す
    chosen = app.query_params.get("group")
    chosen = chosen[0] if isinstance(chosen, list) else chosen
    assert chosen == "grp-second", (
        f"2つ目のグループを選んでも切り替わらない: {dict(app.query_params)}"
    )


# --- 壊れたデータで画面ごと落とさないこと -----------------------------------


def test_tournaments_page_survives_a_non_dict_ruleset(monkeypatch, backend):
    """ruleset は jsonb なので、SQL から文字列や配列も書けてしまう。

    旧実装は素の AttributeError が漏れて大会一覧が丸ごと開けなくなっていた。
    """
    backend.tournaments[0]["ruleset"] = "gosha_rokunyu"

    app = run("views/tournaments.py", monkeypatch, backend)
    assert not app.exception, f"大会一覧が落ちた: {app.exception}"
    assert any("形式が不正" in w.value for w in app.warning)


@pytest.mark.parametrize("role", [None, "viewer"])
def test_members_page_survives_an_unexpected_role(monkeypatch, backend, role):
    """想定外の役割が入っていてもメンバー画面を開けること。

    DB 側の CHECK 制約で防いではいるが、`.index()` の ValueError で
    画面が丸ごと開けなくなると、直すための操作すらできなくなる。
    """
    backend.players[1]["user_id"] = "user-2"
    backend.players[1]["role"] = role

    app = run("views/members.py", monkeypatch, backend)
    assert not app.exception, f"メンバー画面が落ちた: {app.exception}"
