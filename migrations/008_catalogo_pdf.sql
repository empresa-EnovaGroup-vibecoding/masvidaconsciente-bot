-- 008: guarda el catálogo PDF EN LA BASE DE DATOS (persistente entre redeploys),
-- en vez del disco, que el servidor (Coolify) borra en cada redeploy.
-- Tabla de una sola fila (id=1). Aditiva e idempotente.
CREATE TABLE IF NOT EXISTS catalogo_pdf (
    id INTEGER PRIMARY KEY DEFAULT 1,
    contenido BYTEA NOT NULL,
    actualizado TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT catalogo_pdf_unico CHECK (id = 1)
);
