"""Lee lo que manda Meta y lo convierte en EVENTOS.

Meta manda TRES cosas por el mismo webhook, y son cosas MUY distintas:

  · `messages`            → un CLIENTE escribió.
  · `smb_message_echoes`  → LA DUEÑA escribió desde su CELULAR (coexistencia).
  · `statuses`            → un mensaje NUESTRO fue entregado / leído / FALLÓ.

Cada una devuelve un TIPO PROPIO (`MensajeEntrante`, `EcoSaliente`, `EstadoEnvio`) a propósito:
si los tres compartieran forma sería fácil tratar un eco como si fuera un cliente, y en el eco
el campo `from` es **el número del negocio** — el bot acabaría respondiéndose a sí mismo en bucle.

🔴 Y se recorre TODO el payload (`entry` → `changes` → cada mensaje). Antes se leía solo
`entry[0]["changes"][0]["messages"][0]`: si Meta agrupaba varios eventos en un POST (lo hace),
los demás se perdían, el webhook respondía 200, Meta no reintentaba, y el "quiero 8 empanadas"
de un cliente **se perdía para siempre**.
"""
from typing import Literal, TypedDict


class MensajeEntrante(TypedDict):
    clase: Literal["mensaje"]
    message_id: str
    telefono: str  # el CLIENTE (msg["from"])
    nombre: str | None
    tipo: str
    texto: str | None
    media_id: str | None
    caption: str | None
    mime_type: str | None
    timestamp: str | None  # el de Meta (segundos epoch), para no desordenar el hilo


class EcoSaliente(TypedDict):
    """Lo que la dueña escribió DESDE SU CELULAR. Ojo: el cliente es `to`, NO `from`."""
    clase: Literal["eco"]
    message_id: str
    telefono: str  # el CLIENTE (eco["to"])
    tipo: str
    texto: str | None
    media_id: str | None
    caption: str | None
    mime_type: str | None
    timestamp: str | None


class EstadoEnvio(TypedDict):
    """Qué pasó con un mensaje que enviamos NOSOTROS (bot o dueña desde el panel)."""
    clase: Literal["estado"]
    wa_message_id: str
    estado: str  # enviado | entregado | leido | fallido
    error: str | None


Evento = MensajeEntrante | EcoSaliente | EstadoEnvio

# Lo que la BD acepta en `mensajes.tipo` (ver el CHECK de la migración 021). Cualquier cosa
# rara que invente Meta cae en 'otro' en vez de reventar el INSERT (y con él, la PAUSA).
_TIPOS = {
    "text", "image", "audio", "document", "sticker", "video",
    "location", "contacts", "reaction",
}

# `mensajes.contenido` es NOT NULL: una foto sin pie de foto NO puede guardarse con None.
_PLACEHOLDER = {
    "image": "[foto]",
    "audio": "[nota de voz]",
    "document": "[documento]",
    "sticker": "[sticker]",
    "video": "[video]",
    "location": "[ubicación]",
    "contacts": "[contacto]",
    "reaction": "[reacción]",
    "otro": "[mensaje]",
}

_ESTADOS = {"sent": "enviado", "delivered": "entregado", "read": "leido", "failed": "fallido"}


def tipo_valido(tipo: str | None) -> str:
    return tipo if tipo in _TIPOS else "otro"


def contenido_seguro(tipo: str, texto: str | None, caption: str | None) -> str:
    """Nunca devuelve vacío: `mensajes.contenido` es NOT NULL."""
    for candidato in (texto, caption):
        if candidato and candidato.strip():
            return candidato.strip()
    return _PLACEHOLDER.get(tipo, "[mensaje]")


def _extraer_media(msg: dict, tipo: str) -> tuple[str | None, str | None, str | None]:
    """(media_id, caption, mime_type) del cuerpo del mensaje, sea del tipo que sea."""
    cuerpo = msg.get(tipo)
    if not isinstance(cuerpo, dict):
        return None, None, None
    return cuerpo.get("id"), cuerpo.get("caption"), cuerpo.get("mime_type")


def _texto_de(msg: dict, tipo: str) -> str | None:
    if tipo == "text":
        return (msg.get("text") or {}).get("body")
    if tipo == "reaction":
        return (msg.get("reaction") or {}).get("emoji")
    return None


def extraer_eventos(payload: dict) -> list[Evento]:
    """TODO lo que trae este POST de Meta. Nada se pierde en silencio."""
    eventos: list[Evento] = []
    for entry in (payload.get("entry") or []):
        if not isinstance(entry, dict):
            continue
        for cambio in (entry.get("changes") or []):
            if not isinstance(cambio, dict):
                continue
            campo = cambio.get("field")
            value = cambio.get("value") or {}
            if not isinstance(value, dict):
                continue

            # 1) Un CLIENTE escribió.
            contactos = value.get("contacts") or []
            nombre = None
            if contactos and isinstance(contactos[0], dict):
                nombre = (contactos[0].get("profile") or {}).get("name")

            for msg in (value.get("messages") or []):
                ev = _mensaje(msg, nombre)
                if ev:
                    eventos.append(ev)

            # 2) LA DUEÑA escribió desde su celular (coexistencia).
            if campo == "smb_message_echoes":
                for eco in (value.get("message_echoes") or []):
                    ev = _eco(eco)
                    if ev:
                        eventos.append(ev)

            # 3) Qué pasó con un mensaje NUESTRO.
            for st in (value.get("statuses") or []):
                ev = _estado(st)
                if ev:
                    eventos.append(ev)
    return eventos


def _mensaje(msg: dict, nombre: str | None) -> MensajeEntrante | None:
    if not isinstance(msg, dict):
        return None
    message_id, telefono = msg.get("id"), msg.get("from")
    if not message_id or not telefono:
        return None  # evento mal formado: no se puede procesar de forma segura
    tipo = tipo_valido(msg.get("type"))
    media_id, caption, mime_type = _extraer_media(msg, msg.get("type") or "")
    return MensajeEntrante(
        clase="mensaje",
        message_id=message_id,
        telefono=telefono,
        nombre=nombre,
        # OJO: se devuelve el tipo CRUDO de Meta (no el normalizado) porque el router encola
        # por tipo ('image'/'document' = comprobante, 'audio' = nota de voz…). El normalizado
        # solo hace falta al GUARDAR en la BD.
        tipo=msg.get("type", "desconocido"),
        texto=_texto_de(msg, tipo),
        media_id=media_id,
        caption=caption,
        mime_type=mime_type,
        timestamp=msg.get("timestamp"),
    )


def _eco(eco: dict) -> EcoSaliente | None:
    if not isinstance(eco, dict):
        return None
    message_id = eco.get("id")
    # ⚠️ EL CLIENTE ES `to`. En el eco, `from` es el número DEL NEGOCIO: usarlo crearía un
    # "cliente" con el número propio y el bot terminaría respondiéndose a sí mismo.
    telefono = eco.get("to")
    if not message_id or not telefono:
        return None
    tipo = tipo_valido(eco.get("type"))
    media_id, caption, mime_type = _extraer_media(eco, eco.get("type") or "")
    return EcoSaliente(
        clase="eco",
        message_id=message_id,
        telefono=telefono,
        tipo=tipo,
        texto=_texto_de(eco, tipo),
        media_id=media_id,
        caption=caption,
        mime_type=mime_type,
        timestamp=eco.get("timestamp"),
    )


def _estado(st: dict) -> EstadoEnvio | None:
    if not isinstance(st, dict):
        return None
    wa_id = st.get("id")
    estado = _ESTADOS.get(st.get("status") or "")
    if not wa_id or not estado:
        return None
    error = None
    errores = st.get("errors") or []
    if errores and isinstance(errores[0], dict):
        e = errores[0]
        error = f"{e.get('code')}: {e.get('title')} — {e.get('message') or ''}".strip(" —")
    return EstadoEnvio(clase="estado", wa_message_id=wa_id, estado=estado, error=error)


def extraer_mensaje(payload: dict) -> MensajeEntrante | None:
    """Compatibilidad: el PRIMER mensaje de cliente del payload (None si no hay).
    El camino nuevo es `extraer_eventos`, que no pierde nada."""
    for ev in extraer_eventos(payload):
        if ev["clase"] == "mensaje":
            return ev  # type: ignore[return-value]
    return None
