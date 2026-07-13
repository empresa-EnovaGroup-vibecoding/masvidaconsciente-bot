-- 022 — PRODUCTO · TAMAÑO · OPCIÓN — LA ESTRUCTURA (aditiva e idempotente)
--
-- El "código de barras" del cobro. Ver PRP-producto-variantes.md (v2, auditado: 4 revisores,
-- 51 hallazgos → 34 reales).
--
-- POR QUÉ: hoy hay DOS productos llamados "Kombucha" (350ml $4 · 700ml $7). El buscador
-- devuelve siempre el primero ⇒ SIEMPRE COBRA $4. Fuga real: $3 por venta. Y si piden la foto
-- de la de 700ml, manda la de 350ml. La causa no es el modelo: es que **el precio vive pegado
-- al producto**, y la dueña no tuvo más remedio que crear dos productos con el mismo nombre.
--
-- LA LÍNEA QUE SEPARA LOS NIVELES ES EL DINERO:
--   PRODUCTO = qué ES (nombre ÚNICO, ficha, ingredientes).
--   TAMAÑO   = lo que se COBRA (presentación + precio + sabores + foto + agotado propios).
--   OPCIÓN   = lo que el cliente escoge y NO mueve el precio (relleno, masa) → va en el pedido.
--
-- ⚠️ ESTE ARCHIVO SOLO CREA LA ESTRUCTURA. Los datos (backfill + fusión de la Kombucha) van en
--    `022b_variantes_datos.sql`, que corre DESPUÉS del seed del catálogo. Si el backfill
--    corriera aquí, en una BD NUEVA (un cliente nuevo) vería `productos` VACÍA → cero tamaños
--    → el bot no podría vender NADA, y sin un solo error en el log.
--
-- ⚠️ NADA de bloques `DO $$`: init_db parte el .sql por ';'.

-- ─── 1. LOS TAMAÑOS ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS producto_variantes (
    id SERIAL PRIMARY KEY,
    producto_id INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
    -- NOT NULL a propósito: si fuera nullable, Postgres trataría dos NULL como DISTINTOS y
    -- dejaría pasar dos tamaños "sin nombre" con precios distintos. El único ('única') es el
    -- caso normal: 25 de los 28 productos tienen un solo tamaño.
    presentacion TEXT NOT NULL DEFAULT 'única',
    precio NUMERIC(10, 2),          -- NULL = precio del día (lo pone la dueña cada día)
    sabores TEXT,                   -- los sabores son DEL TAMAÑO (la de 700ml tiene más)
    disponible BOOLEAN NOT NULL DEFAULT TRUE,
    orden INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_variante_producto_presentacion
    ON producto_variantes (producto_id, presentacion);

-- ─── 2. LA FOTO SABE DE QUÉ TAMAÑO ES ──────────────────────────────────────────
-- ON DELETE SET NULL, JAMÁS CASCADE: `borrar_media` es el ÚNICO sitio que borra el archivo en
-- Cloudflare R2. Si al borrar un tamaño se borrara la fila de la foto, el archivo quedaría
-- huérfano en R2 ocupando espacio para siempre. NULL = foto del producto, sin tamaño (neutra).
ALTER TABLE producto_media ADD COLUMN IF NOT EXISTS variante_id INTEGER;
ALTER TABLE producto_media DROP CONSTRAINT IF EXISTS fk_media_variante;
ALTER TABLE producto_media ADD CONSTRAINT fk_media_variante
    FOREIGN KEY (variante_id) REFERENCES producto_variantes(id) ON DELETE SET NULL;

-- ─── 3. EL PRECIO DEL DÍA, POR TAMAÑO ──────────────────────────────────────────
-- Lo pidió Maired textualmente: "el precio del día también tiene que guardarse por tamaño".
-- Y el índice viejo `(producto_id, fecha)` lo IMPEDÍA: al poner el precio de la torta de 500g
-- rechazaba el de la de 1kg del mismo día. Era el caso estrella del problema.
ALTER TABLE precio_dia ADD COLUMN IF NOT EXISTS variante_id INTEGER;
ALTER TABLE precio_dia DROP CONSTRAINT IF EXISTS fk_preciodia_variante;
ALTER TABLE precio_dia ADD CONSTRAINT fk_preciodia_variante
    FOREIGN KEY (variante_id) REFERENCES producto_variantes(id) ON DELETE CASCADE;
DROP INDEX IF EXISTS ux_precio_dia_producto_fecha;
CREATE UNIQUE INDEX IF NOT EXISTS ux_precio_dia_variante_fecha
    ON precio_dia (variante_id, fecha) WHERE variante_id IS NOT NULL;
