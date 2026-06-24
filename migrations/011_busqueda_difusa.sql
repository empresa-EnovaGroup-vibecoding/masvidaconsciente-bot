-- 011_busqueda_difusa.sql — Búsqueda tolerante a errores de tipeo y acentos.
-- Activa pg_trgm (similitud por trigramas) + unaccent (ignora tildes). Así el bot
-- encuentra "galletas" aunque el cliente escriba "galetas", y "limón" aunque ponga
-- "limon". ADITIVA e idempotente: solo añade extensiones e índices; NO toca datos,
-- NO altera tablas viejas. Ambas extensiones vienen incluidas en la imagen postgres.
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Índices GIN de trigramas: hacen rápida la búsqueda difusa al escalar (cientos/miles
-- de productos, como los 400 de otro cliente). Si no existen, igual funciona (más lento).
CREATE INDEX IF NOT EXISTS idx_productos_nombre_trgm ON productos USING gin (nombre gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_productos_desc_trgm ON productos USING gin (descripcion gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_conocimiento_titulo_trgm ON conocimiento USING gin (titulo gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_conocimiento_contenido_trgm ON conocimiento USING gin (contenido gin_trgm_ops);
