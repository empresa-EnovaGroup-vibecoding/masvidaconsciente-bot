#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  PROMOVER EL TALLER → PRODUCCIÓN     ⚠️ EL SCRIPT MÁS PELIGROSO DEL REPO ⚠️
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
# ═══════════════════════════════════════════════════════════════════════════════
#  🔴🔴 AVISO A QUIEN EDITE ESTE SCRIPT — LEER ANTES DE TOCAR LA LISTA `TABLAS`
#       (copiado aquí desde `migrations/023_zonas_entrega.sql:15-19`, porque el aviso
#        vivía en un `.sql` que nadie iba a abrir justo cuando hacía falta. DAT-6.)
#
#    NO metas `zonas_entrega` —ni ninguna tabla de la que cuelgue el negocio— en la lista
#    `TABLAS`, que se recarga con `TRUNCATE … CASCADE`.
#    `pedidos.zona_id` apunta a `zonas_entrega`, y un TRUNCATE CASCADE sobre ella
#    **se llevaría por delante `pedidos` y `pagos` de PRODUCCIÓN**: el CASCADE de TRUNCATE
#    IGNORA el `ON DELETE SET NULL` y vacía la tabla que referencia. Las zonas se promueven
#    con `INSERT … ON CONFLICT` (paso 11 de este script), NUNCA con TRUNCATE.
#
#    No hace falta que te acuerdes: el **paso 2** calcula el cierre transitivo de claves
#    foráneas en PRODUCCIÓN y aborta si el TRUNCATE llegara a rozar una tabla intocable.
#    Si añades una tabla y el paso 2 se pone rojo, el rojo tiene razón.
# ═══════════════════════════════════════════════════════════════════════════════
#
#  QUÉ HACE (tres mecanismos distintos, a propósito — no todo se promueve igual):
#
#    A) CONTENIDO PURO  → se RECARGA ENTERO (TRUNCATE + COPY, en una transacción):
#         productos · producto_variantes (los TAMAÑOS y sus precios) · conocimiento ·
#         producto_media · catalogo_pdf · feriados
#       Es el catálogo. La verdad vive en el taller y producción es una copia.
#
#    B) CONFIGURACIÓN   → LISTA BLANCA + `INSERT … ON CONFLICT (clave) DO UPDATE`.
#       🔴 NUNCA se vacía. Antes `configuracion` estaba en el TRUNCATE, y como la BD GANA
#       sobre las variables de entorno, promover pisaba `dueno_telefono` con el número de
#       PRUEBAS: **todos los avisos de clientes reales saldrían al teléfono del taller**.
#       (Auditoría 2026-08-02, DAT-1.) Hay además una LISTA NEGRA dura: hay claves que son
#       propiedad del ENTORNO, no del contenido, y no viajan aunque alguien las meta en la
#       lista blanca por descuido.
#
#    C) ZONAS Y MÉTODOS DE PAGO → `INSERT … ON CONFLICT`, aditivo, jamás TRUNCATE.
#       · `zonas_entrega`: producción se quedó con CERO zonas y **el bot no podía cobrar
#         NINGÚN pedido, ni un retiro** (`generar_datos_pago` devolvía `ok:false` el 100%
#         de las veces). La 023 no las siembra a propósito y no había ningún script que las
#         promoviera: este es ese paso (DAT-5).
#       · `metodos_pago`: por defecto NO VIAJA. Son las cuentas bancarias de la dueña —
#         propiedad del entorno, igual que `dueno_telefono`. Promoverlas desde el taller
#         significaba **darle al primer cliente real la cuenta de pruebas** (DAT-4).
#         Con `--promover-metodos-pago` se AÑADEN las que falten, y entran `activo=FALSE`:
#         una cuenta de pruebas nunca puede llegarle a un cliente sin que un humano la
#         active a mano en el panel de producción.
#
#  ⚠️ El PRECIO DEL DÍA (`precio_dia`) se REINICIA a propósito: cuelga de los productos y se
#     vacía por CASCADE. Los valores del taller son de prueba. **La dueña tiene que cargar el
#     precio del día FRESCO en producción el mismo día**, o las tortas y premezclas no se
#     venden. El script lo recuerda al final.
#
#  🔴 JAMÁS toca: clientes, pedidos, pagos, mensajes, intervenciones, usuarios.
#     Esos son los datos REALES de producción. Pisarlos sería borrar el negocio.
#     El paso 11 lo COMPRUEBA contando antes y después, no confiando.
#
#  Uso:
#     bash scripts/promover_a_produccion.sh --ensayo    # el plan completo, sin escribir nada
#     bash scripts/promover_a_produccion.sh --aplicar   # lo hace de verdad (pide confirmación)
#
#  Banderas (todas opcionales):
#     --acepto-perder <tabla>   Autoriza que esa tabla quede con MENOS filas que antes.
#                               Repetible. Sin esto, perder filas ABORTA. Nunca vale para
#                               las tablas intocables.
#     --pisar-zonas             Además de añadir las que faltan, ACTUALIZA costo/referencias
#                               de las zonas que ya existen en producción. Por defecto NO:
#                               si la dueña subió el envío a $4 en producción, el taller no
#                               tiene por qué bajárselo a $3 en silencio.
#     --pisar-personalidad      Promueve también la clave `personalidad`. Por defecto NO: la
#                               auditoría dejó abierto que la personalidad de producción y la
#                               del taller divergieron, y cuál manda es una decisión HUMANA.
#     --promover-metodos-pago   Añade (inactivos) los métodos de pago que falten. Ver arriba.
#
#  Variables de entorno (DAT-15 — este script vivía atado al Git Bash de un portátil concreto,
#  con `/c/Users/herid/.ssh/…` incrustado; ahora funciona igual en macOS, Linux y Git Bash):
#     LLAVE_SSH           llave privada para los dos VPS   (def. ~/.ssh/masvida_vps)
#     RESPALDOS_DIR       dónde se guarda el respaldo       (def. ~/respaldos-masvida)
#     RESPALDO_MIN_BYTES  suelo de tamaño del .gz           (def. 100000)
#     TALLER_HOST/PROD_HOST · TALLER_PG/PROD_PG             (def. los de siempre)
#     SIN_PREGUNTAR=1     salta la confirmación escrita de `--aplicar`
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Rutas y destinos: TODO parametrizable, con default sensato y portable ──
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIGRACIONES="$RAIZ/migrations"

LLAVE="${LLAVE_SSH:-$HOME/.ssh/masvida_vps}"
DESTINO="${RESPALDOS_DIR:-$HOME/respaldos-masvida}"
RESPALDO_MIN_BYTES="${RESPALDO_MIN_BYTES:-100000}"

TALLER_HOST="${TALLER_HOST:-2.25.139.106}"; TALLER_PG="${TALLER_PG:-zedzrztx4bntf5227wedzvt7}"
PROD_HOST="${PROD_HOST:-152.53.89.118}";    PROD_PG="${PROD_PG:-l2z8ukslzip59w1nl3omhf1e}"

# Array, no cadena: así una ruta de llave con espacios (los `~/Library/Mobile Documents/…` de
# macOS son reales) no parte el comando en dos. `accept-new` en vez de `no`: acepta la primera
# vez y AVISA si la huella del servidor cambia — que es justo cuando NO quieres seguir.
SSH=(ssh -i "$LLAVE" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20)

# ─── LAS LISTAS ──────────────────────────────────────────────────────────────
# (A) Contenido puro: se recarga ENTERO. El ORDEN importa: los TAMAÑOS y las fotos cuelgan de
# los productos (van después). 🔴 SIN `producto_variantes` la promoción dejaba producción con
# productos pero CERO tamaños → el bot no podía vender NADA y la verificación reportaba verde
# (fuga B2). El pg_dump ordena las COPY por dependencia, así que respeta las llaves foráneas.
#
# 🔴 `configuracion` y `metodos_pago` SALIERON de esta lista el 2026-08-02 (DAT-1 y DAT-4).
#    No las devuelvas. Se promueven por los pasos 7 y 9, sin vaciar nunca.
TABLAS=(productos producto_variantes conocimiento producto_media catalogo_pdf feriados)

# Se vacían por CASCADE y está PREVISTO. `precio_dia` cuelga de productos/variantes y los
# valores del taller son de prueba (ver la cabecera).
CASCADA_PREVISTA=(precio_dia)

# 🔴 INTOCABLES: el negocio. Después de promover tienen que tener EXACTAMENTE las mismas filas.
# Una sola de menos = catástrofe: se restaura el respaldo y no se abre el bot.
INTOCABLES=(clientes pedidos pagos mensajes intervenciones usuarios)

# Tablas que este script solo puede HACER CRECER (upsert aditivo). Nunca menguan.
SOLO_CRECEN=(configuracion metodos_pago zonas_entrega)

# ─── CONFIGURACIÓN: qué viaja y qué NO ───────────────────────────────────────
# LISTA BLANCA — lo que es CONTENIDO del negocio y por tanto se perfecciona en el taller.
CONFIG_BLANCA=(
  negocio_nombre negocio_ubicacion negocio_pago negocio_instagram
  # Candados del calendario: el código valida la fecha de entrega contra esto.
  dias_entrega hora_apertura hora_cierre hora_corte
  # Sinónimos del buscador: lo que el cliente DICE vs. lo que está ESCRITO en el catálogo.
  sinonimos_busqueda
  # Guías de los mensajes automáticos (services/mensajes.py). Son la INTENCIÓN, no plantillas.
  msg_guia_confirmado msg_guia_rechazado msg_guia_comprobante
  # `personalidad` va aparte: solo con --pisar-personalidad (se añade más abajo).
)

# 🔴 LISTA NEGRA DURA — claves que son propiedad del ENTORNO, no del contenido. No viajan
# JAMÁS, aunque alguien las meta en la lista blanca por descuido: el filtro se aplica dos
# veces (la blanca elige, la negra veta). Cada una tiene su daño concreto:
#   dueno_telefono ........... los avisos de clientes REALES saldrían al número del taller
#   bot_activo ............... el taller lo tiene apagado/encendido según qué se esté probando
#   numeros_permitidos_extra . la lista blanca que mantiene MUDO al bot ante clientes reales
#   tasa_manual/_activa ...... la tasa congelada de una prueba se le cobraría a un cliente
#   tasa_margen_pct .......... el margen es una decisión de negocio de la dueña, no del taller
#   modelo_ia/operador/voz ... palancas de la PROVEEDORA (CLAUDE.md §5), por entorno
#   agente_modo .............. uno/dos agentes: se prueba en el taller antes que en producción
#   tools_activas ............ qué herramientas están vivas en ESE bot
#   pago_movil_* ............. la cuenta bancaria REAL. Mismo daño que DAT-4: el primer cliente
#                              real recibiría los datos de prueba. Se editan en el panel de
#                              producción, nunca se copian.
#   barredor_ultima_corrida .. reloj interno del vigilante; copiarlo hace mentir a `/salud`
CONFIG_NEGRA=(
  dueno_telefono bot_activo numeros_permitidos_extra
  tasa_manual tasa_manual_activa tasa_margen_pct tasa_fuente
  modelo_ia modelo_operador modelo_voz agente_modo tools_activas
  pago_movil_banco pago_movil_cedula pago_movil_telefono pago_movil_titular
  barredor_ultima_corrida
)

# ─── Banderas ────────────────────────────────────────────────────────────────
MODO="--ensayo"
PISAR_ZONAS=0
PISAR_PERSONALIDAD=0
PROMOVER_METODOS=0
ACEPTO_PERDER=()

while [ $# -gt 0 ]; do
  case "$1" in
    --ensayo|--aplicar)      MODO="$1" ;;
    --pisar-zonas)           PISAR_ZONAS=1 ;;
    --pisar-personalidad)    PISAR_PERSONALIDAD=1 ;;
    --promover-metodos-pago) PROMOVER_METODOS=1 ;;
    --acepto-perder)
      [ $# -ge 2 ] || { echo "🔴 --acepto-perder necesita el nombre de una tabla" >&2; exit 1; }
      ACEPTO_PERDER+=("$2"); shift ;;
    --ayuda|-h|--help)
      # La cabecera de este archivo ES la ayuda. Mostrarla entera es más honesto que resumirla.
      grep '^#' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)
      # 🔴 Un argumento con una errata NO puede pasar en silencio: `--aplicarr` cayendo al
      # default `--ensayo` estaría bien, pero `--acepto-perde pedidos` NO.
      echo "🔴 Argumento desconocido: '$1'. Usa --ayuda." >&2; exit 1 ;;
  esac
  shift
done

# `if`, no `[ … ] && …`: con `set -e` un AND-list que termina en falso MATA el script entero.
# Es el clásico que convierte un "no aplica" en una salida silenciosa a mitad de camino.
if [ "$PISAR_PERSONALIDAD" = "1" ]; then CONFIG_BLANCA+=(personalidad); fi

TRABAJO="$(mktemp -d)"
trap 'rm -rf "$TRABAJO"' EXIT
STAMP="$(date -u +%Y%m%d_%H%M%S)"
BLOQUEOS=0   # cuántas cosas impedirían aplicar. En ensayo se acumulan y se cuentan al final.

bloqueo() { echo "   🔴 $*"; BLOQUEOS=$((BLOQUEOS + 1)); }

# ─── psql sin mordaza ────────────────────────────────────────────────────────
# 🔴 NI UN SOLO `2>/dev/null` EN TODO EL SCRIPT (DAT-11). Antes los errores de psql se tiraban
# a la basura y el ensayo imprimía `producto_variantes  taller: 30 → producción:` (en blanco),
# que cualquiera lee como "0". Un error tiene que GRITAR, no imprimirse en blanco.
_psql() {  # _psql taller|produccion   ← SQL por stdin, filas `a|b|c` por stdout
  local lado="$1" host contenedor
  case "$lado" in
    taller)     host="$TALLER_HOST"; contenedor="$TALLER_PG" ;;
    produccion) host="$PROD_HOST";   contenedor="$PROD_PG" ;;
    *) echo "🔴 lado desconocido: '$lado'" >&2; exit 1 ;;
  esac
  "${SSH[@]}" "root@$host" \
    "docker exec -i $contenedor psql -U postgres -d postgres -v ON_ERROR_STOP=1 -qtA"
}

_psql_escribir() {  # igual, pero deja ver los tags (INSERT 0 5, TRUNCATE TABLE…)
  local lado="$1" host contenedor
  case "$lado" in
    taller)     host="$TALLER_HOST"; contenedor="$TALLER_PG" ;;
    produccion) host="$PROD_HOST";   contenedor="$PROD_PG" ;;
    *) echo "🔴 lado desconocido: '$lado'" >&2; exit 1 ;;
  esac
  "${SSH[@]}" "root@$host" \
    "docker exec -i $contenedor psql -U postgres -d postgres -v ON_ERROR_STOP=1"
}

en_lista() {  # en_lista <aguja> <pajar…>
  local aguja="$1"; shift
  local x
  # `if` en vez de `[ … ] && return 0`: con `set -e` el AND-list en falso mataría la función
  # (y con ella el script) en la primera vuelta que no case.
  for x in "$@"; do
    if [ "$x" = "$aguja" ]; then return 0; fi
  done
  return 1
}

comillas() {  # comillas a b c  →  'a','b','c'   (para un IN (…) de SQL)
  local salida="" x
  for x in "$@"; do salida="${salida:+$salida,}'$x'"; done
  printf '%s' "$salida"
}

TODAS_LAS_TABLAS=("${TABLAS[@]}" "${CASCADA_PREVISTA[@]}" "${INTOCABLES[@]}" "${SOLO_CRECEN[@]}")

contar_todo() {  # contar_todo <lado> <archivo>   ← UNA sola conexión para TODAS las tablas
  local lado="$1" archivo="$2" sql="" t
  for t in "${TODAS_LAS_TABLAS[@]}"; do
    sql="${sql:+$sql UNION ALL }SELECT '$t', count(*) FROM $t"
  done
  printf '%s;\n' "$sql" | _psql "$lado" > "$archivo"
  # 🔴 Se valida AQUÍ, de una vez, que TODAS las tablas devolvieron un número. Si no, este
  # `exit 1` mata el script entero (esta función se llama como comando, no dentro de un
  # `$( )`). Sin esto reaparecería el fallo DAT-11 disfrazado: una tabla sin conteo se
  # imprimiría como una columna EN BLANCO, y una columna en blanco se lee como "0".
  for t in "${TODAS_LAS_TABLAS[@]}"; do
    if ! awk -F'|' -v t="$t" '$1 == t && $2 ~ /^[0-9]+$/ { ok = 1 } END { exit !ok }' "$archivo"; then
      echo "🔴 '$lado' no devolvió un conteo válido para la tabla '$t'." >&2
      echo "   El error de psql está arriba, sin mordaza. NO se sigue adelante." >&2
      exit 1
    fi
  done
}

conteo() {  # conteo <archivo> <tabla>  → número, o muere gritando
  local n
  n="$(awk -F'|' -v t="$2" '$1 == t { print $2 }' "$1")"
  case "$n" in
    ''|*[!0-9]*) echo "🔴 No pude leer el conteo de '$2' (respuesta: '$n')" >&2; exit 1 ;;
  esac
  printf '%s' "$n"
}

echo "══════════════════════════════════════════════════════════════"
echo "  PROMOVER TALLER → PRODUCCIÓN   ($MODO)"
echo "  taller=$TALLER_HOST   producción=$PROD_HOST   $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "══════════════════════════════════════════════════════════════"

# ═════════════════════════════════════════════════════════════════════════════
# PASO 1 — PREFLIGHT DE ESQUEMA (OBLIGATORIO, también en --ensayo)
#
# 🔴 POR QUÉ EXISTE. La auditoría del 2026-08-02 lo dijo sin rodeos: *esta comprobación, de
# haber existido, habría hecho innecesario todo el reporte externo*. Producción llevaba
# semanas con migraciones sin aplicar y **nadie lo sabía**, porque los detectores de drift
# solo corrían contra el taller —que sí estaba al día—. Promover CONTENIDO nuevo a un
# ESQUEMA viejo no es "medio bien": revienta el COPY, o peor, entra y el bot lee columnas
# que no existen con un cliente delante.
#
# Es lo PRIMERO porque es gratis, es de solo lectura y ataja el 90% de los abortos: no tiene
# sentido volcar 300 MB de respaldo para descubrir después que producción está atrasada.
# ═════════════════════════════════════════════════════════════════════════════
echo "→ 1. PREFLIGHT DE ESQUEMA (taller vs producción vs disco)"

# Mismo filtro de ocultos que `init_db.py` y `probar_drift.py`: un `tar` hecho desde macOS
# cuela ficheros AppleDouble (`._026_algo.sql`) y ya puso el vigilante en rojo una vez (DAT-10).
find "$MIGRACIONES" -maxdepth 1 -name '*.sql' -not -name '.*' -exec basename {} \; \
  | LC_ALL=C sort > "$TRABAJO/disco.txt"

if ! printf 'SELECT nombre FROM schema_migrations ORDER BY 1;\n' \
     | _psql produccion | LC_ALL=C sort > "$TRABAJO/prod_mig.txt"; then
  echo "   🔴 NO PUDE LEER 'schema_migrations' EN PRODUCCIÓN."
  echo "      O la conexión falló (el error de psql está justo arriba, sin mordaza), o esa"
  echo "      base es ANTERIOR a la fase 0. Arrancar el contenedor nuevo —que crea y anota"
  echo "      esa tabla— ANTES de promover nada."
  exit 1
fi
printf 'SELECT nombre FROM schema_migrations ORDER BY 1;\n' \
  | _psql taller | LC_ALL=C sort > "$TRABAJO/taller_mig.txt"

echo "   disco: $(wc -l < "$TRABAJO/disco.txt" | tr -d ' ')  ·  taller: $(wc -l < "$TRABAJO/taller_mig.txt" | tr -d ' ')  ·  producción: $(wc -l < "$TRABAJO/prod_mig.txt" | tr -d ' ')"

FALTAN_PROD="$(LC_ALL=C comm -23 "$TRABAJO/disco.txt" "$TRABAJO/prod_mig.txt" | tr '\n' ' ')"
FALTAN_TALLER="$(LC_ALL=C comm -23 "$TRABAJO/disco.txt" "$TRABAJO/taller_mig.txt" | tr '\n' ' ')"
SOBRAN_PROD="$(LC_ALL=C comm -13 "$TRABAJO/disco.txt" "$TRABAJO/prod_mig.txt" | tr '\n' ' ')"

if [ -n "${FALTAN_PROD// /}" ]; then
  echo "   🔴 PRODUCCIÓN ESTÁ ATRASADA. Migraciones que NUNCA se le aplicaron:"
  echo "      $FALTAN_PROD"
  echo "      Esto es EXACTAMENTE el desfase que nadie vio. Primero DESPLIEGA el código a"
  echo "      producción y compruébalo, y solo después promueve contenido:"
  echo "         gh workflow run deploy.yml -f destino=produccion"
  echo "      (ese flujo ya corre probar_migraciones y probar_drift DENTRO de producción)"
  exit 1
fi
if [ -n "${SOBRAN_PROD// /}" ]; then
  echo "   🔴 PRODUCCIÓN VA POR DELANTE DE ESTE REPO: $SOBRAN_PROD"
  echo "      Tu copia del repo está vieja. Un 'git pull' antes de promover nada."
  exit 1
fi
if [ -n "${FALTAN_TALLER// /}" ]; then
  echo "   🔴 EL TALLER está atrasado respecto al disco: $FALTAN_TALLER"
  echo "      Promover desde un taller viejo es promover contenido que nadie probó."
  exit 1
fi
echo "   ✓ los tres lados dicen lo mismo: mismo esquema en taller, producción y disco"

# ═════════════════════════════════════════════════════════════════════════════
# PASO 2 — LA REJA DEL CASCADE (el aviso de la 023, pero EJECUTABLE)
#
# El comentario de la cabecera es para el humano; esto es para cuando el humano no lo lea.
# Calcula en PRODUCCIÓN el cierre transitivo de las llaves foráneas que apuntan a las tablas
# del TRUNCATE, y aborta si alcanza una intocable. Si mañana alguien mete `zonas_entrega` en
# `TABLAS`, el cierre incluirá `pedidos` (y por él `pagos`) y este paso lo para en seco.
# ═════════════════════════════════════════════════════════════════════════════
echo "→ 2. ¿A dónde llegaría el TRUNCATE … CASCADE?"
printf "%s\n" "
WITH RECURSIVE semilla(t) AS (
    SELECT unnest(ARRAY[$(comillas "${TABLAS[@]}")]::regclass[])
), cierre(t) AS (
    SELECT t FROM semilla
  UNION
    SELECT c.conrelid::regclass
      FROM pg_constraint c JOIN cierre ON c.confrelid = cierre.t
     WHERE c.contype = 'f'
)
SELECT t::text FROM cierre ORDER BY 1;" | _psql produccion > "$TRABAJO/cascade.txt"

ALCANZADAS="$(tr '\n' ' ' < "$TRABAJO/cascade.txt")"
echo "   alcanza: $ALCANZADAS"
while read -r TABLA; do
  [ -n "$TABLA" ] || continue
  if en_lista "$TABLA" "${INTOCABLES[@]}" || [ "$TABLA" = "zonas_entrega" ]; then
    echo "   🔴🔴 EL TRUNCATE LLEGARÍA A '$TABLA', QUE ES INTOCABLE."
    echo "        Lee el aviso de la cabecera de este archivo. NO se promueve nada."
    exit 1
  fi
  if ! en_lista "$TABLA" "${TABLAS[@]}" && ! en_lista "$TABLA" "${CASCADA_PREVISTA[@]}"; then
    echo "   🔴 El TRUNCATE vaciaría '$TABLA' y NADIE lo había previsto."
    echo "      Si es correcto, añádela a CASCADA_PREVISTA y explica POR QUÉ. Si no, para."
    exit 1
  fi
done < "$TRABAJO/cascade.txt"
echo "   ✓ el CASCADE no roza ninguna tabla intocable"

# ═════════════════════════════════════════════════════════════════════════════
# PASO 3 — RESPALDO DE PRODUCCIÓN, BLINDADO (DAT-2)
#
# 🔴 EL FALLO ORIGINAL, que hacía inútil la única red de seguridad: el `pg_dump | gzip`
# corría DENTRO del shell remoto, y **el `set -euo pipefail` local NO VIAJA POR SSH**. Si
# `pg_dump` fallaba, `gzip` terminaba en 0, se escribía un `.gz` válido de ~20 bytes, se
# imprimía un `✓` precioso… y el script SEGUÍA hasta el TRUNCATE. Con el respaldo vacío.
#
# Arreglo, en cuatro cierres:
#   1. `set -euo pipefail` DENTRO del comando remoto (por eso el heredoc con `bash -s`).
#   2. el `.gz` tiene que superar un suelo de bytes.
#   3. `gzip -t` (integridad del contenedor comprimido).
#   4. el dump tiene que TERMINAR en la línea que pg_dump escribe al acabar bien: un fichero
#      grande pero CORTADO a la mitad pasaría los tres primeros y no serviría para restaurar.
# Sin los cuatro en verde: `exit 1` ANTES de tocar nada.
# ═════════════════════════════════════════════════════════════════════════════
echo "→ 3. RESPALDO de producción (antes de tocar nada, también en ensayo)"
mkdir -p "$DESTINO"
RESPALDO="$DESTINO/ANTES_de_promover_${STAMP}.sql.gz"

if ! "${SSH[@]}" "root@$PROD_HOST" 'bash -s' -- "$PROD_PG" > "$RESPALDO" <<'REMOTO'
set -euo pipefail
# `docker exec` SIN -i: pg_dump no lee stdin, y stdin aquí es este propio script.
docker exec "$1" pg_dump --no-owner --no-acl -U postgres -d postgres | gzip -c
REMOTO
then
  echo "   🔴 EL RESPALDO FALLÓ (pg_dump o la conexión). No se toca producción."
  rm -f "$RESPALDO"
  exit 1
fi

BYTES="$(wc -c < "$RESPALDO" | tr -d ' ')"
if [ "$BYTES" -lt "$RESPALDO_MIN_BYTES" ]; then
  echo "   🔴 EL RESPALDO ES SOSPECHOSAMENTE PEQUEÑO: $BYTES bytes (< $RESPALDO_MIN_BYTES)."
  echo "      Un pg_dump fallido deja un .gz VÁLIDO de ~20 bytes. Esto no es una red de"
  echo "      seguridad. Revisa el contenedor $PROD_PG. (Suelo ajustable: RESPALDO_MIN_BYTES)"
  exit 1
fi
if ! gzip -t "$RESPALDO"; then
  echo "   🔴 EL RESPALDO ESTÁ CORRUPTO (gzip -t falló). No se toca producción."
  exit 1
fi
# Sin tuberías con `head`/`grep -q`: con `pipefail`, un SIGPIPE del lado izquierdo daría un
# falso rojo. Se lee a una variable y se busca dentro.
COLA="$(gzip -dc "$RESPALDO" | tail -5)"
case "$COLA" in
  *"PostgreSQL database dump complete"*) : ;;
  *)
    echo "   🔴 EL RESPALDO ESTÁ CORTADO: no termina en la marca de pg_dump."
    echo "      Pesa $BYTES bytes y descomprime, pero NO restaura entero. No se toca nada."
    exit 1 ;;
esac
echo "   ✓ $RESPALDO"
echo "     $BYTES bytes · gzip -t OK · termina en la marca de pg_dump"

# ═════════════════════════════════════════════════════════════════════════════
# PASO 4 — LA FOTO DE ANTES
# La verificación vieja comparaba "taller == producción" DESPUÉS de hacer producción = taller:
# era tautológica y CIEGA a la destrucción (borraba el catálogo PDF de producción y reportaba
# `✓ catalogo_pdf 0`, DAT-3). Ahora se guarda el ANTES y se compara contra el DESPUÉS **del
# mismo lado**. Un `0 == 0` ya no puede pasar por verde.
# ═════════════════════════════════════════════════════════════════════════════
echo "→ 4. Contenido ACTUAL de los dos lados"
contar_todo taller     "$TRABAJO/taller.txt"
contar_todo produccion "$TRABAJO/antes.txt"

printf "   %-22s %8s %12s\n" "tabla" "taller" "producción"
for T in "${TABLAS[@]}" "${CASCADA_PREVISTA[@]}" "${SOLO_CRECEN[@]}"; do
  printf "   %-22s %8s %12s\n" "$T" "$(conteo "$TRABAJO/taller.txt" "$T")" "$(conteo "$TRABAJO/antes.txt" "$T")"
done
echo "   ── INTOCABLES (los datos REALES; tienen que quedar IDÉNTICOS) ──"
for T in "${INTOCABLES[@]}"; do
  printf "   %-22s %8s %12s\n" "$T" "$(conteo "$TRABAJO/taller.txt" "$T")" "$(conteo "$TRABAJO/antes.txt" "$T")"
done

# ── ¿Alguna tabla PERDERÍA filas? Se comprueba ANTES de escribir, no después. ──
# La auditoría pedía fallar si una tabla queda con menos filas. Se hace DOS veces: aquí, ANTES
# de escribir —que es el chequeo que de verdad protege, porque evita el daño en vez de
# denunciarlo— y otra vez en el paso 14 sobre lo que ocurrió de verdad (por si el COPY entró
# a medias o cargó menos de lo que el taller decía tener).
echo "→ 5. ¿Alguna tabla perdería filas?"
PERDIDA=0
for T in "${TABLAS[@]}"; do
  A="$(conteo "$TRABAJO/taller.txt" "$T")"; B="$(conteo "$TRABAJO/antes.txt" "$T")"
  if [ "$A" -lt "$B" ]; then
    if en_lista "$T" ${ACEPTO_PERDER+"${ACEPTO_PERDER[@]}"}; then
      echo "   ⚠️  $T: $B → $A (AUTORIZADO con --acepto-perder $T)"
    else
      bloqueo "$T: producción tiene $B filas y el taller solo $A. Se perderían $((B - A))."
      PERDIDA=1
    fi
  fi
done
for T in ${ACEPTO_PERDER+"${ACEPTO_PERDER[@]}"}; do
  if en_lista "$T" "${INTOCABLES[@]}"; then
    echo "   🔴 --acepto-perder $T: NO. '$T' es un dato REAL del negocio, no se negocia."
    exit 1
  fi
done
if [ "$PERDIDA" = "0" ]; then echo "   ✓ ninguna tabla del catálogo mengua"; fi
PD_ANTES="$(conteo "$TRABAJO/antes.txt" precio_dia)"
if [ "$PD_ANTES" -gt 0 ]; then
  echo "   ⚠️  precio_dia: $PD_ANTES → 0 (RESET PREVISTO, ver cabecera). La dueña tiene que"
  echo "      cargar el precio del día de HOY, o no se venden tortas ni premezclas."
fi

# ═════════════════════════════════════════════════════════════════════════════
# PASO 6 — EL PLAN de la configuración y de las zonas (lo que se vería mal si se hiciera solo)
# ═════════════════════════════════════════════════════════════════════════════
echo "→ 6. CONFIGURACIÓN — qué claves viajarían (lista blanca, menos la lista negra)"

# `md5` para comparar valores largos (la `personalidad` son miles de caracteres) y `chr(9)`
# como separador para no pelearme con los escapes del heredoc. `translate` aplasta los saltos
# de línea: si no, una `personalidad` multilínea rompería el `read` línea a línea de abajo.
_consulta_config() {
  printf "%s\n" "
SELECT clave || chr(9) || coalesce(md5(valor), 'NULO') || chr(9)
    || coalesce(length(valor)::text, '0') || chr(9)
    || left(translate(coalesce(valor, ''), chr(10) || chr(13) || chr(9), '   '), 60)
  FROM configuracion
 WHERE clave IN ($(comillas "${CONFIG_BLANCA[@]}"))
   AND clave NOT IN ($(comillas "${CONFIG_NEGRA[@]}"))
 ORDER BY clave;"
}
_consulta_config | _psql taller     > "$TRABAJO/cfg_taller.txt"
_consulta_config | _psql produccion > "$TRABAJO/cfg_prod.txt"

CFG_CAMBIOS=0
while IFS=$'\t' read -r CLAVE HASH LARGO MUESTRA; do
  [ -n "$CLAVE" ] || continue
  PREV="$(awk -F'\t' -v c="$CLAVE" '$1 == c { print $2 }' "$TRABAJO/cfg_prod.txt")"
  if [ -z "$PREV" ]; then
    printf "   + %-22s NUEVA en producción (%s car.)  %s\n" "$CLAVE" "$LARGO" "$MUESTRA"
    CFG_CAMBIOS=$((CFG_CAMBIOS + 1))
  elif [ "$PREV" != "$HASH" ]; then
    printf "   ~ %-22s CAMBIA (%s car.)  %s\n" "$CLAVE" "$LARGO" "$MUESTRA"
    CFG_CAMBIOS=$((CFG_CAMBIOS + 1))
  else
    printf "   = %-22s igual\n" "$CLAVE"
  fi
done < "$TRABAJO/cfg_taller.txt"
echo "   ($CFG_CAMBIOS clave(s) cambiarían · vetadas por lista negra: ${#CONFIG_NEGRA[@]})"
if [ "$PISAR_PERSONALIDAD" = "0" ]; then
  echo "   ⚠️  'personalidad' NO viaja (usa --pisar-personalidad si esa es la decisión)."
  echo "      La auditoría dejó ABIERTO cuál de las dos manda: hay que LEER la de producción."
fi

echo "→ 7. ZONAS DE ENTREGA — sin ellas el bot no puede cobrar NADA (DAT-5)"
printf "SELECT lower(nombre) || '|' || costo || '|' || es_retiro FROM zonas_entrega ORDER BY 1;\n" \
  | _psql taller > "$TRABAJO/zonas_taller.txt"
printf "SELECT lower(nombre) FROM zonas_entrega ORDER BY 1;\n" \
  | _psql produccion > "$TRABAJO/zonas_prod.txt"
ZONAS_NUEVAS=0
while IFS='|' read -r Z COSTO RETIRO; do
  [ -n "$Z" ] || continue
  if grep -Fxq "$Z" "$TRABAJO/zonas_prod.txt"; then
    if [ "$PISAR_ZONAS" = "1" ]; then printf "   ~ %-28s ya existe → SE PISA (--pisar-zonas)\n" "$Z"
    else                             printf "   = %-28s ya existe → se respeta\n" "$Z"; fi
  else
    printf "   + %-28s NUEVA  costo=%s retiro=%s\n" "$Z" "$COSTO" "$RETIRO"
    ZONAS_NUEVAS=$((ZONAS_NUEVAS + 1))
  fi
done < "$TRABAJO/zonas_taller.txt"
ZONAS_PROD="$(conteo "$TRABAJO/antes.txt" zonas_entrega)"
ZONAS_TALLER="$(conteo "$TRABAJO/taller.txt" zonas_entrega)"
if [ "$ZONAS_PROD" = "0" ] && [ "$ZONAS_TALLER" = "0" ]; then
  bloqueo "NI EL TALLER NI PRODUCCIÓN tienen zonas. Promover así deja el cobro DECAPITADO:"
  echo "      'generar_datos_pago' devolverá ok:false en el 100% de los pedidos, incluso los"
  echo "      de retiro. Cárgalas en el panel del taller (o en el de producción) primero."
fi

echo "→ 8. MÉTODOS DE PAGO"
if [ "$PROMOVER_METODOS" = "1" ]; then
  echo "   --promover-metodos-pago: se AÑADEN los que falten, y entran DESACTIVADOS."
  echo "   Ninguno se modifica ni se borra. Activarlos es un acto humano, en el panel."
else
  echo "   NO viajan (por defecto). Son las cuentas REALES de la dueña: promoverlas desde el"
  echo "   taller le daría al primer cliente real la cuenta de PRUEBAS (DAT-4)."
  printf "   producción tiene %s método(s); el taller %s.\n" \
    "$(conteo "$TRABAJO/antes.txt" metodos_pago)" "$(conteo "$TRABAJO/taller.txt" metodos_pago)"
fi

# ═════════════════════════════════════════════════════════════════════════════
# FIN DEL ENSAYO
# ═════════════════════════════════════════════════════════════════════════════
if [ "$MODO" != "--aplicar" ]; then
  echo
  if [ "$BLOQUEOS" -ne 0 ]; then
    echo "  🔴 ENSAYO EN ROJO: $BLOQUEOS cosa(s) ABORTARÍAN con --aplicar (arriba, en rojo)."
    echo "     No se escribió nada. El respaldo de producción sí se hizo y es válido."
    exit 1
  fi
  echo "  ✅ ENSAYO EN VERDE: no se escribió nada. Para hacerlo de verdad: --aplicar"
  exit 0
fi

if [ "$BLOQUEOS" -ne 0 ]; then
  echo
  echo "  🔴 $BLOQUEOS BLOQUEO(S). No se toca producción. Resuélvelos o autorízalos"
  echo "     explícitamente con --acepto-perder <tabla>."
  exit 1
fi

if [ "${SIN_PREGUNTAR:-0}" != "1" ]; then
  echo
  echo "  Vas a escribir en PRODUCCIÓN ($PROD_HOST), con clientes reales."
  printf "  Escribe PROMOVER (mayúsculas) para continuar: "
  if [ -r /dev/tty ]; then read -r RESP < /dev/tty; else read -r RESP; fi
  [ "$RESP" = "PROMOVER" ] || { echo "  Cancelado. No se escribió nada."; exit 1; }
fi

# ═════════════════════════════════════════════════════════════════════════════
# PASO 9 — CONTENIDO PURO: volcado del taller → TRUNCATE + COPY en UNA transacción
# ═════════════════════════════════════════════════════════════════════════════
echo "→ 9. Copiando el CATÁLOGO del taller a producción…"
VOLCADO="$TRABAJO/contenido_${STAMP}.sql"
REMOTO_SQL="/tmp/masvida_contenido_${STAMP}.sql"

"${SSH[@]}" "root@$TALLER_HOST" "docker exec $TALLER_PG pg_dump --no-owner --no-acl --data-only \
  $(printf ' -t %s' "${TABLAS[@]}") -U postgres -d postgres" > "$VOLCADO"

# Un volcado vacío + TRUNCATE = producción sin catálogo. El pg_dump de arriba no lleva tubería
# remota (su código de salida SÍ llega), pero comprobarlo cuesta una línea y cierra el hueco.
LINEAS="$(wc -l < "$VOLCADO" | tr -d ' ')"
if ! grep -q '^COPY public.productos' "$VOLCADO"; then
  echo "   🔴 EL VOLCADO DEL TALLER NO TRAE PRODUCTOS ($LINEAS líneas). No se trunca nada."
  exit 1
fi
echo "   ✓ volcado del taller: $LINEAS líneas"

scp -q -i "$LLAVE" -o StrictHostKeyChecking=accept-new "$VOLCADO" "root@$PROD_HOST:$REMOTO_SQL"
"${SSH[@]}" "root@$PROD_HOST" "docker cp $REMOTO_SQL $PROD_PG:$REMOTO_SQL"

# TRUNCATE + COPY dentro de UNA transacción: si algo falla, no queda a medias.
# `\set ON_ERROR_STOP on` va ADEMÁS del `-v` de la línea de comandos (DAT-14): si el `\i`
# falla y psql sigue, el COMMIT confirma el TRUNCATE y producción se queda VACÍA. Que la
# red dependa de un flag que se puede borrar de un manotazo es exactamente lo que no queremos.
_psql_escribir produccion <<SQL
\set ON_ERROR_STOP on
BEGIN;
TRUNCATE $(IFS=,; echo "${TABLAS[*]}") CASCADE;
\i $REMOTO_SQL
COMMIT;
SQL

# ═════════════════════════════════════════════════════════════════════════════
# PASO 10 — CONFIGURACIÓN por lista blanca (nunca vaciando), zonas y métodos de pago
# ═════════════════════════════════════════════════════════════════════════════
echo "→ 10. CONFIGURACIÓN (lista blanca, ON CONFLICT DO UPDATE)…"
# El SQL se GENERA en el taller con `format(%L)`: `quote_literal` de Postgres se encarga del
# escapado (comillas, backslashes, saltos de línea de la `personalidad`), que a mano sería
# una fuga garantizada.
printf "%s\n" "
SELECT format(
  'INSERT INTO configuracion (clave, valor, updated_at) VALUES (%L, %L, now()) '
  'ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor, updated_at = now();',
  clave, valor)
  FROM configuracion
 WHERE clave IN ($(comillas "${CONFIG_BLANCA[@]}"))
   AND clave NOT IN ($(comillas "${CONFIG_NEGRA[@]}"))
 ORDER BY clave;" | _psql taller > "$TRABAJO/config.sql"
_psql_escribir produccion < "$TRABAJO/config.sql"

echo "→ 11. ZONAS DE ENTREGA (INSERT … ON CONFLICT — jamás TRUNCATE)…"
# `id` NO viaja: se deja que el SERIAL de producción lo asigne. Copiar los ids del taller
# chocaría con los de producción y, peor, `pedidos.zona_id` de un pedido viejo pasaría a
# apuntar a otra zona distinta. El conflicto se resuelve por el índice único de la 023,
# `ux_zona_nombre ON zonas_entrega (lower(nombre))`.
if [ "$PISAR_ZONAS" = "1" ]; then
  COLA_ZONA="ON CONFLICT (lower(nombre)) DO UPDATE SET costo = EXCLUDED.costo, referencias = EXCLUDED.referencias, es_retiro = EXCLUDED.es_retiro, disponible = EXCLUDED.disponible, orden = EXCLUDED.orden, updated_at = now();"
else
  COLA_ZONA="ON CONFLICT (lower(nombre)) DO NOTHING;"
fi
printf "%s\n" "
SELECT format(
  'INSERT INTO zonas_entrega (nombre, costo, referencias, es_retiro, disponible, orden) '
  'VALUES (%L, %L, %L, %L, %L, %L) $COLA_ZONA',
  nombre, costo, referencias, es_retiro, disponible, orden)
  FROM zonas_entrega ORDER BY orden, id;" | _psql taller > "$TRABAJO/zonas.sql"
_psql_escribir produccion < "$TRABAJO/zonas.sql"

if [ "$PROMOVER_METODOS" = "1" ]; then
  echo "→ 12. MÉTODOS DE PAGO (solo AÑADIR, y DESACTIVADOS)…"
  # `metodos_pago` no tiene índice único por el que hacer ON CONFLICT, así que el candado es
  # un `WHERE NOT EXISTS` por `lower(titulo)`. `activo = FALSE` es deliberado: aunque se cuele
  # una cuenta de pruebas, el bot NO se la ofrece a nadie hasta que un humano la active.
  printf "%s\n" "
SELECT format(
  'INSERT INTO metodos_pago (tipo, titulo, titular, banco, telefono, cedula, cuenta, correo, '
  'wallet, instrucciones, activo, orden) '
  'SELECT %L, %L, %L, %L, %L, %L, %L, %L, %L, %L, FALSE, %L '
  'WHERE NOT EXISTS (SELECT 1 FROM metodos_pago WHERE lower(titulo) = lower(%L));',
  tipo, titulo, titular, banco, telefono, cedula, cuenta, correo,
  wallet, instrucciones, orden, titulo)
  FROM metodos_pago ORDER BY orden, id;" | _psql taller > "$TRABAJO/metodos.sql"
  _psql_escribir produccion < "$TRABAJO/metodos.sql"
else
  echo "→ 12. MÉTODOS DE PAGO: no se tocan (por defecto). Se editan en el panel de producción."
fi

# ═════════════════════════════════════════════════════════════════════════════
# PASO 13 — LAS SECUENCIAS (DAT-13)
# Tras cargar filas con `id` explícito, el SERIAL de producción sigue donde estaba: el
# próximo INSERT (un producto nuevo desde el panel) chocaría con una PK ya ocupada. El
# `pg_dump --data-only` moderno suele traer los `setval`, pero "suele" no es una garantía y
# esto es idempotente: se recalcula desde el MAX(id) real y se acabó la duda.
# ═════════════════════════════════════════════════════════════════════════════
echo "→ 13. Reiniciando las secuencias…"
# El SELECT NO lleva `;` final A PROPÓSITO: `\gexec` ejecuta el BUFFER de consulta y luego
# corre cada fila devuelta como SQL. Con `;` la consulta ya se habría enviado, el buffer
# quedaría vacío y `\gexec` no ejecutaría NADA — en silencio, que es el peor final posible.
# Las tablas sin SERIAL (feriados, cuya PK es la fecha; configuracion, cuya PK es la clave)
# caen solas: `pg_get_serial_sequence` devuelve NULL y el WHERE las descarta.
_psql_escribir produccion <<SQL
\set ON_ERROR_STOP on
SELECT format('SELECT setval(%L, coalesce((SELECT max(%I) FROM %I), 0) + 1, false);',
              pg_get_serial_sequence(c.table_name, c.column_name), c.column_name, c.table_name)
  FROM information_schema.columns c
 WHERE c.table_schema = 'public'
   AND c.table_name IN ($(comillas "${TABLAS[@]}" "${CASCADA_PREVISTA[@]}" "${SOLO_CRECEN[@]}"))
   AND pg_get_serial_sequence(c.table_name, c.column_name) IS NOT NULL
 ORDER BY c.table_name
\gexec
SQL

# ═════════════════════════════════════════════════════════════════════════════
# PASO 14 — VERIFICACIÓN REAL: ANTES vs DESPUÉS, EN PRODUCCIÓN (DAT-3)
# La vieja comparaba taller contra producción DESPUÉS de igualarlos: no podía ver la
# destrucción ni queriendo. Esta compara producción consigo misma.
# ═════════════════════════════════════════════════════════════════════════════
echo "→ 14. Verificando (producción ANTES vs DESPUÉS)…"
contar_todo produccion "$TRABAJO/despues.txt"
FALLOS=0

printf "   %-22s %8s %10s\n" "tabla" "antes" "después"
for T in "${TABLAS[@]}" "${CASCADA_PREVISTA[@]}"; do
  A="$(conteo "$TRABAJO/antes.txt" "$T")"; B="$(conteo "$TRABAJO/despues.txt" "$T")"
  MARCA="✓"
  if [ "$B" -lt "$A" ]; then
    if en_lista "$T" "${CASCADA_PREVISTA[@]}" || en_lista "$T" ${ACEPTO_PERDER+"${ACEPTO_PERDER[@]}"}; then
      MARCA="⚠️ "
    else
      MARCA="🔴"; FALLOS=$((FALLOS + 1))
    fi
  fi
  printf "   %s %-20s %8s %10s\n" "$MARCA" "$T" "$A" "$B"
done

echo "   ── INTOCABLES: cualquier diferencia es una CATÁSTROFE ──"
for T in "${INTOCABLES[@]}"; do
  A="$(conteo "$TRABAJO/antes.txt" "$T")"; B="$(conteo "$TRABAJO/despues.txt" "$T")"
  if [ "$A" = "$B" ]; then printf "   ✓ %-20s %8s (intacta)\n" "$T" "$B"
  else printf "   🔴 %-20s antes=%s DESPUÉS=%s\n" "$T" "$A" "$B"; FALLOS=$((FALLOS + 1)); fi
done

echo "   ── SOLO PUEDEN CRECER ──"
for T in "${SOLO_CRECEN[@]}"; do
  A="$(conteo "$TRABAJO/antes.txt" "$T")"; B="$(conteo "$TRABAJO/despues.txt" "$T")"
  if [ "$B" -ge "$A" ]; then printf "   ✓ %-20s %8s → %s\n" "$T" "$A" "$B"
  else printf "   🔴 %-20s MENGUÓ: %s → %s\n" "$T" "$A" "$B"; FALLOS=$((FALLOS + 1)); fi
done

if [ "$FALLOS" -ne 0 ]; then
  echo
  echo "  🔴 $FALLOS PROBLEMA(S): la promoción quedó A MEDIAS o destruyó datos. NO abrir el bot."
  echo "     RESTAURAR:  gzip -dc '$RESPALDO' \\"
  echo "                   | ssh -i '$LLAVE' root@$PROD_HOST 'docker exec -i $PROD_PG psql -U postgres -d postgres'"
  exit 1
fi

# Limpieza de los temporales remotos solo si todo fue bien (si falló, ahí quedan para mirarlos).
"${SSH[@]}" "root@$PROD_HOST" "rm -f $REMOTO_SQL; docker exec $PROD_PG rm -f $REMOTO_SQL"

echo
echo "══════════════════════════════════════════════════════════════"
echo "  ✅ PROMOCIÓN COMPLETA. Respaldo: $RESPALDO"
echo "══════════════════════════════════════════════════════════════"
echo "  AHORA, EN ESTE ORDEN (DAT-7: los detectores nunca corrían contra producción, y por eso"
echo "  el desfase de producción no lo vio nadie durante semanas):"
echo
echo "   1. Los DOS detectores DENTRO de producción (son de SOLO LECTURA, se pueden correr ya):"
echo "        BOT=\$(ssh -i '$LLAVE' root@$PROD_HOST \"docker ps --format '{{.Names}}' | grep -i bot | head -1\")"
echo "        ssh -i '$LLAVE' root@$PROD_HOST \"docker exec -w /app -e PYTHONPATH=/app \\\$BOT python scripts/probar_migraciones.py\""
echo "        ssh -i '$LLAVE' root@$PROD_HOST \"docker exec -w /app -e PYTHONPATH=/app \\\$BOT python scripts/probar_drift.py\""
echo "      (el flujo 'deploy.yml -f destino=produccion' ya los corre solo en cada despliegue)"
echo
echo "   2. La dueña carga el PRECIO DEL DÍA de hoy en el panel de PRODUCCIÓN."
echo "      Sin eso, tortas y premezclas NO se venden: precio_dia quedó en 0 a propósito."
echo
echo "   3. Comprobar que el cobro está VIVO: un pedido de prueba con zona de entrega."
echo "      Si 'generar_datos_pago' devuelve ok:false, faltan ZONAS (paso 11 de este script)."
echo
echo "   4. Revisar en el panel de producción: dueno_telefono, métodos de pago y la tasa."
echo "      Este script NO los toca A PROPÓSITO — son del entorno, no del contenido."
echo
echo "   5. Solo entonces: vaciar NUMEROS_PERMITIDOS para que el bot atienda a los reales."
