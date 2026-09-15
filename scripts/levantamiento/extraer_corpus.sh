#!/usr/bin/env bash
# LEVANTAMIENTO · A1 — vuelca `mensajes` y una foto del sistema desde PRODUCCIÓN a una carpeta LOCAL.
# Solo lectura (COPY … TO STDOUT). Nunca escribe en el VPS. La salida va FUERA del repo (es público).
#
#   scripts/levantamiento/extraer_corpus.sh /c/Developer/AI/Proyectos/respaldos-masvida/levantamiento-2026-09
#
# Requiere la llave ssh de netcup (~/.ssh/masvida_vps). El contenedor de postgres de producción es el
# que tiene las tablas del negocio (ver ESTADO.md); no confundir con `coolify-db`.
set -euo pipefail
LEV="${1:?carpeta de salida (fuera del repo)}"
HOST="${HOST:-root@152.53.89.118}"
PG="${PG:-l2z8ukslzip59w1nl3omhf1e}"
if git -C "$LEV" rev-parse --show-toplevel >/dev/null 2>&1; then
  echo "NO: $LEV está dentro de un repositorio git. El corpus va fuera del repo." >&2; exit 1
fi
mkdir -p "$LEV/crudo" "$LEV/anon" "$LEV/analisis" "$LEV/top20"
SSH="ssh -i $HOME/.ssh/masvida_vps -o StrictHostKeyChecking=no -o ConnectTimeout=20 $HOST"
PSQL="docker exec -i $PG psql -U postgres -d postgres -qtA"

echo "→ mensajes.jsonl"
$SSH "$PSQL" <<'SQL' > "$LEV/crudo/mensajes.jsonl"
-- SELECT plano, no COPY: COPY escapa barras y rompe el JSON al leerlo.
SELECT row_to_json(m)::text FROM (SELECT id, cliente_telefono, rol, tipo, contenido, media_id, media_mime, created_at, wa_message_id FROM mensajes ORDER BY cliente_telefono, created_at, id) m;
SQL
echo "   $(wc -l < "$LEV/crudo/mensajes.jsonl") mensajes"

FECHA="$(date +%F)"
echo "→ sistema_snapshot_$FECHA.json"
$SSH "$PSQL" <<'SQL' > "$LEV/crudo/sistema_snapshot_$FECHA.json"
SELECT json_build_object(
  'fecha', now()::date,
  'clientes', (SELECT json_agg(row_to_json(c)) FROM (SELECT telefono, nombre, bot_pausado, pausado_por, no_leidos, ultima_interaccion FROM clientes ORDER BY telefono) c),
  'productos', (SELECT json_agg(row_to_json(p)) FROM (SELECT * FROM productos ORDER BY id) p),
  'variantes', (SELECT json_agg(row_to_json(v)) FROM (SELECT * FROM producto_variantes ORDER BY producto_id, id) v),
  'zonas', (SELECT json_agg(row_to_json(z)) FROM (SELECT * FROM zonas_entrega ORDER BY orden, id) z),
  'metodos_pago', (SELECT json_agg(row_to_json(m)) FROM (SELECT id, tipo, titulo, activo, orden FROM metodos_pago ORDER BY orden, id) m),
  'configuracion', (SELECT json_object_agg(clave, valor) FROM configuracion WHERE clave NOT IN ('personalidad')),
  'conocimiento', (SELECT json_agg(row_to_json(k)) FROM (SELECT id, titulo, contenido FROM conocimiento ORDER BY id) k),
  'feriados', (SELECT json_agg(row_to_json(f)) FROM (SELECT * FROM feriados ORDER BY 1) f)
);
SQL
echo "   listo. Siguiente: python scripts/levantamiento/anonimizar_corpus.py --crudo $LEV/crudo --salida $LEV/anon"
