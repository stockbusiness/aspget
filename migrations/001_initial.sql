-- aspget 初期スキーマ
-- 実行: psql $DATABASE_URL -f migrations/001_initial.sql

BEGIN;

-- ============================================
-- 案件マスタ（現在の状態）
-- ============================================
CREATE TABLE IF NOT EXISTS programs (
    id                 BIGSERIAL PRIMARY KEY,
    asp_name           TEXT NOT NULL,          -- 'valuecommerce' | 'a8net' | 'moshimo'
    asp_program_id     TEXT NOT NULL,          -- ASP側の案件ID（必ずTEXT）
    program_name       TEXT NOT NULL,
    advertiser_name    TEXT,
    advertiser_key     TEXT,                   -- 正規化済み広告主名（ASP間比較用）
    category           TEXT,
    reward_type        TEXT,                   -- 'fixed' | 'percentage' | 'mixed' | 'unknown'
    reward_amount      NUMERIC(12,2),          -- 固定額（円）
    reward_rate        NUMERIC(6,3),           -- 料率（%）
    reward_raw         TEXT,                   -- 元の表記をそのまま保持
    condition_text     TEXT,
    partnership_status TEXT,                   -- 'affiliated' | 'not_affiliated' | 'pending' | 'rejected' | 'unknown'
    program_url        TEXT,
    is_active          BOOLEAN NOT NULL DEFAULT TRUE,
    first_seen_at      TIMESTAMPTZ NOT NULL,
    last_seen_at       TIMESTAMPTZ NOT NULL,
    updated_at         TIMESTAMPTZ NOT NULL,
    UNIQUE (asp_name, asp_program_id)
);

CREATE INDEX IF NOT EXISTS idx_programs_advertiser_key ON programs (advertiser_key);
CREATE INDEX IF NOT EXISTS idx_programs_active         ON programs (is_active);
CREATE INDEX IF NOT EXISTS idx_programs_asp            ON programs (asp_name);

-- ============================================
-- 日次スナップショット（差分検知の元データ）
-- ============================================
CREATE TABLE IF NOT EXISTS program_snapshots (
    id                 BIGSERIAL PRIMARY KEY,
    snapshot_date      DATE NOT NULL,          -- JSTの日付
    asp_name           TEXT NOT NULL,
    asp_program_id     TEXT NOT NULL,
    program_name       TEXT,
    advertiser_key     TEXT,
    reward_amount      NUMERIC(12,2),
    reward_rate        NUMERIC(6,3),
    reward_raw         TEXT,
    condition_text     TEXT,
    partnership_status TEXT,
    payload            JSONB,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (snapshot_date, asp_name, asp_program_id)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_date ON program_snapshots (snapshot_date);

-- ============================================
-- 検知した変更履歴
-- ============================================
CREATE TABLE IF NOT EXISTS program_changes (
    id                 BIGSERIAL PRIMARY KEY,
    detected_date      DATE NOT NULL,
    asp_name           TEXT NOT NULL,
    asp_program_id     TEXT NOT NULL,
    change_type        TEXT NOT NULL,          -- 'new'|'reward_up'|'reward_down'|'condition_changed'|'ended'|'resumed'
    field_name         TEXT,
    old_value          TEXT,
    new_value          TEXT,
    notified           BOOLEAN NOT NULL DEFAULT FALSE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_changes_date ON program_changes (detected_date, notified);

-- ============================================
-- 成果データ（Step 4以降で使用）
-- ============================================
CREATE TABLE IF NOT EXISTS conversions (
    id                 BIGSERIAL PRIMARY KEY,
    asp_name           TEXT NOT NULL,
    asp_conversion_id  TEXT,
    asp_program_id     TEXT,
    occurred_at        TIMESTAMPTZ,
    status             TEXT,                   -- 'pending' | 'approved' | 'rejected'
    reward_amount      NUMERIC(12,2),
    site_identifier    TEXT,
    custom_param       TEXT,                   -- もしもの「任意パラメータ」(s_v) 等
    payload            JSONB,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (asp_name, asp_conversion_id)
);

CREATE INDEX IF NOT EXISTS idx_conversions_occurred ON conversions (occurred_at);
CREATE INDEX IF NOT EXISTS idx_conversions_param    ON conversions (custom_param);

-- ============================================
-- 実行ログ
-- ============================================
CREATE TABLE IF NOT EXISTS collection_runs (
    id                 BIGSERIAL PRIMARY KEY,
    run_date           DATE NOT NULL,
    asp_name           TEXT NOT NULL,
    status             TEXT NOT NULL,          -- 'success' | 'partial' | 'failed'
    records_fetched    INTEGER,
    error_message      TEXT,
    started_at         TIMESTAMPTZ NOT NULL,
    finished_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_runs_date ON collection_runs (run_date, asp_name);

COMMIT;
