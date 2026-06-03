from typing import TypedDict


class MensajeEntrante(TypedDict):
    message_id: str
    telefono: str
    nombre: str | None
    tipo: str
    texto: str | None
    media_id: str | None
    caption: str | None
    mime_type: str | None


def extraer_mensaje(payload: dict) -> MensajeEntrante | None:
    """Extrae el mensaje entrante del payload de Meta.

    Devuelve None si el evento no es un mensaje de usuario (ej. un status
    update de 'entregado'/'leido', que Meta tambien manda al mismo webhook)
    o si viene mal formado (sin id o sin remitente).

    Para imagenes y documentos rellena media_id/caption/mime_type; el binario
    se descarga aparte con services.meta_client.descargar_media.
    """
    try:
        value = payload["entry"][0]["changes"][0]["value"]
    except (KeyError, IndexError, TypeError):
        return None

    mensajes = value.get("messages")
    if not mensajes:
        return None  # no es un mensaje entrante (probablemente un status)

    msg = mensajes[0]
    message_id = msg.get("id")
    telefono = msg.get("from")
    if not message_id or not telefono:
        return None  # evento mal formado: no se puede procesar de forma segura

    contactos = value.get("contacts", [])
    nombre = None
    if contactos:
        nombre = contactos[0].get("profile", {}).get("name")

    tipo = msg.get("type", "desconocido")
    texto = None
    media_id = None
    caption = None
    mime_type = None

    if tipo == "text":
        texto = msg.get("text", {}).get("body")
    elif tipo in ("image", "document", "audio"):
        media = msg.get(tipo, {})
        media_id = media.get("id")
        caption = media.get("caption")  # audio no trae caption (queda None)
        mime_type = media.get("mime_type")

    return MensajeEntrante(
        message_id=message_id,
        telefono=telefono,
        nombre=nombre,
        tipo=tipo,
        texto=texto,
        media_id=media_id,
        caption=caption,
        mime_type=mime_type,
    )
