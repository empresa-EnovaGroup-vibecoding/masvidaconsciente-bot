-- 009_metodos_pago.sql — ADITIVA e idempotente. NO toca migraciones viejas.
-- Varias cuentas/métodos de pago (Pago Móvil, Banesco, Binance, Zelle, Efectivo…)
-- editables desde el panel, por cliente. SQL plano (sin DO $$): el splitter de
-- init_db parte por ';'.

CREATE TABLE IF NOT EXISTS metodos_pago (
  id SERIAL PRIMARY KEY,
  tipo TEXT NOT NULL DEFAULT 'pago_movil',   -- pago_movil | banco | binance | zelle | efectivo | otro
  titulo TEXT NOT NULL,                       -- "Pago Móvil - Banco de Venezuela"
  titular TEXT,
  banco TEXT,
  telefono TEXT,
  cedula TEXT,
  correo TEXT,                                -- Zelle / PayPal
  wallet TEXT,                                -- Binance / USDT
  instrucciones TEXT,                         -- texto libre que el bot muestra
  activo BOOLEAN NOT NULL DEFAULT TRUE,
  orden INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_metodos_pago_activo ON metodos_pago(activo);

-- Siembra el Pago Móvil actual (de configuracion) como primer método, SOLO si la
-- tabla está vacía, para no perder lo que ya estaba configurado.
INSERT INTO metodos_pago (tipo, titulo, titular, banco, telefono, cedula, activo, orden)
SELECT 'pago_movil', 'Pago Móvil',
  (SELECT valor FROM configuracion WHERE clave = 'pago_movil_titular'),
  (SELECT valor FROM configuracion WHERE clave = 'pago_movil_banco'),
  (SELECT valor FROM configuracion WHERE clave = 'pago_movil_telefono'),
  (SELECT valor FROM configuracion WHERE clave = 'pago_movil_cedula'),
  TRUE, 0
WHERE NOT EXISTS (SELECT 1 FROM metodos_pago);
