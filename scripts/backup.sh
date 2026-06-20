#!/bin/sh
# Respaldo diario de los DATOS de la clienta (lo IRREEMPLAZABLE):
#   - la base de datos PostgreSQL (clientes, pedidos, pagos, catalogo PDF, config, personalidad)
#   - las imagenes de los comprobantes (/data/comprobantes)
#   - el volumen del catalogo (/data/catalogo) por si acaso
# Se CIFRA con restic (clave RESTIC_PASSWORD, que SOLO controla la proveedora) y se sube
# AFUERA del VPS a Cloudflare R2. El codigo del bot ya vive en GitHub (no hace falta respaldarlo).
#
# Este servicio es AISLADO: si falla, NO afecta al bot. Si muere el VPS, este respaldo es
# lo unico que recupera el negocio de la clienta.

# --- Guard: si falta configurar el destino, NO romper; pausar con aviso claro ---
if [ -z "${RESTIC_REPOSITORY:-}" ] || [ -z "${RESTIC_PASSWORD:-}" ] || \
   [ -z "${AWS_ACCESS_KEY_ID:-}" ] || [ -z "${AWS_SECRET_ACCESS_KEY:-}" ]; then
  echo "[backup] Falta configurar el respaldo (RESTIC_REPOSITORY / RESTIC_PASSWORD / llaves R2) en Coolify."
  echo "[backup] Respaldo EN PAUSA hasta configurarlo. El bot sigue funcionando normal. Ver RESPALDO.md."
  while true; do sleep 3600; done
fi

if [ -z "${DATABASE_URL:-}" ]; then
  echo "[backup] No hay DATABASE_URL; no se puede respaldar la base. Pausa."
  while true; do sleep 3600; done
fi

# pg_dump usa una URL libpq estandar; el bot usa el driver asyncpg, lo quitamos.
PG_URL=$(printf '%s' "${DATABASE_URL}" | sed 's#+asyncpg##')

# Carpeta de trabajo (NO /tmp): asi la ruta dentro del respaldo es predecible al restaurar
# -> restic la recrea como /restore/backup/db_*.sql.gz
WORKDIR=/backup
mkdir -p "$WORKDIR"

run_backup() {
  ts=$(date -u +%Y%m%d_%H%M%S)
  dump="$WORKDIR/db_${ts}.sql.gz"
  echo "[backup] $ts -> pg_dump"
  # --no-owner --no-acl: el dump restaura limpio aunque el rol dueno tenga otro nombre (DR portatil).
  if ! pg_dump --no-owner --no-acl "$PG_URL" | gzip > "$dump"; then
    echo "[backup] ERROR: pg_dump fallo. NO se subio respaldo este ciclo."
    rm -f "$dump"
    return 1
  fi
  # Inicializa el repo cifrado la primera vez (idempotente).
  if ! restic snapshots >/dev/null 2>&1; then
    if ! restic init; then
      echo "[backup] ERROR: no se pudo acceder/inicializar el repo restic."
      echo "[backup]        Causas posibles: llaves R2 malas, RESTIC_REPOSITORY mal, o RESTIC_PASSWORD equivocada."
      rm -f "$dump"
      return 1
    fi
  fi
  echo "[backup] restic backup (base + comprobantes + catalogo)"
  if ! restic backup "$dump" /data/comprobantes /data/catalogo --tag masvida --host masvida; then
    echo "[backup] ERROR: restic backup fallo."
    rm -f "$dump"
    return 1
  fi
  rm -f "$dump"
  # Retencion: quitar snapshots viejos cada dia (barato, sin lock pesado).
  restic forget --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --tag masvida --host masvida \
    || echo "[backup] aviso: 'restic forget' fallo (NO critico: el respaldo de hoy ya subio)."
  # El 'prune' (reescribe packs, toma lock del repo) solo los domingos: no lockear el repo a diario.
  if [ "$(date -u +%u)" = "7" ]; then
    echo "[backup] domingo: restic prune"
    restic prune || echo "[backup] aviso: 'restic prune' fallo (revisar si quedo un lock en el repo)."
  fi
  echo "[backup] OK $ts"
}

echo "[backup] Servicio de respaldo iniciado. Primer respaldo AHORA; luego cada 24h."
while true; do
  if run_backup; then
    sleep 86400
  else
    echo "[backup] ciclo con fallo; reintento en 1h (no espero 24h)."
    sleep 3600
  fi
done
