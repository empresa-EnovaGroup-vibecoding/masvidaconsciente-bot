import json
import logging

from fastapi import APIRouter, Query, Request, Response

from app.config import get_settings
from app.webhook.parser import extraer_mensaje
from app.webhook.signature import verificar_firma

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhook", tags=["webhook"])
settings = get_settings()


@router.get("/whatsapp")
def verificar(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
):
    """Handshake de verificación que Meta hace una sola vez al registrar el webhook."""
    if hub_mode == "subscribe" and hub_verify_token == settings.meta_verify_token:
        return Response(content=hub_challenge or "", media_type="text/plain")
    return Response(status_code=403)


@router.post("/whatsapp")
async def recibir(request: Request):
    """Recibe mensajes de WhatsApp. Valida la firma, extrae el mensaje y lo encola.

    Responde 200 de inmediato: el procesamiento con IA ocurre en background
    (Celery) para no exceder el límite de ~5s que Meta espera.
    """
    raw = await request.body()
    firma = request.headers.get("x-hub-signature-256")
    if not verificar_firma(settings.meta_app_secret, raw, firma):
        logger.warning("Webhook con firma inválida — descartado")
        return Response(status_code=401)

    payload = json.loads(raw)
    _testigo(payload)
    mensaje = extraer_mensaje(payload)

    if mensaje is None:
        return {"status": "ignored"}  # status update u otro evento sin mensaje

    # EL RELOJ DE LAS 24 HORAS arranca AQUÍ y no en el worker: este es el único embudo por
    # el que pasan los CUATRO caminos (texto, voz, comprobante, sticker). El comprobante, por
    # ejemplo, nunca pasa por el worker de texto: si el reloj viviera allá, un cliente que solo
    # manda la captura del pago aparecería con la ventana CERRADA y la dueña no podría
    # responderle justo en el momento del dinero.
    await _marcar_entrante(mensaje["telefono"], mensaje.get("nombre"))

    # Mostrar "escribiendo…" de inmediato: el cliente ve que lo estamos atendiendo
    # (se siente humano, no robotico). Import perezoso; no critico si falla.
    from app.services.meta_client import marcar_leido_y_escribiendo
    await marcar_leido_y_escribiendo(mensaje["message_id"])

    tipo = mensaje["tipo"]

    # Tope de gasto / anti-abuso: los comprobantes (image/document) SIEMPRE pasan
    # (es dinero); el resto cuenta para el limite diario por cliente.
    if tipo not in ("image", "document") and await _excede_tope(
        mensaje["telefono"], mensaje.get("nombre")
    ):
        return {"status": "limite"}

    if tipo == "text":
        logger.info("Mensaje de %s: %s", mensaje["telefono"], mensaje["texto"])
        estado = await _encolar_mensaje(mensaje)
        return {"status": estado}

    if tipo in ("image", "document"):
        logger.info("Comprobante (%s) de %s", tipo, mensaje["telefono"])
        estado = await _encolar_comprobante(mensaje)
        return {"status": estado, "tipo": tipo}

    if tipo == "audio":
        logger.info("Nota de voz de %s", mensaje["telefono"])
        estado = await _encolar_audio(mensaje)
        return {"status": estado, "tipo": tipo}

    # sticker / video / ubicacion / contactos / etc.: el agente responde como humano.
    logger.info("Evento %s de %s", tipo, mensaje["telefono"])
    estado = await _encolar_evento(mensaje)
    return {"status": estado, "tipo": tipo}


def _testigo(payload: dict) -> None:
    """SOLO MIRA Y ANOTA. No cambia nada, no responde nada, no toca la BD.

    Existe para poder responder CON PRUEBAS a la única pregunta peligrosa de la Fase 2:
    cuando se active `smb_message_echoes` en Meta, ¿el eco se dispara TAMBIÉN con los
    mensajes que manda el BOT? Si así fuera, el bot se pausaría a sí mismo después de cada
    respuesta y se quedaría MUDO con todos los clientes.

    Anota, de cada evento: qué campo llegó (`field`), qué claves trae (`messages`,
    `message_echoes`, `statuses`…) y, si es un eco, de quién es y qué dice. Con eso se
    decide si la Fase 2 se construye o no. Cualquier fallo aquí se traga: un testigo JAMÁS
    puede tumbar el webhook.
    """
    try:
        cambio = payload["entry"][0]["changes"][0]
        campo = cambio.get("field")
        value = cambio.get("value") or {}
        claves = sorted(k for k in value if k not in ("messaging_product", "metadata"))
        if "messages" in claves:
            return  # el camino normal (un cliente escribiendo): ya se registra en otro lado
        detalle = ""
        ecos = value.get("message_echoes")
        if ecos:
            e = ecos[0] if isinstance(ecos, list) and ecos else {}
            detalle = (
                f" | eco: from={e.get('from')} to={e.get('to')} tipo={e.get('type')}"
                f" id={e.get('id')}"
            )
        logger.info("TESTIGO webhook: field=%s claves=%s%s", campo, claves, detalle)
    except Exception:  # noqa: BLE001 — un testigo nunca puede tumbar el webhook
        pass


async def _marcar_entrante(telefono: str, nombre: str | None) -> None:
    """El cliente ESCRIBIÓ: se abre su ventana de 24h y sube su contador de no leídos.

    Va con UPSERT a propósito: si el cliente es NUEVO, la fila todavía no existe (la crea el
    worker, después). Con un UPDATE simple no se guardaría nada y el cliente estrenaría con la
    ventana en NULL = CERRADA — o sea, la dueña no podría contestarle a quien le escribe por
    primera vez, que es justo el que más importa.

    Si esto falla, se loguea pero NO se rompe el webhook: perder el reloj es malo, pero
    devolverle un error a Meta (y que reintente el mensaje) es peor.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models import Cliente, now_utc
    from app.services.db import get_session_factory

    ahora = now_utc()
    try:
        factory = get_session_factory()
        async with factory() as session:
            stmt = pg_insert(Cliente).values(
                telefono=telefono,
                nombre=nombre,
                ultima_interaccion=ahora,
                ultimo_entrante_at=ahora,
                no_leidos=1,
            )
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[Cliente.telefono],
                    set_={
                        "ultima_interaccion": ahora,
                        "ultimo_entrante_at": ahora,
                        # El nombre NO se pisa: el que ya tenemos (o el que puso la dueña a mano)
                        # vale más que el del perfil de WhatsApp.
                        "no_leidos": Cliente.no_leidos + 1,
                    },
                )
            )
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo marcar el mensaje entrante de %s", telefono)


async def _encolar_mensaje(mensaje) -> str:
    """Idempotencia + buffer + encolado en Celery.

    Imports perezosos: así importar el router no exige Redis/Celery, y los
    tests pueden sustituir esta función sin esos servicios.
    """
    from app.services import redis_client as rc
    from app.workers.tasks import procesar_buffer

    if await rc.ya_procesado(mensaje["message_id"]):
        return "duplicado"

    await rc.agregar_a_buffer(mensaje["telefono"], mensaje["texto"])
    procesar_buffer.apply_async(
        (mensaje["telefono"], mensaje["nombre"]),
        countdown=settings.buffer_segundos,
    )
    return "ok"


async def _encolar_comprobante(mensaje) -> str:
    """Encola un comprobante (imagen/PDF) en su carril propio.

    NO pasa por el buffer de texto de 15s. La idempotencia NO se marca aqui:
    se consolida en el worker SOLO tras un registro exitoso (y la BD la blinda
    con el UNIQUE de comprobante_media_id), para que un fallo transitorio de
    descarga no descarte el reintento legitimo de Meta y se pierda el pago.
    """
    from app.workers.tasks import procesar_comprobante

    if not mensaje.get("media_id"):
        logger.warning("Comprobante sin media_id de %s", mensaje["telefono"])
        return "sin_media"

    procesar_comprobante.apply_async((
        mensaje["telefono"],
        mensaje["message_id"],
        mensaje["media_id"],
        mensaje.get("caption"),
        mensaje.get("nombre"),
        mensaje.get("mime_type"),
    ))
    return "ok"


async def _encolar_audio(mensaje) -> str:
    """Nota de voz: se descarga, se transcribe y el agente responde como a un texto."""
    from app.services import redis_client as rc
    from app.workers.tasks import procesar_audio

    if not mensaje.get("media_id"):
        return "sin_media"
    if await rc.ya_procesado(mensaje["message_id"]):
        return "duplicado"
    procesar_audio.apply_async((
        mensaje["telefono"],
        mensaje["message_id"],
        mensaje["media_id"],
        mensaje.get("nombre"),
        mensaje.get("mime_type"),
    ))
    return "ok"


async def _encolar_evento(mensaje) -> str:
    """Sticker/video/ubicacion/etc.: el agente responde natural, sin frases roboticas."""
    from app.services import redis_client as rc
    from app.workers.tasks import procesar_evento

    if await rc.ya_procesado(mensaje["message_id"]):
        return "duplicado"
    procesar_evento.apply_async((mensaje["telefono"], mensaje["tipo"], mensaje.get("nombre")))
    return "ok"


async def _excede_tope(telefono: str, nombre: str | None) -> bool:
    """True si el cliente supero el tope de mensajes del dia: se frena la respuesta
    automatica y se avisa a la duena (una vez). limite<=0 = sin tope.
    Cualquier fallo del contador deja pasar el mensaje (no frena el bot)."""
    limite = settings.limite_mensajes_cliente_dia
    if limite <= 0:
        return False
    from app.services import redis_client as rc

    try:
        n = await rc.contar_mensaje_dia(telefono)
    except Exception:  # noqa: BLE001 — un fallo del contador no debe frenar el bot
        logger.exception("No se pudo contar mensajes de %s", telefono)
        return False
    if n <= limite:
        return False
    logger.warning("Cliente %s supero el tope diario (%s > %s)", telefono, n, limite)
    try:
        if await rc.aviso_abuso_nuevo(telefono):
            await _avisar_duena_abuso(telefono, nombre, n)
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo avisar del abuso de %s", telefono)
    return True


async def _avisar_duena_abuso(telefono: str, nombre: str | None, n: int) -> None:
    """Avisa a la duena (su WhatsApp) que un cliente supero el tope del dia."""
    from sqlalchemy import select

    from app.models import Configuracion
    from app.services.db import get_session_factory
    from app.services.meta_client import enviar_texto

    factory = get_session_factory()
    async with factory() as session:
        fila = (
            await session.execute(
                select(Configuracion).where(Configuracion.clave == "dueno_telefono")
            )
        ).scalar_one_or_none()
    destino = (fila.valor if fila else None) or settings.dueno_telefono
    if not destino:
        return
    quien = nombre or telefono
    await enviar_texto(
        destino,
        f"⚠️ {quien} superó el límite de mensajes de hoy ({n}). El bot pausó las "
        f"respuestas automáticas con ese cliente por hoy; si quieres, escríbele tú.",
    )
