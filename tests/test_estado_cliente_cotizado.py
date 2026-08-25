"""El pedido cotizado ENSEÑA su cifra en bolívares en ESTADO DEL CLIENTE.

🔴 EL PORQUÉ (prueba en vivo de Maired, 24-ago-2026): con el cobro ya presentado, la clienta
preguntó "Cuanto seria en bolívares?" y el bot contestó "Dame un momentito y te confirmo 😊" —
la promesa vacía que _REGLAS prohíbe. El monto exacto YA estaba guardado en el pedido
(`cotizado_bs`, migración 027); el modelo simplemente no volvió a llamar a generar_datos_pago
para releerlo — los resultados de las herramientas no viven en el historial, así que a los dos
turnos la cifra desaparece de su vista. Estos tests fijan la corrección: la cifra viaja en el
bloque ESTADO DEL CLIENTE, lista para COPIAR, y solo cuando de verdad existe.

⚠️ Solo bolívares, a propósito: un monto en USD inyectado sin herramienta chocaría con la red
del TOTAL de `_dinero_inventado` (que vigila únicamente dólares). Ver el comentario en
`_estado_cliente_texto`."""
from decimal import Decimal
from types import SimpleNamespace

import app.agent.system_prompt as sp


class _Resultado:
    def __init__(self, filas):
        self._filas = filas

    def scalars(self):
        return self

    def all(self):
        return self._filas


class _Sesion:
    """Sesión de mentira: devuelve las filas que le den, sin base de datos real."""

    def __init__(self, filas):
        self._filas = filas

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def execute(self, *_a, **_k):
        return _Resultado(self._filas)


def _con_pedidos(monkeypatch, pedidos):
    """get_session_factory() devuelve una fábrica; la fábrica() devuelve la sesión."""
    monkeypatch.setattr(sp, "get_session_factory", lambda: (lambda: _Sesion(pedidos)))


def _pedido(**kw):
    base = dict(
        id=1918,
        estado="esperando_pago",
        cotizado_bs=None,
        cotizado_usd=None,
        cotizado_usd_divisas=None,
        tasa_cotizada=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


async def test_cotizado_ensena_los_bolivares(monkeypatch):
    """Esperando pago y YA cotizado: la cifra en Bs va en el bloque, al estilo Venezuela."""
    _con_pedidos(monkeypatch, [_pedido(cotizado_bs=Decimal("7846.63"))])
    texto = await sp._estado_cliente_texto("584140000000")
    assert "7.846,63 Bs" in texto
    assert "YA ESTÁ COTIZADO" in texto
    assert "ESPERANDO PAGO" in texto  # lo de siempre sigue intacto
    assert "pedido_id=1918" in texto


async def test_sin_cotizar_no_inventa_cifra(monkeypatch):
    """Esperando pago pero SIN cotización guardada (pedido viejo): ni cifra ni línea nueva.
    La línea solo existe cuando el dato existe — nunca se inventa un monto."""
    _con_pedidos(monkeypatch, [_pedido()])
    texto = await sp._estado_cliente_texto("584140000000")
    assert "COTIZADO" not in texto
    assert " Bs" not in texto
    assert "ESPERANDO PAGO" in texto


async def test_pendiente_queda_como_siempre(monkeypatch):
    """Un pedido apenas ARMADO no se cotizó en generar_datos_pago: el bloque queda idéntico
    al de hoy, aunque el pedido traiga un cotizado_bs viejo de otra vida (estado manda)."""
    _con_pedidos(monkeypatch, [_pedido(estado="pendiente", cotizado_bs=Decimal("100.00"))])
    texto = await sp._estado_cliente_texto("584140000000")
    assert "ARMADO pero SIN cobro" in texto
    assert "COTIZADO" not in texto
