-- 021 — QUE EL HILO DIGA LA VERDAD (aditiva e idempotente)
--
-- Lo que falta para la Fase 2 de la bandeja. Sale de la auditoría adversarial del plan
-- (28 hallazgos confirmados contra el código real, antes de escribir una línea).
--
-- ⚠️ NADA de bloques `DO $$ ... $$`: init_db._statements parte el .sql por ';' y los rompería.

-- ─── 1. La media del cliente, servible desde el panel ───────────────────────────
-- El binario del comprobante ya se guarda en disco como {media_id}.{ext}, pero en `mensajes`
-- solo teníamos `media_id`: el endpoint tendría que ADIVINAR la extensión (.jpg/.png/.pdf/.bin).
-- Y resolverla por el Pago no sirve: la imagen que la VISIÓN RECHAZA nunca crea Pago — o sea,
-- se caería justo en el mensaje del dinero, que es el que la dueña necesita ver con sus ojos.
ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS media_url  TEXT;  -- ruta REAL en disco
ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS media_mime TEXT;  -- para servirlo con su tipo

-- ─── 2. El eco puede traer tipos que el CHECK viejo NO admitía ──────────────────
-- La dueña, desde su celular, manda fotos, notas de voz, stickers, ubicaciones y hasta
-- reacciones (❤️). Con el CHECK anterior, cualquiera de esas REVENTABA el INSERT... y la
-- excepción se llevaba por delante la PAUSA (el bot volvía a hablarle encima al cliente) y
-- devolvía un 500 a Meta ⇒ reintentos en bucle ⇒ calidad del número ⇒ riesgo Tech Provider.
ALTER TABLE mensajes DROP CONSTRAINT IF EXISTS ck_mensaje_tipo;
ALTER TABLE mensajes ADD CONSTRAINT ck_mensaje_tipo CHECK (tipo IN (
    'text', 'image', 'audio', 'document', 'sticker', 'video',
    'location', 'contacts', 'reaction', 'otro'
));

-- ─── 3. El candado de los ESTADOS (entregado / leído / FALLÓ) ───────────────────
-- `wa_message_id` es el id que Meta devuelve al ENVIAR; los estados vienen casados contra él.
-- El índice de la 019 era normal (NO único) ⇒ un reintento de Meta duplicaba la fila y el
-- estado se aplicaba a una sola de las dos. Con UNIQUE, un id = una fila, siempre.
-- (Primero se limpian los duplicados que pudieran existir, quedándose con el más viejo.)
DELETE FROM mensajes m USING mensajes d
    WHERE m.wa_message_id IS NOT NULL
      AND m.wa_message_id = d.wa_message_id
      AND m.id > d.id;
DROP INDEX IF EXISTS idx_mensajes_wa_id;
CREATE UNIQUE INDEX IF NOT EXISTS ux_mensajes_wa_id
    ON mensajes (wa_message_id) WHERE wa_message_id IS NOT NULL;

-- ─── 4. El hilo se lee SIEMPRE por teléfono + fecha ─────────────────────────────
CREATE INDEX IF NOT EXISTS idx_mensajes_cliente_fecha ON mensajes (cliente_telefono, created_at);
