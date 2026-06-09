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
    mensaje = extraer_mensaje(payload)

    if mensaje is None:
        return {"status": "ignored"}  # status update u otro evento sin mensaje

    # Mostrar "escribiendo…" de inmediato: el cliente ve que lo estamos atendiendo
    # (se siente humano, no robotico). Import perezoso; no critico si falla.
    from app.services.meta_client import marcar_leido_y_escribiendo
    await marcar_leido_y_escribiendo(mensaje["message_id"])

    tipo = mensaje["tipo"]

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
