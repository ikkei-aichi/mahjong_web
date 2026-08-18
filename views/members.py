"""メンバーと参加者の管理。

このアプリの「参加者」は2種類ある。
    メンバー … ログインアカウントを持ち、自分でこのグループの記録を見られる人
    ゲスト   … 名前だけ登録されている人（たまに来る人、まだアカウントが無い人）

ゲストは後から本人のアカウントに紐付けられる。紐付けても player_id は
変わらないので、それまでの成績はそのまま引き継がれる。
"""

from __future__ import annotations

import streamlit as st

from mahjong import session, ui
from mahjong.errors import AppError
from mahjong.repo import groups as groups_repo
from mahjong.timeutil import format_jst

ui.show_flashes()
group = session.require_group()
is_admin = session.is_admin(group)

st.title("👥 メンバー")

try:
    members = groups_repo.list_players(group["group_id"])
except AppError as exc:
    st.error(str(exc))
    st.stop()

my_player_id = group.get("my_player_id")


# --- 本人紐付け -------------------------------------------------------------

if group.get("is_provisional"):
    st.markdown("### 🙋 あなたは誰ですか？")
    st.info(
        f"いまの表示名は「{group.get('my_player_name')}」です。"
        "すでに対戦記録がある名前を選ぶと、その成績が自分のものになります。"
    )
    unclaimed = [
        m for m in members if not m.get("user_id") and m["id"] != my_player_id
    ]
    if unclaimed:
        # 誰の成績を引き継ぐかを決める操作なので、名前ではなく player_id で選ぶ。
        chosen_id = ui.select_one(
            "自分の名前",
            [m["id"] for m in unclaimed],
            [m["name"] for m in unclaimed],
            key="link_pick",
        )
        if st.button("この名前を自分にする", type="primary", width="stretch"):
            target = next(m for m in unclaimed if m["id"] == chosen_id)
            try:
                groups_repo.link_me_to_player(target["id"])
            except AppError as exc:
                st.error(str(exc))
            else:
                ui.flash(f"「{target['name']}」として紐付けました。")
                st.rerun()
    else:
        st.caption("紐付けられる名前がありません。下で表示名を変更してください。")
    st.markdown("---")


# --- 一覧 -------------------------------------------------------------------

st.markdown("### 一覧")

for member in members:
    is_me = member["id"] == my_player_id
    has_account = bool(member.get("user_id"))
    badge = groups_repo.ROLE_LABELS.get(member.get("role"), "メンバー") if has_account else "ゲスト"

    with st.container(border=True):
        title = f"**{member['name']}**　`{badge}`"
        if is_me:
            title += "　（あなた）"
        st.markdown(title)

        can_rename = is_admin or is_me
        if can_rename:
            new_name = st.text_input(
                "表示名",
                value=member["name"],
                # 保存していない編集が保存済みに見えないよう、
                # 名前が変わったらキーも変える。
                key=f"pname_{member['id']}_{member['name']}",
                label_visibility="collapsed",
            )
            col1, col2 = st.columns(2)
            with col1:
                if st.button("名前を保存", key=f"rn_{member['id']}", width="stretch"):
                    try:
                        groups_repo.rename_player(member["id"], new_name)
                    except AppError as exc:
                        st.error(str(exc))
                    else:
                        ui.flash("名前を変更しました。")
                        st.rerun()
            with col2:
                if is_admin and has_account and not is_me:
                    roles = list(groups_repo.MEMBER_ROLES)
                    # 想定外の役割（NULL や未知の値）が入っていても画面ごと落とさない。
                    # DB 側の CHECK 制約で防いではいるが、ここで .index() が
                    # ValueError を投げるとメンバー画面が丸ごと開けなくなる。
                    current_role = member.get("role") or "member"
                    if current_role not in roles:
                        current_role = "member"
                    role = st.selectbox(
                        "役割",
                        roles,
                        index=roles.index(current_role),
                        format_func=lambda r: groups_repo.ROLE_LABELS[r],
                        key=f"role_{member['id']}",
                        label_visibility="collapsed",
                    )
                    if role != member.get("role") and st.button(
                        "役割を変更", key=f"sr_{member['id']}", width="stretch"
                    ):
                        try:
                            groups_repo.set_member_role(member["id"], role)
                        except AppError as exc:
                            st.error(str(exc))
                        else:
                            ui.flash("役割を変更しました。")
                            st.rerun()

        if is_admin and not is_me:
            if ui.confirm_delete(
                f"member_{member['id']}",
                "🗑️ この参加者を外す",
                f"「{member['name']}」を一覧から外します。"
                "過去の対戦成績はそのまま残り、成績表にも表示され続けます。",
            ):
                try:
                    groups_repo.delete_player(member["id"])
                except AppError as exc:
                    st.error(str(exc))
                else:
                    ui.flash(f"「{member['name']}」を外しました。")
                    st.rerun()


# --- ゲストの追加 -----------------------------------------------------------

st.markdown("### ゲストを追加")
st.caption("アカウントを持たない参加者。対戦の席を作るときにその場で追加することもできます。")

# clear_on_submit は使わない。追加に失敗した（同名が既にいる等）ときにも
# 入力欄を空にしてしまい、打ち直しになる。
# 代わりにキーへ人数を混ぜてあり、追加が成功して人数が変わったときだけ空に戻る。
with st.form("add_guest"):
    guest_name = st.text_input(
        "名前", placeholder="例: 佐藤", key=f"guest_name_{len(members)}"
    )
    if st.form_submit_button("追加する", width="stretch"):
        try:
            groups_repo.create_player(group["group_id"], guest_name)
        except AppError as exc:
            st.error(str(exc))
        else:
            ui.flash(f"「{guest_name}」を追加しました。")
            st.rerun()


# --- 招待 -------------------------------------------------------------------

if not is_admin:
    st.stop()

st.markdown("### 招待")
st.caption(
    "招待コードを渡すと、相手は自分のアカウントでこのグループの記録を見られるようになります。"
    "参加時に「自分はこの名前です」と選んでもらえば、過去の成績も引き継がれます。"
)

if st.button("招待コードを発行", type="primary", width="stretch"):
    try:
        code = groups_repo.create_invite(group["group_id"])
    except AppError as exc:
        st.error(str(exc))
    else:
        ui.flash(f"招待コード: {code}（7日間・20人まで有効）")
        st.rerun()

try:
    invites = groups_repo.list_invites(group["group_id"])
except AppError as exc:
    st.error(str(exc))
    invites = []

active = [i for i in invites if not i.get("revoked_at")]
for invite in active:
    with st.container(border=True):
        st.code(invite["code"], language=None)
        used = invite.get("used_count", 0)
        limit = invite.get("max_uses")
        st.caption(
            f"使用 {used}/{limit if limit is not None else '∞'} ・ "
            f"期限 {format_jst(invite.get('expires_at'), '%Y/%m/%d %H:%M') or 'なし'}"
        )
        if st.button("無効にする", key=f"rv_{invite['id']}", width="stretch"):
            try:
                groups_repo.revoke_invite(invite["id"])
            except AppError as exc:
                st.error(str(exc))
            else:
                ui.flash("招待コードを無効にしました。")
                st.rerun()

if not active:
    st.caption("有効な招待コードはありません。")
