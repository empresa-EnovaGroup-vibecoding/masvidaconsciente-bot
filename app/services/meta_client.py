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


def wa_message_id(respuesta: dict | None) -> str | None:
    """El id que Meta le pone al mensaje que acabamos de enviar ('wamid.XXX').

    Viene en TODAS las respuestas de envío (`{"messages": [{"id": "wamid…"}]}`) y hasta ahora
    se TIRABA en el camino de la media: `enviar_imagen`/`enviar_video`/`enviar_documento`
    devolvían el JSON y quien las llamaba lo descartaba. Sin ese id no hay forma de casar los
    acuses de Meta (entregado / leído / **FALLÓ**) con la foto que se mandó — así que una foto
    que Meta rechaza se pierde en silencio, sin rastro en el panel.
    """
    try:
        return ((respuesta or {}).get("messages") or [{}])[0].get("id") or None
    except (AttributeError, IndexError, TypeError):
        return None


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


async def enviar_documento(
    telefono: str, link: str, filename: str, caption: str | None = None
) -> dict:
    """Envía un documento (PDF) por WhatsApp con un link PÚBLICO que Meta descarga.
    El link debe ser HTTPS y accesible sin login."""
    documento: dict = {"link": link, "filename": filename}
    if caption:
        documento["caption"] = caption
    payload = {
        "messaging_product": "whatsapp",
        "to": telefono,
        "type": "document",
        "document": documento,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(_url(), headers=_headers(), json=payload)
        if resp.status_code >= 400:
            logger.error("Meta rechazó el documento (%s): %s", resp.status_code, resp.text)
            resp.raise_for_status()
        return resp.json()


async def enviar_imagen(telefono: str, link: str, caption: str | None = None) -> dict:
    """Envía una IMAGEN por WhatsApp con un link PÚBLICO (HTTPS) que Meta descarga."""
    imagen: dict = {"link": link}
    if caption:
        imagen["caption"] = caption
    payload = {
        "messaging_product": "whatsapp",
        "to": telefono,
        "type": "image",
        "image": imagen,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(_url(), headers=_headers(), json=payload)
        if resp.status_code >= 400:
            logger.error("Meta rechazó la imagen (%s): %s", resp.status_code, resp.text)
            resp.raise_for_status()
        return resp.json()


async def enviar_video(telefono: str, link: str, caption: str | None = None) -> dict:
    """Envía un VIDEO por WhatsApp con un link PÚBLICO (HTTPS) que Meta descarga."""
    video: dict = {"link": link}
    if caption:
        video["caption"] = caption
    payload = {
        "messaging_product": "whatsapp",
        "to": telefono,
        "type": "video",
        "video": video,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(_url(), headers=_headers(), json=payload)
        if resp.status_code >= 400:
            logger.error("Meta rechazó el video (%s): %s", resp.status_code, resp.text)
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
