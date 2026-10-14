"""🌡️🛟 SONNET 5.5 SIN ROMPER NADA (14-oct, SESIONES (51)).

Del 9 al 14-oct pruebas tuvo `modelo_ia=anthropic/claude-sonnet-5.5`. El bot mandaba `temperature: 0.15`
con `provider.require_parameters`, Sonnet 5.5 no la acepta, OpenRouter respondía 404, y el bot contestaba en
silencio con el respaldo de la variable (GPT-4.1). La sonda del 14-oct confirmó que Sonnet 5.5 funciona y
cachea si no se le manda temperatura. Lo que fijan estos tests:
  · la temperatura se omite SOLO en los modelos de Claude que no la aceptan; los demás la reciben igual;
  · si el modelo de Alejandra falla, el primer respaldo es OTRO Claude, y GPT-4.1 queda de último.
"""
import pytest

from app.agent import agent


@pytest.mark.parametrize("modelo", [
    "anthropic/claude-sonnet-5.5", "anthropic/claude-sonnet-5", "anthropic/claude-opus-5.5",
    "anthropic/claude-opus-4.8", "anthropic/claude-opus-4.7", "anthropic/claude-haiku-5.5",
    "anthropic/claude-fable-5.1",
])
def test_los_claude_nuevos_no_reciben_temperatura(modelo):
    assert "temperature" not in agent._con_temperatura({"model": modelo}, 0.15)


@pytest.mark.parametrize("modelo", [
    "anthropic/claude-sonnet-4.6", "anthropic/claude-haiku-4.5", "anthropic/claude-opus-4.6",
    "openai/gpt-4.1", "google/gemini-2.5-flash",
])
def test_los_demas_la_reciben_como_siempre(modelo):
    assert agent._con_temperatura({"model": modelo}, 0.15)["temperature"] == 0.15


def test_el_respaldo_es_otro_claude_y_gpt_queda_de_ultimo(monkeypatch):
    monkeypatch.setattr(agent.settings, "openrouter_model_fallback", "openai/gpt-4.1")
    assert agent._respaldos("anthropic/claude-sonnet-5.5") == ["anthropic/claude-sonnet-4.6", "openai/gpt-4.1"]
    assert agent._respaldos("anthropic/claude-sonnet-4.6") == ["anthropic/claude-sonnet-5.5", "openai/gpt-4.1"]
    # Nunca se repite el que acaba de fallar.
    monkeypatch.setattr(agent.settings, "openrouter_model_fallback", "anthropic/claude-sonnet-4.6")
    assert agent._respaldos("anthropic/claude-sonnet-5.5") == ["anthropic/claude-sonnet-4.6"]


async def test_si_falla_el_principal_contesta_el_otro_claude(monkeypatch):
    monkeypatch.setattr(agent.settings, "openrouter_model_fallback", "openai/gpt-4.1")
    monkeypatch.setattr(agent, "_anotar_si_es_falta_de_saldo", _nada)
    llamados = []

    async def llm(_messages, _tools, modelo):
        llamados.append(modelo)
        if modelo == "anthropic/claude-sonnet-5.5":
            raise RuntimeError("404")
        return {"modelo": modelo}

    r = await agent._llamar_con_fallback([], llm, "anthropic/claude-sonnet-5.5", [])
    assert r == {"modelo": "anthropic/claude-sonnet-4.6"}
    assert llamados == ["anthropic/claude-sonnet-5.5", "anthropic/claude-sonnet-4.6"]  # GPT ni se toca


async def test_si_fallan_todos_sube_el_error_del_ultimo(monkeypatch):
    monkeypatch.setattr(agent.settings, "openrouter_model_fallback", "openai/gpt-4.1")
    monkeypatch.setattr(agent, "_anotar_si_es_falta_de_saldo", _nada)

    async def llm(_messages, _tools, modelo):
        raise RuntimeError(f"cayó {modelo}")

    with pytest.raises(RuntimeError, match="cayó openai/gpt-4.1"):
        await agent._llamar_con_fallback([], llm, "anthropic/claude-sonnet-5.5", [])


async def _nada(*_a, **_k):
    return None
