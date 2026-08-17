-- プレイヤー統合だけを取り消す（他の移行はそのまま残す）。
--
-- 「別人を同一人物として統合してしまった」と分かったときに使う。
-- 監査テーブル mig003_*_remap に行単位の記録があるので正確に戻せる。
--
-- 戻す方向も衝突しない: 元は妥当だった状態へ復元するだけで、
-- 行は代表(survivor)から「その半荘に誰もいない player_id」へ移るため。
--
-- ★実行前に必ず件数を確認し、COMMIT する前に検算すること★

BEGIN;

-- 1. 結果の付け替えを戻す
UPDATE public.round_results rr SET player_id = m.old_player_id
FROM public.mig003_round_results_remap m
WHERE rr.round_id = m.round_id AND rr.seat = m.seat
  AND rr.player_id = m.new_player_id;

-- 2. 席の付け替えを戻す
UPDATE public.game_players gp SET player_id = m.old_player_id
FROM public.mig003_game_players_remap m
WHERE gp.game_id = m.game_id AND gp.seat = m.seat
  AND gp.player_id = m.new_player_id;

-- 3. 統合で畳んだプレイヤーを復活させる
UPDATE public.players p
   SET deleted_at = m.loser_deleted_at, merged_into = NULL
FROM public.mig003_player_merge_map m
WHERE p.id = m.loser_id;

-- 4. 同名が復活するので、グループ内の名前一意インデックスは外す
--    （外さないと手順3で一意違反になる場合は、3の前にここを実行すること）
DROP INDEX IF EXISTS public.players_group_name_uniq;

-- 5. 台帳から消して、再実行できるようにする
DELETE FROM public.schema_migrations WHERE version = '003b_data';

-- ▼ 検算: ポイント総和と結果件数がバックアップと一致すること
SELECT (SELECT sum(point) FROM public.round_results) AS total_now,
       (SELECT sum(point) FROM bak_003.round_results) AS total_backup,
       (SELECT count(*)   FROM public.round_results) AS rows_now,
       (SELECT count(*)   FROM bak_003.round_results) AS rows_backup;

-- 問題なければ COMMIT、おかしければ ROLLBACK
-- COMMIT;
ROLLBACK;
