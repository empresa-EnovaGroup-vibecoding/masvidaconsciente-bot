-- 023 — EL DELIVERY: las ZONAS y su COSTO (el "código de barras" del envío)
--
-- 🔴 POR QUÉ EXISTE (caso REAL con una clienta, 2026-07-13 21:26):
--    Una clienta quería un producto de $20 con delivery. El bot escribió:
--        "El total en bolívares es de $23 USD a la tasa BCV del día."
--    Sumó $20 + $3 de cabeza, llamó bolívares a unos dólares, y NO había ningún pedido en la base.
--    ¿Por qué improvisó? **Porque el sistema NO SABÍA COBRAR DELIVERY.** No existía ni la tabla.
--    El prompt se lo prohibía DOS VECES ("No sumes el envío al total", "no calcules delivery") y lo
--    hizo igual. Lo que vive solo en el texto se rompe: el dinero va en el CÓDIGO.
--
-- LA DOCTRINA (la misma que ya cerró la fuga de la Kombucha, migración 022):
--    El bot NO ESCRIBE el envío: lo ELIGE de una lista CERRADA (`id_zona`) que el código le
--    inyecta. El COSTO lo pone el código, y el TOTAL lo suma el código. Imposible equivocarse.
--
-- ⚠️⚠️ AVISO A QUIEN TOQUE `scripts/promover_a_produccion.sh`:
--    NO metas `zonas_entrega` en la lista `TABLAS` de ese script mientras use `TRUNCATE … CASCADE`.
--    `pedidos.zona_id` apunta aquí, y un TRUNCATE CASCADE sobre esta tabla **se llevaría por delante
--    `pedidos` y `pagos` de PRODUCCIÓN** (CASCADE ignora el ON DELETE SET NULL). Las zonas se
--    promueven con un INSERT … ON CONFLICT, nunca con TRUNCATE.

CREATE TABLE IF NOT EXISTS zonas_entrega (
    id          SERIAL PRIMARY KEY,
    nombre      TEXT           NOT NULL,           -- "Barquisimeto oeste", "Retiro en La Mendera"
    costo       NUMERIC(10, 2) NOT NULL DEFAULT 0, -- lo que se COBRA por llevarlo ahí
    -- Los barrios/urbanizaciones que caen en esta zona. Es lo que impide que el bot ADIVINE:
    -- si el cliente dice "El Ujano" y no está aquí, el bot NO deduce la zona más barata —
    -- pregunta, o se lo escala a la dueña.
    referencias TEXT,
    -- La zona de RETIRO (el cliente va a buscarlo): sale gratis y no es un envío.
    es_retiro   BOOLEAN        NOT NULL DEFAULT FALSE,
    disponible  BOOLEAN        NOT NULL DEFAULT TRUE,
    orden       INTEGER        NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ    NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ    NOT NULL DEFAULT now()
);

-- Dos zonas con el mismo nombre = el bot elige una al azar y cobra mal (la enfermedad de la
-- Kombucha otra vez). El panel además lo bloquea, pero la BD manda.
CREATE UNIQUE INDEX IF NOT EXISTS ux_zona_nombre ON zonas_entrega (lower(nombre));

-- ─── EL ENVÍO, DENTRO DEL PEDIDO Y CONGELADO ────────────────────────────────────
-- `costo_envio` y `zona_nombre` se COPIAN al pedido, igual que el precio del producto: si mañana
-- la dueña sube el envío de $3 a $4, un pedido de AYER **no puede cambiar de precio solo**.
-- ON DELETE SET NULL: si borra una zona, el pedido viejo conserva su nombre y su costo congelados.
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS zona_id     INTEGER REFERENCES zonas_entrega(id) ON DELETE SET NULL;
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS zona_nombre TEXT;
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS costo_envio NUMERIC(10, 2) NOT NULL DEFAULT 0;

-- ⚠️ NO se siembran zonas aquí A PROPÓSITO. Las zonas de Whuilianny (Barquisimeto $3, el oeste $5,
-- retiro en La Mendera) son SUS datos, no del producto: sembrarlas en una migración se las metería
-- también al PRÓXIMO cliente de la fábrica — que es exactamente el error de la 003 (que le siembra
-- la cuenta bancaria REAL de Maired a todo cliente nuevo). Las zonas las carga la dueña en el panel.
