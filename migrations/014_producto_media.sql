-- 014_producto_media.sql — Fotos y videos de cada producto (el archivo vive en R2).
-- En la BD se guarda SOLO la ruta (clave) del archivo, no el archivo. ADITIVA e idempotente.
CREATE TABLE IF NOT EXISTS producto_media (
  id SERIAL PRIMARY KEY,
  producto_id INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
  tipo TEXT NOT NULL DEFAULT 'imagen',
  clave TEXT NOT NULL,
  orden INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_producto_media_producto ON producto_media(producto_id);
