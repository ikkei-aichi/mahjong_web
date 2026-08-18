"""設定。グループ名、大会のルール、削除。

ルールを変えても過去の記録は自動では変わらない。変えたければ「再計算」を押す。
再計算は保存済みの持ち点(raw_score)から全半荘を計算し直し、
**1回のトランザクションでまとめて適用する**。

旧実装は半荘ごとに個別のRPCを呼ぶPythonループで、途中で通信が切れると
新旧のルールが混在したまま、どれがどちらか判別できない状態になっていた。
しかも0件成功でも緑の「再計算しました」が出ていた。
"""

from __future__ import annotations

import streamlit as st

from mahjong import session, ui
from mahjong.errors import AppError
from mahjong.repo import groups as groups_repo
from mahjong.repo import queries, tournaments as tournaments_repo
from mahjong.scoring import ScoringError, calc_round

ui.show_flashes()
group = session.require_group()
is_admin = session.is_admin(group)

st.title("⚙️ 設定")

try:
    tournaments = tournaments_repo.list_tournaments(group["group_id"])
except AppError as exc:
    st.error(str(exc))
    st.stop()

tab_tournament, tab_group = st.tabs(["大会のルール", "グループ"])


# --- 大会のルール -----------------------------------------------------------

with tab_tournament:
    if not tournaments:
        st.info("まだ大会がありません。")
    else:
        wanted = ui.param("tournament")
        index = next((i for i, t in enumerate(tournaments) if t["id"] == wanted), 0)
        # 大会名に一意制約は無い。表示名で引き直すと、同名の大会が2つあるとき
        # 常に1つ目が選ばれ、2つ目のつもりで1つ目のルールを上書き・削除してしまう。
        tournament_id = ui.select_one(
            "大会",
            [t["id"] for t in tournaments],
            [t["name"] for t in tournaments],
            index=index,
            key="settings_tournament",
        )
        tournament = next(t for t in tournaments if t["id"] == tournament_id)

        current_rules, warnings = tournaments_repo.get_ruleset(tournament_id)
        if warnings:
            st.warning("保存されているルールに不備があったため補正しました: " + " / ".join(warnings))

        st.markdown("#### 大会の情報")
        # 名前が変わったらキーも変える（未保存の編集が保存済みに見えないように）
        new_name = st.text_input(
            "大会名", value=tournament["name"], key=f"tn_{tournament_id}_{tournament['name']}"
        )
        new_note = st.text_area(
            "詳細・メモ",
            value=tournament.get("note") or "",
            key=f"tnote_{tournament_id}_{tournament.get('note')}",
        )
        if st.button("情報を保存", key="save_info", width="stretch"):
            try:
                tournaments_repo.update_tournament(
                    tournament_id, name=new_name, note=new_note
                )
            except AppError as exc:
                st.error(str(exc))
            else:
                ui.flash("大会の情報を保存しました。")
                st.rerun()

        st.markdown("#### ルール")
        edited = ui.ruleset_editor(current_rules, key_prefix=f"rules_{tournament_id}")

        st.caption(
            "保存しただけでは過去の記録は変わりません。"
            "下の「再計算」を実行すると、保存されている持ち点から全半荘を計算し直します。"
        )

        if st.button(
            "ルールを保存", type="primary", key="save_rules",
            width="stretch", disabled=edited is None,
        ):
            try:
                tournaments_repo.update_tournament(tournament_id, rules=edited)
            except AppError as exc:
                st.error(str(exc))
            else:
                ui.flash("ルールを保存しました。過去の記録に反映するには再計算してください。")
                st.rerun()

        # --- 再計算 ---
        st.markdown("#### 過去の記録を再計算")

        try:
            stored = queries.fetch_stored_rounds_for_recalc(tournament_id)
        except AppError as exc:
            st.error(str(exc))
            stored = []

        if not stored:
            st.caption("再計算できる記録がありません。")
        else:
            mismatched = [r for r in stored if len(r["seats"]) != current_rules.player_count]
            st.caption(
                f"対象: {len(stored)}半荘"
                + (f"（うち人数がルールと違うもの {len(mismatched)}件）" if mismatched else "")
            )
            if mismatched:
                st.warning(
                    f"{len(mismatched)}件の半荘が{current_rules.player_count}人用ルールと"
                    "人数が合わないため再計算できません。先にルールの人数を直してください。"
                )

            if st.button(
                "保存済みのルールで再計算する", key="recalc",
                width="stretch", disabled=bool(mismatched),
            ):
                calculated = []
                failures: list[str] = []
                for rnd in stored:
                    try:
                        results = calc_round(
                            rnd["raw_scores"],
                            rnd["kazes"],
                            current_rules,
                            seats=rnd["seats"],
                            # 保存済みの記録には供託で合計が合わないものが混ざりうる。
                            # ここで弾くと過去データを再計算できなくなる。
                            strict=False,
                        )
                    except ScoringError as exc:
                        failures.append(str(exc))
                        continue
                    calculated.append(
                        (rnd["round_id"], results, dict(zip(rnd["seats"], rnd["player_ids"])))
                    )

                if failures:
                    st.error(f"{len(failures)}件を計算できませんでした: {failures[0]}")
                elif not calculated:
                    st.warning("再計算できる半荘がありませんでした。")
                else:
                    try:
                        applied = queries.apply_recalculated_rounds(
                            tournament_id, current_rules, calculated
                        )
                    except AppError as exc:
                        st.error(str(exc))
                    else:
                        ui.flash(f"{applied}半荘を再計算しました。")
                        st.rerun()

        # --- 大会の削除 ---
        if is_admin:
            st.markdown("#### 大会の削除")
            if ui.confirm_delete(
                f"tournament_{tournament_id}",
                "🗑️ この大会を削除",
                f"「{tournament['name']}」を削除します。"
                "開催日・対戦・成績もすべて表示されなくなります。"
                "データベースには残るので、必要なら復元を依頼できます。",
                danger=True,
            ):
                try:
                    tournaments_repo.delete_tournament(tournament_id)
                except AppError as exc:
                    st.error(str(exc))
                else:
                    ui.flash("大会を削除しました。")
                    ui.nav("views/tournaments.py", group=group["group_id"])


# --- グループ ---------------------------------------------------------------

with tab_group:
    if not is_admin:
        st.info("グループの設定を変更できるのは管理者だけです。")
    else:
        try:
            detail = groups_repo.get_group(group["group_id"]) or {}
        except AppError as exc:
            st.error(str(exc))
            detail = {}

        name = st.text_input(
            "グループ名", value=detail.get("name", group["name"]),
            key=f"gname_{detail.get('name')}",
        )
        description = st.text_area(
            "説明", value=detail.get("description") or "",
            key=f"gdesc_{detail.get('description')}",
        )
        if st.button("保存", type="primary", key="save_group", width="stretch"):
            try:
                groups_repo.update_group(group["group_id"], name=name, description=description)
            except AppError as exc:
                st.error(str(exc))
            else:
                ui.flash("グループの設定を保存しました。")
                st.rerun()

    st.markdown("#### 別のグループ")
    ui.link_button(
        "グループを作る／招待コードで参加する", "views/onboarding.py",
        key="settings_onboarding",
    )
