-- 013_producto_ficha.sql — Ficha de cada producto para el bot.
-- Guarda info ESPECÍFICA de cada producto (duración, si se congela, si es apto para
-- diabéticos, y más info libre) para que el bot responda con datos de ESE producto y
-- no generalice info de otro. ADITIVA e idempotente: solo agrega columnas.
ALTER TABLE productos ADD COLUMN IF NOT EXISTS duracion TEXT;
ALTER TABLE productos ADD COLUMN IF NOT EXISTS se_congela TEXT;
ALTER TABLE productos ADD COLUMN IF NOT EXISTS apto_diabeticos TEXT;
ALTER TABLE productos ADD COLUMN IF NOT EXISTS info TEXT;
