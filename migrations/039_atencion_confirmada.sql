-- Las entradas existentes conservan su texto; su alcance debe revisarse antes de responder.
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS borrador_confirmado JSONB;
ALTER TABLE conocimiento ADD COLUMN IF NOT EXISTS tema_confirmado TEXT;
ALTER TABLE conocimiento ADD COLUMN IF NOT EXISTS producto_id INTEGER REFERENCES productos(id);
ALTER TABLE conocimiento ADD COLUMN IF NOT EXISTS confirmado BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE intervenciones ADD COLUMN IF NOT EXISTS acuse_intentado BOOLEAN NOT NULL DEFAULT FALSE;
