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

    if mensaje["tipo"] != "text":
        logger.info("Mensaje no-texto (%s) de %s", mensaje["tipo"], mensaje["telefono"])
        # Fase 5: encolar una respuesta "por ahora solo texto"
        return {"status": "ok", "tipo": mensaje["tipo"]}

    logger.info("Mensaje de %s: %s", mensaje["telefono"], mensaje["texto"])
    estado = await _encolar_mensaje(mensaje)
    return {"status": estado}


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
