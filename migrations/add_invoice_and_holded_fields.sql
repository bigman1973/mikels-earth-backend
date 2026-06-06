-- Migration: Add invoice/billing fields and Holded tracking to orders table
-- Date: 2026-06-06
-- Description: Adds needs_invoice, fiscal data, and holded_id fields

ALTER TABLE orders ADD COLUMN IF NOT EXISTS needs_invoice BOOLEAN DEFAULT FALSE;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS fiscal_name VARCHAR(200);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS fiscal_nif VARCHAR(20);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS fiscal_address VARCHAR(200);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS fiscal_city VARCHAR(100);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS fiscal_postal_code VARCHAR(20);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS holded_id VARCHAR(100);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS holded_invoice_id VARCHAR(100);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS holded_doc_number VARCHAR(50);
