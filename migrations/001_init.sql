-- masvidaconsciente-bot — esquema inicial
-- Todas las tablas. Ejecutar una vez al preparar un cliente nuevo.

CREATE TABLE IF NOT EXISTS productos (
  id SERIAL PRIMARY KEY,
  nombre TEXT NOT NULL,
  categoria TEXT,
  descripcion TEXT,
  precio NUMERIC(10,2),
  presentacion TEXT,
  disponible BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS clientes (
  id SERIAL PRIMARY KEY,
  telefono TEXT UNIQUE NOT NULL,
  nombre TEXT,
  primera_interaccion TIMESTAMPTZ DEFAULT NOW(),
  ultima_interaccion TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pedidos (
  id SERIAL PRIMARY KEY,
  cliente_telefono TEXT NOT NULL REFERENCES clientes(telefono),
  estado TEXT NOT NULL DEFAULT 'pendiente'
    CHECK (estado IN ('pendiente','confirmado','preparando','entregado','cancelado')),
  items JSONB NOT NULL,
  total NUMERIC(10,2),
  notas TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS mensajes (
  id SERIAL PRIMARY KEY,
  message_id TEXT UNIQUE,
  cliente_telefono TEXT NOT NULL,
  rol TEXT NOT NULL CHECK (rol IN ('user','assistant')),
  contenido TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS usuarios (
  id SERIAL PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  nombre TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS configuracion (
  clave TEXT PRIMARY KEY,
  valor TEXT,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pedidos_cliente ON pedidos(cliente_telefono);
CREATE INDEX IF NOT EXISTS idx_pedidos_fecha ON pedidos(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mensajes_cliente ON mensajes(cliente_telefono, created_at);
