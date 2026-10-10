-- 042 · EL TOTAL QUE DIO WHUILIANNY (SESIONES (48), 10-oct-2026, decisión de Maired).
--
-- Whuilianny entra al chat y cierra un precio a mano ("son 36, nena, te lo llevo mañana"). Después
-- Alejandra sigue la venta, y hasta hoy el pedido salía a precio de catálogo. Esta casilla guarda el
-- total que ELLA dijo: si existe, es el que se cobra (no se recalcula con el catálogo ni se le suma
-- el envío) y NO lleva el 20% de pagar en dólares — su precio ya es el final (Maired, 10-oct).
-- Solo se llena si el número aparece en sus mensajes (`app/agent/montos_duena.py`).
-- Aditiva e idempotente; NULL = precio normal del catálogo.
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS total_acordado NUMERIC(10, 2);
