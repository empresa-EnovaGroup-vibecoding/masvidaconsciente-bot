-- 036_foto_principal.sql — LA FOTO PRINCIPAL DE CADA PRODUCTO (aditiva e idempotente).
--
-- POR QUÉ (decisión de producto del 2026-09-03, pedida por Maired tras la prueba en vivo):
-- el envío PROACTIVO de fotos pasa de "hasta 3 del producto" a "UNA, la que lo representa".
-- Hoy nadie puede decir CUÁL foto manda: la Torta baja en carbohidratos tiene 5 y la que
-- encabeza es simplemente la primera que se subió (orden de subida, router.py). Esta columna
-- le da a la dueña la palanca: la foto marcada es la cara del producto — la que el bot manda
-- proactivo, la primera de la lista del panel y la miniatura del catálogo.
--
-- FALSE = SIN MARCAR, que es lo que quedan las filas de hoy. Cero backfill y cero adivinanza
-- (nadie sabe cuál foto prefiere la dueña): mientras un producto no tenga principal marcada,
-- el código sigue usando la primera por (orden, id) — EXACTAMENTE lo de siempre. La columna
-- solo agrega la palanca; no cambia nada hasta que la dueña la use.
--
-- EL ÍNDICE PARCIAL ÚNICO: un producto tiene UNA principal como mucho. La fila duplicada solo
-- la puede impedir la base de datos — es el único sitio donde la comprobación y la escritura
-- son un mismo acto (doctrina de la 026). Como la columna nace FALSE en todas las filas, el
-- índice se crea sin limpieza previa. Nombre canónico y para siempre: IF NOT EXISTS compara
-- el NOMBRE, no la definición (lección de la 034).
--
-- Nada de bloques DO $$ ni un solo ';' dentro de un literal: `_statements` parte por ';'
-- (app/init_db.py). Misma regla que la 029.

ALTER TABLE producto_media ADD COLUMN IF NOT EXISTS es_principal BOOLEAN NOT NULL DEFAULT FALSE;

CREATE UNIQUE INDEX IF NOT EXISTS ux_media_principal_por_producto ON producto_media (producto_id) WHERE es_principal;
