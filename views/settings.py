"""タイトルのルール設定・プレイヤー管理・削除。"""

from __future__ import annotations

import streamlit as st

from mahjong import repo, ui
from mahjong.rules import RuleSet
from mahjong.scoring import ScoringError, calc_round

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

st.title(f"⚙️ {title['name']} の設定")

tab_rules, tab_players, tab_danger = st.tabs(
    ["ルール", "プレイヤー", "タイトルの操作"]
)

# --- ルール -----------------------------------------------------------------

with tab_rules:
    st.markdown("#### 現在のルール")
    st.code(ui.ruleset_summary(rules))

    st.markdown("#### 変更")
    new_rules = ui.ruleset_editor(rules, key_prefix=f"rules_{title_id}")

    st.caption(
        "保存しただけでは過去の記録は変わりません。"
        "下の再計算を実行すると、保存されている持ち点から全半荘を計算し直します。"
    )

    if st.button("ルールを保存", type="primary", use_container_width=True):
        try:
            repo.update_ruleset(title_id, new_rules)
        except repo.RepoError as exc:
            st.error(str(exc))
        else:
            st.success("保存しました。")
            st.rerun()

    st.divider()
    st.markdown("#### 過去のデータを再計算")
    st.caption(
        "入力された持ち点を保存しているため、ウマや返し点を変えても"
        "過去の全半荘をやり直せます。"
    )

    stored = repo.fetch_stored_rounds_for_recalc(title_id)
    st.write(f"対象: {len(stored)} 半荘")

    if stored and st.button("現在のルールで再計算する", use_container_width=True):
        mismatched = [r for r in stored if len(r["raw_scores"]) != rules.player_count]
        if mismatched:
            st.error(
                f"{len(mismatched)} 件の半荘が人数と合いません"
                f"（ルールは{rules.player_count}人）。先にルールの人数を合わせてください。"
            )
        else:
            updated = 0
            failed: list[str] = []
            progress = st.progress(0.0)
            for index, rnd in enumerate(stored, start=1):
                try:
                    results = calc_round(
                        rnd["raw_scores"], rnd["kazes"], rules, seats=rnd["seats"]
                    )
                    seat_to_player = dict(zip(rnd["seats"], rnd["player_ids"]))
                    repo.update_round(rnd["round_id"], results, seat_to_player)
                    updated += 1
                except (ScoringError, repo.RepoError) as exc:
                    failed.append(str(exc))
                progress.progress(index / len(stored))

            if failed:
                st.error(f"{len(failed)} 件で失敗しました: {failed[0]}")
            st.success(f"{updated} 半荘を再計算しました。")

# --- プレイヤー -------------------------------------------------------------

with tab_players:
    players = repo.list_players(title_id)

    st.markdown("#### プレイヤーを追加")
    new_name = st.text_input("名前", key=f"add_player_{title_id}")
    if st.button("追加", use_container_width=True):
        try:
            repo.create_player(title_id, new_name)
        except repo.RepoError as exc:
            st.error(str(exc))
        else:
            st.success("追加しました。")
            st.rerun()

    st.divider()
    st.markdown("#### 登録済みプレイヤー")

    if not players:
        st.info("まだプレイヤーがいません。")

    for player in players:
        with st.container(border=True):
            col_name, col_save, col_del = st.columns([3, 1, 1])
            with col_name:
                edited = st.text_input(
                    "名前",
                    value=player["name"],
                    key=f"pname_{player['id']}",
                    label_visibility="collapsed",
                )
            with col_save:
                if st.button("保存", key=f"psave_{player['id']}", use_container_width=True):
                    try:
                        repo.rename_player(player["id"], edited)
                    except repo.RepoError as exc:
                        st.error(str(exc))
                    else:
                        st.rerun()
            with col_del:
                if st.button("削除", key=f"pdel_{player['id']}", use_container_width=True):
                    st.session_state["pending_delete_player"] = player["id"]

            if st.session_state.get("pending_delete_player") == player["id"]:
                st.warning(
                    f"「{player['name']}」を削除します。"
                    "過去の成績は残りますが、新しい対戦では選べなくなります。"
                )
                col_yes, col_no = st.columns(2)
                with col_yes:
                    if st.button(
                        "削除する", key=f"pyes_{player['id']}", type="primary",
                        use_container_width=True,
                    ):
                        try:
                            repo.delete_player(player["id"])
                        except repo.RepoError as exc:
                            st.error(str(exc))
                        else:
                            st.session_state.pop("pending_delete_player", None)
                            st.rerun()
                with col_no:
                    if st.button(
                        "やめる", key=f"pno_{player['id']}", use_container_width=True
                    ):
                        st.session_state.pop("pending_delete_player", None)
                        st.rerun()

# --- タイトルの操作 ---------------------------------------------------------

with tab_danger:
    st.markdown("#### タイトル名の変更")
    renamed = st.text_input("タイトル名", value=title["name"], key=f"tname_{title_id}")
    if st.button("名前を保存", use_container_width=True):
        try:
            repo.rename_title(title_id, renamed)
        except repo.RepoError as exc:
            st.error(str(exc))
        else:
            st.success("保存しました。")
            st.rerun()

    st.divider()
    st.markdown("#### 対戦の削除")

    games = repo.list_games(title_id)
    if not games:
        st.caption("削除できる対戦はありません。")
    for game in games:
        col_name, col_del = st.columns([3, 1])
        with col_name:
            st.write(f"**{game['name']}**　({game['round_count']}半荘)")
        with col_del:
            if st.button("削除", key=f"gdel_{game['id']}", use_container_width=True):
                st.session_state["pending_delete_game"] = game["id"]

        if st.session_state.get("pending_delete_game") == game["id"]:
            st.warning(f"「{game['name']}」を削除します。成績から除外されます。")
            col_yes, col_no = st.columns(2)
            with col_yes:
                if st.button(
                    "削除する", key=f"gyes_{game['id']}", type="primary",
                    use_container_width=True,
                ):
                    try:
                        repo.delete_game(game["id"])
                    except repo.RepoError as exc:
                        st.error(str(exc))
                    else:
                        st.session_state.pop("pending_delete_game", None)
                        st.rerun()
            with col_no:
                if st.button("やめる", key=f"gno_{game['id']}", use_container_width=True):
                    st.session_state.pop("pending_delete_game", None)
                    st.rerun()

    st.divider()
    st.markdown("#### タイトルの削除")
    st.caption("一覧から消えます。データ自体は残るため、必要なら復元を依頼できます。")

    if st.button("このタイトルを削除", use_container_width=True):
        st.session_state["pending_delete_title"] = title_id

    if st.session_state.get("pending_delete_title") == title_id:
        st.error(f"「{title['name']}」を削除します。よろしいですか？")
        col_yes, col_no = st.columns(2)
        with col_yes:
            if st.button("削除する", type="primary", use_container_width=True):
                try:
                    repo.delete_title(title_id)
                except repo.RepoError as exc:
                    st.error(str(exc))
                else:
                    st.session_state.pop("pending_delete_title", None)
                    ui.goto("views/home.py")
        with col_no:
            if st.button("やめる", use_container_width=True):
                st.session_state.pop("pending_delete_title", None)
                st.rerun()
