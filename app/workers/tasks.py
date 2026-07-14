import asyncio
import json
import logging
import os
import re

from app.agent.agent import (
    leer_comprobante,
    redactar_mensaje,
    responder,
    transcribir_audio,
)
from app.config import get_settings
from app.services import redis_client as rc
from app.services.db import get_session_factory
from app.services.meta_client import descargar_media, enviar_texto
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)
settings = get_settings()

# Loop de asyncio persistente POR PROCESO del worker.
#
# Celery (prefork) corre tareas sincronas. Usar asyncio.run() en cada tarea
# crea y CIERRA un loop nuevo cada vez, dejando invalidas las conexiones async
# cacheadas (redis / engine de la BD) -> a partir de la 2da tarea explota con
# "RuntimeError: Event loop is closed". Reusar UN solo loop por proceso mantiene
# esas conexiones vivas entre tareas. Cada proceso de Celery tiene el suyo.
_LOOP = None


def _run(coro):
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_LOOP)
    return _LOOP.run_until_complete(coro)


async def _guardar_en_panel(
    telefono: str, nombre: str | None, texto_usuario: str, partes: list[dict]
) -> None:
    """Persiste la conversacion en Postgres para que aparezca en el panel.

    El historial en Redis es para el contexto del agente; el PANEL lee de Postgres
    (tablas clientes + mensajes). Sin esto, las charlas no se ven en el panel.
    No es critico: si falla, el bot ya respondio igual.

    🔴 UNA FILA POR GLOBO. El bot responde en varios mensajitos (hasta 6), y Meta devuelve un
    id por cada uno. Antes se guardaba UNA sola fila con todo el texto junto y se TIRABAN los
    ids: cuando Meta avisaba de que un globo había FALLADO, ese aviso no casaba con ninguna
    fila y se perdía. O sea: si fallaba justo el globo con LOS DATOS BANCARIOS, en el panel se
    veía todo verde y nadie se enteraba de que el cliente nunca supo dónde pagar.
    """
    from sqlalchemy import select

    from app.models import Cliente, Mensaje, now_utc
    from app.services.db import get_session_factory

    try:
        factory = get_session_factory()
        async with factory() as session:
            cliente = (
                await session.execute(select(Cliente).where(Cliente.telefono == telefono))
            ).scalar_one_or_none()
            if cliente is None:
                session.add(Cliente(telefono=telefono, nombre=nombre, ultima_interaccion=now_utc()))
            else:
                cliente.ultima_interaccion = now_utc()
                if nombre and not cliente.nombre:
                    cliente.nombre = nombre
            if texto_usuario:
                session.add(
                    Mensaje(cliente_telefono=telefono, rol="user", contenido=texto_usuario)
                )
            for p in partes:
                session.add(Mensaje(
                    cliente_telefono=telefono,
                    rol="assistant",
                    contenido=p["texto"],
                    wa_message_id=p.get("wa_message_id"),
                    estado=p.get("estado"),
                    error=p.get("error"),
                ))
            await session.commit()
    except Exception:  # noqa: BLE001 — no critico: la respuesta ya se envio
        logger.exception("No se pudo guardar la conversacion en el panel de %s", telefono)


# ─── Cinturon de seguridad del DINERO (anti-alucinacion) ─────────────
# Solo la duena confirma un pago, desde el panel (eso dispara notificar_cliente_pago).
# Si el AGENTE, en una charla normal, afirma que un pago quedo confirmado, es una
# alucinacion: el bot NUNCA debe confirmar dinero por su cuenta. Lo interceptamos.
_FRASES_PAGO_CONFIRMADO = (
    "pago confirmado",
    "pago fue confirmado",
    "pago ya confirmado",
    "pago quedo confirmado",
    "pago esta confirmado",
    "confirmado tu pago",
    "confirme tu pago",
    "pago verificado",
    "verifique tu pago",
    "tu pago ya esta listo",
    "ya quedo confirmado tu pago",
)
_RESPUESTA_PAGO_SEGURA = "¡Recibido! Estoy revisando tu pago y te confirmo en un ratito 😊"


def _proteger_afirmacion_de_pago(respuesta: str) -> str:
    """Si el agente afirma que un pago quedo confirmado (cosa que SOLO la duena
    puede hacer desde el panel), lo reemplaza por un mensaje seguro de 'revisando'.
    Compara sin acentos para atrapar 'confirmo'/'confirmo', etc."""
    import unicodedata

    t = unicodedata.normalize("NFKD", respuesta.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    if any(frase in t for frase in _FRASES_PAGO_CONFIRMADO):
        logger.warning(
            "Anti-alucinacion dinero: el agente afirmo un pago confirmado en charla; reemplazado"
        )
        return _RESPUESTA_PAGO_SEGURA
    return respuesta


# ─── Envío humano: plano + varios mensajitos cortos (no un mensajote) ─

def _aplanar(texto: str) -> str:
    """Quita el formato que delata a un bot: viñetas (* - •) al inicio de línea,
    negritas/cursivas markdown (*texto*) y los decimales .00 de los precios.
    La dueña escribe PLANO; esto es una red de seguridad por si el modelo igual
    mete formato (a veces ignora la instrucción)."""
    lineas = [re.sub(r"^[ \t]*[\*\-•]+[ \t]+", "", ln) for ln in texto.split("\n")]
    t = "\n".join(lineas)
    t = t.replace("*", "")  # negritas / asteriscos sueltos
    t = t.replace(" — ", ", ").replace("—", ", ")  # raya larga (em-dash) -> coma (suena a folleto)
    t = re.sub(r"\$\s?(\d+)\.00(?!\d)", r"$\1", t)  # $18.00 -> $18
    return t


async def _enviar_en_partes(telefono: str, texto: str) -> list[dict]:
    """Envía la respuesta PLANA y como VARIOS mensajes cortos (como una persona real
    en WhatsApp), no un mensajote. El agente separa cada globo con una línea en blanco;
    aquí aplanamos el formato, partimos por las líneas en blanco y enviamos cada parte
    por separado, con una pausa breve. Tope de globos para proteger la calidad del número.

    Devuelve UNA ENTRADA POR GLOBO: {texto, wa_message_id, estado, error}. El `wa_message_id`
    es el id que devuelve Meta, y es lo ÚNICO con lo que después se puede casar el aviso de
    "entregado / leído / FALLÓ". Antes ese id se tiraba a la basura: si fallaba el globo con
    los datos bancarios, el aviso de Meta no casaba con nada y en el panel se veía todo verde.

    Lista VACÍA = no se envió nada (texto vacío, o la dueña tomó el chat) → el que llama NO
    debe guardar nada en el historial: el bot no puede "recordar" algo que el cliente no vio.
    """
    if not texto or not texto.strip():
        return []

    # ÚLTIMA MIRADA AL FRENO, ya con la respuesta en la mano.
    # El bot tarda ~20s en contestar (15s de buffer + lo que piensa). En ese rato la dueña
    # pudo haber tomado el chat desde el panel. Si solo se mirara la pausa AL EMPEZAR, el bot
    # soltaría su respuesta ENCIMA de la de ella y el cliente vería a dos personas hablándole
    # a la vez. Este es el único embudo por el que salen las 4 respuestas del bot.
    #
    # OJO: se pregunta si lo pausó UNA PERSONA, no si está pausado a secas. El propio bot se
    # pausa al escalar (pedir_ayuda), y en ESE caso su mensaje de despedida al cliente ("dame
    # un momentito, te confirmo") TIENE que salir. Confundir los dos casos dejaba al cliente
    # con silencio total. Ver migración 020.
    if await _lo_paso_una_persona(telefono):
        logger.info(
            "No envío: la dueña tomó el chat de %s mientras el bot pensaba (relevo)", telefono
        )
        return []

    texto = _aplanar(texto)
    partes = [p.strip() for p in re.split(r"\n\s*\n", texto.strip()) if p.strip()]
    if not partes:
        partes = [texto.strip()]
    if len(partes) > 6:  # tope anti-spam: junta el exceso en el último globo
        partes = partes[:5] + ["\n\n".join(partes[5:])]

    enviados: list[dict] = []
    for i, parte in enumerate(partes):
        if i:
            await asyncio.sleep(1.0)  # pausa breve entre globos, como una persona
        try:
            resp = await enviar_texto(telefono, parte)
            wa_id = ((resp.get("messages") or [{}])[0] or {}).get("id")
            enviados.append(
                {"texto": parte, "wa_message_id": wa_id, "estado": "enviado", "error": None}
            )
        except Exception as exc:  # noqa: BLE001 — Meta lo rechazó: queda ESCRITO, no perdido
            logger.exception("Meta rechazó un globo para %s", telefono)
            enviados.append(
                {"texto": parte, "wa_message_id": None, "estado": "fallido",
                 "error": str(exc)[:400]}
            )
            break  # si el primero no pasó, los siguientes tampoco: no se insiste
    return enviados


def _algo_llego(partes: list[dict]) -> bool:
    """True si al menos un globo LLEGÓ. Un globo 'fallido' se guarda (se ve en rojo en el
    panel) pero NO cuenta como dicho: el bot no puede recordar lo que el cliente no recibió."""
    return any(p.get("estado") == "enviado" for p in partes)


async def _guardar_media_en_hilo(
    *, telefono: str, message_id: str | None, media_id: str, ruta: str,
    mime: str, caption: str | None, es_imagen: bool,
) -> None:
    """Mete la FOTO DEL CLIENTE (el comprobante) en el hilo del panel.

    Va en sesión PROPIA y con todo tragado: si esto falla, el pago se registra igual. Nunca
    al revés. Y con `message_id` (que tiene UNIQUE desde la 001) como candado: un reintento de
    Meta no puede duplicar la burbuja.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models import Mensaje
    from app.services.db import get_session_factory

    try:
        factory = get_session_factory()
        async with factory() as session:
            ins = pg_insert(Mensaje).values(
                message_id=message_id,
                cliente_telefono=telefono,
                rol="user",
                # El tipo REAL: un PDF no es una imagen (si se guardara como 'image', el panel
                # intentaría pintarlo con <img> y saldría roto).
                tipo="image" if es_imagen else "document",
                contenido=(caption or "").strip() or "(comprobante)",
                media_id=media_id,
                media_url=ruta,
                media_mime=mime or None,
            ).on_conflict_do_nothing(index_elements=[Mensaje.message_id])
            await session.execute(ins)
            await session.commit()
    except Exception:  # noqa: BLE001 — la burbuja es cosmética; el DINERO no puede caerse
        logger.exception("No se pudo meter el comprobante de %s en el hilo", telefono)


# ─── Interruptor del bot (encender / apagar) ─────────────────────────

async def _bot_activo() -> bool:
    """Lee el interruptor del bot (config 'bot_activo'). Por defecto ENCENDIDO.
    Si falla la lectura, deja el bot encendido (no se queda mudo por un error de BD)."""
    from sqlalchemy import select

    from app.models import Configuracion
    from app.services.db import get_session_factory

    try:
        factory = get_session_factory()
        async with factory() as session:
            fila = (
                await session.execute(
                    select(Configuracion).where(Configuracion.clave == "bot_activo")
                )
            ).scalar_one_or_none()
        if fila and fila.valor is not None:
            return fila.valor.strip().lower() not in ("0", "false", "no", "off")
    except Exception:  # noqa: BLE001
        pass
    return True


async def _cliente_pausado(telefono: str) -> bool:
    """True si el bot está pausado en ESE chat (lo pausara quien lo pausara).
    Si la BD falla, devuelve False: un error de lectura no puede dejar MUDO al bot entero."""
    try:
        return (await _estado_pausa(telefono))[0]
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo leer la pausa de %s (sigue respondiendo)", telefono)
        return False


async def _lo_paso_una_persona(telefono: str) -> bool:
    """True SOLO si el freno lo apretó UNA PERSONA (la dueña tomó ese chat).

    🔴 Por qué existe (bug cazado en vivo el 2026-07-12): la red anti-atropello miraba si el
    chat estaba pausado, pero no QUIÉN lo pausó — y hay dos casos OPUESTOS:
      · La DUEÑA tomó el chat → el bot debe CALLARSE (si no, le habla encima al cliente).
      · El BOT se pausó SOLO (pedir_ayuda: está escalando) → su último mensaje al cliente
        ("dame un momentito, te confirmo") SÍ tiene que salir.
    Al confundirlos, el bot se tragaba su propio mensaje de despedida y el cliente se quedaba
    con SILENCIO TOTAL: escribía "Hola" y no recibía absolutamente nada.

    Ante cualquier duda o error, devuelve True (el bot se CALLA): es el lado seguro. Callarse
    de más cuesta un mensaje; hablarle encima a la dueña delante de un cliente, en medio de un
    cobro, cuesta la venta y la confianza. OJO: esto es lo CONTRARIO de `_cliente_pausado`, que
    ante un error deja hablar al bot — son dos preguntas distintas con dos lados seguros
    distintos, y por eso NO comparten el except.
    """
    try:
        pausado, por = await _estado_pausa(telefono)
    except Exception:  # noqa: BLE001
        logger.exception("No sé quién pausó a %s → el bot se CALLA (lado seguro)", telefono)
        return True
    if not pausado:
        return False
    return por != "bot"


async def _estado_pausa(telefono: str) -> tuple[bool, str | None]:
    """(¿pausado?, ¿quién lo pausó?) — 'dueña' | 'bot' | None.

    PROPAGA la excepción a propósito: cada quien tiene su lado seguro (el bot sigue hablando
    si no sabemos si está pausado; el bot se CALLA si no sabemos QUIÉN lo pausó). Tragarse el
    error aquí obligaba a los dos a compartir el mismo, y uno de los dos quedaba mal.
    """
    from sqlalchemy import select

    from app.models import Cliente
    from app.services.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        cliente = (
            await session.execute(select(Cliente).where(Cliente.telefono == telefono))
        ).scalar_one_or_none()
    if cliente is None or not cliente.bot_pausado:
        return False, None
    return True, cliente.pausado_por


async def _guardar_entrante(telefono: str, nombre: str | None, texto: str) -> None:
    """Guarda SOLO el mensaje entrante del cliente (sin respuesta), para que la
    dueña lo vea en Conversaciones cuando el bot está apagado y responda ella."""
    from sqlalchemy import select

    from app.models import Cliente, Mensaje, now_utc
    from app.services.db import get_session_factory

    try:
        factory = get_session_factory()
        async with factory() as session:
            cliente = (
                await session.execute(select(Cliente).where(Cliente.telefono == telefono))
            ).scalar_one_or_none()
            if cliente is None:
                session.add(Cliente(telefono=telefono, nombre=nombre, ultima_interaccion=now_utc()))
            else:
                cliente.ultima_interaccion = now_utc()
                if nombre and not cliente.nombre:
                    cliente.nombre = nombre
            session.add(Mensaje(cliente_telefono=telefono, rol="user", contenido=texto))
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo guardar el mensaje entrante de %s", telefono)


async def _numero_permitido(telefono: str) -> bool:
    """LISTA BLANCA de pruebas: si la lista NO esta vacia, el bot SOLO responde a esos numeros;
    a los demas les guarda el mensaje pero NO responde (probar en produccion sin contestarle a
    clientes reales). Vacia = responde a todos. Compara por la COLA de 10 digitos, asi tolera el
    codigo de pais (+57, 0058, +593...).

    La lista sale de DOS sitios que se SUMAN:
      · settings.numeros_permitidos (variable de entorno, la fija) y
      · el config `numeros_permitidos_extra` (editable SIN redeploy: para añadir un numero de
        prueba al vuelo sin tocar Coolify — que reiniciaria el contenedor).
    """
    from sqlalchemy import select

    from app.models import Configuracion
    from app.services.db import get_session_factory

    # Los teléfonos INTERNOS (simulador del panel, bancos de prueba) empiezan por "__" y NUNCA
    # son un WhatsApp real: pasan siempre, la lista blanca es solo para números de verdad. Sin
    # esto, poner un número real en `numeros_permitidos_extra` volvía la lista NO vacía y de
    # rebote bloqueaba a `__prueba_dinero__` / `__simulador__` (rompía los bancos).
    if (telefono or "").startswith("__"):
        return True

    permitidos = (settings.numeros_permitidos or "").strip()
    extra = ""
    try:
        factory = get_session_factory()
        async with factory() as s:
            extra = (
                await s.execute(
                    select(Configuracion.valor).where(
                        Configuracion.clave == "numeros_permitidos_extra"
                    )
                )
            ).scalars().first() or ""
    except Exception:  # noqa: BLE001 — si la BD falla, manda solo la lista de entorno
        pass

    juntos = ",".join(x for x in (permitidos, extra.strip()) if x)
    if not juntos:
        return True  # sin lista blanca = responde a todos (produccion normal)

    def _cola(s: str) -> str:
        d = "".join(c for c in (s or "") if c.isdigit())
        return d[-10:] if len(d) >= 10 else d

    objetivo = _cola(telefono)
    return any(_cola(n) == objetivo for n in juntos.split(",") if n.strip())


@celery_app.task(name="procesar_buffer")
def procesar_buffer(telefono: str, nombre: str | None = None):
    """Tarea Celery: procesa los mensajes acumulados de un cliente y responde."""
    _run(_procesar(telefono, nombre))


async def _procesar(telefono: str, nombre: str | None) -> None:
    # Solo un worker procesa el buffer de este cliente a la vez.
    if not await rc.adquirir_lock(telefono):
        return
    try:
        mensajes = await rc.vaciar_buffer(telefono)
        if not mensajes:
            return  # otra tarea ya lo procesó

        texto = "\n".join(mensajes)

        if not await _bot_activo() or await _cliente_pausado(telefono) or not await _numero_permitido(telefono):
            # Bot apagado (global o solo en este chat): guarda lo que escribió el
            # cliente para que la dueña lo vea en Conversaciones y responda ella.
            await rc.guardar_historial(telefono, "user", texto)
            await _guardar_entrante(telefono, nombre, texto)
            return

        historial = await rc.obtener_historial(telefono)

        respuesta = await responder(telefono, texto, historial, nombre)
        respuesta = _proteger_afirmacion_de_pago(respuesta)

        partes = await _enviar_en_partes(telefono, respuesta)
        await rc.guardar_historial(telefono, "user", texto)
        if not partes:
            # La dueña tomó el chat mientras el bot pensaba: su respuesta se DESCARTA
            # (no se envía ni se recuerda). Lo que sí se guarda es lo que dijo el cliente,
            # para que ella lo vea y le conteste.
            await _guardar_entrante(telefono, nombre, texto)
            return
        # Los globos FALLIDOS también se guardan (se ven en ROJO en el panel), pero el bot no
        # "recuerda" haber dicho algo que el cliente nunca recibió.
        if _algo_llego(partes):
            await rc.guardar_historial(telefono, "assistant", respuesta)
        await _guardar_en_panel(telefono, nombre, texto, partes)
    except Exception:  # noqa: BLE001
        logger.exception("Error procesando el buffer de %s", telefono)
    finally:
        await rc.liberar_lock(telefono)


# ─── RETOMAR: la dueña devolvió el chat y el cliente quedó esperando ─
#
# EL HUECO (lo reportó Maired con una captura real): mientras la dueña tiene el chat tomado, el
# cliente sigue escribiendo ("¿cuánto sería en Bs?", "quedo pendiente del monto"). Al devolverle
# el chat al bot, el bot NO contestaba: "Devolver al bot" solo apagaba la bandera de pausa, y el
# bot únicamente habla cuando ENTRA un mensaje nuevo por el webhook. Esos pendientes YA habían
# entrado ⇒ nadie disparaba nada ⇒ silencio, y la venta se moría ahí. Faltaba el DISPARADOR.
#
# Esto es RESPUESTA, no envío proactivo: el cliente escribió y está esperando, y el botón que
# aprieta la dueña ES la aprobación humana. Por eso es seguro con Meta.

# 🔥 AUTO-BLINDAJE (ensayo general del 2026-07-13): la PRIMERA versión de esta instrucción decía
# "la dueña te devolvió el chat, RESPÓNDELE TÚ" — y el modelo lo leyó como "ahora la dueña eres
# tú". Al cliente que pidió *"quiero hablar con una persona de verdad, no con una máquina"* le
# contestó: **"Soy Whuilianny, la dueña de masvidaconsciente"**. MINTIÓ SOBRE SER HUMANA — la falla
# más grave que hay, y por el camino normal el bot NO la comete (ahí escala con `pedir_ayuda`).
# Lección: al devolverle el turno al modelo hay que RE-ANCLAR quién es; una orden ambigua sobre el
# relevo se lee como un cambio de identidad. La regla vive en `_REGLAS`, pero esta orden la pisaba.
# 🔥 Y LA SEGUNDA VERSIÓN TAMBIÉN FALLÓ, POR OTRO LADO (mismo ensayo). Decía "lee LO ÚLTIMO que
# escribió el cliente" — y el modelo se ancló en la ÚLTIMA LÍNEA, perdiendo lo que el cliente había
# pedido antes. Al cliente que escribió "quiero hablar con una PERSONA de verdad" y luego "¿sigue
# ahí alguien?", el bot le contestó "Sí, aquí estoy 💚 ¿En qué te puedo ayudar?": cero herramientas,
# cero `pedir_ayuda`, cero aviso. Se comió la petición y NADIE se enteró. (Por el camino normal el
# bot SÍ escala: la anteojera la puse yo.) Lo pendiente casi nunca es UN mensaje: es un BLOQUE.
_INSTRUCCION_RETOMAR = (
    "[SISTEMA] Vuelves a atender este chat (la dueña te lo devolvió). Mira TODO lo que el cliente "
    "escribió y quedó SIN RESPONDER —puede ser más de un mensaje— y contéstale lo que pidió, "
    "retomando donde quedó. Incluye lo que pidió ANTES de que la dueña entrara si todavía está sin "
    "resolver: el cliente sigue esperando eso. "
    "SIGUES SIENDO LA MISMA DE SIEMPRE: la asistente virtual del negocio. NO eres la dueña ni una "
    "persona; si el cliente pide hablar con una persona, llama a `pedir_ayuda` (motivo "
    "'pide_persona') como siempre. "
    "No repitas lo que ya se dijo, no vuelvas a saludar ni a presentarte, y no menciones este "
    "aviso ni que estuviste ausente. Si te falta un dato, pídeselo al cliente o llama a la "
    "herramienta que lo dé: jamás inventes un precio ni un monto."
)

# El caso ESTRELLA: el bot escaló (no sabía el precio del día), la dueña lo cargó y le devolvió el
# chat. Lo que le faltaba YA ESTÁ en el sistema — pero solo lo verá si vuelve a preguntárselo a la
# herramienta. Si no se le dice esto, el modelo "recuerda" que no lo sabía y se queda ahí.
_INSTRUCCION_RETOMAR_ESCALADO = (
    "[SISTEMA] Le pediste ayuda a la dueña sobre este chat y ella YA la resolvió: el dato que te "
    "faltaba (por ejemplo, el precio del día) ya está cargado en el sistema. VUELVE A CONSULTARLO "
    "con tus herramientas —no des por hecho que sigue faltando— y dale al cliente la respuesta que "
    "le prometiste, retomando la venta donde quedó. "
    "SIGUES SIENDO LA MISMA DE SIEMPRE: la asistente virtual del negocio. NO eres la dueña ni una "
    "persona. No vuelvas a saludar ni a presentarte, no repitas lo que ya se dijo y no menciones "
    "este aviso. Y si el dato SIGUE sin estar, NO lo inventes: llama otra vez a `pedir_ayuda`."
)


async def _ventana_abierta(telefono: str) -> bool:
    """¿Se le puede escribir texto libre a este cliente AHORA? (la regla de las 24h de Meta).

    FAIL-CLOSED: ante cualquier duda (el cliente no existe, no hay fecha, falla la BD) devuelve
    False y el bot NO envía. Un envío fuera de ventana lo rechaza Meta y le baja la calidad al
    número; siendo Enova Tech Provider, eso arriesga la cuenta de Meta de TODOS los clientes.

    El flujo normal (webhook) nunca necesita esto: el cliente ACABA de escribir, así que la
    ventana está abierta por definición. Aquí sí: entre el último mensaje del cliente y el
    momento en que la dueña devuelve el chat pueden haber pasado horas o días. Y `_enviar_en_partes`
    no la valida por su cuenta.

    Se reusa `_ventana` del panel a propósito: la regla de las 24h vive en UN solo sitio.
    """
    from sqlalchemy import select

    from app.api.router import _ventana
    from app.models import Cliente

    try:
        factory = get_session_factory()
        async with factory() as session:
            cliente = (
                await session.execute(select(Cliente).where(Cliente.telefono == telefono))
            ).scalar_one_or_none()
        if cliente is None:
            return False
        return bool(_ventana(cliente)["abierta"])
    except Exception:  # noqa: BLE001
        logger.exception("No sé si la ventana de %s está abierta → NO se envía (lado seguro)", telefono)
        return False


async def _avisar_ventana_cerrada(telefono: str, nombre: str | None) -> None:
    """La dueña devolvió el chat, pero pasaron +24h desde el último mensaje del cliente: WhatsApp
    no deja escribirle texto libre. El bot NO envía nada (lado seguro) — y se lo dice a ELLA, o el
    silencio se vería exactamente igual que el bug que vinimos a arreglar."""
    from sqlalchemy import select

    from app.models import Configuracion, Intervencion

    quien = nombre or telefono
    try:
        factory = get_session_factory()
        async with factory() as session:
            session.add(Intervencion(
                cliente_telefono=telefono,
                motivo="ventana_cerrada",
                detalle=(
                    f"Le devolviste el chat de {quien} al bot, pero pasaron más de 24 horas desde "
                    "su último mensaje: WhatsApp NO deja escribirle texto libre hasta que él "
                    "vuelva a escribir. El bot no le mandó nada."
                ),
                mensaje_cliente="(quedó esperando respuesta)",
            ))
            fila = (
                await session.execute(
                    select(Configuracion).where(Configuracion.clave == "dueno_telefono")
                )
            ).scalar_one_or_none()
            await session.commit()
        destino = (fila.valor if fila else None) or settings.dueno_telefono
        if destino:
            await enviar_texto(
                destino,
                f"⏰ Le devolviste el chat de {quien} al bot, pero pasaron más de 24 horas desde "
                "su último mensaje: WhatsApp no deja escribirle. El bot NO le respondió nada.",
            )
    except Exception:  # noqa: BLE001 — el aviso es lo único que hay aquí; que no tumbe al worker
        logger.exception("No se pudo avisar de la ventana cerrada de %s", telefono)


# 🔴🔴 EL CASO ESTRELLA, QUE NO FUNCIONABA (auditoría de arquitectura, 2026-07-13).
#
# El ROADMAP promete esto: *"pon el precio del día y devuelve el chat: el bot lo venderá solo"*.
# Probado con el bot vivo, hacía ESTO:
#     cliente: "¿cuánto la torta keto de 1kg?"  →  el bot NO lo sabe (precio del día) → escala:
#     le deja el aviso a la dueña y le dice al cliente "te lo confirmo enseguida".
#     La dueña pone el precio y aprieta "Ya lo atendí (reactivar el bot)".
#     El bot… SE QUEDA MUDO. El cliente nunca se entera del precio. Se pierde la venta.
#     Y la dueña se queda creyendo que el bot le contestó.
#
# La causa era MI guard: preguntaba "¿el último mensaje es del cliente?" — y NO lo es: el último
# es el del propio bot ("te lo confirmo enseguida"). Así que concluía "aquí no hay nada pendiente".
#
# El error de fondo: **el mensaje del bot al escalar NO es una respuesta, es un pagaré.** La
# pregunta del cliente sigue viva. Por eso ahora el disparador trae la FIRMA de la pausa:
#   · pausado_por='bot'   → el bot escaló y NADIE le ha contestado al cliente ⇒ el bot habla.
#   · pausado_por='dueña' → ella tomó el chat ⇒ solo habla si el cliente escribió DESPUÉS.
# (Si ella contesta —por el panel o desde su celular— la firma pasa a 'dueña' sola, así que el
#  bot nunca le habla encima.)

@celery_app.task(name="retomar_chat")
def retomar_chat(telefono: str, nombre: str | None = None, pausado_por: str | None = None):
    """Tarea Celery: la dueña devolvió el chat → el bot contesta lo que quedó pendiente."""
    _run(_retomar(telefono, nombre, pausado_por))


async def _retomar(telefono: str, nombre: str | None, pausado_por: str | None = None) -> None:
    # EL MISMO lock por-teléfono que usa el buffer: si justo ahora el bot ya está contestando un
    # mensaje nuevo de ese cliente, no hay nada que retomar (ese turno ya arrastra los pendientes).
    # Se sale ANTES de gastar el candado, para que la dueña pueda volver a apretar.
    if not await rc.adquirir_lock(telefono):
        logger.info("Retomar %s: ya hay un turno en curso; no hace falta disparar", telefono)
        return
    try:
        # Doble click (o los dos caminos de resume a la vez) ⇒ UNA sola respuesta.
        if not await rc.candado_retomar(telefono):
            logger.info("Retomar %s: ya se disparó hace un momento (doble click)", telefono)
            return

        if not await _bot_activo() or not await _numero_permitido(telefono):
            return

        # Entre el click y esta tarea, la dueña pudo volver a tomar el chat (o el bot pudo
        # pausarse solo al escalar algo). Si está pausado, el bot no habla: punto.
        if await _cliente_pausado(telefono):
            logger.info("Retomar %s: el chat está pausado otra vez; el bot no habla", telefono)
            return

        historial = await rc.obtener_historial(telefono)

        # ¿El bot había ESCALADO? Entonces su último mensaje ("te lo confirmo enseguida") NO es una
        # respuesta: es un PAGARÉ. La pregunta del cliente sigue viva y hay que pagarla.
        venia_de_escalada = pausado_por == "bot"

        # GUARD DE HONESTIDAD: el bot solo habla si el cliente quedó ESPERANDO. Si la dueña ya le
        # contestó a mano (el último turno es de ella), abrir la boca sería un envío PROACTIVO
        # —lo que Meta prohíbe sin aprobación humana— y encima le hablaría encima.
        if not historial:
            return
        if historial[-1].get("role") != "user" and not venia_de_escalada:
            logger.info(
                "Retomar %s: no hay nada pendiente (el último turno no es del cliente)", telefono
            )
            return

        # LA VENTANA DE 24H, FAIL-CLOSED. Va DESPUÉS del guard a propósito: si no había nada
        # pendiente, no hay por qué molestar a la dueña con un aviso de ventana cerrada.
        if not await _ventana_abierta(telefono):
            logger.warning("Retomar %s: ventana de 24h CERRADA → el bot NO escribe", telefono)
            await _avisar_ventana_cerrada(telefono, nombre)
            return

        # Lo que escribió el cliente YA está en el historial: NO se reinyecta como mensaje (se
        # duplicaría el turno). Lo que va en su lugar es una orden EFÍMERA de sistema, que el bot
        # lee y NO se guarda en ningún lado: en la memoria solo queda su respuesta.
        #
        # `pregunta_cliente`: lo que el cliente preguntó DE VERDAD. Sin esto, si el bot vuelve a
        # escalar, el aviso de la bandeja le decía a la dueña: *El cliente preguntó: "[SISTEMA]
        # Vuelves a atender este chat…"*. Basura, justo donde ella mira para entender qué pasa.
        ultima_del_cliente = next(
            (h.get("content") for h in reversed(historial) if h.get("role") == "user"), ""
        )
        instruccion = _INSTRUCCION_RETOMAR_ESCALADO if venia_de_escalada else _INSTRUCCION_RETOMAR
        respuesta = await responder(
            telefono, instruccion, historial, nombre, pregunta_cliente=ultima_del_cliente
        )
        respuesta = _proteger_afirmacion_de_pago(respuesta)

        partes = await _enviar_en_partes(telefono, respuesta)
        if not partes:
            # La dueña volvió a tomar el chat mientras el bot pensaba (~20s): su respuesta se
            # DESCARTA (ni se envía ni se recuerda). Lo que dijo el cliente ya está guardado.
            return
        if _algo_llego(partes):
            await rc.guardar_historial(telefono, "assistant", respuesta)
        # texto_usuario="" a propósito: lo que dijo el cliente YA está en `mensajes` (se guardó
        # cuando llegó, durante la pausa). Volver a insertarlo lo duplicaría en el hilo del panel.
        await _guardar_en_panel(telefono, nombre, "", partes)
    except Exception:  # noqa: BLE001
        logger.exception("Error retomando el chat de %s", telefono)
    finally:
        await rc.liberar_lock(telefono)


# ─── Comprobantes de pago (imagenes / PDF) ───────────────────────────

_EXT_POR_MIME = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "application/pdf": "pdf",
}


def _guardar_comprobante(media_id: str, contenido: bytes, mime: str) -> str:
    """Guarda el binario del comprobante en COMPROBANTES_DIR y devuelve la ruta."""
    os.makedirs(settings.comprobantes_dir, exist_ok=True)
    base_mime = (mime or "").split(";")[0].strip().lower()
    ext = _EXT_POR_MIME.get(base_mime, "bin")
    ruta = os.path.join(settings.comprobantes_dir, f"{media_id}.{ext}")
    with open(ruta, "wb") as f:
        f.write(contenido)
    return ruta


async def _leer_comprobante_seguro(telefono, contenido, base_mime) -> dict:
    """Lee el comprobante con visión, dándole TODAS las cuentas de pago de la dueña
    (tabla metodos_pago) para reconocer SOLO pagos hacia alguna de ellas. Nunca lanza."""
    from sqlalchemy import select

    from app.models import MetodoPago

    cuentas: list[dict] = []
    try:
        factory = get_session_factory()
        async with factory() as session:
            metodos = (
                await session.execute(select(MetodoPago).where(MetodoPago.activo.is_(True)))
            ).scalars().all()
        cuentas = [
            {
                "titular": m.titular,
                "banco": m.banco,
                "telefono": m.telefono,
                "cedula": m.cedula,
                "cuenta": m.cuenta,
                "correo": m.correo,
                "wallet": m.wallet,
            }
            for m in metodos
        ]
    except Exception:  # noqa: BLE001
        pass
    try:
        return await leer_comprobante(contenido, base_mime, cuentas=cuentas)
    except Exception:  # noqa: BLE001 — defensa extra: nunca tumbar el worker
        logger.exception("Fallo leyendo el comprobante de %s", telefono)
        return {"es_comprobante": None, "leido": False}


async def _responder_situacion(
    telefono: str, situacion: str, nombre: str | None
) -> list[dict]:
    """Whuilianny REDACTA un mensaje para el cliente según la situación (no plantilla),
    lo protege contra afirmaciones de pago, lo envía en partes y lo guarda en historial.

    🔴 El INTERRUPTOR no cubría este carril (auditoría 2026-07-13): con el bot APAGADO desde el
    panel, un cliente que mandaba su comprobante RECIBÍA respuesta igual. El pago se registra
    siempre (el dinero nunca se pierde), pero si la dueña apagó el bot, el bot NO habla.
    """
    if not await _numero_permitido(telefono) or not await _bot_activo():
        return []  # bot apagado o fuera de la lista blanca: se registra el pago, pero no se habla
    try:
        historial = await rc.obtener_historial(telefono)
        _usd, _bs = await _montos_decibles(telefono)
        mensaje = await redactar_mensaje(
            situacion, historial, nombre, telefono, montos_usd=_usd, montos_bs=_bs
        )
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo redactar el mensaje al cliente %s", telefono)
        return []
    mensaje = _proteger_afirmacion_de_pago(mensaje or "")
    if not mensaje.strip():
        # La red del dinero tumbó el mensaje (montos inventados o una frase prohibida) y el modelo
        # insistió. NO se le manda una mentira al cliente — pero tampoco se le deja en silencio
        # justo cuando acaba de pagar: acuse sobrio + la dueña se entera.
        logger.error("Carril del dinero: no salió un mensaje limpio para %s; acuse seguro", telefono)
        partes = await _enviar_en_partes(telefono, _RESPUESTA_PAGO_SEGURA)
        if _algo_llego(partes):
            await rc.guardar_historial(telefono, "assistant", _RESPUESTA_PAGO_SEGURA)
        if partes:
            await _guardar_en_panel(telefono, nombre, "", partes)
        await _avisar_mensaje_frenado(telefono, nombre)
        return partes
    partes = await _enviar_en_partes(telefono, mensaje)
    # Si no se envió (la dueña tomó el chat), NO se guarda en el historial: el bot no puede
    # "recordar" haber dicho algo que el cliente nunca vio.
    if _algo_llego(partes):
        await rc.guardar_historial(telefono, "assistant", mensaje)
    # Y AHORA SÍ se guarda en el panel: hasta hoy, TODO este carril (el del comprobante)
    # NO escribía una sola línea en `mensajes` — en el hilo del panel ese tramo estaba EN
    # BLANCO, y la dueña tenía que responder a ciegas justo en el momento del dinero.
    if partes:
        await _guardar_en_panel(telefono, nombre, "", partes)
    return partes


def _a_float(x):
    """Convierte un monto leído ('39.480,47' o '39480.47') a float. None si no se puede."""
    s = "".join(c for c in str(x or "") if c.isdigit() or c in ".,")
    if not s:
        return None
    if "," in s:  # formato venezolano: 39.480,47 -> 39480.47
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


async def _montos_decibles(telefono: str) -> tuple[set[float], set[float]]:
    """LA LISTA CERRADA de montos que el bot puede decir en el carril del DINERO: (dólares, bolívares).

    Son los que el CÓDIGO cobró de verdad en esta conversación (la cotización que guardó
    `generar_datos_pago`). Nada más. El catálogo NO entra: aquí el bot no está cotizando productos,
    está hablando de UN pago — y autorizar el catálogo entero fue justo lo que dejaba pasar el "$12"
    inventado (12 = precio de otro producto).

    🔴 Y van SEPARADOS POR MONEDA. Devolverlos en un solo saco era repetir el bug del "$23": el bot
    llamaba "bolívares" a una cifra en dólares y la red la daba por buena porque el número estaba
    en la lista. Un dólar solo autoriza dólares; un bolívar solo autoriza bolívares.
    """
    bs, usd, divisas = await _montos_cobrados(telefono)
    return (
        {m for m in (usd, divisas) if m is not None},   # dólares
        {m for m in (bs,) if m is not None},            # bolívares
    )


async def _montos_cobrados(telefono: str):
    """Devuelve (monto_bs, monto_usd, monto_usd_divisas) que el bot le cobró (de la
    cotización en Redis). El comprobante puede venir en Bs (Pago Móvil/transferencia),
    en USD pleno, o en USD con 20% de descuento (divisas: Zelle/Binance/efectivo)."""
    try:
        guardado = await rc.get_cache(f"cobro:{telefono}")
        if guardado:
            d = json.loads(guardado)
            return (
                _a_float(d.get("monto_bs")),
                _a_float(d.get("monto_usd")),
                _a_float(d.get("monto_usd_divisas")),
            )
    except Exception:  # noqa: BLE001
        pass
    return None, None, None


@celery_app.task(name="procesar_comprobante")
def procesar_comprobante(telefono, message_id, media_id, caption=None, nombre=None, mime_type=None):
    """Tarea dedicada (fuera del buffer de texto): descarga y guarda el comprobante."""
    _run(_procesar_comprobante(telefono, message_id, media_id, caption, nombre, mime_type))


async def _procesar_comprobante(telefono, message_id, media_id, caption, nombre, mime_type) -> None:
    # Idempotencia del carril de DINERO: se marca SOLO tras un registro exitoso.
    # Si la descarga o el registro fallan, NO se marca, para que el reintento de
    # Meta tenga otra oportunidad real (la URL del media caduca a ~5 min).
    if message_id and await rc.comprobante_procesado(message_id):
        return

    try:
        contenido, mime = await descargar_media(media_id)
    except Exception:  # noqa: BLE001 — fallo transitorio: NO marcar, dejar reintentar
        logger.exception("No se pudo descargar el comprobante %s de %s", media_id, telefono)
        return
    ruta = _guardar_comprobante(media_id, contenido, mime or mime_type or "")
    logger.info("Comprobante de %s guardado en %s (%s bytes)", telefono, ruta, len(contenido))

    base_mime = (mime or mime_type or "").split(";")[0].strip().lower()
    es_imagen = base_mime.startswith("image/")

    # LA FOTO ENTRA AL HILO **AQUÍ**, apenas se descarga y ANTES de que la visión la juzgue.
    # Si se insertara junto al registro del pago, la imagen que la visión RECHAZA (la captura
    # borrosa, el reflejo, el PDF) NUNCA aparecería en el chat — y es justo la que la dueña
    # necesita ver con sus ojos para decidir. Va en SESIÓN PROPIA: jamás puede compartir
    # transacción con el dinero (un fallo al guardar la burbuja no puede tumbar el Pago).
    await _guardar_media_en_hilo(
        telefono=telefono,
        message_id=message_id,
        media_id=media_id,
        ruta=ruta,
        mime=base_mime,
        caption=caption,
        es_imagen=es_imagen,
    )

    # VISIÓN: extrae los datos y valida EN CÓDIGO que el pago sea A LA CUENTA de la
    # dueña. Solo para imágenes; un PDF no se analiza por visión.
    lectura = {}
    if es_imagen:
        lectura = await _leer_comprobante_seguro(telefono, contenido, base_mime)
    es_comprobante = lectura.get("es_comprobante")
    monto = lectura.get("monto")
    monto_ok = bool(monto) and str(monto).strip().lower() not in ("", "null", "none", "0")
    logger.info(
        "Visión comprobante de %s: imagen=%s leido=%s es_comprobante=%s pantalla=%s beneficiario=%r monto=%s",
        telefono, es_imagen, lectura.get("leido"), es_comprobante,
        lectura.get("es_pantalla_bancaria"), lectura.get("beneficiario_nombre"), monto,
    )

    # IMÁGENES = ESTRICTO: solo es un pago si la visión RECONOCIÓ un comprobante a la
    # cuenta de la dueña (es_comprobante True + monto). Si NO lo reconoció —no es
    # comprobante, es de otra cuenta, o la visión no pudo leer— se PIDE la captura y
    # NO se registra. (Antes la red de seguridad registraba ante la duda, y por eso
    # se colaban fotos cualquiera.)
    if es_imagen and not (es_comprobante is True and monto_ok):
        logger.info("Imagen de %s NO reconocida como comprobante de la dueña; no se registra", telefono)
        if message_id:
            await rc.marcar_comprobante(message_id)  # atendido: no reprocesar
        _pant = lectura.get("es_pantalla_bancaria")
        es_pantalla = _pant is True or str(_pant).strip().lower() in ("true", "si", "sí", "yes", "1")
        if es_pantalla:
            # SÍ es la pantalla de un pago/transferencia, pero NO a la cuenta de la dueña.
            # OJO: método-NEUTRAL a propósito. Antes decía "verifica que lo envió a tu Pago
            # Móvil" aunque el cliente hubiera pagado por Zelle o Binance — confundía justo
            # en el momento del dinero.
            situacion = (
                "El cliente te mandó una imagen de un pago o transferencia, pero ese pago NO te "
                "aparece hecho a TU cuenta (parece que fue a otra cuenta). Contéstale "
                "con calidez y con TUS PROPIAS PALABRAS, natural y DISTINTA cada vez (JAMÁS repitas "
                "la misma frase ni suenes a plantilla o robot): dile con cariño que ese pago no te "
                "aparece a tu cuenta, pídele que verifique que lo envió a los datos exactos que le "
                "diste (del método que él eligió) y que te reenvíe la captura. No lo acuses ni des "
                "el pago por hecho; solo pídele que confirme."
            )
        else:
            # No parece un comprobante (foto cualquiera, o no se ve el pago).
            situacion = (
                "El cliente te envió una imagen que no parece un comprobante de pago (parece otra "
                "cosa o no se alcanza a ver el pago). Contéstale con calidez y con TUS PROPIAS "
                "PALABRAS, natural y DISTINTA cada vez (JAMÁS repitas la misma frase ni suenes a "
                "plantilla): dile con cariño que ahí no ves el comprobante y pídele que te reenvíe "
                "la captura clara del pago (donde se vea el monto y la referencia)."
            )
        await _responder_situacion(telefono, situacion, nombre)
        return

    # ¿El MONTO del comprobante cuadra con lo cobrado? Comparamos contra el monto en
    # Bs (Pago Móvil/Transferencia) Y en USD (Binance/Zelle): basta que coincida con UNO.
    monto_cuadra = True
    if es_imagen:
        esperado_bs, esperado_usd, esperado_div = await _montos_cobrados(telefono)
        leido_monto = _a_float(monto)
        candidatos = [c for c in (esperado_bs, esperado_usd, esperado_div) if c is not None]
        if leido_monto is not None and candidatos:
            monto_cuadra = any(abs(leido_monto - c) <= max(1.0, c * 0.02) for c in candidatos)
        logger.info(
            "Monto comprobante de %s: leido=%s bs=%s usd=%s divisa=%s cuadra=%s",
            telefono, leido_monto, esperado_bs, esperado_usd, esperado_div, monto_cuadra,
        )

    # Aquí: la visión reconoció el comprobante (imagen), O es un PDF/otro -> red de
    # seguridad: se registra como 'reportado' para que la dueña lo revise.
    from app.agent.tools import registrar_comprobante

    # La referencia leída por visión solo se confía si reconoció un comprobante.
    referencia = lectura.get("referencia") if es_comprobante is True else None
    if not isinstance(referencia, str) or not referencia.strip():
        referencia = None

    factory = get_session_factory()
    try:
        async with factory() as session:
            resultado = await registrar_comprobante(
                session,
                telefono,
                referencia=referencia,
                comprobante_media_id=media_id,
                comprobante_url=ruta,
                # El monto que la visión leyó: sirve para saber si pagó en divisas (con el
                # 20% de descuento) o en bolívares (precio completo).
                monto_leido=_a_float(monto) if es_comprobante is True else None,
            )
    except Exception:  # noqa: BLE001 — error de BD: NO marcar, dejar reintentar a Meta
        logger.exception("No se pudo registrar el comprobante de %s", telefono)
        return
    logger.info("Comprobante de %s registrado: %s", telefono, resultado)

    # Registro exitoso: marcar para que un reintento de Meta no repita el cierre.
    if message_id:
        await rc.marcar_comprobante(message_id)

    # Cierre del CLOSER: si se registró el pago, SIGUE la venta (agradece, coordina,
    # ofrece más). No avisa a la dueña (su banco ya le avisa) ni afirma verificación.
    if resultado.get("ok") and monto_cuadra:
        situacion = (
            "el cliente acaba de mandar el comprobante de su pago y ya lo registraste. "
            "Agradécele con calidez, dile que recibiste su pago y que coordinas la "
            "entrega/envío, y déjale la puerta abierta por si quiere algo más. "
            "NO digas que verificaste el pago en el banco ni que está 'confirmado'."
        )
    elif resultado.get("ok"):
        # Registrado, pero el monto NO cuadra con lo cobrado: no afirmar que está completo.
        situacion = (
            "el cliente mandó el comprobante pero el MONTO no cuadra con el de su pedido. "
            "Con calidez dile que ya lo recibiste y lo estás revisando, y que le confirmas "
            "en un momentito. NO afirmes que el pago está completo ni confirmado."
        )
    else:
        situacion = (
            "el cliente te envió una imagen pero no hay un pedido esperando pago; "
            "pregúntale con calidez si es un comprobante y en qué lo puedes ayudar"
        )
    partes = await _responder_situacion(telefono, situacion, nombre)

    # 🔴 EL CARRIL DEL DINERO NUNCA ES SILENCIOSO.
    # Si la dueña tiene ese chat tomado, el bot se calla (correcto) — pero el cliente ACABA DE
    # PAGAR y no recibiría absolutamente nada, y ella no se enteraría porque el aviso del bot
    # tampoco salió. Aquí se le avisa a ella, sí o sí.
    if resultado.get("ok") and not partes:
        await _avisar_pago_en_chat_pausado(telefono, nombre)


async def _avisar_pago_en_chat_pausado(telefono: str, nombre: str | None) -> None:
    """Entró un comprobante en un chat que la dueña tiene tomado: el bot no responde, así que
    hay que avisarle a ELLA. Un pago no se puede quedar sin acuse."""
    from sqlalchemy import select

    from app.models import Configuracion, Intervencion
    from app.services.meta_client import enviar_texto

    quien = nombre or telefono
    try:
        factory = get_session_factory()
        async with factory() as session:
            session.add(Intervencion(
                cliente_telefono=telefono,
                motivo="reclamo",
                detalle=(
                    f"{quien} MANDÓ UN COMPROBANTE y tú tienes ese chat tomado, así que el bot "
                    "no le respondió nada. Contéstale tú."
                ),
                mensaje_cliente="(comprobante de pago)",
            ))
            fila = (
                await session.execute(
                    select(Configuracion).where(Configuracion.clave == "dueno_telefono")
                )
            ).scalar_one_or_none()
            await session.commit()
        destino = (fila.valor if fila else None) or settings.dueno_telefono
        if destino:
            await enviar_texto(
                destino,
                f"💰 {quien} te mandó un comprobante, pero ese chat lo estás atendiendo tú: "
                "el bot NO le respondió nada. Entra y contéstale.",
            )
    except Exception:  # noqa: BLE001 — el pago YA está registrado; esto es el aviso
        logger.exception("No se pudo avisar del pago en chat pausado de %s", telefono)


# ─── Notas de voz y otros eventos (respuesta humana) ─────────────────

async def _responder_y_enviar(telefono: str, texto: str, nombre: str | None) -> None:
    """Pasa un texto por el agente y envia la respuesta. Comparte el lock por
    cliente para no responder en paralelo con el flujo de texto."""
    if not await rc.adquirir_lock(telefono):
        return
    try:
        if not await _bot_activo() or await _cliente_pausado(telefono) or not await _numero_permitido(telefono):
            await rc.guardar_historial(telefono, "user", texto)
            await _guardar_entrante(telefono, nombre, texto)
            return
        historial = await rc.obtener_historial(telefono)
        respuesta = await responder(telefono, texto, historial, nombre)
        respuesta = _proteger_afirmacion_de_pago(respuesta)
        partes = await _enviar_en_partes(telefono, respuesta)
        await rc.guardar_historial(telefono, "user", texto)
        if not partes:
            # La dueña tomó el chat mientras el bot pensaba: su respuesta se DESCARTA
            # (no se envía ni se recuerda). Lo que sí se guarda es lo que dijo el cliente,
            # para que ella lo vea y le conteste.
            await _guardar_entrante(telefono, nombre, texto)
            return
        # Los globos FALLIDOS también se guardan (se ven en ROJO en el panel), pero el bot no
        # "recuerda" haber dicho algo que el cliente nunca recibió.
        if _algo_llego(partes):
            await rc.guardar_historial(telefono, "assistant", respuesta)
        await _guardar_en_panel(telefono, nombre, texto, partes)
    except Exception:  # noqa: BLE001
        logger.exception("Error respondiendo a %s", telefono)
    finally:
        await rc.liberar_lock(telefono)


@celery_app.task(name="procesar_audio")
def procesar_audio(telefono, message_id, media_id, nombre=None, mime_type=None):
    """Tarea: descarga la nota de voz, la transcribe y responde como a un texto."""
    _run(_procesar_audio(telefono, media_id, nombre, mime_type))


async def _procesar_audio(telefono, media_id, nombre, mime_type) -> None:
    transcripcion = ""
    try:
        contenido, mime = await descargar_media(media_id)
        transcripcion = await transcribir_audio(contenido, mime or mime_type or "audio/ogg")
    except Exception:  # noqa: BLE001 — escuchar el audio nunca debe tumbar al worker
        logger.exception("No se pudo escuchar la nota de voz de %s", telefono)
    if transcripcion.strip():
        await _responder_y_enviar(telefono, transcripcion, nombre)
    else:
        # No se pudo entender el audio: el agente responde con naturalidad.
        await _responder_y_enviar(
            telefono, "(el cliente envio una nota de voz que no se pudo escuchar bien)", nombre
        )


@celery_app.task(name="procesar_evento")
def procesar_evento(telefono, tipo, nombre=None):
    """Tarea: sticker/video/ubicacion/etc. El agente responde natural, sin robotismos."""
    _run(_responder_y_enviar(telefono, f"(el cliente envio un {tipo}, sin texto)", nombre))


@celery_app.task(name="notificar_cliente_pago")
def notificar_cliente_pago(telefono, situacion):
    """Tarea: avisa al cliente (pago confirmado/rechazado) con un mensaje redactado
    al momento por Whuilianny, en su voz y con contexto — no una plantilla."""
    _run(_notificar_cliente_pago(telefono, situacion))


async def _avisar_a_la_duena(
    telefono: str, *, motivo: str, detalle: str, mensaje_cliente: str, whatsapp: str
) -> None:
    """Deja el aviso en la BANDEJA y le manda un WhatsApp a la dueña.

    Se usa cuando el bot NO pudo hacer su trabajo y alguien tiene que enterarse. Si esto fallara
    en silencio, el cliente se quedaría esperando a nadie — que es el peor final posible.
    """
    from sqlalchemy import select

    from app.models import Configuracion, Intervencion

    try:
        factory = get_session_factory()
        async with factory() as session:
            session.add(Intervencion(
                cliente_telefono=telefono,
                motivo=motivo,
                detalle=detalle,
                mensaje_cliente=mensaje_cliente,
            ))
            fila = (
                await session.execute(
                    select(Configuracion).where(Configuracion.clave == "dueno_telefono")
                )
            ).scalar_one_or_none()
            await session.commit()
        destino = (fila.valor if fila else None) or settings.dueno_telefono
        if destino:
            await enviar_texto(destino, whatsapp)
    except Exception:  # noqa: BLE001 — el aviso es lo último que hay; que no tumbe al worker
        logger.exception("No se pudo avisar a la dueña sobre %s", telefono)


async def _avisar_mensaje_frenado(telefono: str, nombre: str | None) -> None:
    """La red del dinero tumbó lo que el bot iba a decir (dos veces). El cliente recibió un acuse
    sobrio, pero la conversación la tiene que terminar una persona."""
    quien = nombre or telefono
    await _avisar_a_la_duena(
        telefono,
        motivo="bot_frenado",
        detalle=(
            f"Frené un mensaje del bot a {quien}: iba a decir un monto que NO salió del sistema, o "
            "una frase que tiene prohibida (el banco, ser una persona, un tema de salud). Al cliente "
            "solo le llegó un acuse. Entra tú al chat y termínalo."
        ),
        mensaje_cliente="(el bot se frenó solo)",
        whatsapp=(
            f"⚠️ Frené un mensaje del bot a {quien}: iba a decir algo que no puede. Solo le mandé un "
            "acuse. Entra al chat y contéstale tú."
        ),
    )


async def _notificar_cliente_pago(telefono, situacion) -> None:
    """La dueña confirmó o rechazó un pago desde el panel: hay que decírselo al cliente.

    🔴 ES EL ÚNICO CAMINO QUE LE HABLA AL CLIENTE **DÍAS DESPUÉS** (auditoría 2026-07-13). Todos
    los demás contestan a un mensaje que el cliente ACABA de mandar, así que la ventana de 24h de
    Meta está abierta por definición. Aquí NO: la dueña confirma el pago cuando puede (esa misma
    noche, al día siguiente…). Sin esta comprobación, Meta RECHAZA el envío y le BAJA LA CALIDAD
    AL NÚMERO — y siendo Enova Tech Provider, eso arriesga la cuenta de Meta de TODOS los clientes.
    Falla CERRADA: si no se le puede escribir, se le avisa a ELLA para que lo haga desde su
    teléfono. Un pago confirmado no se puede quedar sin avisar.
    """
    if not await _numero_permitido(telefono):
        return
    if not await _ventana_abierta(telefono):
        logger.warning("Aviso de pago a %s: ventana de 24h CERRADA → el bot NO escribe", telefono)
        await _avisar_a_la_duena(
            telefono,
            motivo="ventana_cerrada",
            detalle=(
                "Tocaste confirmar/rechazar el pago de este cliente, pero pasaron más de 24 horas "
                "desde su último mensaje: WhatsApp NO deja escribirle. El bot no le avisó nada — "
                "escríbele tú desde tu teléfono."
            ),
            mensaje_cliente="(esperando el resultado de su pago)",
            whatsapp=(
                f"⏰ No pude avisarle a {telefono} lo de su pago: pasaron más de 24 horas desde su "
                "último mensaje y WhatsApp no deja escribirle. Escríbele tú."
            ),
        )
        return
    try:
        historial = await rc.obtener_historial(telefono)
        _usd, _bs = await _montos_decibles(telefono)
        mensaje = await redactar_mensaje(
            situacion, historial, None, telefono, montos_usd=_usd, montos_bs=_bs
        )
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo redactar el aviso de pago para %s", telefono)
        return
    if not mensaje.strip():
        # La red del dinero lo tumbó dos veces: NO se le manda una mentira sobre su pago.
        logger.error("Aviso de pago a %s: no salió un mensaje limpio; lo pasa la dueña", telefono)
        await _avisar_mensaje_frenado(telefono, None)
        return
    partes = await _enviar_en_partes(telefono, mensaje)
    if _algo_llego(partes):
        await rc.guardar_historial(telefono, "assistant", mensaje)
    if partes:
        await _guardar_en_panel(telefono, None, "", partes)
