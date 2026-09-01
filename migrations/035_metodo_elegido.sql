-- 035 · EL MÉTODO DE PAGO POR FIN TIENE SU CASILLA (rama B del plan "que no repregunte").
--
-- 🔴 POR QUÉ (confirmado EN VIVO por Maired, 31-ago): "vuelve a preguntar los métodos de pago
-- cuando ya mandó los datos". La causa era estructural, no del modelo: la elección del cliente
-- ("te pago por Zelle") NO SE GUARDABA EN NINGUNA PARTE — `pedidos` no tenía columna de método y
-- `Pago.metodo` nace recién cuando llega el comprobante. En esa ventana, `generar_datos_pago`
-- devolvía SIEMPRE los datos de TODOS los métodos y un `resumen_cobro` re-pitcheando las DOS
-- monedas, con la nota ordenando copiarlo EXACTO: reapertura MANDADA por la herramienta.
--
-- El test de la clase (LA VENTANA SIN ESTADO): ¿ese dato tiene casilla? Ahora la tiene.
--
-- Mismo patrón que la cotización (027) y la zona (023): el dato va CONGELADO en el pedido.
-- Se guardan las DOS cosas del método elegido:
--   · `metodo_elegido`      = el TÍTULO tal cual estaba en `metodos_pago` al elegir ("Zelle").
--     Es lo que se le enseña al modelo en ESTADO DEL CLIENTE ("Ya eligió pagar por: Zelle").
--   · `metodo_elegido_tipo` = el `tipo` de esa fila (pago_movil | banco | zelle | binance |
--     efectivo | otro). De aquí sale la MONEDA (Bs o USD) sin parsear el título — si la dueña
--     renombra o borra el método después, el pedido conserva lo que se acordó.
--
-- Nullable a propósito: los pedidos viejos y los que aún no eligen quedan en NULL y todo se
-- comporta como siempre. El validador del comprobante NO se toca: sigue validando por MONTO.

ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS metodo_elegido      TEXT;
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS metodo_elegido_tipo TEXT;
