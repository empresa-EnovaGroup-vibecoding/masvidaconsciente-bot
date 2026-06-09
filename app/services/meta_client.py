"""Cliente de la WhatsApp Cloud API (Meta) para enviar mensajes."""
import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

GRAPH_VERSION = "v21.0"


def _graph_base() -> str:
    return f"https://graph.facebook.com/{GRAPH_VERSION}"


def _url() -> str:
    return f"{_graph_base()}/{settings.meta_phone_number_id}/messages"


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


async def marcar_leido_y_escribiendo(message_id: str) -> None:
    """Marca el mensaje como leído (doble check azul) Y muestra "escribiendo…".

    El indicador de tipeo lo borra Meta solo cuando respondemos o a los 25s.
    Por eso SOLO se llama cuando SÍ vamos a responder (humaniza al agente).
    No es crítico: si falla, el bot responde igual.
    """
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
        "typing_indicator": {"type": "text"},
    }
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.post(_url(), headers=_headers(), json=payload)
        except httpx.HTTPError as e:  # no es crítico si falla
            logger.warning("No se pudo marcar leído / mostrar escribiendo: %s", e)


async def descargar_media(media_id: str) -> tuple[bytes, str]:
    """Descarga un archivo (comprobante) de WhatsApp en 2 pasos.

    1) GET /{media_id} -> JSON con una URL temporal (caduca ~5 min) y el mime_type.
    2) GET de esa URL, con el MISMO token, -> bytes del archivo.

    Devuelve (contenido, mime_type). Lanza si Meta responde error o no da URL.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        meta = await client.get(f"{_graph_base()}/{media_id}", headers=_headers())
        meta.raise_for_status()
        info = meta.json()
        url = info.get("url")
        mime = info.get("mime_type") or "application/octet-stream"
        if not url:
            raise ValueError(f"Meta no devolvio URL de descarga para el media {media_id}")
        archivo = await client.get(url, headers=_headers())
        archivo.raise_for_status()
        return archivo.content, mime
