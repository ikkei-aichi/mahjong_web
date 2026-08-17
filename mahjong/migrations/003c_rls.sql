-- 制約 / インデックス / RLS（冪等）
--
-- 権限モデル: 「グループに所属しているか」だけを判定軸にする。
--   * 参加者は所属グループの全履歴を閲覧・記録できる
--   * 管理者(owner/admin)だけが大会の削除・メンバー管理・招待発行をできる
--   * どのグループにも属さないユーザーには何も見えない
--     （新規登録が開放されたままでも安全になる）
--
-- 旧実装は6テーブルすべて USING (true) WITH CHECK (true) で、
-- ログインさえすれば誰でも全データを削除できた。

-- ===== 1. 複合外部キーの参照先になる一意制約 ========================
-- group_id を各テーブルに非正規化しているので、放っておくと
-- 「games.group_id と games.tournament_id の所属グループがずれる」ことが起きる。
-- ずれた瞬間に RLS が静かに漏れる／隠すため、複合FKで Postgres 自身に守らせる。
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'tournaments_id_group_uniq') THEN
        ALTER TABLE public.tournaments ADD CONSTRAINT tournaments_id_group_uniq UNIQUE (id, group_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'players_id_group_uniq') THEN
        ALTER TABLE public.players ADD CONSTRAINT players_id_group_uniq UNIQUE (id, group_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'tdays_id_tour_group_uniq') THEN
        ALTER TABLE public.tournament_days
            ADD CONSTRAINT tdays_id_tour_group_uniq UNIQUE (id, tournament_id, group_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'games_id_group_uniq') THEN
        ALTER TABLE public.games ADD CONSTRAINT games_id_group_uniq UNIQUE (id, group_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'rounds_id_group_uniq') THEN
        ALTER TABLE public.game_rounds ADD CONSTRAINT rounds_id_group_uniq UNIQUE (id, group_id);
    END IF;
END $$;


-- ===== 2. 旧・単一列の外部キーを外す =================================
ALTER TABLE public.games         DROP CONSTRAINT IF EXISTS games_title_id_fkey;
ALTER TABLE public.game_players  DROP CONSTRAINT IF EXISTS game_players_game_id_fkey;
ALTER TABLE public.game_players  DROP CONSTRAINT IF EXISTS game_players_player_id_fkey;
ALTER TABLE public.game_rounds   DROP CONSTRAINT IF EXISTS game_rounds_game_id_fkey;
ALTER TABLE public.round_results DROP CONSTRAINT IF EXISTS round_results_round_id_fkey;
ALTER TABLE public.round_results DROP CONSTRAINT IF EXISTS round_results_player_id_fkey;


-- ===== 3. 複合外部キー ==============================================
-- games → tournament_days の3列FKは、この1本で
--   「開催日が実在する」「開催日と大会が一致する」「グループが一致する」
-- を同時に保証する。2列だと、同じグループの“別の大会”の日を指せてしまう。
DO $$
DECLARE stmt text;
BEGIN
    FOREACH stmt IN ARRAY ARRAY[
        'ALTER TABLE public.tournaments ADD CONSTRAINT tournaments_group_fkey
            FOREIGN KEY (group_id) REFERENCES public.groups(id) ON DELETE RESTRICT NOT VALID',
        'ALTER TABLE public.players ADD CONSTRAINT players_group_fkey
            FOREIGN KEY (group_id) REFERENCES public.groups(id) ON DELETE RESTRICT NOT VALID',
        'ALTER TABLE public.tournament_days ADD CONSTRAINT tdays_tournament_fkey
            FOREIGN KEY (tournament_id, group_id)
            REFERENCES public.tournaments(id, group_id)
            ON UPDATE CASCADE ON DELETE CASCADE NOT VALID',
        'ALTER TABLE public.games ADD CONSTRAINT games_day_fkey
            FOREIGN KEY (day_id, tournament_id, group_id)
            REFERENCES public.tournament_days(id, tournament_id, group_id)
            ON UPDATE CASCADE ON DELETE CASCADE NOT VALID',
        'ALTER TABLE public.game_rounds ADD CONSTRAINT rounds_game_fkey
            FOREIGN KEY (game_id, group_id) REFERENCES public.games(id, group_id)
            ON UPDATE CASCADE ON DELETE CASCADE NOT VALID',
        'ALTER TABLE public.game_players ADD CONSTRAINT gp_game_fkey
            FOREIGN KEY (game_id, group_id) REFERENCES public.games(id, group_id)
            ON UPDATE CASCADE ON DELETE CASCADE NOT VALID',
        'ALTER TABLE public.game_players ADD CONSTRAINT gp_player_fkey
            FOREIGN KEY (player_id, group_id) REFERENCES public.players(id, group_id)
            ON UPDATE CASCADE ON DELETE RESTRICT NOT VALID',
        'ALTER TABLE public.round_results ADD CONSTRAINT rr_round_fkey
            FOREIGN KEY (round_id, group_id) REFERENCES public.game_rounds(id, group_id)
            ON UPDATE CASCADE ON DELETE CASCADE NOT VALID',
        'ALTER TABLE public.round_results ADD CONSTRAINT rr_player_fkey
            FOREIGN KEY (player_id, group_id) REFERENCES public.players(id, group_id)
            ON UPDATE CASCADE ON DELETE RESTRICT NOT VALID'
    ]
    LOOP
        BEGIN
            EXECUTE stmt;
        EXCEPTION WHEN duplicate_object THEN
            NULL;  -- 既に張ってある
        END;
    END LOOP;
END $$;

-- NOT VALID で入れてから検証する（大きなテーブルを長時間ロックしないため）
DO $$
DECLARE c record;
BEGIN
    FOR c IN SELECT conrelid::regclass AS tbl, conname FROM pg_constraint
             WHERE contype = 'f' AND NOT convalidated
               AND connamespace = 'public'::regnamespace
    LOOP
        EXECUTE format('ALTER TABLE %s VALIDATE CONSTRAINT %I', c.tbl, c.conname);
    END LOOP;
END $$;


-- ===== 4. インデックス ==============================================
-- Postgres は外部キー元の列に自動でインデックスを張らない。
-- RLS の判定が毎行走るので、group_id には必ず必要。
CREATE INDEX IF NOT EXISTS tournaments_group_idx
    ON public.tournaments (group_id, created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS games_group_idx     ON public.games (group_id);
CREATE INDEX IF NOT EXISTS games_day_idx       ON public.games (day_id, created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS rounds_group_idx    ON public.game_rounds (group_id);
CREATE INDEX IF NOT EXISTS gp_group_idx        ON public.game_players (group_id);
CREATE INDEX IF NOT EXISTS rr_group_idx        ON public.round_results (group_id);
CREATE INDEX IF NOT EXISTS rr_player_group_idx ON public.round_results (player_id, group_id);

-- 参加者の一意性はグループ単位（旧スキーマは大会単位だった）
DO $$
BEGIN
    BEGIN
        CREATE UNIQUE INDEX IF NOT EXISTS players_group_name_uniq
            ON public.players (group_id, name) WHERE deleted_at IS NULL;
    EXCEPTION WHEN unique_violation THEN
        RAISE EXCEPTION
            '同じグループに同名の参加者が残っています。'
            '先に migrations/oneshot/02_data_migration.sql（プレイヤーの統合）を実行してください。';
    END;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS players_group_user_uniq
    ON public.players (group_id, user_id)
    WHERE user_id IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS players_group_idx
    ON public.players (group_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS players_user_idx
    ON public.players (user_id) WHERE user_id IS NOT NULL;


-- ===== 5. 所属判定のヘルパー ========================================
-- ★再帰の回避★
-- players のポリシーが「自分の所属グループか」を判定するには players を引く必要があり、
-- 素直に書くと policy → players → policy → ... で
-- 「infinite recursion detected in policy for relation "players"」になる。
--
-- 逃げ道は SECURITY DEFINER そのものではなく、
-- 「テーブルの所有者には RLS が適用されない」という Postgres の仕様。
-- SQL Editor で作ったテーブルも関数も postgres 所有なので、
-- DEFINER 関数の中では players の RLS が外れて再帰しない。
--
-- したがって ALTER TABLE ... FORCE ROW LEVEL SECURITY は絶対に付けないこと。
-- FORCE は所有者にも RLS を適用する設定で、これを付けた瞬間に再帰が復活する。
--
-- SET search_path = '' は必須。空にしないと、呼び出し元が用意したスキーマを
-- 先に見に行かせて postgres 権限で乗っ取られる余地が残る。
-- そのため中の名前はすべてスキーマ修飾する。

CREATE OR REPLACE FUNCTION public.current_group_ids()
RETURNS uuid[] LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
    SELECT COALESCE(array_agg(p.group_id), '{}'::uuid[])
    FROM public.players p
    WHERE p.user_id = auth.uid() AND p.deleted_at IS NULL;
$$;

CREATE OR REPLACE FUNCTION public.current_admin_group_ids()
RETURNS uuid[] LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
    SELECT COALESCE(array_agg(p.group_id), '{}'::uuid[])
    FROM public.players p
    WHERE p.user_id = auth.uid() AND p.deleted_at IS NULL
      AND p.role IN ('owner', 'admin');
$$;

-- array_agg は0行だと NULL を返す。NULL のまま = ANY に渡すと
-- 判定が NULL になり、否定形を書いた瞬間に壊れるので必ず空配列にする。

REVOKE ALL ON FUNCTION public.current_group_ids(), public.current_admin_group_ids()
    FROM public;
GRANT EXECUTE ON FUNCTION public.current_group_ids(), public.current_admin_group_ids()
    TO authenticated;


-- ===== 6. RLS 有効化 ================================================
ALTER TABLE public.groups          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.group_invites   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tournament_days ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tournaments     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.players         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.games           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.game_players    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.game_rounds     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.round_results   ENABLE ROW LEVEL SECURITY;

-- 旧「ログイン済みなら全部許可」を剥がす
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['tournaments', 'players', 'games', 'game_players',
                             'game_rounds', 'round_results']
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS authenticated_all ON public.%I', t);
    END LOOP;
END $$;


-- ===== 7. ポリシー ==================================================
-- 判定は `group_id IN (SELECT unnest(...))` の形で書く。
-- 副問い合わせなので1クエリにつき1回だけ評価される（毎行呼ばれない）。

-- ---- groups --------------------------------------------------------
DROP POLICY IF EXISTS groups_select ON public.groups;
CREATE POLICY groups_select ON public.groups FOR SELECT TO authenticated
    USING (id IN (SELECT unnest(public.current_group_ids())));

DROP POLICY IF EXISTS groups_update ON public.groups;
CREATE POLICY groups_update ON public.groups FOR UPDATE TO authenticated
    USING      (id IN (SELECT unnest(public.current_admin_group_ids())))
    WITH CHECK (id IN (SELECT unnest(public.current_admin_group_ids())));

-- INSERT ポリシーは作らない。グループ作成は create_group() RPC のみ
-- （グループ行とオーナー行を不可分に作る必要があるため）。
-- DELETE ポリシーも作らない。物理削除はさせず deleted_at で消す。

-- ---- players -------------------------------------------------------
DROP POLICY IF EXISTS players_select ON public.players;
CREATE POLICY players_select ON public.players FOR SELECT TO authenticated
    USING (group_id IN (SELECT unnest(public.current_group_ids())));

-- ゲスト（アカウント無しの参加者）の追加はメンバー全員に許す。
-- 席に名前を直接打ち込めるという今の使い勝手を保つため。
-- 列単位の GRANT で挿入できるのは (group_id, name) だけなので、
-- user_id は必ず NULL、role は既定値の 'member' になる。
DROP POLICY IF EXISTS players_insert ON public.players;
CREATE POLICY players_insert ON public.players FOR INSERT TO authenticated
    WITH CHECK (group_id IN (SELECT unnest(public.current_group_ids()))
                AND user_id IS NULL AND role = 'member');

DROP POLICY IF EXISTS players_update ON public.players;
CREATE POLICY players_update ON public.players FOR UPDATE TO authenticated
    USING (group_id IN (SELECT unnest(public.current_admin_group_ids()))
           OR user_id = (SELECT auth.uid()))   -- 自分の表示名は自分で変えられる
    WITH CHECK (group_id IN (SELECT unnest(public.current_group_ids())));

-- ---- group_invites -------------------------------------------------
-- 非メンバーはコードで検索できない。参加の入口は join_group_by_code() RPC だけ。
DROP POLICY IF EXISTS invites_select ON public.group_invites;
CREATE POLICY invites_select ON public.group_invites FOR SELECT TO authenticated
    USING (group_id IN (SELECT unnest(public.current_admin_group_ids())));

DROP POLICY IF EXISTS invites_update ON public.group_invites;
CREATE POLICY invites_update ON public.group_invites FOR UPDATE TO authenticated
    USING      (group_id IN (SELECT unnest(public.current_admin_group_ids())))
    WITH CHECK (group_id IN (SELECT unnest(public.current_admin_group_ids())));

-- ---- tournaments ---------------------------------------------------
DROP POLICY IF EXISTS tournaments_select ON public.tournaments;
CREATE POLICY tournaments_select ON public.tournaments FOR SELECT TO authenticated
    USING (group_id IN (SELECT unnest(public.current_group_ids())));

DROP POLICY IF EXISTS tournaments_insert ON public.tournaments;
CREATE POLICY tournaments_insert ON public.tournaments FOR INSERT TO authenticated
    WITH CHECK (group_id IN (SELECT unnest(public.current_group_ids()))
                AND created_by = (SELECT auth.uid()));

DROP POLICY IF EXISTS tournaments_update ON public.tournaments;
CREATE POLICY tournaments_update ON public.tournaments FOR UPDATE TO authenticated
    USING (group_id IN (SELECT unnest(public.current_admin_group_ids()))
           OR created_by = (SELECT auth.uid()))
    WITH CHECK (group_id IN (SELECT unnest(public.current_group_ids())));

-- ---- 記録系: メンバーなら誰でも読み書きできる ----------------------
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['tournament_days', 'games', 'game_players',
                             'game_rounds', 'round_results']
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_select', t);
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_insert', t);
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_update', t);

        EXECUTE format($p$CREATE POLICY %I ON public.%I FOR SELECT TO authenticated
            USING (group_id IN (SELECT unnest(public.current_group_ids())))$p$,
            t || '_select', t);
        EXECUTE format($p$CREATE POLICY %I ON public.%I FOR INSERT TO authenticated
            WITH CHECK (group_id IN (SELECT unnest(public.current_group_ids())))$p$,
            t || '_insert', t);
        EXECUTE format($p$CREATE POLICY %I ON public.%I FOR UPDATE TO authenticated
            USING (group_id IN (SELECT unnest(public.current_group_ids())))
            WITH CHECK (group_id IN (SELECT unnest(public.current_group_ids())))$p$,
            t || '_update', t);
    END LOOP;
END $$;

-- ★DELETE を許すのは round_results と game_players だけ★
-- update_round_results() は SECURITY INVOKER で「全消し→入れ直し」をする。
-- DELETE ポリシーが無いと、RLS は**エラーを出さずに0行削除**して
-- そのまま INSERT に進み、主キー衝突か結果の二重計上を起こす。
DROP POLICY IF EXISTS round_results_delete ON public.round_results;
CREATE POLICY round_results_delete ON public.round_results FOR DELETE TO authenticated
    USING (group_id IN (SELECT unnest(public.current_group_ids())));

DROP POLICY IF EXISTS game_players_delete ON public.game_players;
CREATE POLICY game_players_delete ON public.game_players FOR DELETE TO authenticated
    USING (group_id IN (SELECT unnest(public.current_group_ids())));


-- ===== 8. 権限 ======================================================
-- ★暗黙の既定に頼らない★
-- Supabase は ALTER DEFAULT PRIVILEGES で新しいテーブルに自動で権限を付ける
-- 設定を入れているが、プロジェクトを初期化したりSQLで作り直したりすると
-- 外れていることがある。外れていると RLS 以前に「permission denied」で
-- 全部落ちるので、ここで明示的に付け直す。
GRANT USAGE ON SCHEMA public TO anon, authenticated;

GRANT SELECT, INSERT, UPDATE ON
    public.groups, public.group_invites, public.tournaments,
    public.tournament_days, public.players, public.games,
    public.game_players, public.game_rounds, public.round_results
    TO authenticated;

-- ===== 8b. 列単位の権限 =============================================
-- ★RLS だけでは防げないもの★
-- ポリシーは「その行が自分のグループのものか」しか言えない。
-- メンバーが自分の players 行に role='owner' を PATCH しても、
-- 行は前も後も自分のグループのものなので、どんなポリシーを書いても通ってしまう。
-- 列に対する GRANT だけがこれを止められる（RLS より先に評価される）。
REVOKE INSERT, UPDATE ON public.players FROM authenticated;
GRANT  INSERT (group_id, name)   ON public.players TO authenticated;
GRANT  UPDATE (name, deleted_at) ON public.players TO authenticated;

-- 大会を別グループへ持ち出せないようにする
REVOKE INSERT, UPDATE ON public.tournaments FROM authenticated;
GRANT  INSERT (group_id, name, ruleset, note, created_by) ON public.tournaments TO authenticated;
GRANT  UPDATE (name, ruleset, note, deleted_at)           ON public.tournaments TO authenticated;

REVOKE UPDATE ON public.groups FROM authenticated;
GRANT  UPDATE (name, description, deleted_at) ON public.groups TO authenticated;

REVOKE UPDATE ON public.group_invites FROM authenticated;
GRANT  UPDATE (revoked_at, expires_at, max_uses) ON public.group_invites TO authenticated;

-- 物理削除は round_results / game_players 以外させない
REVOKE DELETE ON public.groups, public.group_invites, public.tournaments,
                 public.tournament_days, public.games, public.game_rounds,
                 public.players FROM authenticated;
GRANT  DELETE ON public.round_results, public.game_players TO authenticated;

-- 未ログイン(anon)からは何も見えない・触れない
REVOKE ALL ON public.groups, public.group_invites, public.tournaments,
              public.tournament_days, public.players, public.games,
              public.game_players, public.game_rounds, public.round_results
       FROM anon;

NOTIFY pgrst, 'reload schema';
