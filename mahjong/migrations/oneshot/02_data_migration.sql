-- ★一度きり★ 既存データをグループ構造へ移行する。
--
-- 実行前に必ず: 00_backup.sql → 01_preflight.sql（中止条件の確認）→ 003a_groups_schema.sql
--
-- 全体を1つの DO ブロックにしている。DO は「1文」なので、SQL Editor が
-- スクリプトをトランザクションで包むかどうかに関係なく確実に原子的になる。
-- 途中で RAISE EXCEPTION すれば、ここでの変更はすべて巻き戻る。
--
-- やること:
--   1. 既定のグループを1つ作り、既存データを全部そこに入れる
--   2. 同名プレイヤーを1人に統合し、対戦履歴を張り替える（監査ログを残す）
--   3. 既存のログインユーザーをメンバーとして登録する（誰もアクセスを失わない）
--   4. 対局日時(JST)から開催日を生成する
--   5. 各半荘に、計算に使われたルールのスナップショットを埋める

DO $mig$
DECLARE
    v_group_id uuid;
    v_owner    uuid;
    v_bad      integer;
    v_merged   integer;
    r          record;
    v_base     text;
    v_name     text;
    v_i        integer;
BEGIN
    ----------------------------------------------------------------
    -- 0. 二重適用ガード（統合は冪等ではない）
    ----------------------------------------------------------------
    IF EXISTS (SELECT 1 FROM public.schema_migrations WHERE version = '003b_data') THEN
        RAISE NOTICE '003b_data は既に適用済みです。何もせず終了します。';
        RETURN;
    END IF;
    IF to_regclass('public.tournaments') IS NULL THEN
        RAISE EXCEPTION '先に 003a_groups_schema.sql を実行してください。';
    END IF;

    ----------------------------------------------------------------
    -- 1. 既定のグループ
    ----------------------------------------------------------------
    SELECT id INTO v_owner FROM auth.users
     WHERE lower(email) = 'ikkei812@gmail.com' LIMIT 1;
    IF v_owner IS NULL THEN
        SELECT id INTO v_owner FROM auth.users ORDER BY created_at LIMIT 1;
        RAISE NOTICE 'ikkei812@gmail.com が見つかりません。最古のユーザー(%)をオーナーにします。', v_owner;
    END IF;

    INSERT INTO public.groups (name, description, created_by)
    VALUES ('既定のグループ',
            '移行時に既存データをまとめて収容するため自動作成されました。名前は設定から変更できます。',
            v_owner)
    RETURNING id INTO v_group_id;

    INSERT INTO public.mig003_meta(key, value)
    VALUES ('default_group_id', v_group_id::text),
           ('owner_user_id', COALESCE(v_owner::text, '(なし)'));

    ----------------------------------------------------------------
    -- 2. group_id のバックフィル（既存データはすべて1グループ）
    --
    --    先にグループ内名前一意インデックスを外す。003c を先に流していた場合、
    --    group_id が NULL の間は（NULL 同士は別扱いなので）作成できてしまい、
    --    ここの UPDATE で初めて同名が衝突して分かりにくいエラーになる。
    --    統合が終わったら 4. で張り直す。
    ----------------------------------------------------------------
    DROP INDEX IF EXISTS public.players_group_name_uniq;

    UPDATE public.tournaments   SET group_id = v_group_id WHERE group_id IS NULL;
    UPDATE public.players       SET group_id = v_group_id WHERE group_id IS NULL;
    UPDATE public.games         SET group_id = v_group_id WHERE group_id IS NULL;
    UPDATE public.game_rounds   SET group_id = v_group_id WHERE group_id IS NULL;
    UPDATE public.game_players  SET group_id = v_group_id WHERE group_id IS NULL;
    UPDATE public.round_results SET group_id = v_group_id WHERE group_id IS NULL;

    ----------------------------------------------------------------
    -- 3. 同名プレイヤーの統合
    --    旧スキーマでは players が大会ごとに独立していたため、
    --    別の大会の「田中」は別人扱いだった。グループ通算成績を出すために1人にまとめる。
    ----------------------------------------------------------------
    INSERT INTO public.mig003_player_merge_map
        (loser_id, survivor_id, norm_name, loser_title_id, loser_name,
         loser_created_at, loser_deleted_at)
    SELECT c.id, c.survivor_id, c.norm, c.legacy_title_id, c.name,
           c.created_at, c.deleted_at
    FROM (
        SELECT p.id, btrim(p.name) AS norm, p.name, p.legacy_title_id,
               p.created_at, p.deleted_at,
               first_value(p.id) OVER (
                   PARTITION BY btrim(p.name)
                   ORDER BY (p.deleted_at IS NULL) DESC, p.created_at, p.id) AS survivor_id
        FROM public.players p
    ) c
    WHERE c.id <> c.survivor_id;

    SELECT count(*) INTO v_merged FROM public.mig003_player_merge_map;
    RAISE NOTICE '統合対象のプレイヤー行: % 件', v_merged;

    -- 3a. 中止条件の再検査。プリフライトを信用せず、実データで必ず確認する。
    SELECT count(*) INTO v_bad FROM (
        SELECT rr.round_id, COALESCE(m.survivor_id, rr.player_id) AS pid
        FROM public.round_results rr
        LEFT JOIN public.mig003_player_merge_map m ON m.loser_id = rr.player_id
        GROUP BY 1, 2 HAVING count(*) > 1
    ) s;
    IF v_bad > 0 THEN
        RAISE EXCEPTION
            '中止: 統合すると round_results の主キーが衝突する半荘が % 件あります。'
            '（同じ半荘に同名の別プレイヤーがいます）'
            'プリフライト P4 を実行し、該当プレイヤーの名前を変えてから再実行してください。', v_bad;
    END IF;

    SELECT count(*) INTO v_bad FROM (
        SELECT gp.game_id, COALESCE(m.survivor_id, gp.player_id) AS pid
        FROM public.game_players gp
        LEFT JOIN public.mig003_player_merge_map m ON m.loser_id = gp.player_id
        GROUP BY 1, 2 HAVING count(*) > 1
    ) s;
    IF v_bad > 0 THEN
        RAISE EXCEPTION
            '中止: 統合すると game_players の一意制約が衝突する対戦が % 件あります。'
            'プリフライト P5 を参照してください。', v_bad;
    END IF;

    -- 3b. 行単位の監査ログ（巻き戻しの根拠）。
    --     seat は付け替えで動かないので、(game_id,seat) / (round_id,seat) が安定した行IDになる。
    INSERT INTO public.mig003_game_players_remap (game_id, seat, old_player_id, new_player_id)
    SELECT gp.game_id, gp.seat, gp.player_id, m.survivor_id
    FROM public.game_players gp
    JOIN public.mig003_player_merge_map m ON m.loser_id = gp.player_id;

    INSERT INTO public.mig003_round_results_remap (round_id, seat, old_player_id, new_player_id)
    SELECT rr.round_id, rr.seat, rr.player_id, m.survivor_id
    FROM public.round_results rr
    JOIN public.mig003_player_merge_map m ON m.loser_id = rr.player_id;

    -- 3c. 付け替え。
    --     一意インデックスは文の途中で行ごとに検査されるが、ここは全部
    --     loser → survivor の一方向で、survivor 行自体は動かないため
    --     途中状態でも衝突しない（本物の衝突は 3a で0件と確認済み）。
    UPDATE public.game_players gp SET player_id = m.survivor_id
    FROM public.mig003_player_merge_map m WHERE gp.player_id = m.loser_id;

    UPDATE public.round_results rr SET player_id = m.survivor_id
    FROM public.mig003_player_merge_map m WHERE rr.player_id = m.loser_id;

    UPDATE public.players p
       SET deleted_at  = COALESCE(p.deleted_at, now()),
           merged_into = m.survivor_id
    FROM public.mig003_player_merge_map m WHERE p.id = m.loser_id;

    ----------------------------------------------------------------
    -- 4. 一意インデックスを大会単位からグループ単位へ張り替える
    ----------------------------------------------------------------
    CREATE UNIQUE INDEX IF NOT EXISTS players_group_name_uniq
        ON public.players (group_id, name) WHERE deleted_at IS NULL;
    CREATE UNIQUE INDEX IF NOT EXISTS players_group_user_uniq
        ON public.players (group_id, user_id)
        WHERE user_id IS NOT NULL AND deleted_at IS NULL;

    DROP INDEX IF EXISTS public.players_title_name_uniq;
    DROP INDEX IF EXISTS public.players_title_idx;

    CREATE INDEX IF NOT EXISTS players_group_idx
        ON public.players (group_id) WHERE deleted_at IS NULL;
    CREATE INDEX IF NOT EXISTS players_user_idx
        ON public.players (user_id) WHERE user_id IS NOT NULL;

    ----------------------------------------------------------------
    -- 5. 既存のログインユーザーをメンバーとして登録する
    --    統合の「あと」に実行する（名前の重複判定を最終状態に対して行うため）。
    --    is_provisional = true にしておき、アプリ側の「本人紐付け」で
    --    既存の名前（例:「田中」）に付け替えられるようにする。
    ----------------------------------------------------------------
    FOR r IN SELECT u.id, u.email FROM auth.users u
             WHERE u.email IS NOT NULL ORDER BY u.created_at
    LOOP
        v_base := COALESCE(NULLIF(btrim(split_part(r.email, '@', 1)), ''), 'user');
        v_name := v_base;
        v_i := 1;
        WHILE EXISTS (SELECT 1 FROM public.players
                      WHERE group_id = v_group_id AND name = v_name AND deleted_at IS NULL)
        LOOP
            v_i := v_i + 1;
            v_name := v_base || ' (' || v_i || ')';
            IF v_i > 50 THEN
                RAISE EXCEPTION '名前の重複を解消できませんでした: %', v_base;
            END IF;
        END LOOP;

        INSERT INTO public.players (group_id, name, user_id, role, is_provisional)
        VALUES (v_group_id, v_name, r.id,
                CASE WHEN r.id = v_owner THEN 'owner' ELSE 'member' END,
                true);
    END LOOP;

    -- グループには必ず1人 owner が要る（誰もいないと管理不能になる）
    IF NOT EXISTS (SELECT 1 FROM public.players
                   WHERE group_id = v_group_id AND role = 'owner' AND deleted_at IS NULL) THEN
        UPDATE public.players SET role = 'owner'
        WHERE id = (SELECT p.id FROM public.players p
                    WHERE p.group_id = v_group_id AND p.user_id IS NOT NULL
                      AND p.deleted_at IS NULL
                    ORDER BY p.created_at LIMIT 1);
    END IF;

    -- ゲスト（アカウント無し）は必ず member
    UPDATE public.players SET role = 'member'
    WHERE user_id IS NULL AND role <> 'member';

    ----------------------------------------------------------------
    -- 6. 開催日を対局日時(JST)から生成する
    --    論理削除済みの対戦も対象に含める（games.day_id を NOT NULL にするため）。
    ----------------------------------------------------------------
    INSERT INTO public.tournament_days (tournament_id, group_id, held_on, note)
    SELECT DISTINCT g.tournament_id, v_group_id,
                    (g.created_at AT TIME ZONE 'Asia/Tokyo')::date,
                    '移行時に対局日時から自動生成'
    FROM public.games g
    ON CONFLICT (tournament_id, held_on) WHERE deleted_at IS NULL DO NOTHING;

    UPDATE public.games g SET day_id = d.id
    FROM public.tournament_days d
    WHERE d.tournament_id = g.tournament_id
      AND d.held_on = (g.created_at AT TIME ZONE 'Asia/Tokyo')::date
      AND g.day_id IS NULL;

    IF EXISTS (SELECT 1 FROM public.games WHERE day_id IS NULL) THEN
        RAISE EXCEPTION '内部エラー: 開催日を割り当てられない対戦が残っています。';
    END IF;

    RAISE NOTICE '生成した開催日: % 件', (SELECT count(*) FROM public.tournament_days);

    ----------------------------------------------------------------
    -- 7. 各半荘に、そのとき使われたルールのスナップショットを入れる
    --    以後どのルールで計算された点なのかが追跡できる。
    ----------------------------------------------------------------
    UPDATE public.game_rounds r SET ruleset = COALESCE(t.ruleset, '{}'::jsonb)
    FROM public.games g
    JOIN public.tournaments t ON t.id = g.tournament_id
    WHERE g.id = r.game_id AND r.ruleset IS NULL;

    ----------------------------------------------------------------
    -- 8. 制約を締める（NULL を埋め終えてから）
    ----------------------------------------------------------------
    ALTER TABLE public.tournaments   ALTER COLUMN group_id      SET NOT NULL;
    ALTER TABLE public.players       ALTER COLUMN group_id      SET NOT NULL;
    ALTER TABLE public.games         ALTER COLUMN group_id      SET NOT NULL;
    ALTER TABLE public.games         ALTER COLUMN day_id        SET NOT NULL;
    ALTER TABLE public.games         ALTER COLUMN tournament_id SET NOT NULL;
    ALTER TABLE public.game_rounds   ALTER COLUMN group_id      SET NOT NULL;
    ALTER TABLE public.game_rounds   ALTER COLUMN ruleset       SET NOT NULL;
    ALTER TABLE public.game_players  ALTER COLUMN group_id      SET NOT NULL;
    ALTER TABLE public.round_results ALTER COLUMN group_id      SET NOT NULL;

    ALTER TABLE public.players
        ADD CONSTRAINT players_role_chk  CHECK (role IN ('owner', 'admin', 'member')),
        ADD CONSTRAINT players_guest_chk CHECK (user_id IS NOT NULL OR role = 'member');

    ----------------------------------------------------------------
    INSERT INTO public.schema_migrations(version, note)
    VALUES ('003b_data',
            format('group=%s merged=%s days=%s',
                   v_group_id, v_merged,
                   (SELECT count(*) FROM public.tournament_days)));

    RAISE NOTICE '移行が完了しました。既定グループ = %', v_group_id;
END
$mig$;

NOTIFY pgrst, 'reload schema';

-- 移行結果の要約
SELECT * FROM public.mig003_meta;
SELECT version, applied_at, note FROM public.schema_migrations ORDER BY version;
