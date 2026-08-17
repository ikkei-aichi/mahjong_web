"""マイグレーション結合ツールのテスト。旧実装ではテストが1本も無かった。"""

from __future__ import annotations

import pytest

from mahjong import migrator
from mahjong.migrator import MigrationError, build_sql, discover


def write(directory, name: str, body: str = "SELECT 1;") -> None:
    (directory / name).write_text(body, encoding="utf-8")


def test_discover_orders_by_number_not_lexically(tmp_path):
    """辞書順だと 010 が 9 より前に来て結合順が壊れる。

    正規表現で番号は取り出していたのに、ソートは Path の辞書順のままだった。
    """
    for name in ("9_nine.sql", "010_ten.sql", "002_two.sql"):
        write(tmp_path, name)

    assert [p.name for _, _, p in discover(tmp_path)] == [
        "002_two.sql",
        "9_nine.sql",
        "010_ten.sql",
    ]


def test_discover_orders_by_suffix_within_same_number(tmp_path):
    """003a → 003b → 003c の順に流したい。"""
    for name in ("003c_rls.sql", "003a_schema.sql", "003b_data.sql"):
        write(tmp_path, name)

    assert [p.name for _, _, p in discover(tmp_path)] == [
        "003a_schema.sql",
        "003b_data.sql",
        "003c_rls.sql",
    ]


def test_discover_rejects_unnumbered_file(tmp_path):
    write(tmp_path, "helpers.sql")
    with pytest.raises(MigrationError, match="001_xxx.sql"):
        discover(tmp_path)


def test_discover_rejects_duplicate_version(tmp_path):
    write(tmp_path, "001_a.sql")
    write(tmp_path, "001_b.sql")
    with pytest.raises(MigrationError, match="重複"):
        discover(tmp_path)


def test_same_number_with_different_suffix_is_allowed(tmp_path):
    write(tmp_path, "003a_x.sql")
    write(tmp_path, "003b_y.sql")
    assert len(discover(tmp_path)) == 2


def test_discover_ignores_subdirectories(tmp_path):
    """oneshot/ の冪等でないSQLが schema.sql に混入しないこと。

    これが結合対象に入ると、プレイヤー統合のような
    一度きりの処理が二重に流れてデータが壊れる。
    """
    write(tmp_path, "001_init.sql")
    oneshot = tmp_path / "oneshot"
    oneshot.mkdir()
    write(oneshot, "003b_data_migration.sql", "UPDATE players SET x = 1;")

    names = [p.name for _, _, p in discover(tmp_path)]
    assert names == ["001_init.sql"]
    assert "UPDATE players" not in build_sql(tmp_path)


def test_build_sql_concatenates_in_order(tmp_path):
    write(tmp_path, "001_first.sql", "CREATE TABLE a();")
    write(tmp_path, "002_second.sql", "CREATE TABLE b();")

    sql = build_sql(tmp_path)
    assert sql.index("CREATE TABLE a()") < sql.index("CREATE TABLE b()")
    assert "001_first.sql" in sql and "002_second.sql" in sql


def test_build_sql_warns_that_oneshots_are_excluded(tmp_path):
    write(tmp_path, "001_init.sql")
    assert "oneshot" in build_sql(tmp_path)


def test_real_migrations_directory_is_valid():
    """実際の migrations/ が命名規則を満たしていること。"""
    entries = discover()
    assert entries, "マイグレーションが1件も見つからない"
    versions = [(v, s) for v, s, _ in entries]
    assert versions == sorted(versions)
    assert migrator.MIGRATIONS_DIR.is_dir()
