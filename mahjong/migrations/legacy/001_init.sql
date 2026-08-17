-- 麻雀管理アプリ 初期スキーマ
--
-- 設計方針:
--   * 対戦の席順と半荘の結果は正規化する。旧スキーマの player1_*〜player4_* 横持ちは
--     3人麻雀で存在しない4人目に 0 を書き込んでしまい、集計SQLが
--     COALESCE(..., -999) だらけになっていた。
--   * 入力された持ち点(raw_score)を必ず保存する。ルール変更時に過去データを
--     再計算できるようにするため。
--   * 削除は deleted_at による論理削除に統一する。

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- タイトル（対戦グループ・大会）
CREATE TABLE IF NOT EXISTS titles (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name       text NOT NULL CHECK (length(btrim(name)) > 0),
    -- ウマ・オカ・返し点・レート等。列を増やさず項目を追加できるよう jsonb にする
    ruleset    jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Supabase Auth のユーザーID。直結接続では RLS が効かないためアプリ側で権限判定する
    owner_id   uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz
);

CREATE INDEX IF NOT EXISTS titles_active_idx
    ON titles (created_at DESC) WHERE deleted_at IS NULL;

-- プレイヤー（タイトルごとに独立）
CREATE TABLE IF NOT EXISTS players (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title_id   uuid NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    name       text NOT NULL CHECK (length(btrim(name)) > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz
);

-- 同一タイトル内での同名を禁止する。名前を表のキーに使っていたため
-- 同名が2人いると片方のデータが消えていた。削除済みの名前は再利用できる。
CREATE UNIQUE INDEX IF NOT EXISTS players_title_name_uniq
    ON players (title_id, name) WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS players_title_idx ON players (title_id);

-- 対戦（半荘をまとめた1セット）
CREATE TABLE IF NOT EXISTS games (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title_id   uuid NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    name       text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz
);

CREATE INDEX IF NOT EXISTS games_title_idx ON games (title_id, created_at DESC);

-- 対戦の席順。seat は 0..3（3人麻雀なら3行）
CREATE TABLE IF NOT EXISTS game_players (
    game_id   uuid NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    seat      smallint NOT NULL CHECK (seat BETWEEN 0 AND 3),
    player_id uuid NOT NULL REFERENCES players(id) ON DELETE RESTRICT,
    PRIMARY KEY (game_id, seat),
    -- 同じ人が同じ卓に二重で座ることを防ぐ
    UNIQUE (game_id, player_id)
);

CREATE INDEX IF NOT EXISTS game_players_player_idx ON game_players (player_id);

-- 半荘1回。表示上の「回」は取得時に連番を振り直すため、
-- 途中の回を削除しても番号に穴が空かない。
CREATE TABLE IF NOT EXISTS game_rounds (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    game_id    uuid NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz
);

CREATE INDEX IF NOT EXISTS game_rounds_game_idx
    ON game_rounds (game_id, created_at) WHERE deleted_at IS NULL;

-- 半荘1回における1人分の結果
CREATE TABLE IF NOT EXISTS round_results (
    round_id  uuid NOT NULL REFERENCES game_rounds(id) ON DELETE CASCADE,
    player_id uuid NOT NULL REFERENCES players(id) ON DELETE RESTRICT,
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

CREATE INDEX IF NOT EXISTS round_results_player_idx ON round_results (player_id);
