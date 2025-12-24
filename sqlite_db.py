import sqlite3

DB_PATH = "app.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_titles():
    """
    麻雀ゲーム詳細を取得
    title_id を指定した場合は絞り込み
    """
    conn = get_connection()
    cur = conn.cursor()

    # flg（0: 有効、1: 無効） 0のみ取得
    cur.execute(
        """
        SELECT
            title_id,
            title_name,
            create_date,
            flg
        FROM title_table
        WHERE flg = 0
        ORDER BY create_date DESC
        """
    )

    rows = cur.fetchall()
    conn.close()
    return rows


def insert_title(title_name):
    """
    麻雀ゲームタイトルを登録
    """
    conn = get_connection()
    cur = conn.cursor()
    # title_idは20桁の英数字ランダム生成
    title_id = "".join(
        __import__("random").choices(
            __import__("string").ascii_letters + __import__("string").digits, k=20
        )
    )

    cur.execute(
        """
        INSERT INTO title_table (title_id, title_name, create_date, flg)
        VALUES (?, ?, DATETIME('now'), 0)
        """,
        (title_id, title_name),
    )

    conn.commit()
    conn.close()

    return title_id


def fetch_games(title_id):
    """
    指定された title_id の対戦一覧を取得（プレイヤー名も含む）
    """
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            g.title_id,
            g.game_id,
            g.game_name,
            g.player1_id,
            p1.player_name AS player1_name,
            g.player2_id,
            p2.player_name AS player2_name,
            g.player3_id,
            p3.player_name AS player3_name,
            g.player4_id,
            p4.player_name AS player4_name,
            g.create_date,
            g.flg
        FROM game_table g
        LEFT JOIN player_table p1 ON g.player1_id = p1.player_id AND p1.flg = 0
        LEFT JOIN player_table p2 ON g.player2_id = p2.player_id AND p2.flg = 0
        LEFT JOIN player_table p3 ON g.player3_id = p3.player_id AND p3.flg = 0
        LEFT JOIN player_table p4 ON g.player4_id = p4.player_id AND p4.flg = 0
        WHERE g.title_id = ? AND g.flg = 0
        ORDER BY g.create_date DESC
        """,
        (title_id,),
    )

    rows = cur.fetchall()
    conn.close()
    return rows


def insert_game(title_id, game_name, player_ids):
    """
    指定された title_id に対して新しい対戦を登録
    """
    conn = get_connection()
    cur = conn.cursor()
    # game_idは20桁の英数字ランダム生成
    game_id = "".join(
        __import__("random").choices(
            __import__("string").ascii_letters + __import__("string").digits, k=20
        )
    )

    # final_playersはリスト想定（4人分）
    player1_id = player_ids[0] if len(player_ids) > 0 else None
    player2_id = player_ids[1] if len(player_ids) > 1 else None
    player3_id = player_ids[2] if len(player_ids) > 2 else None
    player4_id = player_ids[3] if len(player_ids) > 3 else None

    cur.execute(
        """
        INSERT INTO game_table (
            title_id, game_id, game_name, player1_id, player2_id, player3_id, player4_id, create_date, flg
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, DATETIME('now'), 0)
        """,
        (title_id, game_id, game_name, player1_id, player2_id, player3_id, player4_id),
    )

    conn.commit()
    conn.close()

    return game_id


def fetch_players(title_id):
    """
    指定された title_id のプレイヤー情報を取得
    """
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            player_id,
            player_name
        FROM player_table
        WHERE title_id = ? AND flg = 0
        """,
        (title_id,),
    )

    rows = cur.fetchall()
    conn.close()
    return rows


def insert_player(title_id, player_name):
    """
    指定された title_id に対して新しいプレイヤーを登録
    """
    conn = get_connection()
    cur = conn.cursor()
    # player_idは20桁の英数字ランダム生成
    player_id = "".join(
        __import__("random").choices(
            __import__("string").ascii_letters + __import__("string").digits, k=20
        )
    )

    cur.execute(
        """
        INSERT INTO player_table (
            title_id, player_id, player_name, flg
        )
        VALUES (?, ?, ?, 0)
        """,
        (title_id, player_id, player_name),
    )

    conn.commit()
    conn.close()

    return player_id


def fetch_game_detail(title_id, game_id):
    """
    指定された title_id, game_id のゲーム詳細（スコア履歴）を取得
    """
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            renban,
            player1_score,
            player2_score,
            player3_score,
            player4_score,
            create_date,
            flg
        FROM game_detail_table
        WHERE title_id = ? AND game_id = ? AND flg = 0
        ORDER BY renban ASC
        """,
        (title_id, game_id),
    )

    rows = cur.fetchall()
    conn.close()
    return rows


def insert_game_detail(
    title_id,
    game_id,
    renban,
    player1_score,
    player2_score,
    player3_score,
    player4_score,
    player1_kaze,
    player2_kaze,
    player3_kaze,
    player4_kaze,
):
    """
    指定された title_id, game_id に対して新しいゲーム詳細（スコア履歴）を登録
    """
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO game_detail_table (
            title_id, game_id, renban,
            player1_score, player2_score, player3_score, player4_score,
            player1_kaze, player2_kaze, player3_kaze, player4_kaze,
            create_date, flg
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, DATETIME('now'), 0)
        """,
        (
            title_id,
            game_id,
            renban,
            player1_score,
            player2_score,
            player3_score,
            player4_score,
            player1_kaze,
            player2_kaze,
            player3_kaze,
            player4_kaze,
        ),
    )

    conn.commit()
    conn.close()


def fetch_game_summary(title_id):
    """
    指定された title_id のゲーム集計情報を取得（SQLite対応版：1位回数を含む）
    """
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            p.player_id,
            p.player_name,
            SUM(
                CASE 
                    WHEN (g.player1_id = p.player_id AND gd.player1_score = MAX(COALESCE(gd.player1_score, -999), COALESCE(gd.player2_score, -999), COALESCE(gd.player3_score, -999), COALESCE(gd.player4_score, -999))) THEN 1
                    WHEN (g.player2_id = p.player_id AND gd.player2_score = MAX(COALESCE(gd.player1_score, -999), COALESCE(gd.player2_score, -999), COALESCE(gd.player3_score, -999), COALESCE(gd.player4_score, -999))) THEN 1
                    WHEN (g.player3_id = p.player_id AND gd.player3_score = MAX(COALESCE(gd.player1_score, -999), COALESCE(gd.player2_score, -999), COALESCE(gd.player3_score, -999), COALESCE(gd.player4_score, -999))) THEN 1
                    WHEN (g.player4_id = p.player_id AND gd.player4_score = MAX(COALESCE(gd.player1_score, -999), COALESCE(gd.player2_score, -999), COALESCE(gd.player3_score, -999), COALESCE(gd.player4_score, -999))) THEN 1
                    ELSE 0 END
            ) AS win_count,
            SUM(
                CASE WHEN gd.player1_score IS NOT NULL AND g.player1_id = p.player_id THEN gd.player1_score
                     WHEN gd.player2_score IS NOT NULL AND g.player2_id = p.player_id THEN gd.player2_score
                     WHEN gd.player3_score IS NOT NULL AND g.player3_id = p.player_id THEN gd.player3_score
                     WHEN gd.player4_score IS NOT NULL AND g.player4_id = p.player_id THEN gd.player4_score
                     ELSE 0 END
            ) AS total_score
        FROM player_table p
        LEFT JOIN game_table g ON p.title_id = g.title_id AND (p.player_id = g.player1_id OR p.player_id = g.player2_id OR p.player_id = g.player3_id OR p.player_id = g.player4_id) AND g.flg = 0
        LEFT JOIN game_detail_table gd ON g.title_id = gd.title_id AND g.game_id = gd.game_id AND gd.flg = 0
        WHERE p.title_id = ? AND p.flg = 0
        GROUP BY p.player_id, p.player_name
        ORDER BY total_score DESC
        """,
        (title_id,),
    )

    rows = cur.fetchall()
    conn.close()
    return rows
