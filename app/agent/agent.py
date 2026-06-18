"""Agente único con function calling sobre OpenRouter.

Recibe un mensaje del cliente + su historial, decide qué herramientas usar,
las ejecuta, y devuelve la respuesta final en la voz de Whuilianny.
"""
import base64
import json
import logging
import unicodedata

import httpx

from app.agent.system_prompt import construir_system_prompt
from app.agent.tools import TOOL_SCHEMAS, ejecutar_tool
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
RESPUESTA_SEGURA = "Dame un momentito y te confirmo 😊"


def _sin_acentos(texto: str) -> str:
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


# Frases con las que el agente AFIRMA haber enviado el catálogo (claim declarativo).
_AFIRMA_ENVIO_CATALOGO = (
    "acabo de envi", "acabo de mand",
    "ya te envi", "ya te mand", "ya te paso", "ya te lo",
    "te envie", "te mande", "te lo envie", "te lo mande",
    "aqui te dejo", "aqui tienes",
)


def _afirma_envio_catalogo(texto: str) -> bool:
    """True si el texto AFIRMA haber enviado el catálogo/menú (no si lo ofrece/pregunta)."""
    t = _sin_acentos(texto)
    if "catalogo" not in t and "menu" not in t:
        return False
    return any(frase in t for frase in _AFIRMA_ENVIO_CATALOGO)


async def _asegurar_catalogo(texto: str, catalogo_ok: bool, telefono: str, ejecutar) -> str:
    """Red de seguridad: si el bot DICE que envió el catálogo pero no llamó a la
    herramienta, lo enviamos de verdad (su afirmación se vuelve cierta y el cliente
    SÍ recibe el PDF, además en el orden correcto: PDF primero, texto después).
    Si no hay PDF, evitamos dejar una afirmación falsa."""
    if catalogo_ok or not _afirma_envio_catalogo(texto):
        return texto
    try:
        resultado = await ejecutar("enviar_catalogo", {}, telefono)
    except Exception:  # noqa: BLE001
        resultado = {"ok": False}
    if isinstance(resultado, dict) and resultado.get("ok"):
        return texto  # ahora SÍ se envió: la afirmación es verdad
    # No se pudo enviar (no hay PDF): no dejar una afirmación falsa.
    return "Déjame mostrarte lo que tenemos 😊 ¿Qué estás buscando?"


async def _llamar_openrouter(messages: list, tools: list, model: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            json={"model": model, "messages": messages, "tools": tools, "temperature": 0.15},
        )
        resp.raise_for_status()
        return resp.json()


async def _llamar_con_fallback(messages: list, llm) -> dict:
    try:
        return await llm(messages, TOOL_SCHEMAS, settings.openrouter_model)
    except Exception as e:  # noqa: BLE001
        logger.warning("Modelo principal falló (%s), usando fallback", e)
        return await llm(messages, TOOL_SCHEMAS, settings.openrouter_model_fallback)


async def responder(
    telefono: str,
    mensaje_usuario: str,
    historial: list | None = None,
    nombre_cliente: str | None = None,
    *,
    llm=_llamar_openrouter,
    ejecutar=ejecutar_tool,
) -> str:
    """Devuelve el texto de respuesta para enviar al cliente.

    `llm` y `ejecutar` son inyectables para poder testear el loop sin
    llamar a OpenRouter ni a la base de datos reales.
    """
    messages: list = [{"role": "system", "content": await construir_system_prompt(nombre_cliente)}]
    if historial:
        messages.extend(historial)
    messages.append({"role": "user", "content": mensaje_usuario})

    catalogo_ok = False
    for _ in range(settings.max_iteraciones_agente):
        data = await _llamar_con_fallback(messages, llm)
        msg = data["choices"][0]["message"]
        messages.append(msg)

        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            texto = (msg.get("content") or "").strip() or RESPUESTA_SEGURA
            return await _asegurar_catalogo(texto, catalogo_ok, telefono, ejecutar)

        for tc in tool_calls:
            nombre_tool = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            resultado = await ejecutar(nombre_tool, args, telefono)
            if nombre_tool == "enviar_catalogo" and isinstance(resultado, dict) and resultado.get("ok"):
                catalogo_ok = True
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(resultado, ensure_ascii=False),
                }
            )

    logger.warning("Agente excedió max iteraciones para %s", telefono)
    return RESPUESTA_SEGURA


async def redactar_mensaje(
    situacion: str, historial: list | None = None, nombre: str | None = None
) -> str:
    """Redacta un mensaje natural para el cliente en la voz de Whuilianny.

    NO es una plantilla: usa el contexto de la conversacion y algo de variacion
    para que cada mensaje salga distinto y humano. Se usa para momentos que
    dispara el sistema (comprobante recibido, pago confirmado/rechazado), donde
    no hay un texto del cliente que responder pero hay que decir algo con calidez.
    """
    messages: list = [{"role": "system", "content": await construir_system_prompt(nombre)}]
    if historial:
        messages.extend(historial)
    messages.append({
        "role": "user",
        "content": (
            f"[Instruccion interna, NO es un mensaje del cliente] Situacion: {situacion}. "
            "Escribe SOLO el mensaje de WhatsApp para el cliente, natural, breve y calido, "
            "en tu voz de siempre. No uses comillas ni expliques nada."
        ),
    })
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            json={"model": settings.openrouter_model, "messages": messages, "temperature": 0.7},
        )
        resp.raise_for_status()
        data = resp.json()
        return (data["choices"][0]["message"].get("content") or "").strip()


_FORMATOS_AUDIO = {
    "audio/ogg": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "m4a",
    "audio/aac": "aac",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
}


async def transcribir_audio(contenido: bytes, mime: str = "audio/ogg") -> str:
    """Transcribe una nota de voz a texto con el modelo multimodal de OpenRouter.

    WhatsApp manda las notas de voz como audio/ogg (codec opus), que Gemini
    acepta directo. Si el modelo no logra transcribir, devuelve cadena vacia
    y el llamador responde con gracia (nunca tumba al bot).
    """
    base_mime = (mime or "").split(";")[0].strip().lower()
    formato = _FORMATOS_AUDIO.get(base_mime, "ogg")
    b64 = base64.b64encode(contenido).decode("ascii")
    messages = [
        {
            "role": "system",
            "content": (
                "Transcribe a texto, en espanol, exactamente lo que dice el cliente "
                "en el audio. Devuelve solo la transcripcion, sin comentarios."
            ),
        },
        {
            "role": "user",
            "content": [
                {"type": "input_audio", "input_audio": {"data": b64, "format": formato}},
            ],
        },
    ]
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            json={"model": settings.openrouter_model, "messages": messages, "temperature": 0},
        )
        resp.raise_for_status()
        data = resp.json()
        return (data["choices"][0]["message"].get("content") or "").strip()
