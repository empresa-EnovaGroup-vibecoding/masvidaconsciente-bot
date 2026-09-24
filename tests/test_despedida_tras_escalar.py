"""🔇→🗣️ LA DESPEDIDA DEL BOT SALE AUNQUE ÉL MISMO SE HAYA PAUSADO (24-sep-2026, SESIONES (39)).

Lo que pasó en pruebas: Maired devolvió el chat al bot; `_retomar` leyó el expediente, decidió bien
(escalar con `pedir_ayuda`: ningún pedido tenía entrega acordada), se pausó a sí mismo, avisó por
WhatsApp… y su "ya te confirmo" al cliente NUNCA salió. `_enviar_en_partes` preguntaba
`_cliente_pausado` (¿está pausado?) en vez de `_lo_paso_una_persona` (¿lo pausó una PERSONA?), la
regla del 12-jul (migración 020) que distingue los dos casos opuestos:
  · la dueña tomó el chat → el bot se calla (si no, le habla encima);
  · el bot se pausó solo al escalar → su despedida SÍ tiene que salir (si no, silencio total).
La regresión entró el 22-sep con el código de Codex rescatado sin cambios (98be60fb, PR #59) y vivía
solo en pruebas (producción no tiene ese commit). Aquí queda fijada para siempre.
"""
import inspect
from unittest.mock import AsyncMock

import pytest

from app.agent import fuentes_atencion
from app.agent.contratos_atencion import MensajeConfirmado
from app.workers import tasks

TEL = "584240000000"


@pytest.fixture
def envio(monkeypatch):
    """Meta simulada: cada globo 'llega' con un id; sin pausas entre globos."""
    enviados: list[str] = []

    async def _enviar(telefono, texto):
        enviados.append(texto)
        return {"messages": [{"id": f"wamid.{len(enviados)}"}]}

    monkeypatch.setattr(tasks, "enviar_texto", _enviar)
    monkeypatch.setattr(tasks, "marcar_mensaje_propio", AsyncMock())
    monkeypatch.setattr(tasks.asyncio, "sleep", AsyncMock())
    return enviados


def _pausa(monkeypatch, pausado: bool, por: str | None):
    async def _estado(_tel):
        return pausado, por

    monkeypatch.setattr(tasks, "_estado_pausa", _estado)


async def test_el_bot_se_pauso_solo_y_su_despedida_sale(envio, monkeypatch):
    """El caso de la prueba de Maired: `pausado_por='bot'` (escaló) ⇒ el "te confirmo" SÍ llega."""
    _pausa(monkeypatch, True, "bot")
    partes = await tasks._enviar_en_partes(TEL, "Dame un momentito y te confirmo 😊")
    assert [p["estado"] for p in partes] == ["enviado"]
    assert envio == ["Dame un momentito y te confirmo 😊"]


async def test_con_varios_globos_salen_todos_aunque_el_bot_este_pausado_por_si_mismo(envio, monkeypatch):
    """El chequeo del bucle también preguntaba 'pausado a secas' y cortaba el globo 2 en adelante."""
    _pausa(monkeypatch, True, "bot")
    partes = await tasks._enviar_en_partes(TEL, "Ya lo anoté.\n\nEso te lo confirmo enseguida.")
    assert [p["estado"] for p in partes] == ["enviado", "enviado"]
    assert envio == ["Ya lo anoté.", "Eso te lo confirmo enseguida."]


async def test_si_la_duena_tomo_el_chat_el_bot_se_calla(envio, monkeypatch):
    """El lado opuesto no regresiona: una PERSONA pausó ⇒ ni un globo."""
    _pausa(monkeypatch, True, "dueña")
    assert await tasks._enviar_en_partes(TEL, "Hola! En qué te ayudo?") == []
    assert envio == []


async def test_sin_pausa_sale_normal(envio, monkeypatch):
    _pausa(monkeypatch, False, None)
    partes = await tasks._enviar_en_partes(TEL, "Hola! En qué te ayudo?")
    assert [p["estado"] for p in partes] == ["enviado"] and envio == ["Hola! En qué te ayudo?"]


async def test_el_acuse_del_cerebro_confirmado_sigue_pasando_por_tomar_acuse(envio, monkeypatch):
    """La rama del cerebro `confirmado` (MensajeConfirmado con relevo) no cambia: reserva UN acuse."""
    _pausa(monkeypatch, True, "bot")
    tomar = AsyncMock(return_value=True)
    monkeypatch.setattr(fuentes_atencion, "tomar_acuse", tomar)
    monkeypatch.setattr(fuentes_atencion, "fuentes_vigentes", AsyncMock(return_value=True))
    partes = await tasks._enviar_en_partes(TEL, MensajeConfirmado("Ya te confirmo", relevo=True))
    assert [p["estado"] for p in partes] == ["enviado"] and envio == ["Ya te confirmo"]
    tomar.assert_awaited_once_with(TEL)
    tomar.return_value = False
    assert await tasks._enviar_en_partes(TEL, MensajeConfirmado("Ya te confirmo", relevo=True)) == []


async def test_si_no_se_sabe_quien_pauso_el_bot_se_calla(envio, monkeypatch):
    """Lado seguro intacto (BD caída): sin saber quién pausó, no se envía."""

    async def _revienta(_tel):
        raise RuntimeError("sin base")

    monkeypatch.setattr(tasks, "_estado_pausa", _revienta)
    assert await tasks._enviar_en_partes(TEL, "Hola") == [] and envio == []


def test_el_embudo_de_envio_pregunta_quien_pauso_no_si_esta_pausado():
    """Fijado en la fuente (patrón R50): `_enviar_en_partes` no vuelve a preguntar `_cliente_pausado`."""
    fuente = inspect.getsource(tasks._enviar_en_partes)
    assert "_cliente_pausado(" not in fuente, "volvió la pregunta a secas: la despedida se traga otra vez"
    assert fuente.count("_lo_paso_una_persona(") >= 3, "las tres miradas al freno preguntan quién pausó"
