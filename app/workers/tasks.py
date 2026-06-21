import asyncio
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
    telefono: str, nombre: str | None, texto_usuario: str, respuesta: str
) -> None:
    """Persiste la conversacion en Postgres para que aparezca en el panel.

    El historial en Redis es para el contexto del agente; el PANEL lee de Postgres
    (tablas clientes + mensajes). Sin esto, las charlas no se ven en el panel.
    No es critico: si falla, el bot ya respondio igual.
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
            session.add(Mensaje(cliente_telefono=telefono, rol="user", contenido=texto_usuario))
            session.add(Mensaje(cliente_telefono=telefono, rol="assistant", contenido=respuesta))
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
    t = re.sub(r"\$\s?(\d+)\.00(?!\d)", r"$\1", t)  # $18.00 -> $18
    return t


async def _enviar_en_partes(telefono: str, texto: str) -> None:
    """Envía la respuesta PLANA y como VARIOS mensajes cortos (como una persona real
    en WhatsApp), no un mensajote. El agente separa cada globo con una línea en blanco;
    aquí aplanamos el formato, partimos por las líneas en blanco y enviamos cada parte
    por separado, con una pausa breve. Tope de globos para proteger la calidad del número."""
    if not texto or not texto.strip():
        return
    texto = _aplanar(texto)
    partes = [p.strip() for p in re.split(r"\n\s*\n", texto.strip()) if p.strip()]
    if not partes:
        partes = [texto.strip()]
    if len(partes) > 6:  # tope anti-spam: junta el exceso en el último globo
        partes = partes[:5] + ["\n\n".join(partes[5:])]
    for i, parte in enumerate(partes):
        if i:
            await asyncio.sleep(1.0)  # pausa breve entre globos, como una persona
        await enviar_texto(telefono, parte)


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
    """True si la dueña pausó el bot SOLO para este cliente (atiende ella ese chat).
    Ante error de lectura, devuelve False (el bot sigue respondiendo: fail-safe)."""
    from sqlalchemy import select

    from app.models import Cliente
    from app.services.db import get_session_factory

    try:
        factory = get_session_factory()
        async with factory() as session:
            cliente = (
                await session.execute(select(Cliente).where(Cliente.telefono == telefono))
            ).scalar_one_or_none()
        return bool(cliente and cliente.bot_pausado)
    except Exception:  # noqa: BLE001
        return False


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

        if not await _bot_activo() or await _cliente_pausado(telefono):
            # Bot apagado (global o solo en este chat): guarda lo que escribió el
            # cliente para que la dueña lo vea en Conversaciones y responda ella.
            await rc.guardar_historial(telefono, "user", texto)
            await _guardar_entrante(telefono, nombre, texto)
            return

        historial = await rc.obtener_historial(telefono)

        respuesta = await responder(telefono, texto, historial, nombre)
        respuesta = _proteger_afirmacion_de_pago(respuesta)

        await _enviar_en_partes(telefono, respuesta)
        await rc.guardar_historial(telefono, "user", texto)
        await rc.guardar_historial(telefono, "assistant", respuesta)
        await _guardar_en_panel(telefono, nombre, texto, respuesta)
    except Exception:  # noqa: BLE001
        logger.exception("Error procesando el buffer de %s", telefono)
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
    """Lee el comprobante con visión, dándole los datos de la cuenta de la dueña
    (para reconocer SOLO pagos hacia ella). Nunca lanza."""
    from sqlalchemy import select

    from app.models import Configuracion

    titular = telefono_pago = banco = cedula = None
    try:
        factory = get_session_factory()
        async with factory() as session:
            cfg = {
                f.clave: f.valor
                for f in (await session.execute(select(Configuracion))).scalars().all()
            }
        titular = cfg.get("pago_movil_titular")
        telefono_pago = cfg.get("pago_movil_telefono")
        banco = cfg.get("pago_movil_banco")
        cedula = cfg.get("pago_movil_cedula")
    except Exception:  # noqa: BLE001
        pass
    try:
        return await leer_comprobante(
            contenido,
            base_mime,
            titular=titular,
            telefono_pago=telefono_pago,
            banco=banco,
            cedula=cedula,
        )
    except Exception:  # noqa: BLE001 — defensa extra: nunca tumbar el worker
        logger.exception("Fallo leyendo el comprobante de %s", telefono)
        return {"es_comprobante": None, "leido": False}


async def _responder_situacion(telefono: str, situacion: str, nombre: str | None) -> None:
    """Whuilianny REDACTA un mensaje para el cliente según la situación (no plantilla),
    lo protege contra afirmaciones de pago, lo envía en partes y lo guarda en historial."""
    try:
        historial = await rc.obtener_historial(telefono)
        mensaje = await redactar_mensaje(situacion, historial, nombre)
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo redactar el mensaje al cliente %s", telefono)
        return
    mensaje = _proteger_afirmacion_de_pago(mensaje or "")
    if mensaje.strip():
        await _enviar_en_partes(telefono, mensaje)
        await rc.guardar_historial(telefono, "assistant", mensaje)


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
        await _responder_situacion(
            telefono,
            "el cliente te envió una IMAGEN que NO es un comprobante de pago a tu cuenta "
            "(parece otra cosa, o no se ve claro). Con calidez dile que no ves el "
            "comprobante del pago y pídele la captura donde se vean el monto y el número "
            "de referencia del pago hecho a tu Pago Móvil.",
            nombre,
        )
        return

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
    if resultado.get("ok"):
        situacion = (
            "el cliente acaba de mandar el comprobante de su pago y ya lo registraste. "
            "Agradécele con calidez, dile que recibiste su pago y que coordinas la "
            "entrega/envío, y déjale la puerta abierta por si quiere algo más. "
            "NO digas que verificaste el pago en el banco ni que está 'confirmado'."
        )
    else:
        situacion = (
            "el cliente te envió una imagen pero no hay un pedido esperando pago; "
            "pregúntale con calidez si es un comprobante y en qué lo puedes ayudar"
        )
    await _responder_situacion(telefono, situacion, nombre)


# ─── Notas de voz y otros eventos (respuesta humana) ─────────────────

async def _responder_y_enviar(telefono: str, texto: str, nombre: str | None) -> None:
    """Pasa un texto por el agente y envia la respuesta. Comparte el lock por
    cliente para no responder en paralelo con el flujo de texto."""
    if not await rc.adquirir_lock(telefono):
        return
    try:
        if not await _bot_activo() or await _cliente_pausado(telefono):
            await rc.guardar_historial(telefono, "user", texto)
            await _guardar_entrante(telefono, nombre, texto)
            return
        historial = await rc.obtener_historial(telefono)
        respuesta = await responder(telefono, texto, historial, nombre)
        respuesta = _proteger_afirmacion_de_pago(respuesta)
        await _enviar_en_partes(telefono, respuesta)
        await rc.guardar_historial(telefono, "user", texto)
        await rc.guardar_historial(telefono, "assistant", respuesta)
        await _guardar_en_panel(telefono, nombre, texto, respuesta)
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


async def _notificar_cliente_pago(telefono, situacion) -> None:
    try:
        historial = await rc.obtener_historial(telefono)
        mensaje = await redactar_mensaje(situacion, historial, None)
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo redactar el aviso de pago para %s", telefono)
        return
    if mensaje.strip():
        await _enviar_en_partes(telefono, mensaje)
        await rc.guardar_historial(telefono, "assistant", mensaje)
