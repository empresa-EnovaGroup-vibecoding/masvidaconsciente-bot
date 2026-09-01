#!/usr/bin/env bash
# 🧪 LOS BANCOS, EN LOCAL Y ANTES DE DESPLEGAR.
#
# 🔴 POR QUÉ EXISTE. Los 27 bancos necesitan Postgres, así que hasta el 2026-08-22 solo corrían
# DENTRO del VPS y DESPUÉS de desplegar. Eso dejaba un hueco enorme: el CI —la única puerta que
# valida antes de desplegar— no los veía. Se comprobó midiendo: al invertir la fórmula del dinero
# los 515 tests siguieron verdes, porque el único que fijaba esa regla era un banco (L57).
#
# Con Postgres y Redis en Docker local, 24 de los 27 corren aquí en segundos. Los otros 3
# (`probar_media`, `probar_retomar`, `probar_vigilante`) hablan con Meta o R2 de verdad y
# necesitan credenciales: esos siguen siendo del VPS.
#
# 🪦 Nota (1-sep-2026): este script vivía SOLO en /root del taller (contra la regla "todo en
# GitHub"); se rescató al repo el día que el taller se apagó. La base semilla ya no se clona
# del taller (murió): usa el dump rescatado si existe, y si no, clona de PRODUCCIÓN (pg_dump
# de solo lectura).
#
# Uso:   ./banco_local.sh            (todos)
#        ./banco_local.sh probar_cobro probar_delivery   (algunos)
#
# La primera vez levanta los contenedores y siembra la base. Después reutiliza.
set -uo pipefail
cd "$(dirname "$0")"

PG=masvida_pg_local; RD=masvida_redis_local
DUMP_RESCATADO="/c/Developer/AI/Proyectos/respaldos-masvida/taller_FINAL_antes_de_apagar_20260901.dump"

if ! docker ps --format '{{.Names}}' | grep -q "^${PG}$"; then
  echo "── levantando Postgres 16 y Redis 7 ──"
  docker rm -f $PG $RD >/dev/null 2>&1
  docker run -d --name $PG -e POSTGRES_PASSWORD=local -e POSTGRES_DB=postgres -p 55432:5432 postgres:16-alpine >/dev/null
  docker run -d --name $RD -p 56379:6379 redis:7-alpine >/dev/null
  until docker exec $PG pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
  echo "── sembrando la base (solo la primera vez) ──"
  if [ -f "$DUMP_RESCATADO" ]; then
    echo "   (desde el dump final del taller, rescatado en local)"
    docker exec -i $PG pg_restore -U postgres -d postgres --no-owner --no-acl < "$DUMP_RESCATADO" || true
  else
    echo "   (desde PRODUCCIÓN, pg_dump de solo lectura)"
    ssh -o ConnectTimeout=15 -i ~/.ssh/masvida_vps root@152.53.89.118 \
      "docker exec l2z8ukslzip59w1nl3omhf1e sh -c 'pg_dump -U \$POSTGRES_USER --no-owner --no-acl \$POSTGRES_DB'" \
      | docker exec -i $PG psql -U postgres -d postgres -q
  fi
  echo "── listo ──"
fi

export DATABASE_URL="postgresql+asyncpg://postgres:local@localhost:55432/postgres"
export REDIS_URL="redis://localhost:56379/0"
export JWT_SECRET="clave-de-pruebas-local-solo-para-los-bancos-no-es-real"
export ADMIN_PASSWORD="pruebaslocal123"
export OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-sk-local-no-se-usa}"
export META_ACCESS_TOKEN="${META_ACCESS_TOKEN:-local-no-se-usa}"
export META_APP_SECRET="${META_APP_SECRET:-local-no-se-usa}"
export PYTHONPATH=.

# El python del venv: Linux/mac usa .venv/bin, Windows (Git Bash) usa .venv/Scripts.
PY=".venv/bin/python"; [ -x ".venv/Scripts/python.exe" ] && PY=".venv/Scripts/python.exe"

# Estos tres salen a la red de verdad: sin credenciales reales no pueden correr aquí.
NECESITAN_RED="probar_media probar_retomar probar_vigilante"
BANCOS=${@:-$(ls scripts/probar_*.py | xargs -n1 basename | sed 's/\.py$//')}

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
for n in $BANCOS; do
  ( o=$("$PY" "scripts/$n.py" 2>&1); c=$?
    # ⚠️ El veredicto es el CÓDIGO DE SALIDA y los [MAL], nunca la palabra "Traceback": varios
    # bancos SIMULAN a propósito que Postgres se cae o que OpenRouter da 402, y su traceback es
    # parte de la prueba. Buscarlo daba 6 falsos rojos.
    if [ $c -eq 0 ] && ! grep -qE "^\s*\[MAL\]" <<<"$o"; then echo "✅ $n"
    elif grep -qiE "Meta devolvió|graph.facebook|401 Unauthorized|META_ACCESS_T|R2_PUBLIC|no se pudieron enviar las fotos" <<<"$o"; then echo "🌐 $n (necesita credenciales reales)"
    else echo "🔴 $n"; echo "$o" > "/tmp/banco_$n.log"; fi ) &
done > "$TMP/r" 2>&1
wait; sort "$TMP/r"
echo
echo "  ✅ $(grep -c '✅' "$TMP/r")   🌐 $(grep -c '🌐' "$TMP/r")   🔴 $(grep -c '🔴' "$TMP/r")   (los rojos dejan su log en /tmp/banco_<nombre>.log)"
grep -q '🔴' "$TMP/r" && exit 1 || exit 0
