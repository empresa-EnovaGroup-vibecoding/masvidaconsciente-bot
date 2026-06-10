-- 006_conocimiento.sql — Base de conocimiento del negocio (FAQ + info). ADITIVA e idempotente.
CREATE TABLE IF NOT EXISTS conocimiento (
  id SERIAL PRIMARY KEY,
  categoria TEXT,
  titulo TEXT NOT NULL,
  contenido TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conocimiento_categoria ON conocimiento(categoria);
