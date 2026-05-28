"""Agente único con function calling sobre OpenRouter.

Recibe un mensaje del cliente + su historial, decide qué herramientas usar,
las ejecuta, y devuelve la respuesta final en la voz de Whuilianny.
"""
import json
import logging

import httpx

from app.agent.system_prompt import construir_system_prompt
from app.agent.tools import TOOL_SCHEMAS, ejecutar_tool
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
RESPUESTA_SEGURA = "Dame un momentito y te confirmo 😊"


async def _llamar_openrouter(messages: list, tools: list, model: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            json={"model": model, "messages": messages, "tools": tools, "temperature": 0.3},
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
    messages: list = [{"role": "system", "content": construir_system_prompt(nombre_cliente)}]
    if historial:
        messages.extend(historial)
    messages.append({"role": "user", "content": mensaje_usuario})

    for _ in range(settings.max_iteraciones_agente):
        data = await _llamar_con_fallback(messages, llm)
        msg = data["choices"][0]["message"]
        messages.append(msg)

        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            return (msg.get("content") or "").strip() or RESPUESTA_SEGURA

        for tc in tool_calls:
            nombre_tool = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            resultado = await ejecutar(nombre_tool, args, telefono)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(resultado, ensure_ascii=False),
                }
            )

    logger.warning("Agente excedió max iteraciones para %s", telefono)
    return RESPUESTA_SEGURA
