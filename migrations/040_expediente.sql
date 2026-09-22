-- 040 · EL EXPEDIENTE DE LA VENTA — los cimientos (SESIONES (35), 22-sep-2026).
--
-- Hoy lo que la dueña hace A MANO (toma un pedido, confirma un pago, acuerda una entrega, por
-- texto o por nota de voz) queda solo como texto en `mensajes`: ningún dato lo recoge, y el bot
-- entra ciego después de ella (en 6 de las 7 conversaciones reales donde entró, chocó con ella).
-- Esta migración abre las casillas para que esos hechos puedan ser DATO con PROCEDENCIA
-- (`origen`: bot | dueña | panel), EVIDENCIA (el mensaje exacto que lo respalda) y FECHA, y para
-- que lo dudoso quede como PROPUESTA que una persona confirma con un toque desde el panel.
--
-- NO cambia ninguna conducta: nadie escribe todavía en estas columnas (eso llega en los PRs
-- siguientes, con sus tests). Aditiva e idempotente: sin DO $$ ni ';' dentro de literales, porque
-- `init_db._statements` parte por ';' (ver su comentario).
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS origen TEXT NOT NULL DEFAULT 'bot';
ALTER TABLE pedidos DROP CONSTRAINT IF EXISTS ck_pedido_origen;
ALTER TABLE pedidos ADD CONSTRAINT ck_pedido_origen CHECK (origen IN ('bot','dueña','panel'));
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS evidencia_mensaje_id INTEGER REFERENCES mensajes(id) ON DELETE SET NULL;
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS confianza NUMERIC(3,2);
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS extraido_at TIMESTAMPTZ;
ALTER TABLE pagos ADD COLUMN IF NOT EXISTS origen TEXT NOT NULL DEFAULT 'bot';
ALTER TABLE pagos DROP CONSTRAINT IF EXISTS ck_pago_origen;
ALTER TABLE pagos ADD CONSTRAINT ck_pago_origen CHECK (origen IN ('bot','dueña','panel'));
ALTER TABLE pagos ADD COLUMN IF NOT EXISTS evidencia_mensaje_id INTEGER REFERENCES mensajes(id) ON DELETE SET NULL;
ALTER TABLE pagos ADD COLUMN IF NOT EXISTS confianza NUMERIC(3,2);
ALTER TABLE pagos ADD COLUMN IF NOT EXISTS extraido_at TIMESTAMPTZ;
ALTER TABLE intervenciones ADD COLUMN IF NOT EXISTS propuesta JSONB;
ALTER TABLE intervenciones ADD COLUMN IF NOT EXISTS aplicada_por TEXT;
ALTER TABLE intervenciones ADD COLUMN IF NOT EXISTS aplicada_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_intervenciones_propuestas ON intervenciones (cliente_telefono, estado) WHERE motivo = 'propuesta_expediente';
CREATE INDEX IF NOT EXISTS idx_mensajes_owner_por_id ON mensajes (cliente_telefono, id) WHERE rol = 'owner';
