from sqlite_db import get_connection


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    # テーブル作成
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS notices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            published_at TEXT NOT NULL,
            is_active INTEGER NOT NULL
        )
    """
    )

    # 既存データ確認
    cur.execute("SELECT COUNT(*) FROM notices")
    count = cur.fetchone()[0]

    if count > 0:
        print("すでにデータが存在します。初期化はスキップしました。")
        conn.close()
        return

    notices = [
        (
            "システムメンテナンスのお知らせ",
            "2025年1月20日にシステムメンテナンスを実施します。",
            "2025-01-20",
            1,
        ),
        (
            "新機能リリース",
            "お知らせ一覧機能を追加しました。",
            "お知らせ一覧機能を追加しました。",
            1,
        ),
    ]

    cur.executemany(
        """
        INSERT INTO notices (title, body, published_at, is_active)
        VALUES (?, ?, ?, ?)
    """,
        notices,
    )

    conn.commit()
    conn.close()

    print("SQLite DB と初期データを作成しました。")


if __name__ == "__main__":
    init_db()
