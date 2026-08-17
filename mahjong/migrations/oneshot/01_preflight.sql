-- プリフライト（読み取り専用）。02_data_migration.sql の前に必ず実行し、
-- 出力を目で確認すること。1本も書き込まない。
--
-- ★中止条件★ P4 / P5 / P6 が1行でも返したら 02 を実行しないこと。
--   統合すると一意制約に衝突し、移行が失敗する（または壊れる）。

-- ===== P1  規模 =====================================================
SELECT 'titles'  AS table_name,
       count(*) FILTER (WHERE deleted_at IS NULL) AS live,
       count(*) AS total FROM public.titles
UNION ALL SELECT 'players',      count(*) FILTER (WHERE deleted_at IS NULL), count(*) FROM public.players
UNION ALL SELECT 'games',        count(*) FILTER (WHERE deleted_at IS NULL), count(*) FROM public.games
UNION ALL SELECT 'game_rounds',  count(*) FILTER (WHERE deleted_at IS NULL), count(*) FROM public.game_rounds
UNION ALL SELECT 'game_players', NULL, count(*) FROM public.game_players
UNION ALL SELECT 'round_results',NULL, count(*) FROM public.round_results
ORDER BY table_name;


-- ===== P2  ★最重要★ 統合されるプレイヤーの一覧 =====================
-- 1行ずつ見て「本当に同一人物か」を確認する。
-- 統合キーは btrim(name) の完全一致のみ（大小文字・全角半角は揃えない）。
-- 代表(survivor)の選定: 生存を優先 → created_at が古い順 → id。
WITH c AS (
    SELECT p.id, btrim(p.name) AS norm, p.name, p.title_id,
           t.name AS title_name, p.created_at, p.deleted_at,
           (SELECT count(*) FROM public.game_players gp  WHERE gp.player_id = p.id) AS n_games,
           (SELECT count(*) FROM public.round_results rr WHERE rr.player_id = p.id) AS n_results,
           first_value(p.id) OVER (
               PARTITION BY btrim(p.name)
               ORDER BY (p.deleted_at IS NULL) DESC, p.created_at, p.id) AS survivor_id
    FROM public.players p
    JOIN public.titles t ON t.id = p.title_id
)
SELECT norm, id, (id = survivor_id) AS is_survivor, title_name,
       n_games, n_results, created_at, deleted_at
FROM c
WHERE norm IN (SELECT norm FROM c GROUP BY norm HAVING count(*) > 1)
ORDER BY norm, is_survivor DESC, created_at;


-- ===== P3  惜しい一致（統合されない） ===============================
-- 「田中」と「田中　」（全角空白）のような表記ゆれ。統合したいなら
-- 02 を流す前に手作業で名前を揃えておくこと。
WITH n AS (
    SELECT id, name, btrim(name) AS exact,
           lower(regexp_replace(btrim(name), '\s+', '', 'g')) AS fuzzy
    FROM public.players
)
SELECT fuzzy AS fuzzy_key, count(DISTINCT exact) AS distinct_names,
       array_agg(DISTINCT exact) AS variants
FROM n GROUP BY fuzzy HAVING count(DISTINCT exact) > 1;


-- ===== P4  ★中止条件★ round_results の主キー衝突 ===================
-- 「1つの半荘に同名の別プレイヤーが2人いる」と、統合したとき
-- PRIMARY KEY (round_id, player_id) に衝突する。0行であること。
WITH m AS (
    SELECT id AS old_id,
           first_value(id) OVER (
               PARTITION BY btrim(name)
               ORDER BY (deleted_at IS NULL) DESC, created_at, id) AS new_id
    FROM public.players
)
SELECT rr.round_id, m.new_id AS collides_on_player,
       count(*) AS rows_after_merge,
       array_agg(rr.player_id) AS current_player_ids,
       array_agg(rr.seat) AS seats
FROM public.round_results rr JOIN m ON m.old_id = rr.player_id
GROUP BY rr.round_id, m.new_id HAVING count(*) > 1;


-- ===== P5  ★中止条件★ game_players の一意制約衝突 ==================
WITH m AS (
    SELECT id AS old_id,
           first_value(id) OVER (
               PARTITION BY btrim(name)
               ORDER BY (deleted_at IS NULL) DESC, created_at, id) AS new_id
    FROM public.players
)
SELECT gp.game_id, m.new_id AS collides_on_player,
       count(*) AS rows_after_merge, array_agg(gp.seat) AS seats
FROM public.game_players gp JOIN m ON m.old_id = gp.player_id
GROUP BY gp.game_id, m.new_id HAVING count(*) > 1;


-- ===== P6  ★中止条件★ 既存データの不整合 ===========================
-- 003c で複合外部キーを張るため、いま矛盾があると失敗する。すべて0行が期待値。

-- (a) 別大会のプレイヤーが卓に座っている
SELECT 'game_players_cross_title' AS problem, gp.game_id, gp.player_id,
       g.title_id AS game_title, p.title_id AS player_title
FROM public.game_players gp
JOIN public.games g   ON g.id = gp.game_id
JOIN public.players p ON p.id = gp.player_id
WHERE g.title_id <> p.title_id;

-- (b) 別大会のプレイヤーの結果が入っている
SELECT 'round_results_cross_title' AS problem, rr.round_id, rr.player_id
FROM public.round_results rr
JOIN public.game_rounds gr ON gr.id = rr.round_id
JOIN public.games g        ON g.id  = gr.game_id
JOIN public.players p      ON p.id  = rr.player_id
WHERE g.title_id <> p.title_id;

-- (c) 卓に座っていないのに結果がある（旧 RPC が検証していなかった不変条件）
SELECT 'result_without_seat' AS problem, rr.round_id, rr.player_id, rr.seat
FROM public.round_results rr
JOIN public.game_rounds gr ON gr.id = rr.round_id
WHERE NOT EXISTS (
    SELECT 1 FROM public.game_players gp
    WHERE gp.game_id = gr.game_id AND gp.player_id = rr.player_id AND gp.seat = rr.seat);


-- ===== P7  auth.users =============================================
-- 誰がオーナーになるか、名前が既存プレイヤーとぶつからないかを確認する。
SELECT u.id, u.email, u.created_at, u.last_sign_in_at,
       split_part(u.email, '@', 1) AS suggested_name,
       (lower(u.email) = 'ikkei812@gmail.com') AS will_be_owner,
       EXISTS (SELECT 1 FROM public.players p
               WHERE btrim(p.name) = split_part(u.email, '@', 1)
                 AND p.deleted_at IS NULL) AS name_clashes
FROM auth.users u ORDER BY u.created_at;

-- オーナー候補が存在するか（0件なら最古のユーザーが自動的にオーナーになる）
SELECT count(*) AS owner_candidate_found
FROM auth.users WHERE lower(email) = 'ikkei812@gmail.com';


-- ===== P8  生成される開催日 =========================================
SELECT t.name AS tournament,
       (g.created_at AT TIME ZONE 'Asia/Tokyo')::date AS held_on,
       count(*) AS games
FROM public.games g JOIN public.titles t ON t.id = g.title_id
GROUP BY 1, 2 ORDER BY 1, 2;


-- ===== P9  前提の確認 ===============================================
-- SECURITY DEFINER による RLS 回避が成立するには、テーブルの所有者と
-- 関数の所有者が一致している必要がある（所有者は RLS を素通りするため）。
SELECT current_user, session_user;   -- postgres であること
SELECT c.relname, pg_get_userbyid(c.relowner) AS owner,
       c.relrowsecurity AS rls_enabled,
       c.relforcerowsecurity AS rls_forced   -- ★ forced が true だと再帰する
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v')
ORDER BY 1;
