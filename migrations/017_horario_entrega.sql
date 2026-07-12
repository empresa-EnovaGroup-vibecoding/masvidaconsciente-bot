-- 017 — HORARIO DE ENTREGA: un solo lugar para la verdad (aditiva e idempotente)
--
-- Por qué: el horario vivía SOLO en el texto de la personalidad ("lunes a sábado; domingo no
-- hay entregas") y el bot lo ignoraba: probado en vivo, aceptó un pedido "para el domingo",
-- cobró $42 y pidió el comprobante. El parche del 2026-07-12 (buscar la palabra "domingo")
-- tapa el caso obvio pero NO el real: si el cliente dice "para el 19" y el 19 cae domingo,
-- el candado no se entera. La cura es dejar de adivinar con palabras y trabajar con FECHAS
-- contra un CALENDARIO de verdad.
--
-- Arquitectura (una sola fuente por dato):
--   · `dias_entrega` (configuracion) = qué días de la semana se entrega. Lo edita la dueña.
--   · `feriados`                     = fechas sueltas cerradas (viajes, 24-dic…). Las pone ella.
--   · `productos.dias_anticipacion`  = cuántos días necesita ESE producto (0 = mismo día si hay
--                                      stock; las tortas y lo horneado, 2). Lo pone ella por producto.
--   · `pedidos.entrega_fecha`        = la fecha REAL acordada (no un texto que haya que adivinar).
-- El CÓDIGO valida la fecha contra todo eso y calcula la próxima fecha buena; el modelo solo
-- conversa. Y el bot no puede cobrar un pedido sin fecha de entrega.

-- Cuántos días de anticipación necesita cada producto (0 = puede ser el mismo día).
ALTER TABLE productos ADD COLUMN IF NOT EXISTS dias_anticipacion INTEGER NOT NULL DEFAULT 0;

-- La fecha REAL de entrega acordada con el cliente (el "cómo" sigue en `entrega`, texto libre).
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS entrega_fecha DATE;

-- Días sueltos en que NO se entrega (feriados, vacaciones, un viaje).
CREATE TABLE IF NOT EXISTS feriados (
    fecha DATE PRIMARY KEY,
    motivo TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Qué días de la semana se entrega. Valor por defecto = lunes a sábado (másvida no entrega
-- los domingos). Si la dueña ya lo configuró, NO se pisa.
INSERT INTO configuracion (clave, valor)
VALUES ('dias_entrega', 'lunes,martes,miercoles,jueves,viernes,sabado')
ON CONFLICT (clave) DO NOTHING;
