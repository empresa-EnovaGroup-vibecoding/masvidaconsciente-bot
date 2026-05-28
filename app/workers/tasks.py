import asyncio
import logging

from app.agent.agent import responder
from app.services import redis_client as rc
from app.services.meta_client import enviar_texto
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


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
