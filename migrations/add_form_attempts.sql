-- PENDING APPROVAL / NOT EXECUTED. Existing PostgreSQL only, additive DDL.
-- No old profile, consent, signup, coupon, catalogue or stock rows are changed.
BEGIN;
CREATE TABLE IF NOT EXISTS form_attempts (
    id SERIAL PRIMARY KEY,
    endpoint VARCHAR(10) NOT NULL CHECK (endpoint IN ('vip', 'contact')),
    ip_hash VARCHAR(64) NOT NULL,
    attempted_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    rate_consumed BOOLEAN NOT NULL,
    email VARCHAR(255),
    canonical_email VARCHAR(255),
    duplicate BOOLEAN NOT NULL DEFAULT FALSE,
    vip_claim BOOLEAN NOT NULL DEFAULT FALSE,
    outcome VARCHAR(30) NOT NULL,
    CONSTRAINT ck_form_attempts_vip_claim CHECK
        (NOT vip_claim OR (endpoint = 'vip' AND canonical_email IS NOT NULL AND NOT duplicate))
);
CREATE INDEX IF NOT EXISTS ix_form_attempts_ip_window ON form_attempts (ip_hash, attempted_at);
CREATE INDEX IF NOT EXISTS ix_form_attempts_identity ON form_attempts (endpoint, canonical_email);
CREATE UNIQUE INDEX IF NOT EXISTS uq_form_attempts_vip_claim
    ON form_attempts (canonical_email) WHERE vip_claim = TRUE;
COMMIT;
-- Audit counters (read-only examples; not run):
-- SELECT endpoint, duplicate, outcome, COUNT(*) FROM form_attempts
-- GROUP BY endpoint, duplicate, outcome ORDER BY endpoint, duplicate, outcome;
-- Retention/purge is NOT automated. Retaining VIP claims is required for dedupe.
-- Rollback code first; retain the additive table for audit, never erase claims.
