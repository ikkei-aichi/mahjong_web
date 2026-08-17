# oneshot — 一度きりのSQL

このディレクトリは **`python -m mahjong.migrator` の対象外** です。
`migrator.discover()` の glob が `*.sql`（非再帰）なので、
ここに置いたファイルが `schema.sql` に混ざることは構造的にありません。

冪等でない処理（プレイヤーの統合など）を二度流してデータを壊す事故を防ぐための隔離です。

## 実行順

| # | ファイル | 内容 | 書き込み |
|---|---|---|---|
| 1 | `00_backup.sql` | `bak_003` スキーマへ全テーブルを複製 | する |
| 2 | `01_preflight.sql` | 統合対象の確認・中止条件の検査 | **しない** |
| 3 | （`003a_groups_schema.sql` を適用） | スキーマ追加 | する |
| 4 | `02_data_migration.sql` | グループ作成・統合・開催日生成 | **する（一度きり）** |
| 5 | （`003c_rls.sql` / `003d_views_rpc.sql` を適用） | 制約・RLS・ビュー・RPC | する |
| 6 | `04_verify.sql` | 移行後の検算とRLSの実効確認 | しない |

`03_rollback_merge.sql` は、統合だけを取り消したいときに使います。

## 注意

- **`01_preflight.sql` の中止条件（P4/P5/P6）が1行でも返したら、`02` を実行しないこと。**
  同じ半荘に同名プレイヤーが2人いる等、統合すると一意制約に衝突するケースです。
- `02_data_migration.sql` 全体が1つの `DO` ブロックで、`DO` は1文なので確実に原子的です。
  途中で `RAISE EXCEPTION` すれば全部巻き戻ります。
- Supabase の SQL Editor は `postgres` として動くため **RLS を素通りします**。
  「ダッシュボードで見えたから大丈夫」は検証になりません。`04_verify.sql` の
  `SET LOCAL ROLE authenticated` を使ってください。
