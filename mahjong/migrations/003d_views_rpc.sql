-- ビュー / RPC 関数（冪等）
--
-- PostgREST 経由なので:
--   * 複数テーブルにまたがる読み取りはビューにまとめる
--   * まとめて成功／失敗させたい書き込みは関数にする（関数呼び出し＝1トランザクション）
--
-- ビューは必ず security_invoker = true にする。既定の false だと
-- ビューが所有者(postgres)権限で走り、下のテーブルの RLS を素通りして
-- 全グループのデータが丸見えになる。

-- ===== ビュー =======================================================

DROP VIEW IF EXISTS public.v_round_entries;
CREATE VIEW public.v_round_entries WITH (security_invoker = true) AS
SELECT
    g.group_id,
    g.tournament_id,
    t.name        AS tournament_name,
    g.day_id,
    d.held_on,
    d.label       AS day_label,
    r.game_id,
    g.name        AS game_name,
    r.id          AS round_id,
    r.created_at  AS round_created_at,
    r.ruleset     AS round_ruleset,
    rr.player_id,
    p.name        AS player_name,
    p.user_id,
    rr.seat, rr.raw_score, rr.point, rr.rank, rr.kaze, rr.tobi,
    -- その半荘の人数。3人卓と4人卓が混ざる大会でもラス率を取り違えないために持たせる。
    count(*) OVER (PARTITION BY rr.round_id)::int AS table_size
FROM public.round_results rr
JOIN public.game_rounds     r ON r.id = rr.round_id AND r.deleted_at IS NULL
JOIN public.games           g ON g.id = r.game_id   AND g.deleted_at IS NULL
JOIN public.tournament_days d ON d.id = g.day_id
JOIN public.tournaments     t ON t.id = g.tournament_id
JOIN public.players         p ON p.id = rr.player_id;
-- players は deleted_at で絞らない。削除したプレイヤーの記録も残す
-- （集計から消すと合計がゼロサムでなくなる）。

DROP VIEW IF EXISTS public.v_game_seats;
CREATE VIEW public.v_game_seats WITH (security_invoker = true) AS
SELECT
    g.id AS game_id, g.group_id, g.tournament_id, g.day_id, d.held_on,
    g.name AS game_name, g.created_at AS game_created_at,
    gp.seat, p.id AS player_id, p.name AS player_name, p.user_id,
    COALESCE(agg.total_point, 0) AS total_point,
    COALESCE(cnt.round_count, 0) AS round_count
FROM public.games g
JOIN public.tournament_days d ON d.id = g.day_id
JOIN public.game_players   gp ON gp.game_id = g.id
JOIN public.players         p ON p.id = gp.player_id
LEFT JOIN (
    SELECT r.game_id, rr.player_id, SUM(rr.point) AS total_point
    FROM public.round_results rr
    JOIN public.game_rounds r ON r.id = rr.round_id AND r.deleted_at IS NULL
    GROUP BY r.game_id, rr.player_id
) agg ON agg.game_id = g.id AND agg.player_id = gp.player_id
LEFT JOIN (
    SELECT game_id, COUNT(*) AS round_count
    FROM public.game_rounds WHERE deleted_at IS NULL GROUP BY game_id
) cnt ON cnt.game_id = g.id
WHERE g.deleted_at IS NULL;

-- 自分が所属するグループと、そこでの自分の立場。
-- ログイン直後に「どのグループを開くか」「本人紐付けが必要か」を判断するのに使う。
DROP VIEW IF EXISTS public.v_my_groups;
CREATE VIEW public.v_my_groups WITH (security_invoker = true) AS
SELECT gr.id AS group_id, gr.name, gr.description, gr.created_at,
       p.id AS my_player_id, p.name AS my_player_name,
       p.role, p.is_provisional,
       (SELECT count(*) FROM public.players m
        WHERE m.group_id = gr.id AND m.deleted_at IS NULL) AS member_count
FROM public.groups gr
JOIN public.players p ON p.group_id = gr.id
                     AND p.user_id = (SELECT auth.uid())
                     AND p.deleted_at IS NULL
WHERE gr.deleted_at IS NULL;

GRANT SELECT ON public.v_round_entries, public.v_game_seats, public.v_my_groups
    TO authenticated;


-- ===== 席の整合性チェック ===========================================
-- 旧 update_round_results は検証なしの DELETE + INSERT だったため、
-- その卓に座っていないプレイヤーの結果を書き込めてしまっていた。
CREATE OR REPLACE FUNCTION public.assert_results_match_seats(p_game_id uuid, p_results jsonb)
RETURNS void LANGUAGE plpgsql STABLE SET search_path = '' AS $$
DECLARE v_bad int; v_given int; v_seated int;
BEGIN
    SELECT count(*) INTO v_bad
    FROM jsonb_array_elements(p_results) e
    WHERE NOT EXISTS (
        SELECT 1 FROM public.game_players gp
        WHERE gp.game_id   = p_game_id
          AND gp.player_id = (e->>'player_id')::uuid
          AND gp.seat      = (e->>'seat')::smallint);
    IF v_bad > 0 THEN
        RAISE EXCEPTION 'この対戦に座っていないプレイヤー、または席番号の食い違いが % 件あります。', v_bad;
    END IF;

    v_given := jsonb_array_length(p_results);
    SELECT count(*) INTO v_seated FROM public.game_players WHERE game_id = p_game_id;
    IF v_given <> v_seated THEN
        RAISE EXCEPTION '結果が%人分ですが、この対戦には%人が座っています。', v_given, v_seated;
    END IF;
END $$;
GRANT EXECUTE ON FUNCTION public.assert_results_match_seats(uuid, jsonb) TO authenticated;


-- ===== 記録系 RPC ===================================================
-- SECURITY INVOKER のままにする。RLS を唯一の権限判定にしておくと、
-- 監査すべき SECURITY DEFINER 関数を最小限に保てる。

-- 引数名が変わる（p_title_id → p_day_id）ので CREATE OR REPLACE では置き換えられない。
DROP FUNCTION IF EXISTS public.create_game_with_players(uuid, text, jsonb);

CREATE OR REPLACE FUNCTION public.create_game_with_players(
    p_day_id uuid, p_name text, p_seats jsonb
) RETURNS uuid LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
DECLARE
    v_day       record;
    v_game_id   uuid;
    v_seat      int := 0;
    v_item      jsonb;
    v_player_id uuid;
    v_new_name  text;
    v_resolved  uuid[] := '{}';
BEGIN
    SELECT d.id, d.tournament_id, d.group_id INTO v_day
    FROM public.tournament_days d
    WHERE d.id = p_day_id AND d.deleted_at IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION '開催日が見つかりません。';
    END IF;

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
            -- 同名が既にいれば使い回す（一意制約との衝突を避ける）
            SELECT id INTO v_player_id FROM public.players
            WHERE group_id = v_day.group_id AND name = v_new_name AND deleted_at IS NULL;
            IF v_player_id IS NULL THEN
                -- 列単位の GRANT に合わせ、group_id と name だけを指定する
                INSERT INTO public.players (group_id, name)
                VALUES (v_day.group_id, v_new_name) RETURNING id INTO v_player_id;
            END IF;
        ELSE
            PERFORM 1 FROM public.players
            WHERE id = v_player_id AND group_id = v_day.group_id AND deleted_at IS NULL;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'このグループに属さないプレイヤーが指定されました。';
            END IF;
        END IF;

        IF v_player_id = ANY(v_resolved) THEN
            RAISE EXCEPTION '同じプレイヤーが重複して選択されています。';
        END IF;
        v_resolved := array_append(v_resolved, v_player_id);
    END LOOP;

    INSERT INTO public.games (tournament_id, day_id, group_id, name)
    VALUES (v_day.tournament_id, v_day.id, v_day.group_id,
            COALESCE(NULLIF(btrim(p_name), ''), '無題の対戦'))
    RETURNING id INTO v_game_id;

    FOREACH v_player_id IN ARRAY v_resolved LOOP
        INSERT INTO public.game_players (game_id, group_id, seat, player_id)
        VALUES (v_game_id, v_day.group_id, v_seat, v_player_id);
        v_seat := v_seat + 1;
    END LOOP;

    RETURN v_game_id;
END $$;


CREATE OR REPLACE FUNCTION public.add_round_with_results(
    p_game_id uuid, p_results jsonb
) RETURNS uuid LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
DECLARE v_round_id uuid; v_group_id uuid; v_ruleset jsonb;
BEGIN
    SELECT g.group_id, COALESCE(t.ruleset, '{}'::jsonb) INTO v_group_id, v_ruleset
    FROM public.games g JOIN public.tournaments t ON t.id = g.tournament_id
    WHERE g.id = p_game_id AND g.deleted_at IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION '対戦が見つかりません。';
    END IF;

    PERFORM public.assert_results_match_seats(p_game_id, p_results);

    -- 適用したルールをここで固定する。あとで大会のルールを変えても、
    -- この半荘の点がどのルールで出たものかを追跡できる。
    INSERT INTO public.game_rounds (game_id, group_id, ruleset)
    VALUES (p_game_id, v_group_id, v_ruleset) RETURNING id INTO v_round_id;

    INSERT INTO public.round_results
        (round_id, group_id, player_id, seat, raw_score, point, rank, kaze, tobi)
    SELECT v_round_id, v_group_id,
           (e->>'player_id')::uuid, (e->>'seat')::smallint,
           (e->>'raw_score')::int, (e->>'point')::int, (e->>'rank')::smallint,
           e->>'kaze', COALESCE((e->>'tobi')::boolean, false)
    FROM jsonb_array_elements(p_results) AS e;

    RETURN v_round_id;
END $$;


CREATE OR REPLACE FUNCTION public.update_round_results(
    p_round_id uuid, p_results jsonb
) RETURNS void LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
DECLARE v_game_id uuid; v_group_id uuid; v_deleted int;
BEGIN
    SELECT gr.game_id, gr.group_id INTO v_game_id, v_group_id
    FROM public.game_rounds gr WHERE gr.id = p_round_id AND gr.deleted_at IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION '半荘が見つかりません。';
    END IF;

    PERFORM public.assert_results_match_seats(v_game_id, p_results);

    DELETE FROM public.round_results WHERE round_id = p_round_id;
    GET DIAGNOSTICS v_deleted = ROW_COUNT;
    -- DELETE は該当ポリシーが無いと「エラーを出さずに0行削除」になる。
    -- そのまま INSERT に進むと結果が二重になるので、必ず確認する。
    IF v_deleted = 0 THEN
        RAISE EXCEPTION '既存の結果を削除できませんでした（権限を確認してください）。';
    END IF;

    INSERT INTO public.round_results
        (round_id, group_id, player_id, seat, raw_score, point, rank, kaze, tobi)
    SELECT p_round_id, v_group_id,
           (e->>'player_id')::uuid, (e->>'seat')::smallint,
           (e->>'raw_score')::int, (e->>'point')::int, (e->>'rank')::smallint,
           e->>'kaze', COALESCE((e->>'tobi')::boolean, false)
    FROM jsonb_array_elements(p_results) AS e;
END $$;


-- 大会まるごとの再計算を1トランザクションで適用する。
-- 点数計算そのものは Python（mahjong/scoring.py）に一本化したまま、
-- 「N回の往復＋途中で止まると新旧ルールが混在」という問題だけを解消する。
CREATE OR REPLACE FUNCTION public.apply_recalculated_rounds(
    p_tournament_id uuid, p_ruleset jsonb, p_rounds jsonb
) RETURNS integer LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
DECLARE
    v_applied int := 0; v_live int; e jsonb;
    v_round_id uuid; v_game_id uuid; v_group_id uuid;
BEGIN
    -- 大会行をロックして同時再計算を防ぐ。RLS で見えなければ0行＝権限なし。
    PERFORM 1 FROM public.tournaments
    WHERE id = p_tournament_id AND deleted_at IS NULL FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION '大会が見つからないか、権限がありません。';
    END IF;

    -- 送られてきた半荘の集合が、いまDBにある生きた半荘と一致するか確認する。
    -- 一致しなければ、画面が読み込んだ後に誰かが半荘を足した／消した＝古い前提。
    SELECT count(*) INTO v_live
    FROM public.game_rounds gr JOIN public.games g ON g.id = gr.game_id
    WHERE g.tournament_id = p_tournament_id
      AND gr.deleted_at IS NULL AND g.deleted_at IS NULL;
    IF v_live <> jsonb_array_length(p_rounds) THEN
        RAISE EXCEPTION
            '再計算の対象が変化しました（DB:%件 / 送信:%件）。画面を再読み込みしてやり直してください。',
            v_live, jsonb_array_length(p_rounds);
    END IF;

    FOR e IN SELECT * FROM jsonb_array_elements(p_rounds) LOOP
        v_round_id := (e->>'round_id')::uuid;

        SELECT gr.game_id, gr.group_id INTO v_game_id, v_group_id
        FROM public.game_rounds gr JOIN public.games g ON g.id = gr.game_id
        WHERE gr.id = v_round_id AND gr.deleted_at IS NULL
          AND g.tournament_id = p_tournament_id AND g.deleted_at IS NULL;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'この大会に属さない半荘が含まれています: %', v_round_id;
        END IF;

        PERFORM public.assert_results_match_seats(v_game_id, e->'results');

        -- UNIQUE(round_id, rank) は文の途中でも行ごとに検査されるため、
        -- 順位の入れ替えを UPDATE でやると偽の衝突が起きる。必ず消してから入れ直す。
        DELETE FROM public.round_results WHERE round_id = v_round_id;

        INSERT INTO public.round_results
            (round_id, group_id, player_id, seat, raw_score, point, rank, kaze, tobi)
        SELECT v_round_id, v_group_id,
               (x->>'player_id')::uuid, (x->>'seat')::smallint,
               (x->>'raw_score')::int, (x->>'point')::int, (x->>'rank')::smallint,
               x->>'kaze', COALESCE((x->>'tobi')::boolean, false)
        FROM jsonb_array_elements(e->'results') x;

        UPDATE public.game_rounds SET ruleset = p_ruleset WHERE id = v_round_id;
        v_applied := v_applied + 1;
    END LOOP;

    UPDATE public.tournaments SET ruleset = p_ruleset WHERE id = p_tournament_id;
    RETURN v_applied;
END $$;

GRANT EXECUTE ON FUNCTION
    public.create_game_with_players(uuid, text, jsonb),
    public.add_round_with_results(uuid, jsonb),
    public.update_round_results(uuid, jsonb),
    public.apply_recalculated_rounds(uuid, jsonb, jsonb)
    TO authenticated;


-- ===== ブートストラップ用 RPC =======================================
-- どのグループにも属さないユーザーは current_group_ids() が空なので、
-- どのテーブルも見えず、どの INSERT ポリシーも通らない。
-- 「最初の1歩」だけは SECURITY DEFINER で用意する必要がある。
-- SECURITY DEFINER でも auth.uid() は呼び出し元のままなので本人確認はできる。

CREATE OR REPLACE FUNCTION public.gen_invite_code()
RETURNS text LANGUAGE sql VOLATILE SET search_path = '' AS $$
    -- UUIDv4 の hex は16記号が一様なので、16文字への写像に偏りが出ない。
    -- 読み違えやすい 0/1 だけ Y/Z に置換する。8文字 = 16^8 ≒ 43億通り。
    SELECT translate(
        upper(substr(replace(pg_catalog.gen_random_uuid()::text, '-', ''), 1, 8)),
        '01', 'YZ');
$$;

CREATE OR REPLACE FUNCTION public.create_group(p_name text, p_display_name text DEFAULT NULL)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
    v_uid uuid := auth.uid();
    v_group_id uuid;
    v_name text := btrim(COALESCE(p_name, ''));
    v_disp text := btrim(COALESCE(p_display_name, ''));
BEGIN
    IF v_uid IS NULL THEN
        RAISE EXCEPTION 'ログインが必要です。' USING ERRCODE = '42501';
    END IF;
    IF v_name = '' THEN
        RAISE EXCEPTION 'グループ名を入力してください。';
    END IF;
    IF length(v_name) > 60 THEN
        RAISE EXCEPTION 'グループ名は60文字以内にしてください。';
    END IF;
    IF (SELECT count(*) FROM public.players
        WHERE user_id = v_uid AND role = 'owner' AND deleted_at IS NULL) >= 20 THEN
        RAISE EXCEPTION '作成できるグループ数の上限に達しました。';
    END IF;

    INSERT INTO public.groups (name, created_by) VALUES (v_name, v_uid)
    RETURNING id INTO v_group_id;

    IF v_disp = '' THEN
        SELECT COALESCE(NULLIF(btrim(split_part(u.email, '@', 1)), ''), 'オーナー')
        INTO v_disp FROM auth.users u WHERE u.id = v_uid;
    END IF;

    INSERT INTO public.players (group_id, name, user_id, role, is_provisional)
    VALUES (v_group_id, v_disp, v_uid, 'owner', false);

    RETURN v_group_id;
END $$;


CREATE OR REPLACE FUNCTION public.create_invite(
    p_group_id uuid,
    p_expires_at timestamptz DEFAULT (now() + interval '7 days'),
    p_max_uses integer DEFAULT 20
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE v_uid uuid := auth.uid(); v_code text;
BEGIN
    IF v_uid IS NULL THEN
        RAISE EXCEPTION 'ログインが必要です。' USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.players
                   WHERE group_id = p_group_id AND user_id = v_uid
                     AND deleted_at IS NULL AND role IN ('owner', 'admin')) THEN
        RAISE EXCEPTION '招待コードを作れるのは管理者だけです。';
    END IF;

    FOR i IN 1..5 LOOP
        v_code := public.gen_invite_code();
        BEGIN
            INSERT INTO public.group_invites (group_id, code, created_by, expires_at, max_uses)
            VALUES (p_group_id, v_code, v_uid, p_expires_at, p_max_uses);
            RETURN v_code;
        EXCEPTION WHEN unique_violation THEN
            NULL;  -- まれな衝突。引き直す。
        END;
    END LOOP;
    RAISE EXCEPTION '招待コードを生成できませんでした。もう一度お試しください。';
END $$;


-- 参加前の下見。グループ名と「自分はこの人です」と選べる候補を返す。
-- 有効なコードを持つ人にだけ名前一覧が見えるので、期限と使用上限で露出を絞る。
CREATE OR REPLACE FUNCTION public.preview_invite(p_code text)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
DECLARE v_uid uuid := auth.uid(); v_inv record;
BEGIN
    IF v_uid IS NULL THEN
        RAISE EXCEPTION 'ログインが必要です。' USING ERRCODE = '42501';
    END IF;

    SELECT i.*, g.name AS group_name INTO v_inv
    FROM public.group_invites i JOIN public.groups g ON g.id = i.group_id
    WHERE i.code = upper(btrim(p_code)) AND g.deleted_at IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION '招待コードが見つかりません。';
    END IF;
    IF v_inv.revoked_at IS NOT NULL THEN
        RAISE EXCEPTION 'この招待コードは無効化されています。';
    END IF;
    IF v_inv.expires_at IS NOT NULL AND v_inv.expires_at < now() THEN
        RAISE EXCEPTION 'この招待コードは期限切れです。';
    END IF;
    IF v_inv.max_uses IS NOT NULL AND v_inv.used_count >= v_inv.max_uses THEN
        RAISE EXCEPTION 'この招待コードは使用上限に達しています。';
    END IF;

    RETURN jsonb_build_object(
        'group_id', v_inv.group_id,
        'group_name', v_inv.group_name,
        'already_member', EXISTS (
            SELECT 1 FROM public.players p
            WHERE p.group_id = v_inv.group_id AND p.user_id = v_uid AND p.deleted_at IS NULL),
        'claimable', COALESCE((
            SELECT jsonb_agg(jsonb_build_object('id', p.id, 'name', p.name) ORDER BY p.name)
            FROM public.players p
            WHERE p.group_id = v_inv.group_id AND p.user_id IS NULL
              AND p.deleted_at IS NULL AND p.is_provisional = false), '[]'::jsonb));
END $$;


CREATE OR REPLACE FUNCTION public.join_group_by_code(
    p_code text, p_claim_player_id uuid DEFAULT NULL, p_new_name text DEFAULT NULL
) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE v_uid uuid := auth.uid(); v_inv record; v_pid uuid; v_name text;
BEGIN
    IF v_uid IS NULL THEN
        RAISE EXCEPTION 'ログインが必要です。' USING ERRCODE = '42501';
    END IF;

    -- 上限の確認と加算を1文で行う。分けると同時実行で上限を超えられる。
    UPDATE public.group_invites i SET used_count = i.used_count + 1
    WHERE i.code = upper(btrim(p_code))
      AND i.revoked_at IS NULL
      AND (i.expires_at IS NULL OR i.expires_at > now())
      AND (i.max_uses IS NULL OR i.used_count < i.max_uses)
    RETURNING i.id, i.group_id INTO v_inv;
    IF NOT FOUND THEN
        RAISE EXCEPTION '招待コードが無効です。';
    END IF;

    SELECT p.id INTO v_pid FROM public.players p
    WHERE p.group_id = v_inv.group_id AND p.user_id = v_uid AND p.deleted_at IS NULL;
    IF FOUND THEN
        UPDATE public.group_invites SET used_count = used_count - 1 WHERE id = v_inv.id;
        RETURN v_inv.group_id;   -- 既に参加済み。冪等に返す。
    END IF;

    IF p_claim_player_id IS NOT NULL THEN
        -- 「まだ誰のものでもない」の確認と確保を1文で行う。
        -- 同時実行で先を越された側は user_id IS NULL を満たさず0行になる。
        UPDATE public.players p
           SET user_id = v_uid, role = 'member', is_provisional = false
        WHERE p.id = p_claim_player_id
          AND p.group_id = v_inv.group_id
          AND p.user_id IS NULL
          AND p.deleted_at IS NULL
        RETURNING p.id INTO v_pid;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'この参加者は既に他のアカウントと紐付いているか、選べません。';
        END IF;
    ELSE
        v_name := btrim(COALESCE(p_new_name, ''));
        IF v_name = '' THEN
            SELECT COALESCE(NULLIF(btrim(split_part(u.email, '@', 1)), ''), 'メンバー')
            INTO v_name FROM auth.users u WHERE u.id = v_uid;
        END IF;
        IF EXISTS (SELECT 1 FROM public.players p
                   WHERE p.group_id = v_inv.group_id AND p.name = v_name
                     AND p.deleted_at IS NULL) THEN
            RAISE EXCEPTION '「%」はこのグループで既に使われています。別の名前にしてください。', v_name;
        END IF;
        INSERT INTO public.players (group_id, name, user_id, role, is_provisional)
        VALUES (v_inv.group_id, v_name, v_uid, 'member', false);
    END IF;

    RETURN v_inv.group_id;
END $$;


-- 本人紐付け。移行時に作られた暫定メンバー行を、既存の名前（例:「田中」）に付け替える。
CREATE OR REPLACE FUNCTION public.link_me_to_player(p_target_player_id uuid)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE v_uid uuid := auth.uid(); v_me record; v_target record; v_role text;
BEGIN
    IF v_uid IS NULL THEN
        RAISE EXCEPTION 'ログインが必要です。' USING ERRCODE = '42501';
    END IF;

    SELECT * INTO v_target FROM public.players
    WHERE id = p_target_player_id AND deleted_at IS NULL FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION '対象の参加者が見つかりません。';
    END IF;
    IF v_target.user_id IS NOT NULL THEN
        IF v_target.user_id = v_uid THEN
            RETURN v_target.id;   -- 既に自分。冪等。
        END IF;
        RAISE EXCEPTION 'この参加者は既に他のアカウントと紐付いています。';
    END IF;

    SELECT * INTO v_me FROM public.players
    WHERE group_id = v_target.group_id AND user_id = v_uid AND deleted_at IS NULL FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'このグループのメンバーではありません。';
    END IF;
    IF v_me.id = v_target.id THEN
        RETURN v_target.id;
    END IF;

    -- 今の自分の行に対戦記録があるなら、これは「紐付け」ではなく「統合」になる。
    -- 履歴の付け替えは主キー衝突があり得るので自動ではやらない。
    IF EXISTS (SELECT 1 FROM public.game_players  WHERE player_id = v_me.id)
       OR EXISTS (SELECT 1 FROM public.round_results WHERE player_id = v_me.id) THEN
        RAISE EXCEPTION '今の参加者行に対戦記録があるため自動で統合できません。管理者に依頼してください。';
    END IF;

    v_role := CASE WHEN 'owner' IN (v_me.role, v_target.role) THEN 'owner'
                   WHEN 'admin' IN (v_me.role, v_target.role) THEN 'admin'
                   ELSE 'member' END;

    -- ★順序が重要★ players_group_user_uniq は
    --   (group_id, user_id) WHERE user_id IS NOT NULL AND deleted_at IS NULL。
    -- 先に対象へ user_id を入れると、まだ生きている自分の行と衝突する。
    -- 部分一意インデックスは DEFERRABLE にできないので、先に古い行を畳む。
    UPDATE public.players
       SET deleted_at = now(), user_id = NULL, merged_into = v_target.id
    WHERE id = v_me.id;

    UPDATE public.players
       SET user_id = v_uid, role = v_role, is_provisional = false
    WHERE id = v_target.id;

    RETURN v_target.id;
END $$;


CREATE OR REPLACE FUNCTION public.set_member_role(p_player_id uuid, p_role text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE v_uid uuid := auth.uid(); v_t record;
BEGIN
    IF v_uid IS NULL THEN
        RAISE EXCEPTION 'ログインが必要です。' USING ERRCODE = '42501';
    END IF;
    IF p_role NOT IN ('owner', 'admin', 'member') THEN
        RAISE EXCEPTION '不正な役割です。';
    END IF;
    SELECT * INTO v_t FROM public.players
    WHERE id = p_player_id AND deleted_at IS NULL FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'メンバーが見つかりません。';
    END IF;
    IF v_t.user_id IS NULL THEN
        RAISE EXCEPTION 'アカウントを持たないゲストには役割を設定できません。';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.players
                   WHERE group_id = v_t.group_id AND user_id = v_uid
                     AND deleted_at IS NULL AND role = 'owner') THEN
        RAISE EXCEPTION '役割を変更できるのはオーナーだけです。';
    END IF;
    IF v_t.role = 'owner' AND p_role <> 'owner'
       AND (SELECT count(*) FROM public.players
            WHERE group_id = v_t.group_id AND role = 'owner' AND deleted_at IS NULL) <= 1 THEN
        RAISE EXCEPTION 'グループには最低1人のオーナーが必要です。';
    END IF;
    UPDATE public.players SET role = p_role WHERE id = p_player_id;
END $$;


-- 脱退。行は消さず user_id を外してゲストに戻す。
-- round_results が ON DELETE RESTRICT で掴んでいるので行自体は消せないし、
-- 消すと過去の成績まで失われる。
CREATE OR REPLACE FUNCTION public.remove_member(p_player_id uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE v_uid uuid := auth.uid(); v_t record;
BEGIN
    IF v_uid IS NULL THEN
        RAISE EXCEPTION 'ログインが必要です。' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_t FROM public.players
    WHERE id = p_player_id AND deleted_at IS NULL FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'メンバーが見つかりません。';
    END IF;
    -- 「自分自身の脱退」か「管理者による除名」のどちらかであること。
    -- v_t.user_id = v_uid と書くと、ゲスト(user_id IS NULL)を未ログイン(v_uid IS NULL)が
    -- 外せてしまう（NULL 同士は IS DISTINCT FROM が偽になるため）。
    -- 上の NULL チェックで防いでいるが、条件自体も NULL に依存しない形にしておく。
    IF NOT (v_t.user_id IS NOT NULL AND v_t.user_id = v_uid)
       AND NOT EXISTS (SELECT 1 FROM public.players
                       WHERE group_id = v_t.group_id AND user_id = v_uid
                         AND deleted_at IS NULL AND role IN ('owner', 'admin')) THEN
        RAISE EXCEPTION '他のメンバーを外せるのは管理者だけです。';
    END IF;
    IF v_t.role = 'owner'
       AND (SELECT count(*) FROM public.players
            WHERE group_id = v_t.group_id AND role = 'owner' AND deleted_at IS NULL) <= 1 THEN
        RAISE EXCEPTION '最後のオーナーは脱退できません。先に他の人をオーナーにしてください。';
    END IF;
    UPDATE public.players SET user_id = NULL, role = 'member', is_provisional = false
    WHERE id = p_player_id;   -- 成績はこの行に残り続ける
END $$;


REVOKE ALL ON FUNCTION
    public.gen_invite_code(),
    public.create_group(text, text),
    public.create_invite(uuid, timestamptz, integer),
    public.preview_invite(text),
    public.join_group_by_code(text, uuid, text),
    public.link_me_to_player(uuid),
    public.set_member_role(uuid, text),
    public.remove_member(uuid)
    FROM public;

GRANT EXECUTE ON FUNCTION
    public.create_group(text, text),
    public.create_invite(uuid, timestamptz, integer),
    public.preview_invite(text),
    public.join_group_by_code(text, uuid, text),
    public.link_me_to_player(uuid),
    public.set_member_role(uuid, text),
    public.remove_member(uuid)
    TO authenticated;


-- ===== 未ログイン(anon)からは1つも関数を呼べないようにする ==========
-- ★REVOKE ... FROM public では足りない★
-- Supabase は ALTER DEFAULT PRIVILEGES で新しい関数の EXECUTE を
-- anon にも明示的に付与する。PUBLIC 疑似ロールからの REVOKE では
-- この「anon への明示的な付与」は消えないため、未ログインでも RPC を
-- 呼べてしまう。ロールを名指しで剥がす必要がある。
--
-- 関数を足すたびに書き足すのを忘れないよう、public スキーマの関数を
-- 全部まとめて剥がしてから、必要なものだけ上で authenticated に渡している。
DO $$
DECLARE f record;
BEGIN
    FOR f IN
        SELECT p.oid::regprocedure AS signature
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
    LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM anon', f.signature);
    END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';
