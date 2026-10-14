-- 043 · LA ESCRITURA DE CACHÉ, ANOTADA (SESIONES (50), 14-oct-2026, workflow /claude-api cost-optimize).
--
-- `llamadas_ia.tokens_cache` guarda lo que se LEYÓ de la caché (a 0,1× del precio). Faltaba lo que se
-- ESCRIBIÓ (a 2× con la caché de 1 hora): sin eso no se distingue una llamada que falló la caché de una
-- que simplemente no tenía nada que cachear, ni se puede comprobar un arreglo de caché con tráfico real.
-- OpenRouter lo manda en `usage.prompt_tokens_details.cache_write_tokens` (verificado con la sonda del
-- 14-oct). Aditiva e idempotente; las filas viejas quedan en 0 (no se sabía).
ALTER TABLE llamadas_ia ADD COLUMN IF NOT EXISTS tokens_cache_escritura INTEGER NOT NULL DEFAULT 0;
