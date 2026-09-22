"""El modo confirmado cierra los hechos del pago sin cambiar los modos existentes."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent import agent
from app.agent.eventos_atencion import redactar_evento
from app.api import router as api
from app.workers import tasks


@pytest.mark.parametrize("modo", ["uno", "dos"])
async def test_modos_existentes_conservan_el_redactor_natural(monkeypatch, modo):
    natural = AsyncMock(return_value="mensaje natural")
    cerrado = AsyncMock(side_effect=AssertionError("no corresponde"))
    monkeypatch.setattr(agent, "leer_config_agente", AsyncMock(return_value=(modo, "op", "voz")))
    monkeypatch.setattr(agent, "redactar_mensaje", natural)
    monkeypatch.setattr(agent, "redactar_evento_confirmado", cerrado)

    r = await agent.redactar_pago(
        "situación", [{"role": "user", "content": "hola"}], "Ana", "5842",
        montos_usd={16.0}, montos_bs=set(), evento={"tipo": "captura"},
    )

    assert r == "mensaje natural"
    natural.assert_awaited_once_with(
        "situación", [{"role": "user", "content": "hola"}], "Ana", "5842",
        montos_usd={16.0}, montos_bs=set(),
    )
    cerrado.assert_not_awaited()


async def test_confirmado_usa_el_evento_y_no_la_situacion_libre(monkeypatch):
    natural = AsyncMock(side_effect=AssertionError("no corresponde"))
    cerrado = AsyncMock(return_value="mensaje comprobado")
    monkeypatch.setattr(
        agent, "leer_config_agente", AsyncMock(return_value=("confirmado", "op", "voz"))
    )
    monkeypatch.setattr(agent, "redactar_mensaje", natural)
    monkeypatch.setattr(agent, "redactar_evento_confirmado", cerrado)
    evento = {"tipo": "confirmado", "pago_id": 8}

    assert await agent.redactar_pago(
        "texto que no autoriza el estado", [], None, "5842",
        montos_usd=set(), montos_bs=set(), evento=evento,
    ) == "mensaje comprobado"
    cerrado.assert_awaited_once_with(
        evento, [], None, "5842", montos_usd=set(), montos_bs=set()
    )
    natural.assert_not_awaited()


async def test_confirmado_sin_evento_se_bloquea(monkeypatch, caplog):
    natural = AsyncMock(side_effect=AssertionError("no corresponde"))
    monkeypatch.setattr(
        agent, "leer_config_agente", AsyncMock(return_value=("confirmado", "op", "voz"))
    )
    monkeypatch.setattr(agent, "redactar_mensaje", natural)

    assert await agent.redactar_pago("situación", telefono="5842") == ""
    assert "respuesta bloqueada" in caplog.text
    natural.assert_not_awaited()


async def test_sobrepago_sale_de_la_fila_y_deja_saldo_a_favor():
    leer = AsyncMock(return_value={
        "estado": "confirmado", "monto_bs": None, "monto_usd": Decimal("16"),
        "monto_recibido": Decimal("20"),
    })
    r = await redactar_evento(
        {"tipo": "confirmado", "pago_id": 3}, "5842", leer=leer
    )
    assert "$4" in r and "saldo a favor" in r


async def test_evento_parcial_calcula_el_faltante_desde_la_fila():
    leer = AsyncMock(return_value={
        "estado": "parcial", "monto_bs": Decimal("10000"), "monto_usd": None,
        "monto_recibido": Decimal("8000"),
    })
    r = await redactar_evento({"tipo": "parcial", "pago_id": 3}, "5842", leer=leer)
    assert "Bs 8.000,00" in r
    assert "Bs 10.000,00" in r
    assert "Bs 2.000,00" in r


async def test_evento_no_acepta_montos_escritos_por_quien_llama():
    assert await redactar_evento(
        {"tipo": "confirmado", "pago_id": 3, "sobrante": 99}, "5842"
    ) == ""


@pytest.mark.parametrize("evento,fila", [
    (
        {"tipo": "confirmado", "pago_id": 3},
        {"estado": "confirmado", "monto_bs": None, "monto_usd": Decimal("16"),
         "monto_recibido": Decimal("16")},
    ),
    (
        {"tipo": "rechazado", "pago_id": 3},
        {"estado": "rechazado", "monto_bs": None, "monto_usd": Decimal("16"),
         "monto_recibido": None},
    ),
    (
        {"tipo": "parcial", "pago_id": 3},
        {"estado": "parcial", "monto_bs": None, "monto_usd": Decimal("16"),
         "monto_recibido": Decimal("8")},
    ),
])
async def test_mensajes_cerrados_de_pago_pasan_la_proteccion_existente(evento, fila):
    texto = await redactar_evento(evento, "5842", leer=AsyncMock(return_value=fila))

    assert texto
    assert tasks._proteger_afirmacion_de_pago(texto) == texto


def test_evento_del_comprobante_distingue_los_tres_casos():
    assert tasks._evento_comprobante({"ok": True, "pago_id": 4}, True) == {
        "tipo": "revision", "pago_id": 4,
    }
    assert tasks._evento_comprobante({"ok": True, "pago_id": 4}, False) == {
        "tipo": "revision_importe", "pago_id": 4,
    }
    assert tasks._evento_comprobante({"error": "sin pedido"}, False) == {
        "tipo": "sin_pedido",
    }


async def test_bd_caida_el_bot_calla(monkeypatch):
    async def falla(_telefono):
        raise RuntimeError("bd caída")

    enviar = AsyncMock(side_effect=AssertionError("no debe enviar"))
    monkeypatch.setattr(tasks, "_estado_pausa", falla)
    monkeypatch.setattr(tasks, "enviar_texto", enviar)

    assert await tasks._cliente_pausado("5842") is True
    assert await tasks._enviar_en_partes("5842", "hola") == []
    enviar.assert_not_awaited()


# ══ Los 3 botones del panel mandan el EVENTO junto a la situación natural ══
#
# `redactar_pago` solo puede elegir el evento tipado si quien encola el aviso lo manda. Estos tests
# fijan que aprobar, rechazar y verificar monto mandan las DOS cosas: la situación natural de
# siempre (para `uno`/`dos`) y el evento `{"tipo", "pago_id"}` (para `confirmado`). Si mañana
# alguien quita el tercer elemento "porque no se usa", el modo confirmado quedaría mudo en los
# pagos (bloqueado a propósito por `test_confirmado_sin_evento_se_bloquea`) y nada gritaría.

TEL = "584240000000"


class _Res:
    def __init__(self, escalar=None, rowcount=1):
        self._escalar = escalar
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return self._escalar


class _Sesion:
    """Sirve pago/pedido por `get` y los resultados de `execute` EN ORDEN."""

    def __init__(self, pago, pedido, resultados):
        self._pago, self._pedido, self._resultados = pago, pedido, list(resultados)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, modelo, pk):
        return self._pago if modelo.__name__ == "Pago" else self._pedido

    async def execute(self, _q):
        return self._resultados.pop(0)

    async def commit(self):
        return None


def _panel(monkeypatch, pago, pedido, resultados):
    """Monta la sesión de mentira, las guías de la dueña y captura lo que se encola."""
    import app.services.mensajes as mensajes

    monkeypatch.setattr(api, "get_session_factory", lambda: (lambda: _Sesion(pago, pedido, resultados)))
    monkeypatch.setattr(mensajes, "leer_guia", AsyncMock(return_value="guía de la dueña"))
    monkeypatch.setattr(mensajes, "contexto_entrega", AsyncMock(return_value=" · retiro en La Mendera"))
    encolado = []
    monkeypatch.setattr(api, "_encolar_notificacion", lambda tarea, args: encolado.append(args) or True)
    return encolado


def _pago(estado="reportado", monto_usd=Decimal("16")):
    return SimpleNamespace(id=7, estado=estado, pedido_id=50, monto_bs=None, monto_usd=monto_usd,
                           monto_recibido=None, referencia="REF123")


def _pedido(estado="esperando_pago"):
    return SimpleNamespace(id=50, estado=estado, cliente_telefono=TEL)


async def test_aprobar_manda_la_situacion_natural_y_el_evento(monkeypatch):
    encolado = _panel(monkeypatch, _pago(), _pedido(), [_Res(escalar=None), _Res(rowcount=1)])
    r = await api.confirmar_pago(7, usuario="prueba@masvida.local")
    assert r["ok"] and r["notificacion_encolada"] is True
    telefono, situacion, evento = encolado[0]
    assert telefono == TEL
    assert "guía de la dueña" in situacion and "La Mendera" in situacion  # lo de siempre, intacto
    assert evento == {"tipo": "confirmado", "pago_id": 7}


async def test_rechazar_manda_la_situacion_natural_y_el_evento(monkeypatch):
    encolado = _panel(monkeypatch, _pago(), _pedido(), [_Res(rowcount=1), _Res(escalar=None)])
    await api.rechazar_pago(7, datos=None, usuario="prueba@masvida.local")
    telefono, situacion, evento = encolado[0]
    assert telefono == TEL and situacion == "guía de la dueña"
    assert evento == {"tipo": "rechazado", "pago_id": 7}


@pytest.mark.parametrize("recibido,tipo,palabra", [
    (20.0, "confirmado", "saldo a favor"),   # pagó de más: la regla de Maired sigue en la situación
    (8.0, "parcial", "faltan"),
])
async def test_verificar_monto_manda_el_evento_segun_lo_recibido(monkeypatch, recibido, tipo, palabra):
    encolado = _panel(monkeypatch, _pago(), _pedido(), [_Res(escalar=None), _Res(rowcount=1)])
    r = await api.verificar_monto(7, api.MontoIn(monto_recibido=recibido), usuario="prueba@masvida.local")
    assert r["estado"] == tipo
    telefono, situacion, evento = encolado[0]
    assert telefono == TEL and palabra in situacion
    assert evento == {"tipo": tipo, "pago_id": 7}
