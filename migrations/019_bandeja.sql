-- 019 — LA BANDEJA: que el hilo diga la VERDAD (aditiva e idempotente)
--
-- Hoy la dueña NO puede responder desde el panel: no existe en ninguna capa (58 rutas en la API
-- y ni una es un POST de mensajes). El botón de la bandeja dice "Abrir el chat en WhatsApp": el
-- producto la EXPULSA. Esto pone los datos que hacen falta para que pueda atender DENTRO.
--
-- EL PRINCIPIO: cada mensaje sabe QUIÉN lo dijo (cliente · bot · la dueña), QUÉ era (texto ·
-- foto · comprobante) y CÓMO llegó (enviado · entregado · leído · FALLÓ). De ahí sale todo lo
-- demás sin inventar nada.

-- ─── 1. El hilo admite a la DUEÑA ───────────────────────────────────────────────
-- Hoy `rol IN ('user','assistant')`: su mensaje NO CABE. Y si se guardara como 'assistant', el
-- bot al retomar creería que lo dijo él y arrastraría promesas que no hizo.
-- 'owner' = lo escribió una PERSONA (venga del panel o de su celular).
--
-- ⚠️ OJO CON EL NOMBRE (lo cazó el banco de pruebas, no la lectura del código): la regla vieja
-- nació DENTRO del `CREATE TABLE` de la 001 (`rol TEXT ... CHECK (...)`), así que Postgres la
-- bautizó ÉL: `mensajes_rol_check`, no `ck_mensaje_rol`. Borrar solo el nombre "bonito" no
-- borraba nada: la migración decía "aplicada" y el rol 'owner' SEGUÍA prohibido en la BD.
-- Por eso se sueltan LOS DOS nombres. (No se puede usar un bloque DO $$ dinámico: init_db
-- parte el .sql por ';' y lo rompería.)
ALTER TABLE mensajes DROP CONSTRAINT IF EXISTS mensajes_rol_check;
ALTER TABLE mensajes DROP CONSTRAINT IF EXISTS ck_mensaje_rol;
ALTER TABLE mensajes ADD CONSTRAINT ck_mensaje_rol
    CHECK (rol IN ('user', 'assistant', 'owner'));

-- ─── 2. QUÉ era el mensaje (para que el comprobante se VEA en el chat) ──────────
-- Hoy el comprobante se guarda y se liga al Pago, pero NUNCA entra en `mensajes`: en el panel
-- no se ve NADA del intercambio del pago. Responder así es responder a ciegas.
ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS tipo TEXT NOT NULL DEFAULT 'text';
ALTER TABLE mensajes DROP CONSTRAINT IF EXISTS ck_mensaje_tipo;
-- 🔴🔴 ESTA LISTA REVENTÓ PRODUCCIÓN EN SILENCIO DURANTE DÍAS (encontrado el 2026-07-14).
--
-- Aquí decía solo ('text','image','audio','document','sticker','video'). La migración 021 la
-- AMPLIÓ después (para el eco de la dueña: ubicaciones, contactos, reacciones…). Pero como NO hay
-- tabla de migraciones aplicadas (deuda D1), **init_db vuelve a correr TODAS en cada arranque** —
-- y esta corre ANTES que la 021. En cuanto un cliente real mandó un contacto o algo raro, la fila
-- quedó con tipo='contacts'/'otro' y esta línea empezó a fallar:
--     CheckViolationError: check constraint "ck_mensaje_tipo" is violated by some row
-- `main.py` se TRAGA esa excepción, así que **el contenedor arrancaba VERDE**… y las migraciones
-- 020, 021, 022 y 022b **YA NO SE APLICABAN NUNCA**. Consecuencia real y cara: la 015 volvía a
-- crear el índice viejo `ux_precio_dia_producto_fecha` y la 022 (que lo borra) no llegaba a correr
-- ⇒ **en producción NO se podía cargar el precio del día de dos tamaños del mismo producto**
-- (la torta de 250g y la de 1kg). El bug que la 022 vino a arreglar seguía VIVO en producción.
--
-- La lista se pone IGUAL que la de la 021: así esta migración se puede re-correr contra datos ya
-- evolucionados sin reventar. Una migración que no aguanta volver a correr sobre datos REALES no
-- es idempotente, por muchos IF NOT EXISTS que lleve.
ALTER TABLE mensajes ADD CONSTRAINT ck_mensaje_tipo
    CHECK (tipo IN ('text', 'image', 'audio', 'document', 'sticker', 'video',
                    'location', 'contacts', 'reaction', 'otro'));
ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS media_id TEXT;

-- ─── 3. CÓMO llegó (nada de fallos en silencio) ────────────────────────────────
-- `wa_message_id` = el id que devuelve Meta al ENVIAR. Sin él no hay con qué casar el estado
-- que Meta manda después. OJO: `message_id` (que ya existe) es el id del mensaje ENTRANTE y
-- tiene UNIQUE — son cosas distintas y no se pueden mezclar.
ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS wa_message_id TEXT;
ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS estado TEXT;  -- enviado|entregado|leido|fallido
ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS error TEXT;   -- por qué falló (se ve en rojo)
CREATE INDEX IF NOT EXISTS idx_mensajes_wa_id ON mensajes (wa_message_id)
    WHERE wa_message_id IS NOT NULL;

-- ─── 4. EL RELOJ DE LAS 24 HORAS (la regla de Meta) ────────────────────────────
-- Meta solo deja responder con texto libre dentro de las 24h desde el ÚLTIMO MENSAJE DEL
-- CLIENTE. Hace falta una columna PROPIA: `ultima_interaccion` no sirve como reloj porque su
-- semántica es otra (actividad general del chat).
-- ⚠️ NADA de DEFAULT now(): mentiría diciendo que un cliente de abril escribió hoy.
-- ⚠️ NULL ⇒ ventana CERRADA (fail-closed). Nunca fail-open: mandar fuera de ventana quema la
--    calidad del número, y para un Tech Provider eso arriesga la cuenta de TODOS los clientes.
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS ultimo_entrante_at TIMESTAMPTZ;

-- Backfill HONESTO: la hora del último mensaje que ESE cliente escribió de verdad (rol='user').
-- Idempotente: solo rellena lo que está vacío (init_db re-ejecuta los .sql en cada arranque).
UPDATE clientes c
SET ultimo_entrante_at = m.ultimo
FROM (
    SELECT cliente_telefono, MAX(created_at) AS ultimo
    FROM mensajes WHERE rol = 'user' GROUP BY cliente_telefono
) m
WHERE m.cliente_telefono = c.telefono AND c.ultimo_entrante_at IS NULL;

-- ─── 5. LA COLA (sin no-leídos, una lista no es una cola) ───────────────────────
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS no_leidos INTEGER NOT NULL DEFAULT 0;
