"""EL MODO DOS (Operador + Voz), y el agujero del RE-PROMPT DEL DINERO — en el CI.

`ROADMAP.md` bloquea encender `agente_modo='dos'` por tres cosas. Este archivo cubre la que
importa y la que no tenía forma de detectarse: **el reintento del dinero podía autorizar el mismo
monto que la red acababa de rechazar.**

EL BUG, tal como estaba (`agent.py`, el `or` del re-prompt):

    hoja.encargo = (msg.get("content") or "").strip() or hoja.encargo
                                                      └─ si el reintento traía SOLO tool_calls
                                                         (el patrón NORMAL de un modelo que va a
                                                         buscar el dato), el encargo RECHAZADO se
                                                         quedaba…

…y veinte líneas más abajo:

    u_enc, b_enc = autorizados_por_moneda(hoja.encargo)   # ← se extraían montos de ESE texto
    hoja.montos_usd |= u_enc                              # ← y entraban en la LISTA BLANCA

Resultado: el monto inventado se volvía "verdad" y la Voz podía repetirlo con la red mirando.

⚠️ POR QUÉ ESTA PRUEBA VIVE EN `tests/` Y NO EN UN BANCO. El job `verificar` del CI corre `pytest`
en CADA push; los bancos (`scripts/probar_*.py`) solo corren tras un despliegue MANUAL al taller.
Un bug del carril del dinero que solo se caza después de desplegar no está cazado. Y no hace falta
contenedor: `responder` deja inyectar `llm`, `voz` y `ejecutar` a propósito (su docstring lo dice),
así que aquí no se toca OpenRouter, ni Postgres, ni WhatsApp.

CÓMO SE OBSERVA que el monto no se autorizó, sin leer variables privadas: se le hace decir a la
Voz el monto inventado. Si estuviera en la lista blanca, saldría al cliente; con el arreglo, la red
del dinero lo tumba y el turno devuelve `RESPUESTA_SEGURA`. La prueba mira **lo que le llega al
cliente**, que es lo único que importa.
"""

import json

import pytest

from app.agent import agent as ag
from app.agent.hoja import _renderizar

# El monto INVENTADO: no está en el catálogo de mentira, ni lo devuelve ninguna tool, ni lo
# escribió el cliente. Si aparece en la respuesta, la lista blanca se contaminó.
INVENTADO = "$99"
# El precio LEGÍTIMO: está en el catálogo de mentira (o sea, autorizado como precio suelto).
LEGITIMO = "$14"

_CATALOGO_FALSO = (
    "CATÁLOGO (para ti):\n"
    "· Empanadas — 8 unidades — $14.00 — id_para_pedir=23\n"
)


def _msg(content: str = "", tools: list | None = None) -> dict:
    """Una respuesta del proveedor con la forma que espera el bucle del Operador."""
    m: dict = {"role": "assistant", "content": content}
    if tools:
        m["tool_calls"] = [
            {
                "id": f"call_{i}",
                "type": "function",
                "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)},
            }
            for i, (n, a) in enumerate(tools)
        ]
    return m


def _respuesta(msg: dict) -> dict:
    return {"choices": [{"message": msg}]}


@pytest.fixture(autouse=True)
def modo_dos(monkeypatch):
    """Deja correr `_responder_dos_agentes` sin BD: solo se falsean las dos lecturas de
    configuración. El resto del camino (redes, hoja, re-prompt) es el CÓDIGO REAL.

    `autouse` a propósito: si hay que pedirlo test por test, el día que alguien añada un caso y se
    olvide, ese test sale a buscar Postgres de verdad y falla por la razón equivocada — que es
    exactamente lo que pasó al escribir este archivo."""

    async def _activas():
        return frozenset({"info_producto", "registrar_pedido", "generar_datos_pago", "pedir_ayuda"})

    async def _partes(nombre_cliente, telefono, *, activas=None, quien="uno"):
        # El Operador ve el catálogo; la Voz NO (es el diseño). Que el catálogo traiga $14.00 es lo
        # que autoriza el precio legítimo, igual que en producción.
        if quien == "voz":
            return ("Eres Whuilianny. Escribe corto y cálido.", "")
        return (f"Eres el Operador.\n{_CATALOGO_FALSO}", "Hoy es lunes.")

    monkeypatch.setattr(ag, "leer_tools_activas", _activas)
    monkeypatch.setattr(ag, "construir_partes_prompt", _partes)
    return None


async def _correr(*, respuestas: list[dict], texto_voz, resultados_tool=None):
    """Corre un turno de modo dos y devuelve (lo_que_sale_al_cliente, tools_llamadas)."""
    pendientes = list(respuestas)
    llamadas: list[tuple[str, dict]] = []
    resultados_tool = resultados_tool or {}

    async def llm(messages, tools, model):
        return _respuesta(pendientes.pop(0) if pendientes else _msg("listo"))

    async def ejecutar(nombre, args, telefono):
        llamadas.append((nombre, args))
        return resultados_tool.get(nombre, {"ok": True})

    async def voz(messages, modelo):
        if isinstance(texto_voz, Exception):
            raise texto_voz
        return texto_voz

    salida = await ag._responder_dos_agentes(
        "584120000000", "¿cuánto cuestan las empanadas?",
        [{"role": "assistant", "content": "hola, ¿en qué te ayudo?"}],
        "Ana",
        pregunta_cliente="¿cuánto cuestan las empanadas?",
        llm=llm, voz=voz, ejecutar=ejecutar,
        modelo_operador="modelo/operador", modelo_voz="modelo/voz",
    )
    return salida, llamadas


# ══════════════════════════════════════════════════════════════════════════════════════
# EL BLOQUEADOR: el re-prompt del dinero no puede reautorizar lo que rechazó
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_el_monto_rechazado_no_se_reautoriza_si_el_reintento_solo_trae_tools():
    """EL CASO DEL BUG. El Operador inventa `$99`; la red lo rechaza; el reintento va a buscar el
    precio de verdad y **no reescribe el encargo** (solo `tool_calls`).

    Con el `or` viejo, `$99` se heredaba y entraba en la lista blanca: la Voz podía decirlo y
    salía al cliente. Ahora el encargo se descarta, `$99` NO está autorizado, y aunque la Voz lo
    intente la red lo tumba.
    """
    salida, llamadas = await _correr(
        respuestas=[
            _msg(f"Perfecto, son {INVENTADO} en total"),          # inventa, sin tools
            _msg("", tools=[("info_producto", {"nombre": "Empanadas"})]),  # reintento: SOLO tools
        ],
        texto_voz=f"¡Claro! Son {INVENTADO} 💚",   # la Voz intenta repetir el monto inventado
        resultados_tool={
            "info_producto": {
                "encontrado": True, "nombre": "Empanadas",
                "precio_usd": 14.0, "precio_texto": LEGITIMO,
            },
        },
    )
    assert INVENTADO not in salida, (
        f"el monto rechazado llegó al cliente: {salida!r} — la lista blanca se contaminó"
    )
    assert salida == ag.RESPUESTA_SEGURA
    assert any(n == "pedir_ayuda" for n, _ in llamadas), "nadie avisó a la dueña"


@pytest.mark.asyncio
async def test_el_encargo_rechazado_no_sale_por_el_camino_de_degradacion():
    """La otra mitad del mismo agujero: si la Voz se cae, el turno saca `hoja.encargo`.

    Heredando el encargo rechazado, eso significaba **mandarle el monto inventado al cliente
    directamente**, sin que ninguna red lo mirara (el encargo ya había "pasado" por serlo).
    """
    salida, llamadas = await _correr(
        respuestas=[
            _msg(f"Son {INVENTADO}, te espero"),
            _msg("", tools=[("info_producto", {"nombre": "Empanadas"})]),
        ],
        texto_voz=RuntimeError("la Voz se cayó"),
        resultados_tool={
            "info_producto": {"encontrado": True, "nombre": "Empanadas", "precio_texto": LEGITIMO},
        },
    )
    assert INVENTADO not in salida
    assert salida == ag.RESPUESTA_SEGURA
    assert any(n == "pedir_ayuda" for n, _ in llamadas), (
        "sin Voz y sin encargo el cliente queda esperando: hay que avisar a la dueña"
    )


@pytest.mark.asyncio
async def test_si_el_reintento_SI_reescribe_el_encargo_la_venta_sigue():
    """LA MITAD QUE PROTEGE EL COBRO. Frenar de más rompe la venta.

    Si el Operador corrige de verdad —trae el precio bueno Y reescribe el encargo—, el turno tiene
    que salir normal con el precio legítimo.
    """
    salida, _ = await _correr(
        respuestas=[
            _msg(f"Son {INVENTADO}"),
            _msg(f"Las empanadas están en {LEGITIMO}",
                 tools=[("info_producto", {"nombre": "Empanadas"})]),
        ],
        texto_voz=f"¡Hola Ana! Las empanadas están en {LEGITIMO} 💚",
        resultados_tool={
            "info_producto": {"encontrado": True, "nombre": "Empanadas", "precio_texto": LEGITIMO},
        },
    )
    assert LEGITIMO in salida, f"se frenó una venta legítima: {salida!r}"
    assert salida != ag.RESPUESTA_SEGURA


@pytest.mark.asyncio
async def test_un_turno_normal_sin_dinero_inventado_no_dispara_nada():
    """Control: sin montos raros, el modo dos entrega lo que escribió la Voz y no escala."""
    salida, llamadas = await _correr(
        respuestas=[_msg("Cuéntale que sí tenemos empanadas")],
        texto_voz="¡Hola Ana! Sí tenemos empanadas 💚 ¿Te preparo unas?",
    )
    assert "empanadas" in salida.lower()
    assert not any(n == "pedir_ayuda" for n, _ in llamadas)


# ══════════════════════════════════════════════════════════════════════════════════════
# EL OTRO BLOQUEADOR: `info_producto` sin `precio_texto` dejaba a la Voz sin precio
# ══════════════════════════════════════════════════════════════════════════════════════

def test_la_hoja_muestra_el_precio_de_un_producto_de_un_solo_tamano():
    """`hoja._renderizar` lee literalmente `precio_texto`. `info_producto` no la devolvía, así que
    preguntar por UN producto le llegaba a la Voz SIN precio — y ella no puede ir a buscarlo."""
    bloque = _renderizar("info_producto", {
        "encontrado": True, "nombre": "Pan Keto",
        "precio_usd": 25.0, "precio_texto": "$25",
        "tamanos": [{"tamano": "única", "precio_texto": "$25", "id_para_pedir": 7}],
    })
    assert "$25" in bloque
    assert "Pan Keto" in bloque


def test_la_hoja_muestra_los_tamanos_cuando_hay_varios():
    """Con varios tamaños, `precio_texto` no se iza (cada tamaño cuesta distinto). Sin renderizar
    los tamaños, la ficha se quedaba sin UN SOLO precio."""
    bloque = _renderizar("info_producto", {
        "encontrado": True, "nombre": "Empanadas", "precio_texto": None,
        "tamanos": [
            {"tamano": "4 unidades", "precio_texto": "$12", "id_para_pedir": 24},
            {"tamano": "8 unidades", "precio_texto": "$14", "id_para_pedir": 23},
        ],
    })
    assert "$12" in bloque and "$14" in bloque
    assert "4 unidades" in bloque


def test_la_hoja_nunca_filtra_el_id_para_pedir():
    """El `id_para_pedir` es el bug del "$23" que le llegó a una clienta real: un id que la red
    del dinero podía confundir con un precio. La cabecera de `hoja.py` lo prohíbe explícitamente."""
    bloque = _renderizar("info_producto", {
        "encontrado": True, "nombre": "Empanadas",
        "tamanos": [
            {"tamano": "4 unidades", "precio_texto": "$12", "id_para_pedir": 24},
            {"tamano": "8 unidades", "precio_texto": "$14", "id_para_pedir": 23},
        ],
    })
    assert "id_para_pedir" not in bloque
    assert "24" not in bloque.replace("$14", "").replace("$12", "")


def test_los_tamanos_agotados_no_se_le_ofrecen_a_la_voz():
    """Un tamaño agotado con precio visible es una venta que no se puede cumplir."""
    bloque = _renderizar("info_producto", {
        "encontrado": True, "nombre": "Empanadas",
        "tamanos": [
            {"tamano": "4 unidades", "precio_texto": "$12", "agotado": True},
            {"tamano": "8 unidades", "precio_texto": "$14", "agotado": False},
        ],
    })
    assert "$14" in bloque
    assert "$12" not in bloque
