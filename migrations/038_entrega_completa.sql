-- 038 · LA ENTREGA COMPLETA: franja y referencia por fin tienen casilla (6-sep, Maired).
--
-- 🔴 POR QUÉ (los dos guiones del 6-sep en pruebas, GLM y Sonnet, MISMO hueco): el cliente dijo
-- "a las 8 am" y el bot respondió "anotado" — y no anotó en NINGUNA parte: `pedidos.notas`
-- vacío, `clientes.notas` vacío. La hora vivía solo en el chat. Y los dos registraron un
-- DELIVERY a "Barquisimeto centro" sin pedir jamás un punto de referencia: el lunes el
-- repartidor no tiene a dónde ir. Es la misma clase del método de pago (035): LA VENTANA SIN
-- ESTADO — un dato que el cliente da y que no tiene dónde caer se pierde o se inventa.
--
-- La regla de negocio que decide Maired (6-sep): el cliente NO elige una hora, elige una FRANJA
-- (las define la dueña en Configuración, clave `franjas_entrega`; sin configurar, las de
-- fábrica). La HORA EXACTA la pone Whuilianny según su ruta y la confirma ella. Y un delivery
-- sin referencia NO se cobra (`generar_datos_pago` lo exige).
--
--   · `entrega_franja`     = la franja elegida, TAL CUAL está en la lista cerrada de la dueña.
--   · `entrega_referencia` = dirección / punto de referencia, con las palabras del cliente.
--
-- Nullable a propósito: los pedidos viejos quedan en NULL y todo se comporta como siempre.

ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS entrega_franja     TEXT;
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS entrega_referencia TEXT;
