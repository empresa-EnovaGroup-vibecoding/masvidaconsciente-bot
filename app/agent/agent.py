"""Agente único con function calling sobre OpenRouter.

Recibe un mensaje del cliente + su historial, decide qué herramientas usar,
las ejecuta, y devuelve la respuesta final en la voz de Whuilianny.
"""
import base64
import json
import logging
import unicodedata

import httpx

from app.agent.system_prompt import construir_system_prompt, leer_modelo_ia
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


async def _llamar_con_fallback(messages: list, llm, modelo: str) -> dict:
    try:
        return await llm(messages, TOOL_SCHEMAS, modelo)
    except Exception as e:  # noqa: BLE001
        logger.warning("Modelo principal (%s) falló (%s), usando fallback", modelo, e)
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
    messages: list = [
        {"role": "system", "content": await construir_system_prompt(nombre_cliente, telefono)}
    ]
    if historial:
        messages.extend(historial)
    messages.append({"role": "user", "content": mensaje_usuario})

    modelo = await leer_modelo_ia()  # el que eligió la proveedora en el panel
    catalogo_ok = False
    for _ in range(settings.max_iteraciones_agente):
        data = await _llamar_con_fallback(messages, llm, modelo)
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
    modelo = await leer_modelo_ia()  # mismo modelo elegido para conversar
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            json={"model": modelo, "messages": messages, "temperature": 0.7},
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
            json={"model": settings.openrouter_model_audio, "messages": messages, "temperature": 0},
        )
        resp.raise_for_status()
        data = resp.json()
        return (data["choices"][0]["message"].get("content") or "").strip()


# ─── Visión: reconocer comprobantes de pago ──────────────────────────
_FORMATOS_IMAGEN = {
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/png": "image/png",
    "image/webp": "image/webp",
    "image/heic": "image/heic",  # iPhone
    "image/heif": "image/heif",  # iPhone
}


def _parsear_json_comprobante(texto: str) -> dict | None:
    """Extrae el JSON de la respuesta del modelo (tolera ```json ... ``` y texto
    alrededor). Devuelve None si no se puede parsear (→ el llamador lo trata como
    'no se pudo leer')."""
    t = (texto or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t[:4].lower() == "json":
            t = t[4:]
    i, j = t.find("{"), t.rfind("}")
    if i != -1 and j != -1 and j > i:
        t = t[i : j + 1]
    try:
        d = json.loads(t)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    # Normaliza es_comprobante a True / False / None (el modelo a veces lo manda
    # como texto "true"/"false").
    v = d.get("es_comprobante")
    if isinstance(v, bool):
        pass
    elif isinstance(v, str):
        vl = v.strip().lower()
        d["es_comprobante"] = (
            True if vl in ("true", "si", "sí", "yes", "1")
            else False if vl in ("false", "no", "0")
            else None
        )
    else:
        d["es_comprobante"] = None
    return d


async def leer_comprobante(
    contenido: bytes,
    mime: str,
    *,
    titular: str | None = None,
    telefono_pago: str | None = None,
    banco: str | None = None,
) -> dict:
    """Lee una imagen con visión (Gemini) y dice si es un comprobante de pago REAL.
    Devuelve {es_comprobante, monto, referencia, destinatario, banco, confianza, leido}.

    'leido' indica si la visión SÍ pudo analizar la imagen:
      - leido=False -> no se pudo (no es imagen/PDF, visión caída, respuesta
        ilegible): el llamador cae al flujo MANUAL (registrar 'reportado', red de seguridad).
      - leido=True  -> la visión analizó: el llamador es ESTRICTO (solo es pago si
        es_comprobante True con monto). Ignora fotos de personas/productos, stickers,
        capturas de chats/apps/redes. NUNCA lanza.
    """
    base_mime = (mime or "").split(";")[0].strip().lower()
    fmt = _FORMATOS_IMAGEN.get(base_mime)
    if fmt is None:
        return {"es_comprobante": None, "leido": False}  # PDF u otro: a manual
    b64 = base64.b64encode(contenido).decode("ascii")
    data_url = f"data:{fmt};base64,{b64}"
    cuenta = ", ".join(x for x in (titular, telefono_pago, banco) if x) or "(no configurada)"
    instruccion = (
        "Eres un verificador ESTRICTO de comprobantes de pago de Venezuela (Pago Móvil, "
        "transferencia bancaria, pago móvil interbancario). Mira la imagen y responde SOLO "
        "con un JSON válido, sin ningún texto extra, con EXACTAMENTE estas llaves:\n"
        '{"es_comprobante": true o false, "monto": "<monto en bolívares, solo números, '
        'ej 39480.47, o null>", "referencia": "<número de referencia/operación, o null>", '
        '"destinatario": "<a quién/qué cuenta se pagó, o null>", "banco": "<banco o '
        'plataforma, o null>", "confianza": "alta" o "media" o "baja"}\n\n'
        f"La cuenta de la dueña (a quién deben pagarle) es: {cuenta}.\n"
        "Pon es_comprobante=true SOLO si la imagen es la PANTALLA de un banco o billetera "
        "que muestra una TRANSFERENCIA o PAGO YA REALIZADO, con un MONTO en bolívares y un "
        "NÚMERO DE REFERENCIA/OPERACIÓN visibles. Si no logras leer un monto, es_comprobante=false.\n"
        "Pon es_comprobante=false para CUALQUIER otra imagen: foto de una persona, producto, "
        "comida, paisaje, meme, logo, sticker, captura de un chat de WhatsApp, captura de una "
        "app, red social o tutorial, texto suelto, o cualquier imagen que NO sea la pantalla de "
        "una transacción bancaria. Ante la duda, es_comprobante=false."
    )
    messages = [
        {"role": "system", "content": instruccion},
        {
            "role": "user",
            "content": [{"type": "image_url", "image_url": {"url": data_url}}],
        },
    ]
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
                json={"model": settings.openrouter_model_audio, "messages": messages, "temperature": 0},
            )
            resp.raise_for_status()
            data = resp.json()
            texto = (data["choices"][0]["message"].get("content") or "").strip()
    except Exception:  # noqa: BLE001 — leer el comprobante nunca debe tumbar el worker
        logger.exception("No se pudo leer el comprobante con visión")
        return {"es_comprobante": None, "leido": False}
    parsed = _parsear_json_comprobante(texto)
    if parsed is None:
        return {"es_comprobante": None, "leido": False}
    parsed["leido"] = True
    return parsed
