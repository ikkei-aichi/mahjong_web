import sqlite3

DB_PATH = "app.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# --- fetch_titles, insert_title は変更なしのため省略可能ですが、一応含めます ---


def fetch_titles():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT title_id, title_name, create_date, flg FROM title_table WHERE flg = 0 ORDER BY create_date DESC"
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def insert_title(title_name):
    conn = get_connection()
    cur = conn.cursor()
    title_id = "".join(
        __import__("random").choices(
            __import__("string").ascii_letters + __import__("string").digits, k=20
        )
    )
    cur.execute(
        "INSERT INTO title_table (title_id, title_name, create_date, flg) VALUES (?, ?, DATETIME('now'), 0)",
        (title_id, title_name),
    )
    conn.commit()
    conn.close()
    return title_id


def fetch_games(title_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            g.title_id, g.game_id, g.game_name,
            g.player1_id, p1.player_name AS player1_name,
            g.player2_id, p2.player_name AS player2_name,
            g.player3_id, p3.player_name AS player3_name,
            g.player4_id, p4.player_name AS player4_name,
            g.create_date, g.flg
        FROM game_table g
        LEFT JOIN player_table p1 ON g.player1_id = p1.player_id
        LEFT JOIN player_table p2 ON g.player2_id = p2.player_id
        LEFT JOIN player_table p3 ON g.player3_id = p3.player_id
        LEFT JOIN player_table p4 ON g.player4_id = p4.player_id
        WHERE g.title_id = ? AND g.flg = 0
        ORDER BY g.create_date DESC
        """,
        (title_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def insert_game(title_id, game_name, player_ids):
    conn = get_connection()
    cur = conn.cursor()
    game_id = "".join(
        __import__("random").choices(
            __import__("string").ascii_letters + __import__("string").digits, k=20
        )
    )
    p1, p2, p3, p4 = (player_ids + [None] * 4)[:4]
    cur.execute(
        "INSERT INTO game_table (title_id, game_id, game_name, player1_id, player2_id, player3_id, player4_id, create_date, flg) VALUES (?, ?, ?, ?, ?, ?, ?, DATETIME('now'), 0)",
        (title_id, game_id, game_name, p1, p2, p3, p4),
    )
    conn.commit()
    conn.close()
    return game_id


def fetch_players(title_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT player_id, player_name FROM player_table WHERE title_id = ? AND flg = 0",
        (title_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def insert_player(title_id, player_name):
    conn = get_connection()
    cur = conn.cursor()
    player_id = "".join(
        __import__("random").choices(
            __import__("string").ascii_letters + __import__("string").digits, k=20
        )
    )
    cur.execute(
        "INSERT INTO player_table (title_id, player_id, player_name, flg) VALUES (?, ?, ?, 0)",
        (title_id, player_id, player_name),
    )
    conn.commit()
    conn.close()
    return player_id


# ★重要修正ポイント：fetch_game_detail
def fetch_game_detail(title_id, game_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            renban,
            COALESCE(player1_score, 0) as player1_score,
            COALESCE(player2_score, 0) as player2_score,
            COALESCE(player3_score, 0) as player3_score,
            COALESCE(player4_score, 0) as player4_score,
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
    # 辞書のリストに変換して返す（Pandasで扱いやすくするため）
    return [dict(row) for row in rows]


def insert_game_detail(
    title_id, game_id, renban, p1_s, p2_s, p3_s, p4_s, p1_k, p2_k, p3_k, p4_k
):
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
        (title_id, game_id, renban, p1_s, p2_s, p3_s, p4_s, p1_k, p2_k, p3_k, p4_k),
    )
    conn.commit()
    conn.close()


def delete_game_detail(title_id, game_id, renban):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE game_detail_table SET flg = 1 WHERE title_id = ? AND game_id = ? AND renban = ?",
        (title_id, game_id, renban),
    )
    conn.commit()
    conn.close()


def fetch_game_summary(title_id):
    # (既存のコードと同じですが、COALESCEでNULL対策されているのでOKです)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            p.player_id,
            p.player_name,
            SUM(CASE 
                WHEN (g.player1_id = p.player_id AND gd.player1_score = MAX(COALESCE(gd.player1_score, -999), COALESCE(gd.player2_score, -999), COALESCE(gd.player3_score, -999), COALESCE(gd.player4_score, -999))) THEN 1
                WHEN (g.player2_id = p.player_id AND gd.player2_score = MAX(COALESCE(gd.player1_score, -999), COALESCE(gd.player2_score, -999), COALESCE(gd.player3_score, -999), COALESCE(gd.player4_score, -999))) THEN 1
                WHEN (g.player3_id = p.player_id AND gd.player3_score = MAX(COALESCE(gd.player1_score, -999), COALESCE(gd.player2_score, -999), COALESCE(gd.player3_score, -999), COALESCE(gd.player4_score, -999))) THEN 1
                WHEN (g.player4_id = p.player_id AND gd.player4_score = MAX(COALESCE(gd.player1_score, -999), COALESCE(gd.player2_score, -999), COALESCE(gd.player3_score, -999), COALESCE(gd.player4_score, -999))) THEN 1
                ELSE 0 END) AS win_count,
            SUM(CASE 
                WHEN (g.player1_id = p.player_id AND gd.player1_score = MIN(COALESCE(gd.player1_score, 999999), COALESCE(gd.player2_score, 999999), COALESCE(gd.player3_score, 999999), COALESCE(gd.player4_score, 999999))) THEN 1
                WHEN (g.player2_id = p.player_id AND gd.player2_score = MIN(COALESCE(gd.player1_score, 999999), COALESCE(gd.player2_score, 999999), COALESCE(gd.player3_score, 999999), COALESCE(gd.player4_score, 999999))) THEN 1
                WHEN (g.player3_id = p.player_id AND gd.player3_score = MIN(COALESCE(gd.player1_score, 999999), COALESCE(gd.player2_score, 999999), COALESCE(gd.player3_score, 999999), COALESCE(gd.player4_score, 999999))) THEN 1
                WHEN (g.player4_id = p.player_id AND gd.player4_score = MIN(COALESCE(gd.player1_score, 999999), COALESCE(gd.player2_score, 999999), COALESCE(gd.player3_score, 999999), COALESCE(gd.player4_score, 999999))) THEN 1
                ELSE 0 END) AS lose_count,
            SUM(CASE 
                WHEN gd.player1_score IS NOT NULL AND g.player1_id = p.player_id THEN gd.player1_score
                WHEN gd.player2_score IS NOT NULL AND g.player2_id = p.player_id THEN gd.player2_score
                WHEN gd.player3_score IS NOT NULL AND g.player3_id = p.player_id THEN gd.player3_score
                WHEN gd.player4_score IS NOT NULL AND g.player4_id = p.player_id THEN gd.player4_score
                ELSE 0 END) AS total_score
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
