"""Valida la LÓGICA del agente (el loop de function calling) con simulaciones,
sin llamar a OpenRouter ni a la base de datos reales.
Ejecutar: PYTHONPATH=. .venv/Scripts/python.exe scripts/validar_agente.py
"""
import asyncio

from app.agent.agent import responder
from app.config import get_settings

settings = get_settings()


async def test_usa_tool_y_responde():
    """Cliente pregunta qué hay -> agente llama ver_catalogo -> responde con productos."""
    llamadas = {"n": 0}
    tools_usadas = []

    async def fake_llm(messages, tools, model):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            return {"choices": [{"message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "ver_catalogo", "arguments": "{}"}}],
            }}]}
        return {"choices": [{"message": {
            "role": "assistant",
            "content": "Hola! Tenemos *Pan Keto*, empanadas y más 😊 ¿Qué te provoca?",
        }}]}

    async def fake_ejecutar(nombre, args, telefono):
        tools_usadas.append(nombre)
        return {"productos": [{"nombre": "Pan Keto", "precio_usd": 25.0}]}

    r = await responder("584140000000", "hola, ¿qué tienen?", llm=fake_llm, ejecutar=fake_ejecutar)
    assert tools_usadas == ["ver_catalogo"], tools_usadas
    assert llamadas["n"] == 2, llamadas["n"]
    assert "Pan Keto" in r, r
    print("OK  recibe mensaje -> llama ver_catalogo -> responde con productos")


async def test_respuesta_directa_sin_tool():
    async def fake_llm(messages, tools, model):
        return {"choices": [{"message": {"role": "assistant",
                                         "content": "Soy la asistente de Whuilianny 😊"}}]}

    async def fake_ejecutar(nombre, args, telefono):
        raise AssertionError("no debería ejecutar ninguna tool")

    r = await responder("584140000000", "hola", llm=fake_llm, ejecutar=fake_ejecutar)
    assert "Whuilianny" in r, r
    print("OK  saludo simple -> responde directo, sin usar herramientas")


async def test_fallback_de_modelo():
    """Si el modelo principal falla, usa el de respaldo."""
    async def fake_llm(messages, tools, model):
        if model == settings.openrouter_model:
            raise RuntimeError("modelo principal caído")
        return {"choices": [{"message": {"role": "assistant", "content": "respondo desde el respaldo"}}]}

    async def fake_ejecutar(nombre, args, telefono):
        return {}

    r = await responder("584140000000", "hola", llm=fake_llm, ejecutar=fake_ejecutar)
    assert "respaldo" in r, r
    print("OK  si el modelo principal falla -> usa el de respaldo automáticamente")


async def main():
    await test_usa_tool_y_responde()
    await test_respuesta_directa_sin_tool()
    await test_fallback_de_modelo()
    print("\nTODO OK - la lógica del agente funciona (loop, herramientas y respaldo)")


if __name__ == "__main__":
    asyncio.run(main())
