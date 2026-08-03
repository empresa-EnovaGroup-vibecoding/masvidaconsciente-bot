-- 030_conocimiento_activo.sql — RETIRAR SIN BORRAR (aditiva e idempotente).
--
-- 🔴 POR QUÉ. `conocimiento` es lo que la dueña escribe y el bot repite. Hoy la tabla mezcla
-- DOS cosas que no son lo mismo: HECHOS del negocio ("pagando en divisas hay descuento") y
-- ÓRDENES a la máquina ("Di algo como que no, el precio es el mismo. No des detalles"). Las
-- órdenes entran CRUDAS al hilo del modelo dentro de un campo que se llama `info`
-- (`buscar_info` arma {"tema","info"}, tools.py:1691; agent.py las mete como role='tool'), y la
-- regla @buscar_info (system_prompt.py:84) le ordena "Responde SOLO con lo que devuelva". O sea
-- que el bot recibe una orden en segunda persona PRESENTADA COMO SI FUERA UN DATO.
--
-- Y no hace falta que el cliente pregunte por ellas. Con 9 filas, top-4 y umbral coseno 0.30,
-- `buscar_info` casi nunca devuelve solo lo que se le pidió: "sabores de torta" arrastra "Si
-- preguntan algo que no sabes" (la orden de prometer sin escalar); "cuanto duran los panes"
-- arrastra "Las tortas" con un sabor que NO EXISTE en el catálogo. Se cuelan de polizón.
--
-- ⚠️ LO QUE ESTA MIGRACIÓN NO HACE, A PROPÓSITO: no borra ni una fila y NO REESCRIBE NI UN
-- CARÁCTER del texto de la dueña. Un DELETE o un UPDATE de `contenido` es irreversible — es la
-- misma enfermedad que venimos a curar, con nuestra letra. Retirar = `activo` en FALSE: el texto
-- QUEDA en la base, la dueña lo sigue viendo en el panel (en gris) y lo reenciende con un clic.
-- El typo "almeldra" TAMPOCO se toca: si la masa madre lleva almendra, el problema no es el
-- typo, es que los 5 productos que la llevan tienen un ALÉRGENO DE FRUTO SECO SIN DECLARAR en su
-- ficha. Eso se le pregunta a Whuilianny y se arregla desde el panel, no por SQL.
--
-- ⚠️ El partidor `_statements` de app/init_db.py parte por ';' y es INGENUO: aquí no hay bloques
-- DO $$, ni un solo ';' dentro de un literal, ni ningún ':palabra' que SQLAlchemy pudiera
-- confundir con un bind param. Misma regla que la 028 y la 029.

-- ── 1) EL INTERRUPTOR ──
--
-- NOT NULL DEFAULT TRUE: las 9 filas de hoy siguen encendidas, así que este ALTER solo NO cambia
-- NADA para nadie. Mismo patrón que `productos.disponible` (models.py:65) — la dueña ya sabe
-- apagar un producto sin borrarlo, y este interruptor se lee igual.
--
-- Sin índice, a propósito: la tabla tiene 9 filas y las tres consultas que la leen ya hacen un
-- scan completo (el semántico se trae hasta 500 filas y puntúa en Python). Un índice que nadie
-- necesita es deuda que hay que mantener para siempre.
ALTER TABLE conocimiento ADD COLUMN IF NOT EXISTS activo BOOLEAN NOT NULL DEFAULT TRUE;

-- ── 2) EL ÚNICO HECHO QUE SE PERDERÍA, SALVADO ANTES DE APAGAR NADA ──
--
-- La fila "¿La alulosa cuesta más?" es la ÚNICA de las nueve cuyo hecho no vive en ningún otro
-- sitio: no está en `producto_variantes.precio`, ni en `precio_dia` (vacía), ni en ninguna de las
-- 29 claves de `configuracion`, ni en el prompt. Y la personalidad EMPUJA al cliente hacia esa
-- pregunta ("si es diabético ofrécele la versión con alulosa"), así que retirarla dejaría al bot
-- mudo JUSTO DESPUÉS de haber ofrecido algo. Por eso la fila nueva va en la MISMA transacción que
-- el apagado: sin ventana.
--
-- Se escribe como HECHO, no como orden ("Di algo como que no…"), que es la distinción que toda
-- esta migración defiende. Y sin una sola cifra: así no entra en la lista blanca del dinero
-- (el título viaja al prompt por el índice de Conocimiento).
--
-- ⚠️ IDEMPOTENTE POR `NOT EXISTS`, NO POR `ON CONFLICT DO NOTHING`. Verificado en el taller:
-- `conocimiento` solo tiene la PK sobre `id` (no hay UNIQUE sobre `titulo`), así que un
-- `ON CONFLICT DO NOTHING` no tendría con qué chocar y correr esto dos veces DUPLICARÍA la fila.
--
-- Nace con `embedding` NULL a propósito: `_backfill_embeddings` (init_db.py:217) le calcula el
-- vector en este mismo arranque. Si ese día OpenRouter no tiene saldo, la fila igual se encuentra
-- por texto ("alulosa" está en el título y en el contenido). Degrada, no rompe.
INSERT INTO conocimiento (categoria, titulo, contenido)
SELECT 'politicas',
       'La alulosa no cambia el precio',
       'Endulzar con alulosa no cambia el precio. Es el mismo precio de siempre.'
 WHERE NOT EXISTS (
       SELECT 1 FROM conocimiento WHERE titulo = 'La alulosa no cambia el precio'
 );

-- ── 3) LAS OCHO QUE SE RETIRAN ──
--
-- Verificado fila por fila contra la base del taller. Se van OCHO de nueve:
--   · "Las tortas" y "Si preguntan algo que no sabes" — las dos que se cuelan de polizón en
--     consultas que no tienen nada que ver. La primera anuncia una "torta de piña y pistacho"
--     que NO EXISTE (fusiona el producto 11 con el 30); la segunda viola de frente la regla de
--     system_prompt.py:72 (prometer "te confirmo" SIN llamar a `pedir_ayuda` deja al cliente
--     esperando para siempre), y su TÍTULO ya ensucia el prompt de cada turno dentro de una
--     lista que se llama "TEMAS QUE SÍ SABES".
--   · "¿Hacen envios?", "Qué panes tengo", "Hay descuento pagando en dólares?" y "¿Dónde están
--     ubicados?" — duplicados de lo que la personalidad ya dice en el prompt ESTABLE de CADA
--     turno (puntos 1 MOSTRAR, 5 ENTREGA y 6 PAGO). Retirarlas no le quita NI UN hecho al bot.
--     Y "¿Hacen envios?" además afirma algo FALSO sobre el dinero ("no suma el envío al total"):
--     el código SÍ lo suma (tools.py `total = subtotal_productos + costo_envio`).
--   · "¿La alulosa cuesta más?" — su hecho acaba de quedar salvado arriba.
--   · "¿Se pueden congelar los panes?" — dice "todos los panes duran tres meses". Falso: los tres
--     meses son CONGELADO y ya viven en `productos.se_congela` de los ids 1, 2 y 3; fuera del
--     congelador sus fichas dicen 1 semana, 2 semanas y 1 semana. Es un dato de COMIDA que
--     contradice las fichas, y la propia descripción de `buscar_info` invitaba a preferirlo.
--
-- LA QUE SE QUEDA: "¿De que esta hecha la masa madre?". Es la ÚNICA fuente de que la masa madre
-- lleva almendra, y los 5 productos que la llevan NO declaran ese alérgeno en su ficha. Mientras
-- eso no lo arregle Whuilianny desde el panel, esta fila es lo único que impide que el bot le
-- diga a un alérgico que el Pan de Sándwich no lleva almendra. NO SE TOCA.
--
-- Cada rama calza por TÍTULO **y** por un fragmento del CONTENIDO: en producción no puede apagar
-- una fila distinta que se llame parecido. Verificado en el taller con un SELECT (solo lectura):
-- devuelve EXACTAMENTE los ids 1, 2, 3, 5, 6, 7, 9 y 10 — y NO toca el 4 (la masa madre).
-- El `activo IS TRUE` de arriba es lo que la hace idempotente: al terminar ya no calza ninguna.
UPDATE conocimiento SET activo = FALSE, updated_at = NOW()
 WHERE activo IS TRUE
   AND (
        (titulo ILIKE '%Hacen envios%'       AND contenido ILIKE 'Dos opciones de entrega%')
     OR (titulo ILIKE '%panes tengo%'        AND contenido ILIKE '%los precios no%')
     OR (titulo ILIKE '%Las tortas%'         AND contenido ILIKE '%ponque%')
     OR (titulo ILIKE '%alulosa%'            AND contenido ILIKE 'Di algo como%')
     OR (titulo ILIKE '%descuento pagando%'  AND contenido ILIKE '%Pagando en divisas%')
     OR (titulo ILIKE '%ubicados%'           AND contenido ILIKE 'Le dices algo como%')
     OR (titulo ILIKE '%algo que no sabes%'  AND contenido ILIKE 'Di algo como%')
     OR (titulo ILIKE '%congelar los panes%' AND contenido ILIKE '%todos los panes duran%')
   );

-- ── 4) LA MASA MADRE NO ES UNA PREGUNTA FRECUENTE, ES UN INSUMO ──
--
-- Solo METADATO: cambia dónde se archiva la fila en el panel, NO su texto. Importa porque esa
-- fila deja de ser "una FAQ más" para ser lo que de verdad es — la composición de un insumo que
-- comparten 5 productos —, y porque es la conversación de alérgenos que hay que tener con
-- Whuilianny. El panel aprende la etiqueta "insumos" en el mismo despliegue; si por lo que sea
-- el panel no llega, la sección se titula con la clave cruda y no se rompe nada
-- (conocimiento/page.tsx ya pinta las categorías desconocidas).
UPDATE conocimiento SET categoria = 'insumos', updated_at = NOW()
 WHERE titulo ILIKE '%masa madre%'
   AND coalesce(categoria, '') <> 'insumos';
