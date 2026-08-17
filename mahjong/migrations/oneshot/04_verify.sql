-- 移行後の検証（読み取り専用）。003a〜003d をすべて適用したあとに実行する。

-- ===== 1. 台帳と統合結果 ============================================
SELECT version, applied_at, note FROM public.schema_migrations ORDER BY version;
SELECT * FROM public.mig003_meta;

SELECT count(*) AS merged_rows FROM public.mig003_player_merge_map;
-- 統合された側が生きて残っていないこと（0であること）
SELECT count(*) AS merged_but_still_active
FROM public.players WHERE merged_into IS NOT NULL AND deleted_at IS NULL;


-- ===== 2. ★検算★ ポイント総和と件数が移行前と一致すること ==========
SELECT (SELECT sum(point) FROM public.round_results)  AS total_point_now,
       (SELECT sum(point) FROM bak_003.round_results) AS total_point_before,
       (SELECT count(*)   FROM public.round_results)  AS result_rows_now,
       (SELECT count(*)   FROM bak_003.round_results) AS result_rows_before,
       (SELECT count(*)   FROM public.game_rounds)    AS rounds_now,
       (SELECT count(*)   FROM bak_003.game_rounds)   AS rounds_before;

-- 半荘ごとの合計は必ず0（ゼロサム）。0でない半荘が出たら要調査。
SELECT round_id, sum(point) AS total
FROM public.round_results GROUP BY round_id HAVING sum(point) <> 0;


-- ===== 3. NULL が残っていないこと（すべて0） ========================
SELECT
    (SELECT count(*) FROM public.games         WHERE day_id IS NULL OR group_id IS NULL) AS games_null,
    (SELECT count(*) FROM public.game_rounds   WHERE ruleset IS NULL OR group_id IS NULL) AS rounds_null,
    (SELECT count(*) FROM public.players       WHERE group_id IS NULL) AS players_null,
    (SELECT count(*) FROM public.tournaments   WHERE group_id IS NULL) AS tournaments_null,
    (SELECT count(*) FROM public.round_results WHERE group_id IS NULL) AS results_null;


-- ===== 4. ビューが RLS を素通りしていないこと =======================
-- すべて {security_invoker=true} であること。ここが抜けると全グループ丸見えになる。
SELECT c.relname, c.reloptions
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'v' ORDER BY 1;

-- FORCE ROW LEVEL SECURITY が付いていないこと（付くと RLS が再帰する）
SELECT relname, relrowsecurity, relforcerowsecurity
FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'
  AND relforcerowsecurity;


-- ===== 5. ★最重要★ RLS が実際に効くか ==============================
-- SQL Editor は postgres として動き、テーブル所有者は RLS を素通りする。
-- 「ダッシュボードで見えたから大丈夫」は検証にならない。
-- 必ず authenticated ロールを被って確認すること。
--
-- 下の <ユーザーAのuuid> を実際の auth.users.id に置き換えて実行する。

-- (a) メンバーとして
/*
BEGIN;
    SET LOCAL ROLE authenticated;
    SET LOCAL request.jwt.claims = '{"sub":"<ユーザーAのuuid>","role":"authenticated"}';

    SELECT auth.uid()                       AS who;          -- ユーザーAであること
    SELECT public.current_group_ids()       AS my_groups;    -- 既定グループが1件
    SELECT count(*) FROM public.players     AS visible;      -- 見えるべき件数
    SELECT count(*) FROM public.round_results;               -- 全記録が見えること

    -- ★これは失敗しなければならない（42501 permission denied for column role）★
    -- RLS だけでは止められない自己昇格を、列単位GRANTが止めていることの確認。
    UPDATE public.players SET role = 'owner' WHERE user_id = auth.uid();
ROLLBACK;
*/

-- (b) どのグループにも属さないユーザーとして（すべて0であること）
/*
BEGIN;
    SET LOCAL ROLE authenticated;
    SET LOCAL request.jwt.claims =
        '{"sub":"00000000-0000-0000-0000-000000000000","role":"authenticated"}';

    SELECT count(*) AS players       FROM public.players;        -- 0
    SELECT count(*) AS tournaments   FROM public.tournaments;    -- 0
    SELECT count(*) AS round_results FROM public.round_results;  -- 0
    SELECT count(*) AS groups        FROM public.groups;         -- 0
ROLLBACK;
*/


-- ===== 6. メンバー構成の確認 ========================================
SELECT p.name, p.role, p.is_provisional,
       (p.user_id IS NOT NULL) AS has_account,
       u.email
FROM public.players p
LEFT JOIN auth.users u ON u.id = p.user_id
WHERE p.deleted_at IS NULL
ORDER BY (p.user_id IS NOT NULL) DESC, p.role, p.name;


-- ===== 7. 階層が組み上がっているか ==================================
SELECT g.name AS "グループ", t.name AS "大会", d.held_on AS "開催日",
       count(DISTINCT ga.id) AS "卓", count(DISTINCT gr.id) AS "半荘"
FROM public.groups g
JOIN public.tournaments t     ON t.group_id = g.id
LEFT JOIN public.tournament_days d ON d.tournament_id = t.id
LEFT JOIN public.games ga     ON ga.day_id = d.id AND ga.deleted_at IS NULL
LEFT JOIN public.game_rounds gr ON gr.game_id = ga.id AND gr.deleted_at IS NULL
GROUP BY g.name, t.name, d.held_on
ORDER BY g.name, t.name, d.held_on;
