-- REVIEW AND RUN MANUALLY: never executed by Flask/db.create_all.
-- No backfill: first_at is the first successfully recorded live observation.
BEGIN;
CREATE TABLE IF NOT EXISTS vip_funnel_daily (
    day date PRIMARY KEY,
    first_at timestamptz NOT NULL,
    last_at timestamptz NOT NULL,
    first_pageview_at timestamptz,
    first_submission_at timestamptz,
    pageviews bigint NOT NULL DEFAULT 0 CHECK (pageviews >= 0),
    submissions bigint NOT NULL DEFAULT 0 CHECK (submissions >= 0),
    successful_signup bigint NOT NULL DEFAULT 0 CHECK (successful_signup >= 0),
    ineligible bigint NOT NULL DEFAULT 0 CHECK (ineligible >= 0),
    technical_failure bigint NOT NULL DEFAULT 0 CHECK (technical_failure >= 0),
    validation_rejected bigint NOT NULL DEFAULT 0 CHECK (validation_rejected >= 0),
    honeypot bigint NOT NULL DEFAULT 0 CHECK (honeypot >= 0),
    rate_limited bigint NOT NULL DEFAULT 0 CHECK (rate_limited >= 0),
    unknown_outcome bigint NOT NULL DEFAULT 0 CHECK (unknown_outcome >= 0),
    telemetry_failure bigint NOT NULL DEFAULT 0 CHECK (telemetry_failure >= 0)
);
COMMENT ON TABLE vip_funnel_daily IS
    'Madrid-day aggregate observations only; no identities; counts are lower bounds, not unique visitors.';
COMMIT;
