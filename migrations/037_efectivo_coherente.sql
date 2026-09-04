-- 037 · EL EFECTIVO QUE EL NEGOCIO OFRECE TAMBIÉN EXISTE EN SU FUENTE DE VERDAD.
--
-- La personalidad, el cálculo del 20% y el matcher aceptaban efectivo en dólares, pero la
-- tabla `metodos_pago` no tenía esa fila. Como la herramienta usa esa tabla como vocabulario
-- cerrado, el bot respondía "efectivo no está disponible" aunque el negocio sí lo acepta.
--
-- Idempotente: si la dueña ya creó una fila de efectivo (activa o no), no la duplica ni cambia
-- su decisión. Solo completa la ausencia constatada. No lleva datos bancarios porque no existen.

INSERT INTO metodos_pago (tipo, titulo, instrucciones, activo, orden)
SELECT
  'efectivo',
  'Efectivo en dólares',
  'Pago en dólares en efectivo al recibir o retirar el pedido. No requiere datos bancarios ni captura de comprobante.',
  TRUE,
  COALESCE((SELECT MAX(orden) + 1 FROM metodos_pago), 0)
WHERE NOT EXISTS (
  SELECT 1
  FROM metodos_pago
  WHERE LOWER(TRIM(tipo)) = 'efectivo'
     OR LOWER(TRIM(titulo)) LIKE 'efectivo%'
);
