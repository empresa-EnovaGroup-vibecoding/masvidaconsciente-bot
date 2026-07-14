-- 024: EL ROL DEL USUARIO — proveedora (Enova) vs dueña (la clienta).
--
-- POR QUÉ. Hoy NO hay roles: la tabla `usuarios` no tiene columna de rol y el JWT solo lleva el
-- email, así que cualquiera que entre al panel ve y edita TODO. Pero hay palancas que son de la
-- PROVEEDORA y no de la clienta — el selector de modelo de IA ya está documentado así en
-- CLAUDE.md §5 ("palanca de PROVEEDOR, no de la clienta"), y en la fase 4 se le suma el
-- interruptor de las herramientas del agente. La dueña no debería poder apagarle tools a su
-- propio bot sin querer.
--
-- ⚠️ REGLAS DE MIGRACIÓN DE ESTA CASA (fase 0):
--   · IDEMPOTENTE: se aplica una sola vez (schema_migrations), pero debe poder re-correrse.
--   · SIN ';' dentro de literales y SIN bloques DO $$ … $$: `init_db._statements` parte por ';'
--     y los rompería.
--   · ADITIVA: no toca ninguna migración anterior.
--
-- Los valores van SIN acentos ('duena', no 'dueña') a propósito: son identificadores que viajan
-- por el JWT, la API y el panel. El texto bonito ("Dueña") se pinta en el frontend.

ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS rol TEXT NOT NULL DEFAULT 'duena';

-- El candado: solo existen dos roles. Se sueltan los dos nombres posibles del constraint
-- (el auto-generado por Postgres y el nuestro) porque no se puede usar un DO dinámico.
ALTER TABLE usuarios DROP CONSTRAINT IF EXISTS usuarios_rol_check;
ALTER TABLE usuarios DROP CONSTRAINT IF EXISTS ck_usuario_rol;
ALTER TABLE usuarios ADD CONSTRAINT ck_usuario_rol CHECK (rol IN ('proveedora', 'duena'));

CREATE INDEX IF NOT EXISTS idx_usuarios_rol ON usuarios (rol);

-- OJO: aquí NO se marca a nadie como 'proveedora'. El correo del admin vive en la variable de
-- entorno ADMIN_EMAIL y el SQL no puede leerla. Lo hace `_crear_admin()` (app/init_db.py), que
-- fuerza rol='proveedora' para ADMIN_EMAIL EN CADA ARRANQUE. Eso es, además, la red anti-bloqueo:
-- por mucho que alguien se degrade a sí mismo por la API, el siguiente arranque lo restaura.
