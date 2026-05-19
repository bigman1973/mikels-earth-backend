-- Migración: Quitar constraint unique del campo email en tabla coupons
-- Esto permite que un mismo email tenga múltiples cupones (newsletter + post-compra + reseña)
ALTER TABLE coupons DROP CONSTRAINT IF EXISTS coupons_email_key;
ALTER TABLE coupons DROP CONSTRAINT IF EXISTS uq_coupons_email;
