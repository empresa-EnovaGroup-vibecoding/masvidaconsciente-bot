-- 004_pago_parcial.sql — Pago que no calza (parcial / sobrepago).
-- ADITIVA e idempotente: no toca migraciones anteriores. SQL plano (sin DO $$),
-- porque init_db separa los statements por ';'. Segura de correr varias veces.

-- Monto realmente recibido (en Bs), para cuando no calza con el cobrado.
ALTER TABLE pagos ADD COLUMN IF NOT EXISTS monto_recibido NUMERIC(14,2);

-- Suma el estado 'parcial' a los permitidos del pago (faltó plata).
ALTER TABLE pagos DROP CONSTRAINT IF EXISTS ck_pago_estado;
ALTER TABLE pagos ADD CONSTRAINT ck_pago_estado
  CHECK (estado IN ('reportado','confirmado','rechazado','parcial'));
