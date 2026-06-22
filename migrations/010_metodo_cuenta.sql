-- 010_metodo_cuenta.sql — ADITIVA e idempotente. Agrega el NÚMERO DE CUENTA
-- bancaria a los métodos de pago (lo que identifica una TRANSFERENCIA, distinto
-- del teléfono del Pago Móvil). SQL plano (sin DO $$).

ALTER TABLE metodos_pago ADD COLUMN IF NOT EXISTS cuenta TEXT;
