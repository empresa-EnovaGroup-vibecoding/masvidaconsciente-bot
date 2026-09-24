"""🔇 RETOMAR NO REABRE VENTAS CERRADAS (13-sep-2026, el caso de Amanda — SESIONES (30)).

Lo que pasó: la dueña cerró una venta A MANO por su WhatsApp (dio el total, $64; recibió el
comprobante; "Dios te multiplique"). La clienta contestó "Amén". A la mañana siguiente Maired
devolvió varios chats al bot de golpe; `_retomar` vio que el último turno era del cliente y lo tomó
como PENDIENTE: reabrió la venta, registró OTRO producto por OTRO total ($62) y le pidió pagar de
nuevo a quien ya había pagado. La clienta: *"Yo creo que estás confundida de persona"*.

Tres candados, en código:
  1. `_hay_pendiente`: un acuse de recibo no es una pregunta. Solo se retoma si el bot había
     escalado, si la casa terminó preguntando, o si el cliente pide/pregunta algo.
  2. El lote "Seleccionar chats para devolver" despausa EN SILENCIO: no dispara retomar. El botón
     individual sí (la dueña acaba de leer ese chat: su clic es la aprobación humana de Meta).
  3. La instrucción de retomar prohíbe registrar/cobrar lo que la casa ya cerró a mano, y dice
     Alejandra (no "asistente virtual", que contradecía R130).
Y el umbral de saldo sube a $5: con $1.59 el 402 ya llegó y el bot cayó al respaldo una mañana entera.
"""
from __future__ import annotations

import inspect

import pytest

from app.api import router
from app.services.salud import UMBRAL_SALDO_USD
from app.workers import tasks
from app.workers.tasks import _INSTRUCCION_RETOMAR, _es_acuse, _hay_pendiente

# ══════════════════════════════════════════════════════════════════════════════════
#  1. QUÉ ES UN ACUSE Y QUÉ ES PENDIENTE
# ══════════════════════════════════════════════════════════════════════════════════

AMANDA = [
    {"role": "user", "content": "Si dime cuanto es para hacerte el pago movil"},
    {"role": "assistant", "content": "4 caldos de carne 8X4=32\n2 paquetes de galletas 12x2=24\n2 delivery 6\nTotal 🟰 64$."},
    {"role": "user", "content": "(comprobante)"},
    {"role": "assistant", "content": "Dios te multiplique cariño"},
    {"role": "user", "content": "Amén"},
]


@pytest.mark.parametrize("texto", [
    "Amén", "ok", "Ok esta bien", "Gracias mi reina!", "Listo", "Dale", "👍", "🙏", "",
    "(comprobante)", "(sticker)", "Si mi cielo", "Bueno gracias", "Buenas noches",
])
def test_estos_son_acuses(texto):
    assert _es_acuse(texto), f"{texto!r} debería ser un acuse"


@pytest.mark.parametrize("texto", [
    "¿Cuánto sería en Bs?", "y el envío?", "No entiendo", "Yo creo que estas confundida de persona",
    "quiero unas empanadas", "Para el lunes puede ser", "Yo no vivo en Bqto centro",
    "Ok pero cuanto es", "mándame el número de cuenta",
])
def test_estos_piden_algo(texto):
    assert not _es_acuse(texto), f"{texto!r} NO es un acuse: pide o pregunta"


def test_el_caso_de_amanda_no_es_pendiente():
    """La dueña dio el total, recibió el comprobante, y la clienta dijo Amén. Nada que retomar."""
    assert _hay_pendiente(AMANDA, None) is False


def test_una_pregunta_al_final_si_es_pendiente():
    """El caso estrella no regresiona: el cliente preguntó y sigue esperando."""
    hist = AMANDA[:-1] + [{"role": "user", "content": "¿cuánto sería en Bs?"}]
    assert _hay_pendiente(hist, None) is True


def test_si_la_casa_termino_preguntando_la_respuesta_corta_es_pendiente():
    """'¿Centro u oeste?' → 'Más al centro': dos palabras, pero es la respuesta que esperábamos."""
    hist = [
        {"role": "assistant", "content": "Dime si te queda en Barquisimeto centro o oeste?"},
        {"role": "user", "content": "Más al centro"},
    ]
    assert _hay_pendiente(hist, None) is True


def test_el_bloque_final_se_lee_entero():
    assert _hay_pendiente([{"role": "assistant", "content": "Listo, gracias"},
                           {"role": "user", "content": "ok"},
                           {"role": "user", "content": "gracias"}], None) is False
    assert _hay_pendiente([{"role": "assistant", "content": "Listo, gracias"},
                           {"role": "user", "content": "ok"},
                           {"role": "user", "content": "y el envío?"}], None) is True


def test_si_el_bot_escalo_siempre_hay_pendiente():
    """Su 'te lo confirmo enseguida' es un pagaré: se paga aunque el cliente solo haya dicho ok."""
    hist = [{"role": "user", "content": "cuánto vale la torta de 1kg?"},
            {"role": "assistant", "content": "Te confirmo ese precio enseguida 💚"},
            {"role": "user", "content": "ok"}]
    assert _hay_pendiente(hist, "bot") is True


def test_si_la_casa_hablo_ultima_no_hay_pendiente():
    assert _hay_pendiente([{"role": "user", "content": "hola"},
                           {"role": "assistant", "content": "Te lo dejo en 240 bolívares"}], None) is False
    assert _hay_pendiente([], None) is False


# ══════════════════════════════════════════════════════════════════════════════════
#  2. `_retomar` DE PUNTA A PUNTA CON DOBLES: AMANDA NO RECIBE NADA; LA PREGUNTA SÍ
# ══════════════════════════════════════════════════════════════════════════════════

async def _correr_retomar(monkeypatch, historial, pausado_por=None):
    llamadas: list[str] = []

    async def _true(*_a, **_k):
        return True

    async def _false(*_a, **_k):
        return False

    async def _nada(*_a, **_k):
        return None

    async def _historial(_tel):
        return historial

    async def _pensar(telefono, instruccion, hist, nombre, **kw):
        llamadas.append(instruccion)
        return [{"ok": True, "texto": "respuesta"}], "respuesta"

    monkeypatch.setattr(tasks.rc, "adquirir_lock", _true)
    monkeypatch.setattr(tasks.rc, "candado_retomar", _true)
    monkeypatch.setattr(tasks.rc, "liberar_lock", _nada)
    monkeypatch.setattr(tasks.rc, "guardar_historial", _nada)
    monkeypatch.setattr(tasks.rc, "obtener_historial", _historial)
    monkeypatch.setattr(tasks, "_bot_activo", _true)
    monkeypatch.setattr(tasks, "_numero_permitido", _true)
    monkeypatch.setattr(tasks, "_cliente_pausado", _false)
    monkeypatch.setattr(tasks, "_ventana_abierta", _true)
    monkeypatch.setattr(tasks, "_guardar_en_panel", _nada)
    monkeypatch.setattr(tasks, "_pensar_y_enviar", _pensar)

    async def _sin_expediente(_tel):  # PR4: sin base no hay expediente; el retomar sigue como antes
        return {"pedidos_a_mano": [], "propuestas_pendientes": 0, "venta_cerrada_a_mano": False, "hechos": ""}

    monkeypatch.setattr(tasks, "leer_expediente", _sin_expediente)
    await tasks._retomar("584125198777", "A", pausado_por)
    return llamadas


@pytest.mark.asyncio
async def test_retomar_se_calla_con_amanda(monkeypatch):
    assert await _correr_retomar(monkeypatch, AMANDA) == []


@pytest.mark.asyncio
async def test_retomar_si_contesta_una_pregunta_pendiente(monkeypatch):
    hist = AMANDA[:-1] + [{"role": "user", "content": "¿cuánto sería en Bs?"}]
    llamadas = await _correr_retomar(monkeypatch, hist)
    assert len(llamadas) == 1 and llamadas[0] == _INSTRUCCION_RETOMAR


@pytest.mark.asyncio
async def test_retomar_paga_el_pagare_si_habia_escalado(monkeypatch):
    hist = [{"role": "user", "content": "cuánto vale la torta?"},
            {"role": "assistant", "content": "Te confirmo ese precio enseguida 💚"},
            {"role": "user", "content": "ok"}]
    llamadas = await _correr_retomar(monkeypatch, hist, pausado_por="bot")
    assert len(llamadas) == 1 and llamadas[0] == tasks._INSTRUCCION_RETOMAR_ESCALADO


# ══════════════════════════════════════════════════════════════════════════════════
#  3. EL LOTE ES SILENCIOSO; LA INSTRUCCIÓN NO COBRA LO CERRADO; EL UMBRAL AVISA A TIEMPO
# ══════════════════════════════════════════════════════════════════════════════════

def test_el_lote_no_dispara_retomar_pero_el_boton_individual_si():
    lote = inspect.getsource(router.devolver_clientes_al_bot)
    assert "_disparar_retomar(" not in lote, "el lote volvió a despertar al bot sobre chats no releídos"
    individual = inspect.getsource(router.pausar_bot_cliente)
    assert "_disparar_retomar(" in individual, "el botón individual dejó de retomar (caso estrella)"


def test_la_instruccion_de_retomar_no_cobra_lo_que_la_casa_cerro():
    assert "NO registres pedido" in _INSTRUCCION_RETOMAR
    assert "NO vuelvas a cobrar" in _INSTRUCCION_RETOMAR
    assert "asistente virtual" not in _INSTRUCCION_RETOMAR, "contradice R130: es Alejandra, la asesora"
    assert "Alejandra, la asesora" in _INSTRUCCION_RETOMAR


def test_el_umbral_de_saldo_avisa_antes_del_402():
    assert UMBRAL_SALDO_USD >= 5.0
