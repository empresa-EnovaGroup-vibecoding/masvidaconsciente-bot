-- 032_telemetria_ia.sql — QUÉ MODELO RESPONDIÓ, CUÁNTOS TOKENS Y CUÁNTO COSTÓ (aditiva e idempotente).
--
-- 🔴 POR QUÉ. OpenRouter devuelve un bloque `usage` en CADA respuesta —tokens Y costo en dólares,
-- ya calculado— y hasta hoy se tiraba entero: en todo el repo no había una sola mención a
-- `usage`, `prompt_tokens` ni `cost`. Y `modelo_ia` se cambia desde el panel SIN redeploy, así que
-- el modelo puede cambiar un martes y nadie puede decir después cuál contestó el lunes.
--
-- 🔴 POR QUÉ UNA TABLA APARTE Y NO COLUMNAS EN `mensajes`.
-- Un turno gasta VARIAS llamadas al modelo (hasta 6 vueltas del bucle, mas la Voz, mas el
-- reintento del dinero, mas el fallback) y en `mensajes` hay UNA FILA POR GLOBO ENVIADO. 5
-- llamadas contra 4 globos: no casan. Repartir el gasto entre los globos INVENTA un numero, y
-- copiar el total en cada globo lo cuenta CUATRO veces al sumar. Una llamada HTTP equivale
-- exactamente a un bloque `usage` y a un `cost`: es la unica unidad que se puede sumar sin mentir.
-- Y hay llamadas que NO producen NINGUN globo: la transcripcion de la nota de voz, la vision del
-- comprobante, los embeddings (los del panel y los del arranque) y el simulador, que ni siquiera
-- escribe en `mensajes`. Colgadas de `mensajes` se perderia justo lo que cuesta y no se ve.
-- `turno_id` vuelve a juntar las piezas cuando hace falta, sin repartir nada.
-- Ademas `mensajes` es la tabla que MAS CRECE y NO se poda nunca (es el hilo del negocio, la
-- prueba de lo que se le dijo a un cliente). Esto es dato operativo, con otro ciclo de vida.
--
-- TAMANO: fila ESTRECHA, sin una sola letra de lo que se hablo (solo numeros, modelos y el error).
-- A 4 llamadas por turno y 10 turnos diarios son unas 40 filas al dia por cliente activo: del
-- orden de 100 KB al mes con 40 clientes. Cuando estorbe se poda con UNA linea, y por eso hoy NO
-- se construye ningun barrido automatico (YAGNI):
--     DELETE FROM llamadas_ia WHERE created_at < now() - interval '90 days'
--
-- 🔴 NO HAY ENDPOINT NI PANTALLA, A PROPOSITO (revision cruzada 2026-08-03). Esto es lenguaje de
-- PROVEEDOR: la duena vende comida, no compra tokens. Un endpoint de agregacion dentro del
-- archivo que maneja el DINERO, para alimentar una pantalla que nadie va a construir, es la
-- sobre-ingenieria que ROADMAP.md prohibe. Se lee con psql, y las consultas son estas:
--
--   -- lo que costo HOY, por modelo que RESPONDIO de verdad:
--   SELECT modelo_real, count(*) llamadas, count(*) FILTER (WHERE NOT ok) fallos,
--          sum(costo_usd) usd, sum(tokens_entrada) entrada, sum(tokens_salida) salida,
--          sum(tokens_cache) cache
--     FROM llamadas_ia WHERE created_at >= date_trunc('day', now() - interval '4 hours')
--    GROUP BY modelo_real ORDER BY usd DESC;
--
--   -- lo que costo cada TURNO (la prueba de que no se cuenta doble):
--   SELECT turno_id, carril, count(*) llamadas, sum(costo_usd) usd
--     FROM llamadas_ia GROUP BY turno_id, carril ORDER BY usd DESC LIMIT 20;
--
--   -- por que fallo:
--   SELECT created_at, paso, modelo_pedido, error FROM llamadas_ia
--    WHERE NOT ok ORDER BY id DESC LIMIT 30;
--
--   -- lo que gastan las pruebas del panel, APARTE del gasto real:
--   SELECT sum(costo_usd) FROM llamadas_ia WHERE cliente_telefono LIKE '__simulador__%';
--
-- ⚠️ El partidor `_statements` de app/init_db.py parte por ';' y es INGENUO: aqui no hay bloques
-- DO, ni un ';' dentro de un literal, ni ningun ':palabra'. Misma regla que la 029 y la 030.

-- Sin FK a `clientes.telefono` A PROPOSITO: por aqui pasan telefonos que NO son clientes
-- ('__simulador__…' del panel, '__sistema__' del barredor) y una FK convertiria un apunte de
-- telemetria en un ERROR DEL TURNO. `mensajes.cliente_telefono` tampoco la tiene.
-- `costo_usd` es NULL-able y sin default 0: NULL significa "OpenRouter no lo dijo", que no es lo
-- mismo que "salio gratis". El costo NUNCA se estima.
-- NUMERIC(14,10) y no (12,8): el costo real de una llamada es 0.000033 y el de un embedding
-- 0.00000006 — que con 8 decimales ya esta al filo. Es la columna del DINERO: un redondeo
-- silencioso a cero aqui es la misma clase de mentira que la columna NULL vino a evitar.
CREATE TABLE IF NOT EXISTS llamadas_ia (
    id BIGSERIAL PRIMARY KEY,
    turno_id TEXT,
    cliente_telefono TEXT,
    carril TEXT NOT NULL DEFAULT 'sin_turno',
    paso TEXT NOT NULL DEFAULT 'agente',
    modelo_pedido TEXT,
    modelo_real TEXT,
    proveedor TEXT,
    tokens_entrada INTEGER NOT NULL DEFAULT 0,
    tokens_salida INTEGER NOT NULL DEFAULT 0,
    tokens_cache INTEGER NOT NULL DEFAULT 0,
    costo_usd NUMERIC(14,10),
    ms INTEGER NOT NULL DEFAULT 0,
    ok BOOLEAN NOT NULL DEFAULT TRUE,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- UN SOLO INDICE, y es el que se usa: todas las preguntas (cuanto gaste hoy, que modelo
-- respondio esta semana, cuantas llamadas fallaron en la ultima hora) empiezan por la FECHA, y
-- buscar UN turno concreto tambien se acota por fecha. Un indice de mas en la tabla que mas
-- escribe es peaje en cada llamada al modelo, para siempre.
CREATE INDEX IF NOT EXISTS idx_llamadas_ia_fecha ON llamadas_ia (created_at);
