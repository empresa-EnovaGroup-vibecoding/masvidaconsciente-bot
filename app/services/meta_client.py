"""Cliente de la WhatsApp Cloud API (Meta) para enviar mensajes."""
import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

GRAPH_VERSION = "v21.0"


def _url() -> str:
    return f"https://graph.facebook.com/{GRAPH_VERSION}/{settings.meta_phone_number_id}/messages"


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.meta_access_token}"}


async def enviar_texto(telefono: str, texto: str) -> dict:
    payload = {
        "messaging_product": "whatsapp",
        "to": telefono,
        "type": "text",
        "text": {"body": texto},
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(_url(), headers=_headers(), json=payload)
        if resp.status_code >= 400:
            logger.error("Meta rechazó el envío (%s): %s", resp.status_code, resp.text)
            resp.raise_for_status()
        return resp.json()


async def marcar_leido(message_id: str) -> None:
    payload = {"messaging_product": "whatsapp", "status": "read", "message_id": message_id}
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.post(_url(), headers=_headers(), json=payload)
        except httpx.HTTPError as e:  # no es crítico si falla
            logger.warning("No se pudo marcar como leído: %s", e)
