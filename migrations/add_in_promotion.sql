-- Generic administrator-controlled promotion flag; never seeds/enables products.
-- Idempotent PostgreSQL migration for Railway; run only after authorised review.
ALTER TABLE web_products
    ADD COLUMN IF NOT EXISTS in_promotion BOOLEAN NOT NULL DEFAULT FALSE;
