-- 移行前のバックアップ。003a を適用する前に実行すること。
--
-- 利用環境のネットワークは 443 以外を遮断しているため pg_dump（5432/6543）が
-- 使えない。そこでデータベース内にスキーマを作って複製する。
-- 復元も INSERT ... SELECT で済むので、実はこれが一番速い。
--
-- **存在するテーブルだけを複製する。** 旧名（titles）でも新名（tournaments）でも、
-- 途中まで移行済みでも、まっさらでも、そのまま実行できる。
-- 素朴に `CREATE TABLE bak_003.titles AS TABLE public.titles` と書くと、
-- 改名後や新規プロジェクトで「relation does not exist」で止まってしまう。
--
-- CREATE TABLE AS はデータだけを複製する（制約・索引・既定値は付かない）。
-- 復元時は本番テーブル側の制約が再検査されるので、それでよい。

DO $bak$
DECLARE
    t     text;
    found int := 0;
BEGIN
    CREATE SCHEMA IF NOT EXISTS bak_003;

    FOREACH t IN ARRAY ARRAY[
        -- 旧スキーマの名前
        'titles',
        -- 003a 適用後の名前
        'tournaments', 'tournament_days', 'groups', 'group_invites',
        -- どちらの段階でも同じ名前
        'players', 'games', 'game_players', 'game_rounds', 'round_results'
    ]
    LOOP
        IF to_regclass('public.' || quote_ident(t)) IS NULL THEN
            RAISE NOTICE 'スキップ（存在しない）: public.%', t;
            CONTINUE;
        END IF;
        EXECUTE format('DROP TABLE IF EXISTS bak_003.%I', t);
        EXECUTE format('CREATE TABLE bak_003.%I AS TABLE public.%I', t, t);
        found := found + 1;
        RAISE NOTICE 'バックアップ: public.% → bak_003.%', t, t;
    END LOOP;

    -- パスワードハッシュは複製しない。IDとメールがあれば紐付けを復元できる。
    DROP TABLE IF EXISTS bak_003.auth_users;
    CREATE TABLE bak_003.auth_users AS
        SELECT id, email, created_at, last_sign_in_at FROM auth.users;

    IF found = 0 THEN
        RAISE NOTICE '対象のテーブルが1つも見つかりませんでした。'
                     'まっさらなプロジェクトの可能性があります。'
                     '00_inventory.sql で中身を確認してください。';
    END IF;
END
$bak$;

-- PostgREST は public スキーマしか公開しないので API からは見えないが、念のため。
REVOKE ALL ON SCHEMA bak_003 FROM anon, authenticated;
REVOKE ALL ON ALL TABLES IN SCHEMA bak_003 FROM anon, authenticated;


-- ▼ この件数をスクリーンショットで残しておくこと（移行後の検算に使う）
SELECT
    c.relname AS table_name,
    (xpath('/row/cnt/text()',
           query_to_xml(format('SELECT count(*) AS cnt FROM bak_003.%I', c.relname),
                        false, true, '')))[1]::text::bigint AS rows
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'bak_003' AND c.relkind = 'r'
ORDER BY 1;

-- ▼ ポイントの総和。移行後も一致していなければならない。
SELECT sum(point) AS total_point_before, count(*) AS result_rows_before
FROM bak_003.round_results;
