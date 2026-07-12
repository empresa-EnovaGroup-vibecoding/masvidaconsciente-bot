-- 015 — "EL BOT TE NECESITA": intervención humana + precio del día.
--
-- POR QUÉ:
--  (a) Hay productos cuyo precio CAMBIA de un día a otro (Tortas keto, torta baja en
--      carbohidratos, Premezclas). Están SIN precio A PROPÓSITO: en Venezuela el costo
--      se mueve, y la dueña responde ella esas consultas. El bot NUNCA debe inventar
--      un precio: debe avisarle a ella.
--  (b) Lo mismo cuando el bot NO SABE algo (envío nacional, dudas no cargadas), cuando
--      el cliente PIDE UNA PERSONA, o cuando hay un RECLAMO.
--
-- Aditiva e idempotente: no toca ninguna tabla existente.

-- Bandeja de avisos: "el bot te necesita en este chat".
CREATE TABLE IF NOT EXISTS intervenciones (
    id                SERIAL PRIMARY KEY,
    cliente_telefono  TEXT        NOT NULL,
    motivo            TEXT        NOT NULL,   -- precio_del_dia | no_se | pide_persona | reclamo
    detalle           TEXT,                   -- "pregunta el precio de Tortas keto (1kg)"
    mensaje_cliente   TEXT,                   -- lo último que escribió el cliente
    estado            TEXT        NOT NULL DEFAULT 'pendiente',  -- pendiente | resuelta
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    resuelta_at       TIMESTAMPTZ
);

-- La bandeja se lee por estado y por fecha (lo pendiente primero, lo más nuevo arriba).
CREATE INDEX IF NOT EXISTS ix_intervenciones_estado
    ON intervenciones (estado, created_at DESC);

-- Un mismo chat no debe generar 10 avisos iguales mientras la dueña no ha entrado.
CREATE INDEX IF NOT EXISTS ix_intervenciones_cliente
    ON intervenciones (cliente_telefono, estado);


-- PRECIO DEL DÍA: cuando la dueña dice cuánto está HOY un producto de precio variable,
-- se guarda con la FECHA. El bot lo usa el resto del día (no la molesta dos veces por lo
-- mismo) y mañana se lo vuelve a preguntar. Un precio viejo JAMÁS se reutiliza.
CREATE TABLE IF NOT EXISTS precio_dia (
    id          SERIAL PRIMARY KEY,
    producto_id INTEGER       NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
    precio      NUMERIC(10,2) NOT NULL,
    nota        TEXT,                     -- ej. "1kg" (mientras no existan las variantes)
    fecha       DATE          NOT NULL DEFAULT CURRENT_DATE,
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT now()
);

-- Un solo precio por producto y por día (si lo corrige, se sobreescribe).
CREATE UNIQUE INDEX IF NOT EXISTS ux_precio_dia_producto_fecha
    ON precio_dia (producto_id, fecha);
