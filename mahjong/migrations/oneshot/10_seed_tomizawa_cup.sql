-- 【一度きり】「2026年下期富沢家麻雀杯」に開催日と卓の面子を登録する。
--
-- 卓1: みなみ(東) / りいち(南) / いっけい(西) / じゅんや(北)
-- 卓2: あいき(東) / いっけい(南) / じゅんや(西) / りいち(北)
--
-- 半荘の結果は入れない（元の画面は入力前で、全員が配給原点のままだったため）。
-- スコアはアプリから入力してください。
--
-- 参加者は create_game_with_players が「同名がいれば使い回し、いなければ作る」ので、
-- 事前に登録しておく必要はない。5人ぶんが自動で作られる。
--
-- ★開催日を変えたい場合は下の v_held_on を書き換えてください★

DO $seed$
DECLARE
    v_held_on date := DATE '2026-08-17';
    v_name    text := '2026年下期富沢家麻雀杯';
    v_group   uuid;
    v_tour    uuid;
    v_day     uuid;
BEGIN
    SELECT id, group_id INTO v_tour, v_group
    FROM public.tournaments
    WHERE name = v_name AND deleted_at IS NULL
    LIMIT 1;

    IF v_tour IS NULL THEN
        RAISE EXCEPTION
            '大会「%」が見つかりません。存在する大会: %',
            v_name,
            COALESCE((SELECT string_agg(name, ' / ') FROM public.tournaments
                      WHERE deleted_at IS NULL), '(1件もありません)');
    END IF;

    -- 開催日（同じ日が既にあれば使い回す）
    INSERT INTO public.tournament_days (tournament_id, group_id, held_on)
    VALUES (v_tour, v_group, v_held_on)
    ON CONFLICT (tournament_id, held_on) WHERE deleted_at IS NULL DO NOTHING;

    SELECT id INTO v_day FROM public.tournament_days
    WHERE tournament_id = v_tour AND held_on = v_held_on AND deleted_at IS NULL;

    -- 卓。RPC を通すことで、席数の検査・重複の検査・group_id の整合が
    -- アプリから作ったときと完全に同じ経路で行われる。
    IF NOT EXISTS (SELECT 1 FROM public.games
                   WHERE day_id = v_day AND name = '卓1' AND deleted_at IS NULL) THEN
        PERFORM public.create_game_with_players(v_day, '卓1', jsonb_build_array(
            jsonb_build_object('new_name', 'みなみ'),
            jsonb_build_object('new_name', 'りいち'),
            jsonb_build_object('new_name', 'いっけい'),
            jsonb_build_object('new_name', 'じゅんや')
        ));
        RAISE NOTICE '卓1 を作成しました。';
    ELSE
        RAISE NOTICE '卓1 は既にあります。何もしません。';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM public.games
                   WHERE day_id = v_day AND name = '卓2' AND deleted_at IS NULL) THEN
        PERFORM public.create_game_with_players(v_day, '卓2', jsonb_build_array(
            jsonb_build_object('new_name', 'あいき'),
            jsonb_build_object('new_name', 'いっけい'),
            jsonb_build_object('new_name', 'じゅんや'),
            jsonb_build_object('new_name', 'りいち')
        ));
        RAISE NOTICE '卓2 を作成しました。';
    ELSE
        RAISE NOTICE '卓2 は既にあります。何もしません。';
    END IF;
END
$seed$;


-- ▼ 結果の確認
SELECT g.name AS "グループ", t.name AS "大会", d.held_on AS "開催日",
       ga.name AS "卓", gp.seat + 1 AS "席", p.name AS "参加者"
FROM public.games ga
JOIN public.tournament_days d ON d.id = ga.day_id
JOIN public.tournaments t     ON t.id = ga.tournament_id
JOIN public.groups g          ON g.id = ga.group_id
JOIN public.game_players gp   ON gp.game_id = ga.id
JOIN public.players p         ON p.id = gp.player_id
WHERE t.name = '2026年下期富沢家麻雀杯' AND ga.deleted_at IS NULL
ORDER BY d.held_on, ga.name, gp.seat;

-- ▼ 登録された参加者
SELECT p.name AS "参加者",
       (p.user_id IS NOT NULL) AS "アカウント紐付け済み",
       p.role AS "役割"
FROM public.players p
JOIN public.groups g ON g.id = p.group_id
WHERE p.deleted_at IS NULL
ORDER BY (p.user_id IS NOT NULL) DESC, p.name;
