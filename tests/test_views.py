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


def new_round_inputs(app: AppTest):
    """新規入力フォームの点数欄だけ。修正用フォームの欄と混ざらないよう key で絞る。"""
    return [n for n in app.number_input if (n.key or "").startswith("new_")]


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
    assert len(app.number_input) == 3, "自動計算の席には入力欄が出ないはず"

    app.number_input[0].set_value(40000)
    app.number_input[1].set_value(30000)
    app.number_input[2].set_value(20000)
    app = submit(app).click().run()

    assert not app.exception
    assert len(backend.rounds) == 1
    saved = backend.rounds[0]["results"]
    # 4人目は 100000 - 90000 = 10000 が自動で入る
    assert [r["raw_score"] for r in saved] == [40000, 30000, 20000, 10000]
    assert sum(r["point"] for r in saved) == 0


def test_score_inputs_reset_after_saving(monkeypatch, backend):
    """保存後に前回の点数が残っていると、連打で無言の二重登録になる。"""
    app = run("views/game.py", monkeypatch, backend, game=backend.game_id)
    app.number_input[0].set_value(40000)
    app.number_input[1].set_value(30000)
    app.number_input[2].set_value(20000)
    app = submit(app).click().run()

    assert len(backend.rounds) == 1
    # 入力欄は初期値（配給原点）に戻っていること
    assert [n.value for n in new_round_inputs(app)] == [25000, 25000, 25000]


def test_save_shows_confirmation_after_rerun(monkeypatch, backend):
    """st.success の直後に st.rerun() すると表示が破棄される。

    フラッシュメッセージにして、再描画後に必ず出るようにしてある。
    """
    app = run("views/game.py", monkeypatch, backend, game=backend.game_id)
    app.number_input[0].set_value(40000)
    app.number_input[1].set_value(30000)
    app.number_input[2].set_value(20000)
    app = submit(app).click().run()

    assert any("登録しました" in s.value for s in app.success)


def test_winds_rotate_for_the_next_round(monkeypatch, backend):
    """親は毎半荘移る。前の半荘の風を1つずらした値が初期値になる。"""
    app = run("views/game.py", monkeypatch, backend, game=backend.game_id)
    assert [s.value for s in app.selectbox[:4]] == ["東", "南", "西", "北"]

    app.number_input[0].set_value(40000)
    app.number_input[1].set_value(30000)
    app.number_input[2].set_value(20000)
    app = submit(app).click().run()

    assert [s.value for s in app.selectbox[:4]] == ["南", "西", "北", "東"]


def test_wrong_total_is_rejected(monkeypatch, backend):
    """合計が合わないまま保存すると、差額がまるごとトップの得点になる。"""
    app = run("views/game.py", monkeypatch, backend, game=backend.game_id)
    app.checkbox[0].set_value(False)  # 自動計算を切る
    app = app.run()

    assert len(app.number_input) == 4
    for i, value in enumerate([40000, 30000, 20000, 5000]):  # 合計95,000
        app.number_input[i].set_value(value)
    app = submit(app).click().run()

    assert not backend.rounds, "合計が合わない入力を保存してはいけない"
    assert any("95,000" in e.value for e in app.error)


def test_wrong_total_is_allowed_when_explicitly_permitted(monkeypatch, backend):
    """供託が残る卓もあるので、明示的に許可すれば通す。"""
    app = run("views/game.py", monkeypatch, backend, game=backend.game_id)
    app.checkbox[0].set_value(False)
    app = app.run()
    app.checkbox[1].set_value(True)  # 供託あり
    app = app.run()

    for i, value in enumerate([40000, 30000, 20000, 5000]):
        app.number_input[i].set_value(value)
    app = submit(app).click().run()

    assert len(backend.rounds) == 1
    assert sum(r["point"] for r in backend.rounds[0]["results"]) == 0


def test_score_entry_uses_a_single_form(monkeypatch, backend):
    """入力ごとにサーバーと往復しないよう、フォームにまとめてある。"""
    app = run("views/game.py", monkeypatch, backend, game=backend.game_id)
    assert submit(app) is not None


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

    assert len(app.number_input) == 2  # 3人 − 自動計算1人
    app.number_input[0].set_value(50000)
    app.number_input[1].set_value(35000)
    app = submit(app).click().run()

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
    scope.set_value("2026年春麻雀大会")
    app = app.run()
    assert not app.exception
