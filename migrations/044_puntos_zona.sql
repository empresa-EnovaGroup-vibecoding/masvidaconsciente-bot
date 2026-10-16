-- 044 · LOS SECTORES DE CADA ZONA, EN EL MAPA (SESIONES (53), 16-oct-2026, decisión de Maired).
--
-- Objetivo de Whuilianny: que la clienta mande su ubicación de WhatsApp y Alejandra sepa sola la zona del
-- envío. En internet no hay un mapa de "Barquisimeto Este/Centro/Oeste" (es la organización del repartidor),
-- así que cada zona lista sus sectores en el panel (`referencias`) y el sistema los ubica UNA vez en
-- OpenStreetMap. `puntos` = [{"sector", "lat", "lon"}]; `sin_ubicar` = los que no se encontraron (el panel
-- los marca para corregir el nombre). Aditiva e idempotente; NULL = sin ubicar (todo sigue como antes).
ALTER TABLE zonas_entrega ADD COLUMN IF NOT EXISTS puntos JSONB;
ALTER TABLE zonas_entrega ADD COLUMN IF NOT EXISTS sin_ubicar JSONB;
ALTER TABLE zonas_entrega ADD COLUMN IF NOT EXISTS puntos_at TIMESTAMPTZ;
