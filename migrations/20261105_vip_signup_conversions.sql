-- DRAFT ONLY. Explicit approved migration; never run from request/create_all.
-- Compatible PostgreSQL and SQLite. No existing table/schema alterations.
-- Idempotent DDL only; no historical backfill or remote marketing operations.
-- vip_signups must already exist. IDs are application-generated UUID strings.
CREATE TABLE IF NOT EXISTS vip_signup_conversions (
    id VARCHAR(36) PRIMARY KEY,
    campaign VARCHAR(80) NOT NULL,
    email_hash VARCHAR(64) NOT NULL,
    signup_id INTEGER NOT NULL REFERENCES vip_signups(id),
    event_id VARCHAR(36) NOT NULL UNIQUE,
    list_id VARCHAR(100) NOT NULL,
    profile_id VARCHAR(100),
    pre_checked_at TIMESTAMP NOT NULL,
    post_checked_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    confirmed_at TIMESTAMP,
    last_checked_at TIMESTAMP,
    capi_state VARCHAR(20) NOT NULL DEFAULT 'pending',
    capi_attempts INTEGER NOT NULL DEFAULT 0,
    capi_attempted_at TIMESTAMP,
    capi_sent_at TIMESTAMP,
    capi_reason VARCHAR(40),
    CONSTRAINT uq_vip_conversion_campaign_email UNIQUE (campaign, email_hash),
    CONSTRAINT uq_vip_conversion_signup UNIQUE (signup_id),
    CONSTRAINT ck_vip_conversion_capi_state CHECK
        (capi_state IN ('pending','sending','sent','failed','unknown')),
    CONSTRAINT ck_vip_conversion_attempts CHECK
        (capi_attempts >= 0 AND capi_attempts <= 2),
    CONSTRAINT ck_vip_conversion_hash CHECK (length(email_hash) = 64),
    CONSTRAINT ck_vip_conversion_expiry CHECK (expires_at > created_at),
    CONSTRAINT ck_vip_conversion_post_evidence CHECK
        ((profile_id IS NULL AND post_checked_at IS NULL) OR
         (profile_id IS NOT NULL AND post_checked_at IS NOT NULL)),
    CONSTRAINT ck_vip_conversion_confirmation_evidence CHECK
        (confirmed_at IS NULL OR
         (profile_id IS NOT NULL AND post_checked_at IS NOT NULL)),
    CONSTRAINT ck_vip_conversion_evidence_order CHECK
        (post_checked_at IS NULL OR post_checked_at >= pre_checked_at)
);
