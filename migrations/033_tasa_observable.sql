-- 033_tasa_observable.sql — DE DONDE SALIO LA TASA CON LA QUE SE COBRO (aditiva e idempotente).
--
-- 🔴 POR QUE. `services/tasa.py` resuelve la tasa BCV en cadena: cache -> API en vivo -> respaldo
-- (`configuracion.tasa_manual`) -> TASA_MANUAL_DEFAULT. Cuando la API falla, la caida al respaldo
-- se anotaba con UN `logger.warning` dentro del contenedor y nada mas: ni sonda en /salud, ni
-- telemetria, ni marca de tiempo. El respaldo puede estar congelado semanas y NADIE se entera.
--
-- LO QUE SE MIDIO EN EL TALLER EL 2026-08-09, y es el motivo entero de esta tabla:
--     API en vivo ............ 756,7083 Bs/$
--     configuracion.tasa_manual  567,68     (tasa_manual_activa = 0, tasa_margen_pct = 0.0)
-- Un 25% POR DEBAJO. Si la API se cae, el bot cotiza los Pago Movil un 25% mas baratos EN
-- SILENCIO: el negocio cobra de menos en cada venta y nadie lo nota. Es el camino del DINERO.
--
-- 🔴 POR QUE UNA TABLA Y NO UNA CLAVE EN `configuracion` NI SOLO REDIS.
--   · Tiene que cruzar de PROCESO: quien resuelve la tasa es el WORKER (agent/tools.py, dentro
--     del turno del cliente) y quien la publica es la API (/salud). Una bandera en memoria no
--     llega — el mismo motivo por el que el testigo del 402 vive en Redis (services/salud.py).
--   · Tiene que sobrevivir a un reinicio del PROCESO y tambien a un Redis vaciado. La antiguedad
--     del ultimo dato bueno de la API es justo el numero que no puede perderse al desplegar: si
--     se pierde, el respaldo vuelve a ser invisible y volvemos al punto de partida.
--   · Y ADEMAS es la telemetria: la MISMA fila responde "que se esta sirviendo ahora" y "cuando
--     fue la ultima vez que la API contesto". Dos mecanismos paralelos (una marca para la sonda y
--     un registro para la telemetria) pueden CONTRADECIRSE; uno solo, no.
--   · `configuracion` es la tabla que EDITA la duena desde el panel. Un contador que se reescribe
--     solo no pinta nada ahi, y una escritura automatica sobre esa tabla se puede pisar con el
--     `PUT /configuracion`.
--
-- QUE SE ANOTA Y QUE NO — el detalle que hace que esto no cueste nada:
--   · Se anota cuando se HABLA con la API (una vez por `tasa_ttl`, hoy 1 hora) y CADA caida al
--     respaldo. NO se anota cuando se sirve de la cache de Redis, que es la inmensa mayoria de
--     las resoluciones: el carril normal no paga ni un INSERT.
--   · Con la API sana son ~24 filas al dia. Con la API caida, una por cotizacion. Del orden de
--     unos KB al mes. Cuando estorbe se poda con UNA linea, y por eso hoy NO se construye ningun
--     barrido automatico (YAGNI):
--         DELETE FROM tasa_resoluciones WHERE created_at < now() - interval '180 days';
--
-- NO HAY ENDPOINT NI PANTALLA, A PROPOSITO. El veredicto sale por `/salud` (sin una sola cifra:
-- ese endpoint es PUBLICO) y el detalle se lee con psql:
--
--   -- con que tasa se cobro mientras la API estuvo caida:
--   SELECT created_at, origen, valor, error FROM tasa_resoluciones
--    WHERE origen <> 'api' ORDER BY id DESC LIMIT 50;
--
--   -- cuando fue la ultima vez que la API contesto de verdad:
--   SELECT max(created_at) FROM tasa_resoluciones WHERE origen = 'api';
--
--   -- cuanto se despego el respaldo de la ultima tasa buena (el 25% del 2026-08-09):
--   SELECT origen, count(*), min(valor), max(valor) FROM tasa_resoluciones
--    WHERE created_at > now() - interval '7 days' GROUP BY origen;
--
-- ⚠️ El partidor `_statements` de app/init_db.py parte por ';' y es INGENUO: aqui no hay bloques
-- DO, ni un ';' dentro de un literal, ni ningun ':palabra'. Misma regla que la 032.

-- `origen`: api | respaldo_bd | default. ('cache' no llega nunca aqui, ver arriba.)
-- `valor` NULL-able: si NO hubo ni respaldo (mala configuracion) queda la fila con el error y sin
-- numero. Un cero mentiria. NUMERIC(18,6) porque la tasa venezolana ya va por 756,7083 y sube.
-- `error` guarda POR QUE fallo la API, recortado. Ni una letra de lo que se hablo con nadie.
CREATE TABLE IF NOT EXISTS tasa_resoluciones (
    id BIGSERIAL PRIMARY KEY,
    origen TEXT NOT NULL,
    valor NUMERIC(18,6),
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- DOS INDICES, y son EXACTAMENTE las dos preguntas de la sonda, ninguna mas:
--   1) "cual fue la ULTIMA resolucion" -> la resuelve la PK (id DESC), gratis, sin indice extra.
--   2) "cuando contesto la API por ultima vez" -> este indice PARCIAL. Parcial y no completo
--      porque solo se pregunta por las filas 'api': en una caida larga las filas de respaldo son
--      mayoria y no tienen por que engordar el indice. En una tabla que escribe ~24 filas al dia
--      el peaje es despreciable, y a cambio la sonda es O(1) para siempre.
CREATE INDEX IF NOT EXISTS idx_tasa_resoluciones_api
    ON tasa_resoluciones (created_at DESC) WHERE origen = 'api';
CREATE INDEX IF NOT EXISTS idx_tasa_resoluciones_fecha ON tasa_resoluciones (created_at);
