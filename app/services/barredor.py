"""EL BARREDOR — lo que nadie limpia, y EL VIGILANTE DEL BOT CALLADO.

🔴 POR QUÉ (auditoría 2026-08-02, SIL-13 + SIL-3b + VIG.1). En todo el stack NO HAY UN SOLO
SCHEDULER: `celery_app.py` no declara `beat_schedule`, el worker corre sin `-B` y ninguna imagen
trae cron. Consecuencia: nada que quede a medias se arregla ni se REPORTA solo. La única forma de
despausar un chat es un clic humano, y la única de enterarse de que un cliente lleva horas
esperando es que la dueña abra el panel y lo vea.

Y el fallo que esto vigila es MUDO POR CONSTRUCCIÓN: hasta hoy el webhook respondía 200 pasara lo
que pasara, así que Meta no reintentaba y el mensaje desaparecía sin dejar rastro — ni en el
buffer, ni en la tabla `mensajes`, con el contenedor en VERDE. (Eso ya se cerró en
`webhook/router.py`, F1; esto es la otra mitad: enterarse cuando aun así pase algo.)

TRES REGLAS DURAS DEL DISEÑO, cada una con su porqué:

· **CERO LLM.** Todos los textos de aquí son FIJOS. Tiene que funcionar justo cuando el modelo
  está caído o sin saldo, que es cuando más falta hace. (Contraste deliberado:
  `_notificar_cliente_pago`, tasks.py:1225, SÍ redacta con el agente — porque le habla AL CLIENTE.)
· **NUNCA le escribe al CLIENTE.** Solo a la dueña y a la bandeja. La regla dura de Meta ("ningún
  envío proactivo automático sin aprobación humana", CLAUDE.md §3) queda intacta, y la de
  humanizar tampoco se rompe: los mensajes a la dueña YA son textos fijos en todo el código
  (`_avisar_duena_abuso`, `_avisar_intervencion`, `_avisar_pago_en_chat_pausado`).
· **NO MUTA NADA DEL DINERO NI DE LAS PAUSAS.** Solo escribe filas de `intervenciones` y cierra
  avisos cuya resolución está PROBADA por un mensaje humano posterior. Ver `resumen_pendientes`.

DÓNDE CORRE (hoy, sin scheduler): en el bucle del `lifespan` de la API (`app/main.py`), a mano por
SSH con `scripts/barrer.py`, y el día que vuelva Coolify, también desde un `celery beat`. La
concesión de Postgres hace que sumar puertas NUNCA duplique el barrido.
"""
import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Cada cuánto barre, como mucho. Es también el ancho de la concesión.
MINUTOS_ENTRE_CORRIDAS = 5
# Cuánto puede llevar un cliente sin respuesta antes de que sea una AVERÍA y no una demora.
# El turno sano son ~35s (`settings.buffer_segundos` = 15s de buffer + lo que piensa el modelo).
# 10 minutos es holgadísimo para un turno normal y sigue estando a tiempo de salvar la venta.
MINUTOS_SIN_RESPUESTA = 10
# TECHO DE 24H, y no es un detalle: (a) pasadas 24h la ventana de Meta está CERRADA y no se puede
# hacer nada por chat de todas formas; (b) sin techo, la PRIMERA corrida tras desplegar esto
# abriría un aviso por CADA cliente que alguna vez escribió sin recibir respuesta — meses de
# historia de golpe en la bandeja. Un detector que inunda se ignora, y entonces no detecta nada.
HORAS_MAX = 24
# Más de esto no son N clientes colgados: es el SISTEMA caído. Se avisa UNA vez, no N veces.
MAX_AVISOS = 8

CLAVE_TURNO = "barredor_ultima_corrida"
# Ancho FIJO a propósito: así comparar como TEXTO es comparar como FECHA. `isoformat()` mete
# microsegundos unas veces sí y otras no, y ahí el orden lexicográfico se rompe en silencio.
FORMATO_INSTANTE = "%Y-%m-%dT%H:%M:%SZ"
# El teléfono del sistema, para los avisos que no son de un cliente concreto. Empieza por "__", así
# que la lista blanca lo deja pasar (tasks.py:377) y el propio vigilante lo ignora.
TEL_SISTEMA = "__sistema__"

# El motivo nuevo de la bandeja. Es el ÚNICO aviso que nace sin que el bot haga nada —
# precisamente porque el bot no hizo nada.
MOTIVO = "bot_callado"


# ─── La concesión: ¿me toca a MÍ barrer? ─────────────────────────────

async def tomar_turno(session, minutos: int = MINUTOS_ENTRE_CORRIDAS) -> bool:
    """¿Me toca a MÍ barrer? Un UPSERT condicional = la concesión.

    Hace de reloj y de candado a la vez. Vive en Postgres y no en Redis por dos razones: Redis
    puede ser JUSTO lo que esté caído, y esta sentencia es atómica aunque corran varios procesos
    de uvicorn, o alguien lance `scripts/barrer.py` por SSH a la vez, o algún día llegue el beat:
    solo UNO se lleva el `rowcount = 1`.

    Va como INSERT … ON CONFLICT DO UPDATE … WHERE, y no como UPDATE a secas, para que se
    AUTORREPARE: si por lo que sea la migración 028 no llegó a aplicarse, un UPDATE no encontraría
    la fila y el barredor no correría NUNCA, en silencio. Así la fila se crea sola en la primera
    pasada. (La 028 igual la siembra: es lo que hace que el testigo exista desde el minuto cero.)

    La clave NO está en `CLAVES_CONFIG` (api/router.py:104), así que el `PUT /configuracion` del
    panel —que descarta en silencio lo que no conoce— no la puede pisar nunca. Mismo criterio,
    dicho igual, que `tools_activas` (api/router.py:1122).
    """
    ahora = datetime.now(UTC)
    res = await session.execute(
        text(
            "INSERT INTO configuracion (clave, valor, updated_at) "
            "VALUES (:clave, :ahora, now()) "
            "ON CONFLICT (clave) DO UPDATE SET valor = :ahora, updated_at = now() "
            " WHERE configuracion.valor IS NULL OR configuracion.valor < :umbral"
        ),
        {
            "clave": CLAVE_TURNO,
            "ahora": ahora.strftime(FORMATO_INSTANTE),
            "umbral": (ahora - timedelta(minutes=minutos)).strftime(FORMATO_INSTANTE),
        },
    )
    await session.commit()
    return bool(res.rowcount)


async def ultima_corrida(session) -> datetime | None:
    """CUÁNDO barrió por última vez alguien. None si nunca (o si el valor no se entiende).

    🔴 EL TESTIGO DEL TESTIGO (F5). El vigilante también necesita quien lo vigile: si el bucle del
    lifespan muere por una excepción fuera de su try, la red desaparece EN SILENCIO y nadie lo
    nota — el mismo modo de fallo que vinimos a matar, una capa más arriba. `GET /salud` lee esto
    y devuelve `degradado` si la marca está rancia. Que el vigilante esté muerto tiene que doler.
    """
    from app.models import Configuracion

    fila = (await session.execute(
        select(Configuracion.valor).where(Configuracion.clave == CLAVE_TURNO)
    )).scalars().first()
    if not fila:
        return None
    try:
        return datetime.strptime(fila.strip(), FORMATO_INSTANTE).replace(tzinfo=UTC)
    except ValueError:
        return None


# ─── El aviso a la dueña (texto FIJO, cero LLM) ──────────────────────

async def _whatsapp_duena(session, texto: str) -> None:
    """UN WhatsApp a la dueña, best-effort y con TEXTO FIJO.

    Cero LLM: esto tiene que salir justo cuando el modelo está muerto o sin saldo. Si no sale —sin
    número configurado, o su propia ventana de 24h cerrada— el aviso YA está en la bandeja, que
    nunca falla. Mismo criterio y mismo camino que `_avisar_a_la_duena` (tasks.py:1171).

    El import va DENTRO a propósito (igual que en tasks.py): así el banco puede sustituir
    `meta_client.enviar_texto` por un espía y ninguna prueba manda un WhatsApp de verdad.
    """
    from app.models import Configuracion
    from app.services.meta_client import enviar_texto

    try:
        fila = (await session.execute(
            select(Configuracion).where(Configuracion.clave == "dueno_telefono")
        )).scalar_one_or_none()
        destino = (fila.valor if fila else None) or settings.dueno_telefono
        if destino:
            await enviar_texto(destino, texto)
    except Exception:  # noqa: BLE001 — el aviso de la bandeja es el que manda
        logger.exception("BARREDOR: no se pudo avisar por WhatsApp a la dueña")


# ─── EL VIGILANTE DEL BOT CALLADO ────────────────────────────────────

# 🔴 EL ANCLA ES `clientes.ultimo_entrante_at`, Y ESO LO DECIDE TODO.
#
# Lo escribe el WEBHOOK (`_marcar_entrante`, webhook/router.py:103) en la PRIMERA línea de
# `_procesar_entrante`: antes de `marcar_leido_y_escribiendo`, antes del tope anti-abuso y antes de
# `_encolar_mensaje` — que es donde vive el `rc.ya_procesado` que revienta con Redis caído. La fila
# de `mensajes`, en cambio, la escribe el WORKER, después. Traducido: anclado AQUÍ, el vigilante VE
# los mensajes que nunca llegaron a la tabla `mensajes`. Anclado en `mensajes`, NO los vería —
# justo en el fallo que vinimos a cazar.
#
# "Respondido" = existe un mensaje 'assistant' (el bot) u 'owner' (la dueña, por el panel o por su
# celular) con fecha >= la del último entrante. Cubre los cuatro carriles: texto (tasks.py:448),
# comprobante (tasks.py:781), voz y eventos (tasks.py:1129).
#
# El '>=' y no '>' es a propósito: `_marcar_entrante` y `Mensaje.created_at` usan el MISMO reloj
# (`now_utc()` en Python), y en el caso raro de que coincidan al microsegundo, el lado seguro es
# dar el turno por respondido — un falso NEGATIVO cuesta un aviso menos; un falso POSITIVO le
# cuesta la confianza al detector entero.
SQL_SIN_RESPUESTA = text("""
SELECT c.telefono, c.nombre, c.ultimo_entrante_at
  FROM clientes c
 WHERE c.ultimo_entrante_at IS NOT NULL
   AND c.ultimo_entrante_at <  now() - make_interval(mins  => :minutos)
   AND c.ultimo_entrante_at >  now() - make_interval(hours => :horas)
   AND c.bot_pausado = false
   AND left(c.telefono, 2) <> '__'
   AND NOT EXISTS (
         SELECT 1 FROM mensajes m
          WHERE m.cliente_telefono = c.telefono
            AND m.rol IN ('assistant', 'owner')
            AND m.created_at >= c.ultimo_entrante_at)
 ORDER BY c.ultimo_entrante_at
 LIMIT 100
""")


async def _paso_el_tope(telefono: str) -> bool:
    """¿Este cliente pasó HOY el tope anti-abuso? (best-effort, contra Redis).

    🔴 LA SEXTA ANTI-INUNDACIÓN, y ningún ítem la vio. Cuando un cliente supera
    `limite_mensajes_cliente_dia` (80), el webhook DEJA DE RESPONDERLE A PROPÓSITO
    (`_excede_tope`, webhook/router.py:434) — pero `_marcar_entrante` ya le movió el reloj, así que
    para el vigilante es idéntico a un cliente colgado por una avería. Sin este filtro, cada troll
    o cada bucle abriría un aviso de "el bot no le contestó" encima del WhatsApp de abuso que la
    dueña YA recibió por ese mismo cliente. Dos alarmas para un caso que no es una avería.

    Se LEE (nunca se escribe) la MISMA clave que pone el aviso de abuso
    (`abuso_avisado:{tel}:{hoy}`, redis_client.py:160): si existe, ella ya está enterada. Se va al
    cliente crudo y no a `get_cache` porque esa clave no es del carril `cache:` — es de
    `redis_client`, y aquí solo se asoma.

    Si Redis no contesta, devuelve False (NO filtra) a propósito: Redis caído es exactamente el
    incidente que este vigilante existe para cazar. Ante la duda, avisar.
    """
    try:
        from app.services import redis_client as rc

        return await rc._client().get(f"abuso_avisado:{telefono}:{rc._hoy()}") is not None
    except Exception:  # noqa: BLE001 — sin Redis no hay filtro, y así debe ser
        return False


async def vigilar_bot_callado(session, minutos: int = MINUTOS_SIN_RESPUESTA) -> dict:
    """Clientes que escribieron y NADIE les contestó. Deja el aviso en la bandeja y UN WhatsApp a
    la dueña. Cero LLM, cero mensajes al cliente.

    LAS ANTI-INUNDACIONES, cada una tapando un falso positivo que SÍ existe en este código:
      1. `bot_pausado = false` (en el SQL) — si el chat está pausado lo tiene una PERSONA
         (api/router.py:1844 desde el panel, webhook/router.py:182 desde el celular) o el bot está
         escalando: el silencio ahí es CORRECTO.
      2. Techo de 24h (en el SQL) — sin él, la primera corrida tras desplegar vacía meses de
         historia en la bandeja. Y pasadas 24h la ventana de Meta está cerrada igual.
      3. `left(telefono,2) <> '__'` (en el SQL) — el simulador del panel y los bancos no son gente.
      4. `_numero_permitido` — en el taller el bot SOLO le contesta a la lista blanca y a los demás
         les guarda el mensaje sin responder, A PROPÓSITO (tasks.py:357). Sin esto, el taller sería
         una alarma continua — y una alarma continua se apaga.
      5. Un solo aviso vivo por chat — el mismo candado literal que ya usa `pedir_ayuda`
         (tools.py:1949). Si la dueña todavía no ha entrado, no se le apila un aviso cada 5 min.
      6. El tope anti-abuso (`_paso_el_tope`) — silencio deliberado, no avería.
      7. `MAX_AVISOS` — 9 colgados a la vez NO son 9 clientes: es el SISTEMA caído, y 9 avisos
         esconderían justo el hecho que importa.

    ⚠️ EFECTO CONOCIDO Y ACEPTADO: mientras este aviso siga PENDIENTE, `pedir_ayuda` no dejará
    otro para ese mismo chat (el `ya_hay` de tools.py:1949 gobierna el alta Y el WhatsApp). Se
    asume: el vigilante solo dispara cuando NADIE respondió, o sea cuando ese carril ya está roto,
    y `cerrar_avisos_ya_atendidos` lo cierra en cuanto la dueña escriba. El arreglo de fondo de ese
    `ya_hay` es otro ítem (SIL-9), de otra tanda.
    """
    from app.models import Intervencion
    from app.workers.tasks import _numero_permitido

    filas = (await session.execute(
        SQL_SIN_RESPUESTA, {"minutos": minutos, "horas": HORAS_MAX}
    )).all()
    if not filas:
        return {"colgados": 0, "avisos": 0}

    # EL ORDEN DE LOS FILTROS IMPORTA, y no es estética: el candado del aviso vivo es UNA consulta
    # para todos, mientras que la lista blanca y el tope son una consulta POR CLIENTE cada uno. Como
    # esto corre en el MISMO event loop que atiende el webhook, primero se descarta con el barato:
    # en régimen normal, a partir de la segunda pasada casi todos los candidatos ya tienen su aviso
    # vivo y no se paga por ellos ni una consulta más.
    con_aviso = set((await session.execute(
        select(Intervencion.cliente_telefono).where(Intervencion.estado == "pendiente")
    )).scalars().all())
    candidatos = [f for f in filas if f.telefono not in con_aviso]
    if not candidatos:
        return {"colgados": len(filas), "avisos": 0}

    nuevos = [
        c for c in candidatos
        if await _numero_permitido(c.telefono) and not await _paso_el_tope(c.telefono)
    ]
    if not nuevos:
        return {"colgados": len(filas), "avisos": 0}

    if len(nuevos) > MAX_AVISOS:
        # Y el aviso MASIVO también tiene su candado, que no es un detalle: los clientes de una
        # caída nunca reciben aviso individual, así que sin esto cada pasada volvería a contarlos y
        # la dueña se comería un WhatsApp de "todo está caído" CADA CINCO MINUTOS mientras dure la
        # avería — que es justo cuando menos falta hace repetírselo. Se avisa una vez; el siguiente
        # sale cuando ella cierre el anterior, o sea cuando ya se enteró.
        if TEL_SISTEMA in con_aviso:
            return {"colgados": len(filas), "avisos": 0, "masivo": True}
        session.add(Intervencion(
            cliente_telefono=TEL_SISTEMA,
            motivo=MOTIVO,
            detalle=(
                f"{len(nuevos)} clientes escribieron hace más de {minutos} minutos y el bot NO le "
                "respondió a NINGUNO. Esto no es un chat colgado: algo del sistema está caído "
                "(Redis, el worker o el modelo). Avisa a Enova y mira /salud."
            ),
            mensaje_cliente="(varios clientes esperando)",
        ))
        await session.commit()
        await _whatsapp_duena(
            session,
            f"🔴 {len(nuevos)} clientes llevan más de {minutos} min esperando y el bot NO le "
            "contestó a ninguno. Algo está caído. Entra al panel y avisa a Enova.",
        )
        return {"colgados": len(filas), "avisos": 1, "masivo": True}

    for c in nuevos:
        quien = c.nombre or c.telefono
        session.add(Intervencion(
            cliente_telefono=c.telefono,
            motivo=MOTIVO,
            detalle=(
                f"{quien} te escribió y el bot NO le respondió NADA (lleva más de {minutos} "
                "minutos esperando). Puede que su mensaje ni siquiera se haya llegado a guardar. "
                "Entra al chat y contéstale tú."
            ),
            mensaje_cliente="(el bot se quedó callado)",
        ))
    await session.commit()

    lista = "\n".join(f"· {c.nombre or c.telefono}" for c in nuevos)
    await _whatsapp_duena(
        session,
        f"🔴 EL BOT NO LE CONTESTÓ A {len(nuevos)} CLIENTE(S) (llevan más de {minutos} min "
        f"esperando):\n{lista}\n\nEntra al panel y contéstales tú.",
    )
    return {"colgados": len(filas), "avisos": len(nuevos)}


# ─── Cerrar lo que una PERSONA ya atendió ────────────────────────────

async def cerrar_avisos_ya_atendidos(session) -> int:
    """Cierra los avisos de la bandeja que una PERSONA ya atendió — lo prueba un mensaje
    `rol='owner'` posterior al aviso.

    Nace del hallazgo del ECO: contestando desde el CELULAR, `_procesar_eco` pausa y guarda la
    burbuja pero NUNCA toca `intervenciones` (webhook/router.py:136), así que el aviso se quedaba
    en rojo para siempre. Esta es la RED; el arreglo de origen va aparte (SIL-9).

    🔴 `chat_tomado` NO SE CIERRA NUNCA DESDE AQUÍ (corrección C4 de la revisión cruzada, y es
    CRÍTICA). Ese aviso no es "algo pendiente de atender": es EL ÚNICO BOTÓN que le devuelve el
    chat al bot — `/intervenciones/{id}/resolver` con `reactivar=True` (api/router.py:2181) es lo
    que pone `bot_pausado=False` y dispara el retomar. Y nace ANTES de la burbuja `owner` que lo
    provoca, así que este UPDATE lo cerraría a los 5 minutos de crearse: el aviso desaparecería de
    "Te esperan", nadie apretaría nada, y el cliente se quedaría con el bot MUDO PARA SIEMPRE.
    Justo el desastre que ese aviso vino a evitar, reintroducido por el barredor.
    """
    res = await session.execute(text(
        "UPDATE intervenciones i SET estado = 'resuelta', resuelta_at = now() "
        " WHERE i.estado = 'pendiente' "
        "   AND i.motivo <> 'chat_tomado' "
        "   AND EXISTS (SELECT 1 FROM mensajes m "
        "                WHERE m.cliente_telefono = i.cliente_telefono "
        "                  AND m.rol = 'owner' AND m.created_at > i.created_at)"
    ))
    await session.commit()
    return res.rowcount or 0


# ─── El retrato de lo rancio: SE CUENTA, NO SE TOCA ──────────────────

async def resumen_pendientes(session) -> dict:
    """UN retrato de lo que lleva días sin tocarse. NO cambia NADA: solo cuenta.

    🔴 EL BARREDOR NO DESPAUSA A NADIE. NUNCA. Y es una decisión, no un olvido (SIL-13.6):
    · `pausado_por='dueña'` NO CADUCA POR DECISIÓN DE MAIRED — está escrito literalmente en
      api/router.py:1578. Despausarlo sería revertir una decisión SUYA y meter al bot en una
      conversación que ella tomó: el desastre exacto que la migración 020 vino a evitar. Lo que
      falta no es limpieza automática, es que ella se ENTERE — y enterarse es CONTAR, no MUTAR.
    · `pausado_por='bot'` solo lo ponen DOS motivos: `pide_persona` y `reclamo`
      (`_MOTIVOS_DE_PAUSA`, tools.py:1904). O sea: el cliente pidió hablar con una PERSONA o está
      reclamando. Devolverle el bot automáticamente es justo lo contrario de lo que pidió — y el
      bot ya mintió una vez sobre ser humana (ensayo del 2026-07-13, tasks.py:466).

    🔴 Y NO CANCELA PEDIDOS (SIL-13.7). Fui a mirar si un 'esperando_pago' zombi hace daño AL
    DINERO y NO lo hace: `get_pedido_esperando_pago` ordena por `created_at DESC` (tools.py:1684),
    así que un pedido abandonado de hace tres semanas jamás le roba el comprobante a uno nuevo; y
    `/reporte` suma SOLO pagos en 'confirmado' (api/router.py:287). Lo único que ensucia es
    `ventas_hoy_usd`, que incluye cualquier pedido de HOY no cancelado (api/router.py:252) — y eso
    se corrige solo mañana. Cancelarlos automáticamente sería tocar la máquina de estados del
    DINERO para arreglar RUIDO: la peor relación riesgo/beneficio de todo el bloque. Además la
    regla del proyecto dice que 'pagado' solo se fija desde `/confirmar` (CLAUDE.md §3); por
    simetría, 'cancelado' lo fija una persona, no un barredor. Si la dueña quiere limpiarlos, el
    camino ya existe y es humano: `DELETE /api/pedidos/{id}` o el `PATCH` desde el panel.
    """
    pausas_bot = (await session.execute(text(
        "SELECT count(*) FROM clientes WHERE bot_pausado AND pausado_por = 'bot' "
        "  AND ultima_interaccion < now() - interval '4 hours'"
    ))).scalar() or 0
    pausas_duena = (await session.execute(text(
        "SELECT count(*) FROM clientes WHERE bot_pausado AND pausado_por = 'dueña' "
        "  AND ultima_interaccion < now() - interval '48 hours'"
    ))).scalar() or 0
    bandeja = (await session.execute(text(
        "SELECT count(*) FROM intervenciones WHERE estado = 'pendiente' "
        "  AND created_at < now() - interval '4 hours'"
    ))).scalar() or 0
    pedidos = (await session.execute(text(
        "SELECT count(*) FROM pedidos WHERE estado = 'esperando_pago' "
        "  AND left(cliente_telefono, 2) <> '__' "
        "  AND updated_at < now() - interval '7 days'"
    ))).scalar() or 0
    return {"pausas_escalada": int(pausas_bot), "pausas_duena": int(pausas_duena),
            "bandeja_rancia": int(bandeja), "pedidos_sin_pagar": int(pedidos)}


# ─── EL RESCATADOR DE BUFFERS HUÉRFANOS (SIL-3b) ─────────────────────

# Buffers vistos en la pasada ANTERIOR: {clave_redis: instante en que se vio}. Vive en la memoria
# del proceso a propósito (ver `rescatar_buffers_huerfanos`).
_VISTOS: dict[str, float] = {}
# Un buffer tiene que sobrevivir DOS pasadas separadas por esto para considerarse huérfano. El
# countdown normal es `settings.buffer_segundos` (15s): 60s deja margen de sobra para no pisarle
# el turno a una tarea que simplemente todavía no ha vencido.
SEGUNDOS_HUERFANO = 60


async def rescatar_buffers_huerfanos() -> dict:
    """RED DE ÚLTIMA INSTANCIA para el mensaje que se quedó SIN TAREA que lo dispare.

    🔴 EL CASO REAL: un mensaje de texto viaja con `countdown=15s` (webhook/router.py:371) y ese
    rato lo pasa RESERVADO en la memoria del worker. El despliegue de este taller es `docker cp` +
    restart, así que todo cliente que escribió en esos 15 segundos se queda sin que NADIE dispare
    su turno, y su texto se pudre en `buffer:{telefono}` hasta expirar (1 hora). Nadie se entera:
    es exactamente la semana de mensajes mudos, en pequeño, cada despliegue.

    CÓMO SE DETECTA, y por qué así: un buffer es huérfano si se le ha visto en DOS pasadas
    separadas por más de 60s y NUNCA tuvo `lock:` (nadie lo está atendiendo). Se hace con memoria
    del proceso en vez de calcular la edad desde el TTL (`3600 - ttl`) porque eso obligaría a
    copiar el 3600 de `redis_client.agregar_a_buffer:38` aquí: el día que alguien cambie ese
    número, el rescatador empezaría a mentir EN SILENCIO. Un número copiado es una bomba de
    relojería; dos pasadas no dependen de nada.

    Perder la memoria al reiniciar solo retrasa el rescate ~2 minutos: es una RED, no el camino
    normal. Y `scan_iter` es SCAN, no KEYS: no bloquea el Redis que sostiene locks, historial y
    cobros.
    """
    from app.services import redis_client as rc
    from app.workers.tasks import procesar_buffer

    ahora = time.monotonic()
    vivos: set[str] = set()
    rescatados: list[str] = []
    c = rc._client()
    async for clave in c.scan_iter(match="buffer:*", count=100):
        vivos.add(clave)
        telefono = clave.split(":", 1)[1]
        if await c.exists(f"lock:{telefono}"):
            # Hay un turno EN CURSO: ese ya lo va a vaciar. Y se olvida lo visto, para que al
            # soltarse el lock vuelva a contar dos pasadas limpias.
            _VISTOS.pop(clave, None)
            continue
        primera = _VISTOS.get(clave)
        if primera is None:
            _VISTOS[clave] = ahora
            continue
        if ahora - primera < SEGUNDOS_HUERFANO:
            continue
        logger.warning("BARREDOR: rescatando el buffer huérfano de %s", telefono)
        # El `nombre` se pierde (no viaja en el buffer). No pasa nada: `_guardar_en_panel` y
        # `_guardar_entrante` solo lo escriben `if nombre and not cliente.nombre`, así que un
        # None no pisa el que ya haya.
        procesar_buffer.apply_async((telefono, None), countdown=1)
        rescatados.append(telefono)
        _VISTOS.pop(clave, None)
    # Los que ya no están (se vaciaron solos) se olvidan: si no, este dict crecería para siempre.
    for muerto in [k for k in _VISTOS if k not in vivos]:
        _VISTOS.pop(muerto, None)
    return {"buffers_vistos": len(vivos), "rescatados": rescatados}


# ─── El orquestador ──────────────────────────────────────────────────

async def barrer(*, forzar: bool = False) -> dict:
    """UNA pasada del BARREDOR. Devuelve el resumen (lo imprime `scripts/barrer.py`).

    `forzar=True` se salta la concesión: es para lanzarlo a mano por SSH cuando la dueña llama
    diciendo "el bot no contesta" y no se quiere esperar al turno siguiente. Ojo: forzado NO
    refresca la marca `barredor_ultima_corrida`, a propósito — una corrida manual no puede tapar
    que el bucle automático esté muerto (F5).
    """
    from app.services.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        if not forzar and not await tomar_turno(session):
            return {"corrio": False, "motivo": f"otro proceso barrió hace <{MINUTOS_ENTRE_CORRIDAS} min"}

        # EL BOT APAGADO DESDE EL PANEL NO ES UNA AVERÍA: es una decisión de la dueña
        # (`_bot_activo`, tasks.py:249). Si se ignorara, cada cliente que escribe con el bot
        # apagado abriría un aviso, y ella se comería una bandeja llena por haber apretado un
        # botón a propósito.
        from app.workers.tasks import _bot_activo
        if not await _bot_activo():
            logger.info("BARREDOR: el bot está apagado desde el panel; no se vigila")
            return {"corrio": True, "bot_apagado": True}

        vigilancia = await vigilar_bot_callado(session)
        cerrados = await cerrar_avisos_ya_atendidos(session)
        resumen = await resumen_pendientes(session)
    return {"corrio": True, **vigilancia, "avisos_cerrados": cerrados, "pendientes": resumen}


async def una_pasada() -> dict:
    """LO QUE HACE EL BUCLE DEL `lifespan` EN CADA TICK: el rescate y el barrido, en ese orden.

    Van FUNDIDOS en una sola pasada (y en un solo `asyncio.create_task`) a propósito: son dos
    ritmos distintos —el rescate mira Redis cada minuto, el barrido mira Postgres cada cinco— pero
    dos bucles sueltos serían dos tareas que vigilar, dos sitios donde morir en silencio y dos
    testigos que escribir. El barrido se autolimita SOLO, con la concesión de Postgres: llamarlo
    cada minuto no lo hace correr cada minuto.

    El rescate va PRIMERO porque es el que puede salvar un mensaje que todavía está a tiempo; el
    barrido solo REPORTA. Y cada uno se cae por su cuenta: que Redis esté muerto no puede impedir
    que el vigilante avise de que Redis está muerto.
    """
    salida: dict = {}
    try:
        salida["rescate"] = await rescatar_buffers_huerfanos()
    except Exception:  # noqa: BLE001 — el rescate no puede llevarse por delante al vigilante
        logger.exception("BARREDOR: falló el rescate de buffers huérfanos")
        salida["rescate"] = {"error": True}
    try:
        salida["barrido"] = await barrer()
    except Exception:  # noqa: BLE001
        logger.exception("BARREDOR: falló la pasada del barrido")
        salida["barrido"] = {"error": True}
    return salida
