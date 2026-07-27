"""スキーマ適用用のSQLを組み立てるツール。旧 init_db.py の置き換え。

REST API (PostgREST) 経由では任意のDDLを実行できないため、
migrations/*.sql を連番順に結合して1つのファイルに書き出す。
出力されたSQLを Supabase ダッシュボードの SQL Editor に貼って実行する。

    python -m mahjong.migrator

SQL Editor はブラウザ（HTTPS）で動くので、Postgres のポートが
遮断されているネットワークでも問題なく実行できる。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "schema.sql"
_VERSION_RE = re.compile(r"^(\d+)_")


class MigrationError(RuntimeError):
    """マイグレーションファイルが不正なときに送出する。"""


def discover() -> list[tuple[int, Path]]:
    """migrations ディレクトリの .sql を (version, path) で連番順に返す。"""
    found: list[tuple[int, Path]] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        match = _VERSION_RE.match(path.name)
        if not match:
            raise MigrationError(
                f"マイグレーション名は '001_xxx.sql' の形式にしてください: {path.name}"
            )
        found.append((int(match.group(1)), path))

    versions = [v for v, _ in found]
    duplicates = sorted({v for v in versions if versions.count(v) > 1})
    if duplicates:
        raise MigrationError(f"マイグレーション番号が重複しています: {duplicates}")
    return found


def build_sql() -> str:
    """全マイグレーションを結合したSQLを返す。

    各ファイルは CREATE ... IF NOT EXISTS / CREATE OR REPLACE で書かれているため、
    何度実行しても安全（冪等）。
    """
    parts = [
        "-- 麻雀管理アプリ スキーマ",
        "-- Supabase ダッシュボード → SQL Editor に貼り付けて実行してください。",
        "-- 冪等なので、複数回実行しても問題ありません。",
        "",
    ]
    for version, path in discover():
        parts.append(f"-- ===== {path.name} (v{version}) " + "=" * 40)
        parts.append(path.read_text(encoding="utf-8").strip())
        parts.append("")
    return "\n".join(parts)


def main() -> None:
    try:
        sql = build_sql()
    except MigrationError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        raise SystemExit(1)

    OUTPUT_PATH.write_text(sql, encoding="utf-8")
    files = [p.name for _, p in discover()]
    print(f"{len(files)} 件のSQLを結合しました: {', '.join(files)}")
    print(f"出力先: {OUTPUT_PATH}")
    print()
    print("次の手順で適用してください:")
    print("  1. Supabase ダッシュボード → SQL Editor を開く")
    print(f"  2. {OUTPUT_PATH.name} の中身を全部貼り付ける")
    print("  3. Run を押す")


if __name__ == "__main__":
    main()
