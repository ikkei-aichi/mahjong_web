-- 現行スキーマのベースライン（冪等）
--
-- このファイル1本で、次のどちらでも同じ最終形になる:
--   * まっさらな Supabase プロジェクト → テーブルを新規作成する
--   * 旧スキーマ(legacy/001,002)が入った既存DB → 改名と列追加で移行する
--
-- legacy/001_init.sql と legacy/002_views_rls_rpc.sql は履歴として残してあるが、
-- schema.sql には含めない。含めると移行後に空の titles テーブルが再作成され、
-- 「ログイン済みなら全部許可」の旧RLSポリシーまで復活してしまう。
--
-- 階層:
--   groups（グループ）
--     ├ players            参加者。user_id が NULL ならゲスト（アカウント無し）
--     ├ group_invites      招待コード
--     └ tournaments        大会  ← 旧 titles
--        └ tournament_days 開催日（大会は複数日にわたる）
--           └ games        卓・対戦
--              └ game_rounds       半荘（適用ルールのスナップショット付き）
--                 └ round_results  1人分の結果
--
-- データは一切変更しない。何度実行しても安全。
-- 一度きりのデータ移行（プレイヤーの統合など）は migrations/oneshot/ にある。

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()


-- ===== マイグレーション台帳 =========================================
-- 冪等でない処理を二度流さないための記録。
-- ポリシーを1つも作らない＝API からは存在しないのと同じ。
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    version    text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now(),
    note       text
);
ALTER TABLE public.schema_migrations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.schema_migrations FROM anon, authenticated;


-- ===== 旧ビューを落とす =============================================
-- CREATE OR REPLACE VIEW は既存の列の削除・改名ができない（追加のみ）。
-- title_id → tournament_id に変えるには DROP するしかない。003d で作り直す。
DROP VIEW IF EXISTS public.v_game_seats;
DROP VIEW IF EXISTS public.v_round_entries;


-- ===== 旧スキーマからの改名 =========================================
-- 改名はメタデータの変更だけでデータは動かない。外部キーは OID を見ているので壊れない。
-- 新規DBではどのブロックも動かない（旧テーブルが存在しないため）。

DO $$
BEGIN
    IF to_regclass('public.titles') IS NOT NULL
       AND to_regclass('public.tournaments') IS NULL THEN
        ALTER TABLE public.titles RENAME TO tournaments;
        ALTER INDEX IF EXISTS public.titles_pkey       RENAME TO tournaments_pkey;
        ALTER INDEX IF EXISTS public.titles_active_idx RENAME TO tournaments_active_idx;
        -- owner_id は「作成者」の意味しか無く、検索条件にもRLSにも使われていなかった。
        -- 権限判定はグループ所属で行うので、素直に created_by に改名して残す。
        ALTER TABLE public.tournaments RENAME COLUMN owner_id TO created_by;
    END IF;
END $$;

-- players.title_id への外部キーは必ず外す。
-- 統合後の代表プレイヤーの legacy_title_id は「たまたま最初に所属していた大会」を
-- 指すだけになるため、その大会を物理削除すると ON DELETE CASCADE で
-- プレイヤー行ごと消えてしまう（＝全成績が消滅する）。
ALTER TABLE IF EXISTS public.players DROP CONSTRAINT IF EXISTS players_title_id_fkey;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'public' AND table_name = 'players'
                 AND column_name = 'title_id')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'public' AND table_name = 'players'
                 AND column_name = 'legacy_title_id') THEN
        ALTER TABLE public.players ALTER COLUMN title_id DROP NOT NULL;
        -- 巻き戻せるよう、列自体は消さずに改名して残す。削除は運用が安定してから。
        ALTER TABLE public.players RENAME COLUMN title_id TO legacy_title_id;
    END IF;
END $$;

-- games.title_id は「その対戦が属する大会」そのものなので改名で足りる。値の移送は不要。
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'public' AND table_name = 'games'
                 AND column_name = 'title_id')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'public' AND table_name = 'games'
                 AND column_name = 'tournament_id') THEN
        ALTER TABLE public.games RENAME COLUMN title_id TO tournament_id;
        ALTER INDEX IF EXISTS public.games_title_idx RENAME TO games_tournament_idx;
    END IF;
END $$;


-- ===== テーブル定義（新規DB向け。既存DBでは何も起きない） ===========

CREATE TABLE IF NOT EXISTS public.groups (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 60),
    description text,
    -- auth.users.id。GoTrue 管理のテーブルに外部キーを張ると、ユーザー削除が
    -- こちらに波及したり逆に阻害したりするので、あえて素の uuid にする。
    created_by  uuid,
    created_at  timestamptz NOT NULL DEFAULT now(),
    deleted_at  timestamptz
);

CREATE TABLE IF NOT EXISTS public.group_invites (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id   uuid NOT NULL REFERENCES public.groups(id) ON DELETE CASCADE,
    -- 読み違えやすい 0/O・1/I を含まない大文字英数字
    code       text NOT NULL UNIQUE CHECK (code ~ '^[A-Z2-9YZ]{6,16}$'),
    created_by uuid,
    expires_at timestamptz,
    max_uses   integer CHECK (max_uses IS NULL OR max_uses > 0),
    used_count integer NOT NULL DEFAULT 0 CHECK (used_count >= 0),
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS group_invites_group_idx ON public.group_invites (group_id);

CREATE TABLE IF NOT EXISTS public.tournaments (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id   uuid NOT NULL,
    name       text NOT NULL CHECK (length(btrim(name)) > 0),
    -- ウマ・オカ・返し点・レート等。列を増やさず項目を追加できるよう jsonb にする
    ruleset    jsonb NOT NULL DEFAULT '{}'::jsonb,
    note       text,
    created_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz
);

-- 参加者＝メンバー。user_id IS NOT NULL がそのグループのメンバー、NULL はゲスト。
-- 1テーブルにまとめることで「ゲストとして記録 → 後日アカウントを紐付け」
-- という現実の流れがそのまま表現できる。
CREATE TABLE IF NOT EXISTS public.players (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id        uuid NOT NULL,
    name            text NOT NULL CHECK (length(btrim(name)) > 0),
    user_id         uuid,
    role            text NOT NULL DEFAULT 'member',
    is_provisional  boolean NOT NULL DEFAULT false,
    merged_into     uuid,
    created_at      timestamptz NOT NULL DEFAULT now(),
    deleted_at      timestamptz
);
-- legacy_title_id は旧スキーマから改名して残る列。新規DBには作らない。

CREATE TABLE IF NOT EXISTS public.tournament_days (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tournament_id uuid NOT NULL,
    group_id      uuid NOT NULL,   -- RLS を1回の索引参照で済ませるための非正規化
    held_on       date NOT NULL,
    label         text,            -- 「初日」「決勝」など
    note          text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    deleted_at    timestamptz
);

CREATE TABLE IF NOT EXISTS public.games (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tournament_id uuid NOT NULL,
    day_id        uuid NOT NULL,
    group_id      uuid NOT NULL,
    name          text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    deleted_at    timestamptz
);

-- 対戦の席順。seat は 0..3（3人麻雀なら3行）
CREATE TABLE IF NOT EXISTS public.game_players (
    game_id   uuid NOT NULL,
    group_id  uuid NOT NULL,
    seat      smallint NOT NULL CHECK (seat BETWEEN 0 AND 3),
    player_id uuid NOT NULL,
    PRIMARY KEY (game_id, seat),
    UNIQUE (game_id, player_id)   -- 同じ人が同じ卓に二重で座らない
);

-- 半荘1回。表示上の「回」は取得時に連番を振り直すため、
-- 途中の回を削除しても番号に穴が空かない。
CREATE TABLE IF NOT EXISTS public.game_rounds (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    game_id    uuid NOT NULL,
    group_id   uuid NOT NULL,
    -- この半荘を計算したときのルール。あとで大会のルールを変えても、
    -- どのルールで出た点なのか追跡できる。
    ruleset    jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz
);

-- 半荘1回における1人分の結果
CREATE TABLE IF NOT EXISTS public.round_results (
    round_id  uuid NOT NULL,
    group_id  uuid NOT NULL,
    player_id uuid NOT NULL,
    seat      smallint NOT NULL CHECK (seat BETWEEN 0 AND 3),
    -- 入力された持ち点。これがあるからルール変更後の再計算ができる
    raw_score integer NOT NULL,
    -- ウマ・オカ・飛び賞まで含んだ最終ポイント
    point     integer NOT NULL,
    -- 確定順位。同点は風で決着済みなので、集計時に同点判定は不要
    rank      smallint NOT NULL CHECK (rank BETWEEN 1 AND 4),
    kaze      text NOT NULL CHECK (kaze IN ('東', '南', '西', '北')),
    tobi      boolean NOT NULL DEFAULT false,
    PRIMARY KEY (round_id, player_id),
    UNIQUE (round_id, seat),
    UNIQUE (round_id, rank),
    UNIQUE (round_id, kaze)
);


-- ===== 既存DBへの列追加（新規DBでは何も起きない） ===================
-- 既存行があるため NOT NULL は付けない。oneshot/02 が値を埋めてから締める。
ALTER TABLE public.tournaments   ADD COLUMN IF NOT EXISTS group_id uuid;
ALTER TABLE public.tournaments   ADD COLUMN IF NOT EXISTS note     text;
ALTER TABLE public.players       ADD COLUMN IF NOT EXISTS group_id        uuid;
ALTER TABLE public.players       ADD COLUMN IF NOT EXISTS user_id         uuid;
ALTER TABLE public.players       ADD COLUMN IF NOT EXISTS role            text NOT NULL DEFAULT 'member';
ALTER TABLE public.players       ADD COLUMN IF NOT EXISTS is_provisional  boolean NOT NULL DEFAULT false;
ALTER TABLE public.players       ADD COLUMN IF NOT EXISTS merged_into     uuid;
ALTER TABLE public.games         ADD COLUMN IF NOT EXISTS day_id   uuid;
ALTER TABLE public.games         ADD COLUMN IF NOT EXISTS group_id uuid;
ALTER TABLE public.game_rounds   ADD COLUMN IF NOT EXISTS group_id uuid;
ALTER TABLE public.game_rounds   ADD COLUMN IF NOT EXISTS ruleset  jsonb;
ALTER TABLE public.game_players  ADD COLUMN IF NOT EXISTS group_id uuid;
ALTER TABLE public.round_results ADD COLUMN IF NOT EXISTS group_id uuid;


-- ===== インデックス =================================================
CREATE INDEX IF NOT EXISTS tournaments_active_idx
    ON public.tournaments (created_at DESC) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS tournament_days_uniq
    ON public.tournament_days (tournament_id, held_on) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS tournament_days_group_idx
    ON public.tournament_days (group_id, held_on DESC);
CREATE INDEX IF NOT EXISTS games_tournament_idx
    ON public.games (tournament_id, created_at DESC);
CREATE INDEX IF NOT EXISTS game_players_player_idx ON public.game_players (player_id);
CREATE INDEX IF NOT EXISTS game_rounds_game_idx
    ON public.game_rounds (game_id, created_at) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS round_results_player_idx ON public.round_results (player_id);


-- ===== 役割の制約 ===================================================
-- 既存行は role が既定値 'member'、user_id が NULL なのでどちらも満たす。
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'players_role_chk') THEN
        ALTER TABLE public.players
            ADD CONSTRAINT players_role_chk CHECK (role IN ('owner', 'admin', 'member'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'players_guest_chk') THEN
        -- アカウントを持たないゲストに管理権限は持たせない
        ALTER TABLE public.players
            ADD CONSTRAINT players_guest_chk CHECK (user_id IS NOT NULL OR role = 'member');
    END IF;
END $$;


-- ===== 移行の監査テーブル ===========================================
-- oneshot/02_data_migration.sql が書き込む。統合を巻き戻すための根拠。
-- **旧スキーマから移行する場合だけ作る。** まっさらなプロジェクトに
-- 統合の痕跡を残すためのテーブルを置いても意味がないため。
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = 'players'
                     AND column_name = 'legacy_title_id') THEN
        RAISE NOTICE '旧スキーマからの移行ではないため、監査テーブルは作りません。';
        RETURN;
    END IF;

    CREATE TABLE IF NOT EXISTS public.mig003_meta (
        key text PRIMARY KEY,
        value text
    );
    CREATE TABLE IF NOT EXISTS public.mig003_player_merge_map (
        loser_id         uuid PRIMARY KEY,
        survivor_id      uuid NOT NULL,
        norm_name        text NOT NULL,
        loser_title_id   uuid,
        loser_name       text,
        loser_created_at timestamptz,
        loser_deleted_at timestamptz
    );
    CREATE TABLE IF NOT EXISTS public.mig003_game_players_remap (
        game_id uuid, seat smallint, old_player_id uuid, new_player_id uuid,
        PRIMARY KEY (game_id, seat)
    );
    CREATE TABLE IF NOT EXISTS public.mig003_round_results_remap (
        round_id uuid, seat smallint, old_player_id uuid, new_player_id uuid,
        PRIMARY KEY (round_id, seat)
    );

    ALTER TABLE public.mig003_meta                ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.mig003_player_merge_map    ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.mig003_game_players_remap  ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.mig003_round_results_remap ENABLE ROW LEVEL SECURITY;
    REVOKE ALL ON public.mig003_meta, public.mig003_player_merge_map,
                  public.mig003_game_players_remap, public.mig003_round_results_remap
           FROM anon, authenticated;
END $$;

NOTIFY pgrst, 'reload schema';
