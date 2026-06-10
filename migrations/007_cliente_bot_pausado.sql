-- 007_cliente_bot_pausado.sql — Pausar el bot por cliente ("atiendo yo"). ADITIVA e idempotente.
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS bot_pausado BOOLEAN NOT NULL DEFAULT FALSE;
