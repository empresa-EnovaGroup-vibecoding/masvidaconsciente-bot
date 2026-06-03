import asyncio
import logging
import os

from app.agent.agent import responder, transcribir_audio
from app.config import get_settings
from app.services import redis_client as rc
from app.services.meta_client import descargar_media, enviar_texto
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)
settings = get_settings()


@celery_app.task(name="procesar_buffer")
def procesar_buffer(telefono: str, nombre: str | None = None):
    """Tarea Celery: procesa los mensajes acumulados de un cliente y responde."""
    asyncio.run(_procesar(telefono, nombre))


async def _procesar(telefono: str, nombre: str | None) -> None:
    # Solo un worker procesa el buffer de este cliente a la vez.
    if not await rc.adquirir_lock(telefono):
        return
    try:
        mensajes = await rc.vaciar_buffer(telefono)
        if not mensajes:
            return  # otra tarea ya lo procesó

        texto = "\n".join(mensajes)
        historial = await rc.obtener_historial(telefono)

        respuesta = await responder(telefono, texto, historial, nombre)

        await enviar_texto(telefono, respuesta)
        await rc.guardar_historial(telefono, "user", texto)
        await rc.guardar_historial(telefono, "assistant", respuesta)
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


@celery_app.task(name="procesar_comprobante")
def procesar_comprobante(telefono, message_id, media_id, caption=None, nombre=None, mime_type=None):
    """Tarea dedicada (fuera del buffer de texto): descarga y guarda el comprobante."""
    asyncio.run(_procesar_comprobante(telefono, media_id, caption, nombre, mime_type))


async def _procesar_comprobante(telefono, media_id, caption, nombre, mime_type) -> None:
    try:
        contenido, mime = await descargar_media(media_id)
    except Exception:  # noqa: BLE001 — un fallo de descarga no debe tumbar al worker
        logger.exception("No se pudo descargar el comprobante %s de %s", media_id, telefono)
        return
    ruta = _guardar_comprobante(media_id, contenido, mime or mime_type or "")
    logger.info("Comprobante de %s guardado en %s (%s bytes)", telefono, ruta, len(contenido))
    # Fase 4/5: aqui se amarrara el comprobante al pedido en 'esperando_pago',
    # se registrara en la tabla pagos y se avisara a la duena.


# ─── Notas de voz y otros eventos (respuesta humana) ─────────────────

async def _responder_y_enviar(telefono: str, texto: str, nombre: str | None) -> None:
    """Pasa un texto por el agente y envia la respuesta. Comparte el lock por
    cliente para no responder en paralelo con el flujo de texto."""
    if not await rc.adquirir_lock(telefono):
        return
    try:
        historial = await rc.obtener_historial(telefono)
        respuesta = await responder(telefono, texto, historial, nombre)
        await enviar_texto(telefono, respuesta)
        await rc.guardar_historial(telefono, "user", texto)
        await rc.guardar_historial(telefono, "assistant", respuesta)
    except Exception:  # noqa: BLE001
        logger.exception("Error respondiendo a %s", telefono)
    finally:
        await rc.liberar_lock(telefono)


@celery_app.task(name="procesar_audio")
def procesar_audio(telefono, message_id, media_id, nombre=None, mime_type=None):
    """Tarea: descarga la nota de voz, la transcribe y responde como a un texto."""
    asyncio.run(_procesar_audio(telefono, media_id, nombre, mime_type))


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
    asyncio.run(_responder_y_enviar(telefono, f"(el cliente envio un {tipo}, sin texto)", nombre))
