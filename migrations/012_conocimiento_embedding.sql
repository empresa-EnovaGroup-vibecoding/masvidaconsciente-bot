-- 012_conocimiento_embedding.sql — Búsqueda SEMÁNTICA (por significado) del Conocimiento.
-- Guarda el "embedding" (vector de significado) de cada entrada en una columna JSONB.
-- ADITIVA e idempotente: solo agrega una columna; NO toca datos ni otras tablas.
-- NO requiere pgvector: el parecido se calcula en el código (la base es de cientos de
-- entradas, no millones). Si más adelante crece a miles, se migra a pgvector sin rehacer.
ALTER TABLE conocimiento ADD COLUMN IF NOT EXISTS embedding JSONB;
