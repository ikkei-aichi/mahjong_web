-- 【読み取り専用】いまDBに何があるかを調べる。まずこれを実行して結果を確認する。
--
-- 00_backup.sql が「public.titles が存在しない」で失敗した場合、
-- 次のどれなのかを切り分ける必要がある:
--   (a) まっさらなプロジェクト（テーブルが1つも無い）
--   (b) すでに 003a を適用済み（titles → tournaments に改名された後）
--   (c) テーブルが別のスキーマにある
--   (d) 想定と違う名前で運用されている

-- ===== 1. public スキーマのテーブルと行数 ===========================
SELECT
    c.relname AS table_name,
    CASE c.relkind WHEN 'r' THEN 'テーブル' WHEN 'v' THEN 'ビュー'
                   WHEN 'm' THEN 'マテビュー' WHEN 'p' THEN 'パーティション'
                   ELSE c.relkind::text END AS kind,
    pg_get_userbyid(c.relowner) AS owner,
    c.relrowsecurity AS rls,
    CASE WHEN c.relkind = 'r' THEN (
        xpath('/row/cnt/text()',
              query_to_xml(format('SELECT count(*) AS cnt FROM public.%I', c.relname),
                           false, true, ''))
    )[1]::text::bigint END AS rows
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v', 'm', 'p')
ORDER BY c.relkind, c.relname;


-- ===== 2. public 以外にテーブルがないか ==============================
-- (c) の切り分け。supabase 標準のスキーマ以外に何かあれば、そこにデータがある。
SELECT n.nspname AS schema_name, count(*) AS tables
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r'
  AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
GROUP BY n.nspname ORDER BY 1;


-- ===== 3. 主要テーブルの有無（YES/NO で一目で分かる） ================
SELECT
    to_regclass('public.titles')          IS NOT NULL AS has_titles,
    to_regclass('public.tournaments')     IS NOT NULL AS has_tournaments,
    to_regclass('public.players')         IS NOT NULL AS has_players,
    to_regclass('public.games')           IS NOT NULL AS has_games,
    to_regclass('public.game_players')    IS NOT NULL AS has_game_players,
    to_regclass('public.game_rounds')     IS NOT NULL AS has_game_rounds,
    to_regclass('public.round_results')   IS NOT NULL AS has_round_results,
    to_regclass('public.groups')          IS NOT NULL AS has_groups,
    to_regclass('public.tournament_days') IS NOT NULL AS has_days,
    to_regclass('public.schema_migrations') IS NOT NULL AS has_migration_ledger,
    to_regclass('bak_003.titles')         IS NOT NULL AS has_backup;


-- ===== 4. すでに適用済みのマイグレーション ===========================
-- has_migration_ledger が true のときだけ意味がある。
SELECT version, applied_at, note FROM public.schema_migrations ORDER BY version;


-- ===== 5. players / games の列構成 ==================================
-- title_id なのか legacy_title_id / tournament_id なのかで、
-- どの段階まで進んでいるかが分かる。
SELECT table_name, ordinal_position, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN ('titles', 'tournaments', 'players', 'games',
                     'game_players', 'game_rounds', 'round_results')
ORDER BY table_name, ordinal_position;


-- ===== 6. ログインユーザー ==========================================
SELECT count(*) AS auth_users FROM auth.users;
SELECT id, email, created_at, last_sign_in_at FROM auth.users ORDER BY created_at;
