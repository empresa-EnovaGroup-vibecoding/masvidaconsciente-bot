from typing import TypedDict


class MensajeEntrante(TypedDict):
    message_id: str
    telefono: str
    nombre: str | None
    tipo: str
    texto: str | None


def extraer_mensaje(payload: dict) -> MensajeEntrante | None:
    """Extrae el mensaje entrante del payload de Meta.

    Devuelve None si el evento no es un mensaje de usuario (ej. un status
    update de 'entregado'/'leído', que Meta también manda al mismo webhook).
    """
    try:
        value = payload["entry"][0]["changes"][0]["value"]
    except (KeyError, IndexError, TypeError):
        return None

    mensajes = value.get("messages")
    if not mensajes:
        return None  # no es un mensaje entrante (probablemente un status)

    msg = mensajes[0]
    contactos = value.get("contacts", [])
    nombre = None
    if contactos:
        nombre = contactos[0].get("profile", {}).get("name")

    tipo = msg.get("type", "desconocido")
    texto = msg.get("text", {}).get("body") if tipo == "text" else None

    return MensajeEntrante(
        message_id=msg["id"],
        telefono=msg["from"],
        nombre=nombre,
        tipo=tipo,
        texto=texto,
    )
