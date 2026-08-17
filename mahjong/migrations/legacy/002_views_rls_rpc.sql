-- ビュー / RLSポリシー / RPC関数
--
-- REST API (PostgREST) 経由でアクセスするため:
--   * 複数テーブルにまたがる読み取りはビューにまとめる
--     （埋め込みリソースのフィルタ構文は壊れやすいため）
--   * 複数文をまとめて成功／失敗させたい書き込みは関数にする
--     （PostgREST に複数文トランザクションが無いため。関数呼び出しは
--       それ自体が1トランザクションになる）
--   * RLS が実際に効くので、ログイン済みユーザーにのみ許可する

-- ===== ビュー =====================================================
-- security_invoker = true にすると、ビューは呼び出したユーザーの権限で
-- 実行され、元テーブルの RLS がそのまま適用される。

-- 半荘の全記録。統計・推移グラフ・再計算・対戦詳細のすべてがこれ1本で足りる。
CREATE OR REPLACE VIEW v_round_entries
WITH (security_invoker = true) AS
SELECT
    g.title_id,
    r.game_id,
    r.id         AS round_id,
    r.created_at AS round_created_at,
    rr.player_id,
    rr.seat,
    rr.raw_score,
    rr.point,
    rr.rank,
    rr.kaze,
    rr.tobi
FROM round_results rr
JOIN game_rounds r ON r.id = rr.round_id AND r.deleted_at IS NULL
JOIN games       g ON g.id = r.game_id   AND g.deleted_at IS NULL;

-- 対戦一覧用。席順と各人の合計ポイントを1クエリで取れるようにする
-- （旧実装は対戦ごとに再クエリしていた＝N+1）。
CREATE OR REPLACE VIEW v_game_seats
WITH (security_invoker = true) AS
SELECT
    g.id         AS game_id,
    g.title_id,
    g.name       AS game_name,
    g.created_at AS game_created_at,
    gp.seat,
    p.id         AS player_id,
    p.name       AS player_name,
    COALESCE(agg.total_point, 0) AS total_point,
    COALESCE(cnt.round_count, 0) AS round_count
FROM games g
JOIN game_players gp ON gp.game_id = g.id
JOIN players      p  ON p.id = gp.player_id
LEFT JOIN (
    SELECT r.game_id, rr.player_id, SUM(rr.point) AS total_point
    FROM round_results rr
    JOIN game_rounds r ON r.id = rr.round_id AND r.deleted_at IS NULL
    GROUP BY r.game_id, rr.player_id
) agg ON agg.game_id = g.id AND agg.player_id = gp.player_id
LEFT JOIN (
    SELECT game_id, COUNT(*) AS round_count
    FROM game_rounds
    WHERE deleted_at IS NULL
    GROUP BY game_id
) cnt ON cnt.game_id = g.id
WHERE g.deleted_at IS NULL;


-- ===== RPC関数 =====================================================
-- 関数本体は1トランザクションで実行される。途中で例外が起きれば
-- それまでの INSERT はすべて巻き戻る。

-- 対戦作成。新規プレイヤーの作成まで含めて不可分に行う。
-- 旧実装は席を順に処理しながら INSERT していたため、途中でエラーになると
-- 作成済みのプレイヤーだけが孤立して残っていた。
--
-- p_seats の形式: [{"player_id": "uuid" } または {"new_name": "名前"}, ...]
CREATE OR REPLACE FUNCTION create_game_with_players(
    p_title_id uuid,
    p_name     text,
    p_seats    jsonb
) RETURNS uuid
LANGUAGE plpgsql
SECURITY INVOKER
AS $$
DECLARE
    v_game_id     uuid;
    v_seat        int := 0;
    v_item        jsonb;
    v_player_id   uuid;
    v_new_name    text;
    v_resolved    uuid[] := '{}';
BEGIN
    IF jsonb_array_length(p_seats) < 3 THEN
        RAISE EXCEPTION '3人以上のプレイヤーを選択してください。';
    END IF;
    IF jsonb_array_length(p_seats) > 4 THEN
        RAISE EXCEPTION 'プレイヤーは最大4人です。';
    END IF;

    FOR v_item IN SELECT * FROM jsonb_array_elements(p_seats) LOOP
        v_player_id := NULLIF(v_item->>'player_id', '')::uuid;
        v_new_name  := btrim(COALESCE(v_item->>'new_name', ''));

        IF v_player_id IS NULL THEN
            IF v_new_name = '' THEN
                RAISE EXCEPTION 'プレイヤー名を入力してください。';
            END IF;
            -- 同名が既にいれば使い回す（UNIQUE制約との衝突を避ける）
            SELECT id INTO v_player_id
            FROM players
            WHERE title_id = p_title_id AND name = v_new_name AND deleted_at IS NULL;

            IF v_player_id IS NULL THEN
                INSERT INTO players (title_id, name)
                VALUES (p_title_id, v_new_name)
                RETURNING id INTO v_player_id;
            END IF;
        END IF;

        IF v_player_id = ANY(v_resolved) THEN
            RAISE EXCEPTION '同じプレイヤーが重複して選択されています。';
        END IF;
        v_resolved := array_append(v_resolved, v_player_id);
    END LOOP;

    INSERT INTO games (title_id, name)
    VALUES (p_title_id, COALESCE(NULLIF(btrim(p_name), ''), '無題の対戦'))
    RETURNING id INTO v_game_id;

    FOREACH v_player_id IN ARRAY v_resolved LOOP
        INSERT INTO game_players (game_id, seat, player_id)
        VALUES (v_game_id, v_seat, v_player_id);
        v_seat := v_seat + 1;
    END LOOP;

    RETURN v_game_id;
END;
$$;

-- 半荘の登録。game_rounds と round_results をまとめて作る。
-- p_results の形式:
--   [{"player_id","seat","raw_score","point","rank","kaze","tobi"}, ...]
CREATE OR REPLACE FUNCTION add_round_with_results(
    p_game_id uuid,
    p_results jsonb
) RETURNS uuid
LANGUAGE plpgsql
SECURITY INVOKER
AS $$
DECLARE
    v_round_id uuid;
BEGIN
    IF jsonb_array_length(p_results) < 3 THEN
        RAISE EXCEPTION '3人分以上の結果が必要です。';
    END IF;

    INSERT INTO game_rounds (game_id) VALUES (p_game_id) RETURNING id INTO v_round_id;

    INSERT INTO round_results
        (round_id, player_id, seat, raw_score, point, rank, kaze, tobi)
    SELECT
        v_round_id,
        (e->>'player_id')::uuid,
        (e->>'seat')::smallint,
        (e->>'raw_score')::int,
        (e->>'point')::int,
        (e->>'rank')::smallint,
        e->>'kaze',
        COALESCE((e->>'tobi')::boolean, false)
    FROM jsonb_array_elements(p_results) AS e;

    RETURN v_round_id;
END;
$$;

-- 半荘の差し替え。入力ミスの修正やルール変更後の再計算に使う。
-- 旧実装には update_game_detail という関数がありながら、
-- 呼び出す画面が存在せず編集できなかった。
CREATE OR REPLACE FUNCTION update_round_results(
    p_round_id uuid,
    p_results  jsonb
) RETURNS void
LANGUAGE plpgsql
SECURITY INVOKER
AS $$
BEGIN
    DELETE FROM round_results WHERE round_id = p_round_id;

    INSERT INTO round_results
        (round_id, player_id, seat, raw_score, point, rank, kaze, tobi)
    SELECT
        p_round_id,
        (e->>'player_id')::uuid,
        (e->>'seat')::smallint,
        (e->>'raw_score')::int,
        (e->>'point')::int,
        (e->>'rank')::smallint,
        e->>'kaze',
        COALESCE((e->>'tobi')::boolean, false)
    FROM jsonb_array_elements(p_results) AS e;
END;
$$;


-- ===== RLS =========================================================
-- 直結接続をやめて REST API 経由にしたことで、RLS が実際に効くようになった。
-- 仲間内でデータを共有する前提のため、ログイン済みユーザーには全操作を許可し、
-- 未ログイン(anon)には一切許可しない。

ALTER TABLE titles        ENABLE ROW LEVEL SECURITY;
ALTER TABLE players       ENABLE ROW LEVEL SECURITY;
ALTER TABLE games         ENABLE ROW LEVEL SECURITY;
ALTER TABLE game_players  ENABLE ROW LEVEL SECURITY;
ALTER TABLE game_rounds   ENABLE ROW LEVEL SECURITY;
ALTER TABLE round_results ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['titles','players','games','game_players',
                             'game_rounds','round_results']
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I', 'authenticated_all', t);
        EXECUTE format(
            'CREATE POLICY %I ON %I FOR ALL TO authenticated USING (true) WITH CHECK (true)',
            'authenticated_all', t
        );
    END LOOP;
END;
$$;
