-- 041 · OTROS NOMBRES DEL PRODUCTO (SESIONES (47), 8-oct-2026, idea de Maired).
--
-- Whuilianny y las clientas no siempre llaman a un producto por su nombre del catálogo: "galletas
-- pequeñas choco" son las Mini New York, "yogur" es el Yogurt Kéfirado. En la medición sobre las
-- conversaciones reales eso dejaba ventas claras sin entender. Cada producto guarda aquí sus otros
-- nombres (separados por coma o uno por línea), editables en su ficha del panel; los lee el lector
-- del expediente y el buscador del bot. Aditiva e idempotente; NULL = sin otros nombres.
ALTER TABLE productos ADD COLUMN IF NOT EXISTS apodos TEXT;
