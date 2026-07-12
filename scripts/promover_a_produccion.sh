#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  PROMOVER EL TALLER → PRODUCCIÓN
#
#  El plan de Maired (dicho por ella, 2026-07-12):
#    · TALLER      = Hostinger viejo (2.25.139.106) + SU número de WhatsApp de pruebas.
#                    Ahí se perfecciona TODO el sistema. Ella edita en el panel viejo y
#                    prueba con su número (+57 313 2933806). Una sola verdad: panel viejo
#                    escribe en la BD vieja y el bot viejo la lee.
#    · PRODUCCIÓN  = netcup (152.53.89.118) + el número de la CLIENTA. Ahí están los
#                    clientes REALES (40 personas, 300+ mensajes). El bot está MUDO
#                    (lista blanca) hasta que se abra.
#
#  Este script copia SOLO EL CONTENIDO del taller a producción:
#    productos · configuracion · conocimiento · metodos_pago · producto_media ·
#    catalogo_pdf · feriados
#
#  🔴 JAMÁS toca: clientes, pedidos, pagos, mensajes, intervenciones.
#     Esos son los datos REALES de producción. Pisarlos sería borrar el negocio.
#
#  Uso:
#     bash scripts/promover_a_produccion.sh --ensayo    # muestra qué haría (no escribe)
#     bash scripts/promover_a_produccion.sh --aplicar   # lo hace de verdad
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

LLAVE="${LLAVE_SSH:-/c/Users/herid/.ssh/masvida_vps}"
TALLER_HOST="2.25.139.106";   TALLER_PG="zedzrztx4bntf5227wedzvt7"
PROD_HOST="152.53.89.118";    PROD_PG="l2z8ukslzip59w1nl3omhf1e"
SSH="ssh -i $LLAVE -o StrictHostKeyChecking=no -o ConnectTimeout=20 root"

# El contenido que se promueve. El ORDEN importa (las fotos dependen de los productos).
TABLAS=(productos configuracion conocimiento metodos_pago producto_media catalogo_pdf feriados)

MODO="${1:---ensayo}"

echo "══════════════════════════════════════════════════════════════"
echo "  PROMOVER TALLER → PRODUCCIÓN   ($MODO)"
echo "══════════════════════════════════════════════════════════════"

# ── 1. RESPALDO de producción ANTES de tocar nada (siempre, incluso en ensayo) ──
STAMP=$(date -u +%Y%m%d_%H%M%S)
DESTINO="/c/Mis_Proyectos_IA/respaldos-masvida"
mkdir -p "$DESTINO"
echo "→ Respaldando PRODUCCIÓN antes de tocarla…"
$SSH@$PROD_HOST "docker exec -i $PROD_PG pg_dump --no-owner --no-acl -U postgres -d postgres | gzip -c" \
  > "$DESTINO/ANTES_de_promover_${STAMP}.sql.gz"
echo "  ✓ $DESTINO/ANTES_de_promover_${STAMP}.sql.gz ($(du -h "$DESTINO/ANTES_de_promover_${STAMP}.sql.gz" | cut -f1))"

# ── 2. Lo que hay hoy en cada lado ──
echo "→ Contenido actual:"
for T in "${TABLAS[@]}"; do
  A=$($SSH@$TALLER_HOST "docker exec -i $TALLER_PG psql -U postgres -d postgres -tAc 'select count(*) from $T'" 2>/dev/null | tr -d '[:space:]')
  B=$($SSH@$PROD_HOST   "docker exec -i $PROD_PG   psql -U postgres -d postgres -tAc 'select count(*) from $T'" 2>/dev/null | tr -d '[:space:]')
  printf "   %-16s taller: %-5s → producción: %s\n" "$T" "$A" "$B"
done

# Lo que NO se toca (para verlo y quedarse tranquila)
echo "→ Datos REALES de producción (NO se tocan):"
$SSH@$PROD_HOST "docker exec -i $PROD_PG psql -U postgres -d postgres -tAc \"
  select '   clientes: '||(select count(*) from clientes)
      || ' | pedidos: '||(select count(*) from pedidos)
      || ' | pagos: '||(select count(*) from pagos)
      || ' | mensajes: '||(select count(*) from mensajes)\"" 2>/dev/null

if [ "$MODO" != "--aplicar" ]; then
  echo
  echo "  (ENSAYO: no se escribió nada. Para hacerlo de verdad: --aplicar)"
  exit 0
fi

# ── 3. Copiar el contenido, tabla por tabla, en UNA transacción ──
echo "→ Copiando el contenido del taller a producción…"
VOLCADO="/tmp/contenido_${STAMP}.sql"
$SSH@$TALLER_HOST "docker exec -i $TALLER_PG pg_dump --no-owner --no-acl --data-only \
  $(printf ' -t %s' "${TABLAS[@]}") -U postgres -d postgres" > "$VOLCADO"
echo "  ✓ volcado del taller: $(wc -l < "$VOLCADO") líneas"

scp -q -i "$LLAVE" -o StrictHostKeyChecking=no "$VOLCADO" root@$PROD_HOST:/tmp/contenido.sql
$SSH@$PROD_HOST "docker cp /tmp/contenido.sql $PROD_PG:/tmp/contenido.sql"

# TRUNCATE + COPY dentro de UNA transacción: si algo falla, no queda a medias.
# `catalogo_pdf` y `producto_media` cuelgan de productos -> CASCADE dentro del truncate.
$SSH@$PROD_HOST "docker exec -i $PROD_PG psql -U postgres -d postgres -v ON_ERROR_STOP=1" <<SQL
BEGIN;
TRUNCATE $(IFS=,; echo "${TABLAS[*]}") CASCADE;
\i /tmp/contenido.sql
COMMIT;
SQL

echo "→ Verificando…"
for T in "${TABLAS[@]}"; do
  A=$($SSH@$TALLER_HOST "docker exec -i $TALLER_PG psql -U postgres -d postgres -tAc 'select count(*) from $T'" 2>/dev/null | tr -d '[:space:]')
  B=$($SSH@$PROD_HOST   "docker exec -i $PROD_PG   psql -U postgres -d postgres -tAc 'select count(*) from $T'" 2>/dev/null | tr -d '[:space:]')
  if [ "$A" = "$B" ]; then printf "   ✓ %-16s %s\n" "$T" "$B"; else printf "   🔴 %-16s taller=%s producción=%s\n" "$T" "$A" "$B"; fi
done
$SSH@$PROD_HOST "docker exec -i $PROD_PG psql -U postgres -d postgres -tAc \"
  select '   ✅ INTACTOS -> clientes: '||(select count(*) from clientes)
      || ' | pedidos: '||(select count(*) from pedidos)
      || ' | pagos: '||(select count(*) from pagos)
      || ' | mensajes: '||(select count(*) from mensajes)\"" 2>/dev/null

echo
echo "  Ahora: correr el banco de pruebas del dinero EN PRODUCCIÓN y, si está verde,"
echo "  vaciar NUMEROS_PERMITIDOS para que el bot empiece a atender a los clientes reales."
