-- 028 · EL BARREDOR TIENE DÓNDE APOYARSE (aditiva e idempotente).
--
-- 🔴 POR QUÉ (auditoría 2026-08-02, SIL-13 + VIG.1). En todo el stack NO HAY UN SOLO SCHEDULER:
-- `app/workers/celery_app.py` no declara `beat_schedule`, el worker corre sin `-B`
-- (docker-compose.yml:69) y ninguna imagen instala cron. Consecuencia: nada que quede a medias se
-- arregla ni se REPORTA solo. Un cliente puede escribir, que su mensaje se evapore, y que NADIE
-- —ni la dueña ni el proveedor— se entere nunca. Eso ya pasó: una semana entera de mensajes mudos.
--
-- Esta migración NO CREA TABLAS a propósito. `models.py` no cambia, así que `probar_drift.py` no
-- puede ponerse rojo por esto. El barredor reusa lo que ya existe: `intervenciones` (la bandeja
-- que la dueña ya mira todos los días) y `configuracion`.
--
-- ⚠️ El partidor de sentencias de `init_db._statements` es INGENUO (parte por ';', init_db.py:45):
-- aquí no hay bloques `DO $$ … $$` ni un solo ';' dentro de un literal. Toda migración nueva debe
-- seguir esa regla o hay que endurecer el partidor primero.

-- ── 1) LA FILA DE LA CONCESIÓN (y, de regalo, el testigo del propio vigilante) ──
--
-- El `INSERT … ON CONFLICT DO UPDATE … WHERE` de `barredor.tomar_turno` es lo que garantiza que
-- barra UN SOLO proceso: aunque uvicorn corra con varios workers, aunque alguien lance
-- `scripts/barrer.py` por SSH a la vez, y aunque algún día llegue el `celery beat`. Vive en
-- Postgres y no en Redis porque Redis puede ser JUSTO lo que esté caído.
--
-- Y esta misma fila es el TESTIGO DEL TESTIGO (F5): el bucle la reescribe en cada pasada, y
-- `GET /salud` devuelve `degradado` si tiene más de 15 minutos. Sin eso, el día que el bucle
-- muera por una excepción fuera de su try, la red desaparecería EN SILENCIO — que es exactamente
-- el modo de fallo que este bloque vino a matar.
--
-- La clave NO se añade a `CLAVES_CONFIG` (api/router.py:104) a propósito: así el
-- `PUT /configuracion` del panel —que descarta en silencio lo que no conoce— no la puede pisar
-- nunca. Mismo criterio que `tools_activas` (api/router.py:1122).
--
-- El valor es un instante UTC de ANCHO FIJO ('%Y-%m-%dT%H:%M:%SZ'): así comparar como TEXTO es
-- comparar como FECHA. Con `isoformat()` los microsegundos aparecen unas veces sí y otras no, y
-- ahí el orden lexicográfico se rompe en silencio (y la concesión dejaría de repartir turnos).
INSERT INTO configuracion (clave, valor)
VALUES ('barredor_ultima_corrida', '1970-01-01T00:00:00Z')
ON CONFLICT (clave) DO NOTHING;

-- ── 2) EL ÍNDICE DEL VIGILANTE ──
--
-- El vigilante barre por `clientes.ultimo_entrante_at` cada 5 minutos. Hoy son cientos de filas y
-- un seq scan da igual; el índice PARCIAL (solo los que tienen reloj — los demás no se miran
-- NUNCA, y son la mayoría de un catálogo viejo) cuesta casi nada y evita que dentro de un año la
-- pasada se note en el mismo event loop que atiende el webhook.
--
-- La otra mitad de la consulta —el `NOT EXISTS` sobre `mensajes`— ya está cubierta por
-- `idx_mensajes_cliente_fecha` (migración 021).
CREATE INDEX IF NOT EXISTS idx_clientes_ultimo_entrante
    ON clientes (ultimo_entrante_at)
    WHERE ultimo_entrante_at IS NOT NULL;
