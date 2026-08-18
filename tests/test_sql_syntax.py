"""マイグレーションSQLを実際の PostgreSQL パーサで構文検証する。

DBに接続せずに「SQL Editor に貼ったら構文エラーで落ちる」を防ぐためのテスト。
pglast は PostgreSQL 本体のパーサをそのまま使っているので、
方言の取り違えや括弧の閉じ忘れをここで確実に捕まえられる。

pglast が入っていない環境ではスキップする（本番の実行には不要な開発用依存）。
"""

from __future__ import annotations

import pathlib

import pytest

pglast = pytest.importorskip("pglast", reason="pglast 未インストール")

MIGRATIONS = pathlib.Path(__file__).resolve().parent.parent / "mahjong" / "migrations"
SQL_FILES = sorted(MIGRATIONS.rglob("*.sql"))


def code_only(text: str) -> str:
    """`--` 行コメントを落とす。

    このSQLは解説コメントが厚く、「やってはいけないこと」を文章で書いてある。
    素朴に部分文字列を探すと、禁止事項の**説明文**に引っかかってしまう。
    """
    lines = []
    for line in text.splitlines():
        marker = line.find("--")
        lines.append(line if marker < 0 else line[:marker])
    return "\n".join(lines)


def test_migration_files_exist():
    assert SQL_FILES, "マイグレーションSQLが1件も見つからない"


@pytest.mark.parametrize("path", SQL_FILES, ids=lambda p: p.name)
def test_sql_parses(path: pathlib.Path):
    pglast.parse_sql(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", SQL_FILES, ids=lambda p: p.name)
def test_plpgsql_bodies_parse(path: pathlib.Path):
    """DO ブロックと plpgsql 関数の**中身**まで検証する。

    parse_sql は関数本体を文字列としてしか見ないため、
    これを別に走らせないと本体の構文エラーを見逃す。
    """
    checked = 0
    for statement in pglast.split(path.read_text(encoding="utf-8")):
        lowered = statement.strip().lower()
        if not (lowered.startswith("do") or "language plpgsql" in lowered):
            continue
        pglast.parse_plpgsql(statement)
        checked += 1
    assert checked >= 0  # 0件でもよい（純DDLのファイルがある）


def test_oneshot_is_excluded_from_generated_schema():
    """冪等でないデータ移行が schema.sql に混入しないこと。"""
    from mahjong.migrator import build_sql

    sql = build_sql()
    assert "mig003_player_merge_map\n    (loser_id" not in sql
    assert "002_data_migration" not in sql
    # 一度きりの目印になる文字列が含まれていないこと
    assert "既定のグループ'," not in sql


def test_generated_schema_does_not_recreate_legacy_tables():
    """legacy/001,002 が schema.sql に含まれていないこと。

    含まれていると、移行後に空の titles テーブルが再作成され、
    「ログイン済みなら全部許可」の旧RLSポリシーまで復活してしまう。
    """
    from mahjong.migrator import build_sql

    sql = code_only(build_sql())
    assert "CREATE TABLE IF NOT EXISTS titles" not in sql
    assert "authenticated_all" in sql, "旧ポリシーを DROP する処理は残っているはず"
    assert "USING (true) WITH CHECK (true)" not in sql


def test_views_are_security_invoker():
    """ビューが security_invoker=true でないと RLS を素通りして全グループ丸見えになる。"""
    text = (MIGRATIONS / "003d_views_rpc.sql").read_text(encoding="utf-8")
    created = text.count("CREATE VIEW public.")
    invoker = text.count("WITH (security_invoker = true)")
    assert created > 0
    assert invoker == created, "security_invoker が付いていないビューがある"


def test_security_definer_functions_pin_search_path():
    """SECURITY DEFINER で search_path を固定しないと権限昇格の余地が残る。"""
    for path in SQL_FILES:
        text = code_only(path.read_text(encoding="utf-8"))
        for chunk in text.split("SECURITY DEFINER")[1:]:
            head = chunk[:80]
            assert "SET search_path" in head, f"{path.name}: search_path 未固定の DEFINER 関数がある"


def _definer_functions() -> list[tuple[str, str, str]]:
    """(ファイル名, 関数名, 本文) の一覧。SECURITY DEFINER のものだけ。"""
    found = []
    for path in SQL_FILES:
        text = code_only(path.read_text(encoding="utf-8"))
        for chunk in text.split("CREATE OR REPLACE FUNCTION")[1:]:
            name = chunk.strip().split("(")[0].strip()
            if "SECURITY DEFINER" not in chunk:
                continue
            found.append((path.name, name, chunk))
    return found


def test_security_definer_functions_exist():
    assert _definer_functions(), "SECURITY DEFINER 関数が1つも見つからない"


def test_mutating_definer_functions_require_login():
    """SECURITY DEFINER は RLS を素通りするので、自分でログイン確認をしないといけない。

    書き忘れると未ログイン(anon)から呼ばれたときに素通ししてしまう。
    実際 set_member_role / remove_member でこれが起きていた。
    """
    for filename, name, body in _definer_functions():
        mutates = any(
            f"{verb} " in body.upper() for verb in ("INSERT INTO", "UPDATE PUBLIC.", "DELETE FROM")
        )
        if not mutates:
            continue  # 読み取り専用のヘルパー（所属グループ取得など）は NULL で空を返せばよい
        assert "auth.uid()" in body, f"{filename}:{name} が auth.uid() を見ていない"
        assert "IS NULL" in body and "42501" in body, (
            f"{filename}:{name} に未ログイン時の拒否（IS NULL → ERRCODE 42501）が無い"
        )


def test_anon_execute_is_revoked_for_all_functions():
    """REVOKE ... FROM public では anon への明示的な付与が消えない。

    Supabase は既定で anon にも関数の EXECUTE を付けるため、
    ロールを名指しで剥がさないと未ログインから RPC を呼べてしまう。
    """
    text = code_only((MIGRATIONS / "003d_views_rpc.sql").read_text(encoding="utf-8"))
    assert "FROM anon" in text, "anon から EXECUTE を剥がす処理が無い"


def test_no_force_row_level_security():
    """FORCE ROW LEVEL SECURITY を付けると所有者にもRLSが適用され、

    players のポリシーが players を引く構造なので無限再帰する。
    """
    for path in SQL_FILES:
        text = code_only(path.read_text(encoding="utf-8")).upper()
        assert "FORCE ROW LEVEL SECURITY" not in text, path.name


# --- 論理削除した親を集計に混ぜないこと -------------------------------------


def _view_body(name: str) -> str:
    """指定したビューの CREATE VIEW ... ; の中身（コメント除去済み）。"""
    text = code_only((MIGRATIONS / "003d_views_rpc.sql").read_text(encoding="utf-8"))
    start = text.index(f"CREATE VIEW public.{name} ")
    return text[start : text.index(";", start)]


@pytest.mark.parametrize(
    "view,table",
    [
        ("v_round_entries", "tournament_days"),
        ("v_round_entries", "tournaments"),
        ("v_game_seats", "tournament_days"),
        ("v_game_seats", "tournaments"),
    ],
)
def test_views_exclude_soft_deleted_parents(view: str, table: str):
    """削除した大会・開催日の記録が成績に残り続けないこと。

    delete_tournament / delete_day は deleted_at を立てるだけで、
    カスケードするトリガは無い。ビュー側で絞らないと、一覧からは消えたのに
    「グループ通算成績」と「全N半荘」には残るという食い違いが起きる。
    """
    import re

    body = _view_body(view)
    joins = re.findall(rf"JOIN\s+public\.{table}\b[^\n]*", body)
    assert joins, f"{view} が {table} と結合していない"
    for join in joins:
        assert "deleted_at IS NULL" in join, (
            f"{view} の {table} 結合に論理削除フィルタが無い: {join.strip()}"
        )


def test_views_keep_soft_deleted_players():
    """★プレイヤーだけは絞らない★

    削除したプレイヤーの記録まで落とすと、その人のポイントが集計から消えて
    順位表の合計がゼロサムでなくなる。上のテストに引きずられて
    players にもフィルタを足さないよう、意図をここで固定しておく。
    """
    for view in ("v_round_entries", "v_game_seats"):
        import re

        for join in re.findall(r"JOIN\s+public\.players\b[^\n]*", _view_body(view)):
            assert "deleted_at" not in join, f"{view}: players を論理削除で絞っている"
