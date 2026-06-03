-- 003_pagos.sql — Capa de cobro por Pago Movil.
-- ADITIVA: no toca 001 ni 002. SQL plano e idempotente (sin bloques DO $$,
-- porque el cargador init_db separa los statements por ';').
-- Se puede ejecutar varias veces sin error.

-- Tabla de pagos: un registro por comprobante reportado, ligado a su pedido.
CREATE TABLE IF NOT EXISTS pagos (
  id SERIAL PRIMARY KEY,
  pedido_id INTEGER NOT NULL REFERENCES pedidos(id),
  metodo TEXT NOT NULL DEFAULT 'pago_movil',
  monto_usd NUMERIC(10,2),
  monto_bs NUMERIC(14,2),
  tasa_usada NUMERIC(14,4),
  referencia TEXT,
  comprobante_media_id TEXT UNIQUE,
  comprobante_url TEXT,
  estado TEXT NOT NULL DEFAULT 'reportado',
  confirmado_por TEXT,
  motivo_rechazo TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  CONSTRAINT ck_pago_estado CHECK (estado IN ('reportado','confirmado','rechazado'))
);

CREATE INDEX IF NOT EXISTS idx_pagos_pedido ON pagos(pedido_id);
CREATE INDEX IF NOT EXISTS idx_pagos_estado ON pagos(estado);

-- Ampliar los estados de pedido: suma 'esperando_pago' y 'pagado' a los 5 originales.
-- El CHECK de 001 quedo sin nombre (Postgres lo llamo 'pedidos_estado_check') y el
-- ORM lo nombra 'ck_pedido_estado'; soltamos AMBOS antes de recrearlo con nombre estable.
ALTER TABLE pedidos DROP CONSTRAINT IF EXISTS pedidos_estado_check;
ALTER TABLE pedidos DROP CONSTRAINT IF EXISTS ck_pedido_estado;
ALTER TABLE pedidos ADD CONSTRAINT ck_pedido_estado
  CHECK (estado IN ('pendiente','confirmado','preparando','entregado','cancelado','esperando_pago','pagado'));

-- Datos de cobro reales (editables sin redeploy desde la tabla configuracion).
-- dueno_telefono y tasa_manual arrancan vacios: se setean en pruebas/produccion.
INSERT INTO configuracion (clave, valor) VALUES
  ('pago_movil_banco', 'Banco de Venezuela (0102)'),
  ('pago_movil_cedula', 'V-23.776.448'),
  ('pago_movil_telefono', '+58 426-4399792'),
  ('pago_movil_titular', 'Maired Hernández'),
  ('dueno_telefono', ''),
  ('tasa_fuente', 'cotizave'),
  ('tasa_manual', '')
ON CONFLICT (clave) DO NOTHING;
