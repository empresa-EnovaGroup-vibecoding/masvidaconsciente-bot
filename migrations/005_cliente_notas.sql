-- 005_cliente_notas.sql — Notas internas del cliente (CRM). ADITIVA e idempotente.
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS notas TEXT;
