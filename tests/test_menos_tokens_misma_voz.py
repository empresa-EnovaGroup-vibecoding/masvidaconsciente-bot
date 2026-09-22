"""MENOS TOKENS, MISMA VOZ (6-sep, "no quiero pagar tanto" — Maired).

Tres palancas de COSTO que no tocan ni una regla de la voz. Medidas con la telemetría real
(`llamadas_ia`) del guion de las galletas con Sonnet 4.6 ($0,39 la venta) y de producción:

1. EL CACHÉ DURA UNA HORA. Con el TTL de 5 min, producción llevaba 30 días y 6 llamadas de Sonnet
   con CERO caché: un negocio chico no recibe mensajes cada 4 minutos. Cada turno pagaba la
   escritura (1,25×, $0,066) en vez de la lectura (0,1×, $0,004). Con "1h" la escritura vale 2×
   pero sirve a TODOS los clientes durante una hora.
2. EL CARRIL DEL DINERO PEGA EN EL MISMO CACHÉ. `redactar_mensaje` mandaba el MISMO system que el
   agente pero SIN herramientas → otro prefijo → cero caché → cada comprobante $0,074 (19% de la
   venta). Ahora manda las mismas herramientas con `tool_choice: "none"`.
3. NO SE LLAMA A LA FOTO QUE YA SALIÓ. La memoria de fotos frenaba el reenvío, pero el modelo
   llamaba igual (4 intentos en 4 min, 3 frenados = 3 vueltas al modelo, ~9%). El ESTADO le dice
   qué fotos ya salieron, para que no llame.

Lo que NO cambia: ni una regla, ni la temperatura, ni la firma de `_llamar_openrouter` ni la de
`_pedir_redaccion` para quien la llamaba con dos argumentos (los dobles de los bancos).
"""
import inspect
import json

import pytest

from app.agent import agent, tools

# ══ 1) El caché de una hora, en las CUATRO puertas ══

def test_el_cache_dura_una_hora_y_es_uno_solo():
    assert agent._CACHE_CONTROL == {"type": "ephemeral", "ttl": "1h"}
    src = inspect.getsource(agent)
    assert src.count('"cache_control": _CACHE_CONTROL') == 4, "las 4 puertas al modelo usan el mismo"
    assert '"cache_control": {"type": "ephemeral"}' not in src, "ninguna quedó con el TTL de 5 min"


# ══ 2) El carril del dinero manda las mismas herramientas, sin poder usarlas ══

@pytest.mark.asyncio
async def test_pedir_redaccion_con_tools_manda_tool_choice_none(monkeypatch):
    capturado = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "listo"}}], "usage": {}}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers=None, json=None):
            capturado.update(json or {})
            return _Resp()

    monkeypatch.setattr(agent.httpx, "AsyncClient", _Client)

    async def _no_registra(**kw):
        return None

    monkeypatch.setattr(agent, "registrar", _no_registra)

    herramientas = [{"type": "function", "function": {"name": "x", "parameters": {}}}]
    marca = agent._TOOLS_PARA_REDACCION.set(herramientas)
    try:
        assert await agent._pedir_redaccion([{"role": "user", "content": "hola"}], "m") == "listo"
    finally:
        agent._TOOLS_PARA_REDACCION.reset(marca)
    assert capturado["tools"] == herramientas
    assert capturado["tool_choice"] == "none"
    assert capturado["provider"] == {"require_parameters": True}
    assert capturado["temperature"] == 0.7  # la voz no cambia de dial
    json.dumps(capturado)  # y todo es serializable


@pytest.mark.asyncio
async def test_pedir_redaccion_sin_tools_no_cambia_ni_un_byte(monkeypatch):
    """La Voz del modo dos y los dobles de los bancos llaman con DOS argumentos: mismo cuerpo."""
    capturado = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers=None, json=None):
            capturado.update(json or {})
            return _Resp()

    monkeypatch.setattr(agent.httpx, "AsyncClient", _Client)

    async def _no_registra(**kw):
        return None

    monkeypatch.setattr(agent, "registrar", _no_registra)
    await agent._pedir_redaccion([{"role": "user", "content": "hola"}], "m")
    assert set(capturado) == {"model", "messages", "temperature"}


def test_redactar_mensaje_pasa_las_herramientas_activas_por_contexto():
    """La firma de `_pedir_redaccion` sigue siendo (messages, modelo) — `probar_telemetria` la
    vigila y media docena de bancos le pasan dobles con esos dos argumentos."""
    assert list(inspect.signature(agent._pedir_redaccion).parameters) == ["messages", "modelo"]
    src = inspect.getsource(agent.redactar_mensaje)
    assert "schemas_para(await leer_tools_activas())" in src
    assert "_TOOLS_PARA_REDACCION.set(tools_cache or None)" in src
    assert "_TOOLS_PARA_REDACCION.reset(" in src  # el contexto queda limpio para el turno siguiente
    # y si leerlas falla, redacta sin ellas (el ahorro nunca tumba el mensaje del pago)
    assert "tools_cache = None" in src
    assert agent._TOOLS_PARA_REDACCION.get() is None  # fuera del carril, cuerpo de siempre


# ══ 3) Las fotos ya enviadas se le DICEN al modelo, para que no llame ══

class _Res:
    def __init__(self, filas):
        self._f = filas

    def scalars(self):
        return self

    def all(self):
        return self._f


class _Sesion:
    def __init__(self, filas):
        self._f = filas

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _q):
        return _Res(self._f)


@pytest.mark.asyncio
async def test_productos_ya_mostrados_lee_el_pie_y_quita_la_etiqueta(monkeypatch):
    pies = [
        "(foto de Galletas New York)",
        "(video de Tortas keto — 1kg)",
        "(foto de Galletas New York)",        # repetida: no se duplica
        "(foto de Empanadas — base de yuca)",
        "un texto cualquiera",                # no es un pie de media
    ]
    monkeypatch.setattr(tools, "get_session_factory", lambda: (lambda: _Sesion(pies)))
    assert await tools.productos_ya_mostrados("58424") == ["Galletas New York", "Tortas keto", "Empanadas"]


@pytest.mark.asyncio
async def test_productos_ya_mostrados_falla_abierto(monkeypatch):
    class _Rota:
        async def __aenter__(self):
            raise RuntimeError("postgres caído")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(tools, "get_session_factory", lambda: (lambda: _Rota()))
    assert await tools.productos_ya_mostrados("58424") == []
    assert await tools.productos_ya_mostrados("") == []


def test_el_estado_le_dice_al_modelo_que_fotos_ya_salieron_en_la_parte_dinamica():
    """Contrato de fuente: la línea va en `dinamico` (la estable es la cacheada) y solo si la
    herramienta de fotos está activa."""
    src = inspect.getsource(agent.responder)
    i_partes = src.index("construir_partes_prompt(nombre_cliente, telefono, activas=activas)")
    i_fotos = src.index("FOTOS YA ENVIADAS a este cliente")
    i_msgs = src.index('"role": "system"', i_partes)
    assert i_partes < i_fotos < i_msgs
    assert '"enviar_fotos_producto" in activas' in src
    assert "salvo que el cliente PIDA verlas otra vez" in src
