-- 022b — PRODUCTO · TAMAÑO — LOS DATOS (corre DESPUÉS del seed del catálogo)
--
-- ⚠️ IDEMPOTENTE EN DATOS, no solo en DDL: `init_db` re-ejecuta este .sql COMPLETO en CADA
--    arranque del contenedor (no hay tabla de migraciones aplicadas). Sin los `NOT EXISTS`,
--    cada redespliegue DUPLICARÍA los tamaños y resucitaría precios viejos.
--
-- ⚠️ Y corre DESPUÉS del seed a propósito: en una BD NUEVA (un cliente nuevo) las migraciones
--    corren ANTES de sembrar el catálogo. Un backfill ahí vería `productos` VACÍA ⇒ CERO
--    tamaños ⇒ el bot no podría vender NADA, y sin un solo error en el log.

-- ─── 1. Un tamaño ÚNICO para los productos normales ────────────────────────────
-- (25 de 28.) Se copia también `disponible`: sin eso, el Chucrut que está AGOTADO renacería
-- vendible y el bot lo ofrecería.
INSERT INTO producto_variantes (producto_id, presentacion, precio, disponible, orden)
SELECT p.id,
       COALESCE(NULLIF(TRIM(p.presentacion), ''), 'única'),
       p.precio,
       p.disponible,
       0
FROM productos p
WHERE COALESCE(p.presentacion, '') NOT LIKE '%/%'
  AND p.nombre NOT IN (SELECT nombre FROM productos GROUP BY nombre HAVING count(*) > 1)
  AND NOT EXISTS (SELECT 1 FROM producto_variantes v WHERE v.producto_id = p.id);

-- ─── 2. Los tamaños que estaban metidos en el TEXTO ────────────────────────────
-- Tortas keto y torta baja tienen `presentacion = '250g / 500g / 1kg'`: TRES tamaños escritos
-- en una sola cadena. Un backfill genérico habría creado UNA variante basura llamada
-- "250g / 500g / 1kg", con id válido, y el bot se la habría ofrecido al cliente.
-- El precio queda NULL: son productos de PRECIO DEL DÍA (a propósito, no es un olvido).
INSERT INTO producto_variantes (producto_id, presentacion, precio, disponible, orden)
SELECT p.id, TRIM(t.pres), NULL, p.disponible, (t.ord)::int - 1
FROM productos p
CROSS JOIN LATERAL unnest(string_to_array(p.presentacion, '/')) WITH ORDINALITY AS t(pres, ord)
WHERE COALESCE(p.presentacion, '') LIKE '%/%'
  AND TRIM(t.pres) <> ''
  AND NOT EXISTS (
      SELECT 1 FROM producto_variantes v
      WHERE v.producto_id = p.id AND v.presentacion = TRIM(t.pres)
  );

-- ─── 3. LA FUSIÓN DE LOS NOMBRES REPETIDOS (la Kombucha) ───────────────────────
-- ORDEN OBLIGATORIO. `producto_media` tiene ON DELETE CASCADE: borrar el producto perdedor
-- ANTES de re-apuntar su foto la BORRARÍA — y sin dar ningún error.
-- Se hace por DATOS (nombre repetido), no por ids a mano: los ids pueden no coincidir entre
-- servidores, y un `DELETE ... WHERE nombre='Kombucha'` borraría LAS DOS.

-- 3.1 La ficha del que se va se copia al que se queda (solo lo que le FALTE).
--     La de 700ml tiene ficha ("1 mes en nevera", "no apta diabéticos") y la de 350ml la tiene
--     VACÍA: sin esto, al fusionarlas se perdería.
UPDATE productos sup
SET duracion          = COALESCE(sup.duracion, o.duracion),
    se_congela        = COALESCE(sup.se_congela, o.se_congela),
    apto_diabeticos   = COALESCE(sup.apto_diabeticos, o.apto_diabeticos),
    info              = COALESCE(sup.info, o.info),
    categoria         = COALESCE(sup.categoria, o.categoria),
    dias_anticipacion = GREATEST(COALESCE(sup.dias_anticipacion, 0), COALESCE(o.dias_anticipacion, 0))
FROM (
    SELECT nombre,
           MIN(id) AS superviviente,
           MAX(duracion) AS duracion,
           MAX(se_congela) AS se_congela,
           MAX(apto_diabeticos) AS apto_diabeticos,
           MAX(info) AS info,
           MAX(categoria) AS categoria,
           MAX(dias_anticipacion) AS dias_anticipacion
    FROM productos
    GROUP BY nombre
    HAVING count(*) > 1
) o
WHERE sup.id = o.superviviente;

-- 3.2 Cada producto repetido se convierte en un TAMAÑO del que se queda, con SU precio y SUS
--     sabores (la de 700ml tiene cúrcuma y flor de jamaica; la de 350ml, no).
INSERT INTO producto_variantes (producto_id, presentacion, precio, sabores, disponible, orden)
SELECT s.superviviente,
       COALESCE(NULLIF(TRIM(p.presentacion), ''), 'única'),
       p.precio,
       NULLIF(TRIM(SUBSTRING(p.descripcion FROM 'Sabores:[[:space:]]*([^\n\r]*)')), ''),
       p.disponible,
       (ROW_NUMBER() OVER (PARTITION BY p.nombre ORDER BY p.id))::int - 1
FROM productos p
JOIN (
    SELECT nombre, MIN(id) AS superviviente
    FROM productos GROUP BY nombre HAVING count(*) > 1
) s ON s.nombre = p.nombre
WHERE NOT EXISTS (
    SELECT 1 FROM producto_variantes v
    WHERE v.producto_id = s.superviviente
      AND v.presentacion = COALESCE(NULLIF(TRIM(p.presentacion), ''), 'única')
);

-- 3.3 LAS FOTOS SE MUDAN **ANTES** DEL BORRADO, y cada una se lleva SU tamaño.
--     El dato existe con certeza (hoy son dos filas distintas): la foto de la fila del 350ml
--     ES la del 350ml. Cero adivinanza, cero clics para la dueña.
UPDATE producto_media m
SET producto_id = s.superviviente,
    variante_id = v.id
FROM productos p
JOIN (
    SELECT nombre, MIN(id) AS superviviente
    FROM productos GROUP BY nombre HAVING count(*) > 1
) s ON s.nombre = p.nombre
JOIN producto_variantes v
  ON v.producto_id = s.superviviente
 AND v.presentacion = COALESCE(NULLIF(TRIM(p.presentacion), ''), 'única')
WHERE m.producto_id = p.id;

-- 3.4 AHORA SÍ: se borra el producto duplicado, POR ID (jamás por nombre: borraría los dos).
DELETE FROM productos
WHERE id IN (
    SELECT p.id
    FROM productos p
    JOIN (
        SELECT nombre, MIN(id) AS superviviente
        FROM productos GROUP BY nombre HAVING count(*) > 1
    ) s ON s.nombre = p.nombre
    WHERE p.id <> s.superviviente
);

-- ─── 4. LOS SABORES bajan al TAMAÑO ────────────────────────────────────────────
-- Sin esto, tras la fusión "quiero la kombucha de flor de jamaica" DEJA DE ENCONTRAR NADA, y
-- la regla antiinvención obliga al bot a decir "de eso no tengo" sobre algo que el negocio SÍ
-- vende. (Los ya rellenados en 3.2 no se tocan: `v.sabores IS NULL`.)
UPDATE producto_variantes v
SET sabores = s.sabores
FROM (
    SELECT p.id AS producto_id,
           NULLIF(TRIM(SUBSTRING(p.descripcion FROM 'Sabores:[[:space:]]*([^\n\r]*)')), '') AS sabores
    FROM productos p
) s
WHERE v.producto_id = s.producto_id
  AND v.sabores IS NULL
  AND s.sabores IS NOT NULL;

-- ─── 5. El precio del día que hubiera, colgado de su tamaño ────────────────────
-- (Hoy hay 0 filas; queda por si acaso, y solo cuando NO hay ambigüedad: un solo tamaño.)
UPDATE precio_dia pd
SET variante_id = v.id
FROM producto_variantes v
WHERE v.producto_id = pd.producto_id
  AND pd.variante_id IS NULL
  AND (SELECT count(*) FROM producto_variantes v2 WHERE v2.producto_id = pd.producto_id) = 1;
