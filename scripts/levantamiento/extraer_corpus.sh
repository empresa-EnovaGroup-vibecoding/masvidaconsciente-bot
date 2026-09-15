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
# 15-sep-2026, dos lecciones: (1) SELECT plano, no COPY — COPY escapa barras y rompe el JSON al leerlo;
# (2) NO se trae por la tubería de ssh: desde Git Bash en Windows perdió 341 filas en silencio. Se
# exporta a un archivo en el servidor, se compara con count(*), se trae por scp y se borra allá.
$SSH "docker exec -i $PG psql -U postgres -d postgres -qtAc \"SELECT row_to_json(m)::text FROM (SELECT id, cliente_telefono, rol, tipo, contenido, media_id, media_mime, created_at, wa_message_id FROM mensajes ORDER BY cliente_telefono, created_at, id) m\" > /root/lev_mensajes.jsonl; echo \"   servidor: \$(wc -l < /root/lev_mensajes.jsonl) líneas · count(*)=\$(docker exec $PG psql -U postgres -d postgres -qtAc 'select count(*) from mensajes') · md5 \$(md5sum /root/lev_mensajes.jsonl | cut -c1-12)\""
scp -q -i "$HOME/.ssh/masvida_vps" -o StrictHostKeyChecking=no "$HOST:/root/lev_mensajes.jsonl" "$LEV/crudo/mensajes.jsonl"
$SSH "rm -f /root/lev_mensajes.jsonl"
echo "   local:    $(wc -l < "$LEV/crudo/mensajes.jsonl") líneas · md5 $(md5sum "$LEV/crudo/mensajes.jsonl" | cut -c1-12)  (deben coincidir con el servidor)"

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
