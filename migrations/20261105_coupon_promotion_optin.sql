-- Additive schema only. Exact collaborator activation is a separate audited step.
-- Apply before deploying the ORM that reads this field.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '10s';
ALTER TABLE public.coupons
    ADD COLUMN IF NOT EXISTS valid_in_promotion boolean NOT NULL DEFAULT FALSE;
COMMIT;
