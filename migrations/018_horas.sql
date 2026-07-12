-- 018 — LAS HORAS del negocio (aditiva e idempotente)
--
-- El "Horario" quedaba mocho: solo días, sin horas. Maired: "si estamos hablando de un campo
-- de horarios, debe tener todo". Y falta un concepto que SÍ toca el dinero: la HORA DE CORTE.
-- Sin ella, un cliente puede pedir "para hoy mismo" a las 11 de la noche y el bot lo acepta.
--
-- Tres datos, editables por la dueña en el panel (Horario):
--   · hora_apertura / hora_cierre = el horario de ATENCIÓN (másvida: 8:00 a 18:00).
--     El bot NO deja de responder fuera de hora (un mensaje sin responder de noche es una venta
--     que se va), pero lo SABE y ajusta lo que promete.
--   · hora_corte = hasta qué hora se aceptan pedidos para el MISMO día. Pasada esa hora, el
--     código deja de permitir "hoy" y el bot ofrece el próximo día de entrega.
INSERT INTO configuracion (clave, valor) VALUES ('hora_apertura', '08:00')
ON CONFLICT (clave) DO NOTHING;
INSERT INTO configuracion (clave, valor) VALUES ('hora_cierre', '18:00')
ON CONFLICT (clave) DO NOTHING;
INSERT INTO configuracion (clave, valor) VALUES ('hora_corte', '18:00')
ON CONFLICT (clave) DO NOTHING;
