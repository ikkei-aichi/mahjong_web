"""スキーマ適用用のSQLを組み立てるツール。旧 init_db.py の置き換え。

REST API (PostgREST) 経由では任意のDDLを実行できないため、
migrations/*.sql を連番順に結合して1つのファイルに書き出す。
出力されたSQLを Supabase ダッシュボードの SQL Editor に貼って実行する。

    python -m mahjong.migrator

SQL Editor はブラウザ（HTTPS）で動くので、Postgres のポートが
遮断されているネットワークでも問題なく実行できる。

migrations/oneshot/ は**意図的に対象外**にしている。
プレイヤーの統合のような「1回だけ実行してよい」処理をここに置くことで、
schema.sql に混入して二度流されてしまう事故を構造的に防ぐ
（glob が `*.sql` で再帰しないため、サブディレクトリは拾われない）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
ONESHOT_DIR = MIGRATIONS_DIR / "oneshot"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "schema.sql"
_VERSION_RE = re.compile(r"^(\d+)([a-z]?)_")


class MigrationError(RuntimeError):
    """マイグレーションファイルが不正なときに送出する。"""


def discover(directory: Path | None = None) -> list[tuple[int, str, Path]]:
    """.sql を (version, suffix, path) で連番順に返す。

    ソートキーは**パスの文字列ではなく解析した番号**にする。
    辞書順だと `010_x.sql` が `9_x.sql` より前に来て、結合順が壊れる。

    同じ番号でも接尾辞（003a / 003b / 003c）で順序を付けられる。
    番号だけが完全に重複している場合はエラーにする。
    """
    directory = directory or MIGRATIONS_DIR
    found: list[tuple[int, str, Path]] = []
    for path in directory.glob("*.sql"):
        match = _VERSION_RE.match(path.name)
        if not match:
            raise MigrationError(
                f"マイグレーション名は '001_xxx.sql' の形式にしてください: {path.name}"
            )
        found.append((int(match.group(1)), match.group(2), path))

    keys = [(v, s) for v, s, _ in found]
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    if duplicates:
        pretty = ", ".join(f"{v:03d}{s}" for v, s in duplicates)
        raise MigrationError(f"マイグレーション番号が重複しています: {pretty}")

    found.sort(key=lambda item: (item[0], item[1]))
    return found


def build_sql(directory: Path | None = None) -> str:
    """全マイグレーションを結合したSQLを返す。

    ここに含まれるファイルは CREATE ... IF NOT EXISTS / CREATE OR REPLACE で
    書かれているため、何度実行しても安全（冪等）。
    冪等でない一度きりの処理は migrations/oneshot/ に置くこと。
    """
    parts = [
        "-- 麻雀管理アプリ スキーマ",
        "-- Supabase ダッシュボード → SQL Editor に貼り付けて実行してください。",
        "-- ここに含まれるSQLは冪等なので、複数回実行しても問題ありません。",
        "-- ※ 一度きりのデータ移行は migrations/oneshot/ にあり、これには含まれません。",
        "",
    ]
    for version, suffix, path in discover(directory):
        parts.append(f"-- ===== {path.name} (v{version}{suffix}) " + "=" * 40)
        parts.append(path.read_text(encoding="utf-8").strip())
        parts.append("")
    return "\n".join(parts)


def main() -> None:
    try:
        entries = discover()
        sql = build_sql()
    except MigrationError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        raise SystemExit(1)

    OUTPUT_PATH.write_text(sql, encoding="utf-8")
    files = [p.name for _, _, p in entries]
    print(f"{len(files)} 件のSQLを結合しました: {', '.join(files)}")
    print(f"出力先: {OUTPUT_PATH}")
    print()
    print("次の手順で適用してください:")
    print("  1. Supabase ダッシュボード → SQL Editor を開く")
    print(f"  2. {OUTPUT_PATH.name} の中身を全部貼り付ける")
    print("  3. Run を押す")

    oneshots = sorted(p.name for p in ONESHOT_DIR.glob("*.sql")) if ONESHOT_DIR.is_dir() else []
    if oneshots:
        print()
        print("【注意】以下は一度きりのデータ移行で、上のSQLには含まれていません。")
        print("        バックアップとプリフライト確認のうえ、個別に実行してください。")
        for name in oneshots:
            print(f"  - migrations/oneshot/{name}")


if __name__ == "__main__":
    main()
