"""LA RED DEL TAMAÑO ADIVINADO — el bot elige el tamaño (o sea, el PRECIO) que nadie pidió.

EL CASO MEDIDO (2026-08-22, contra el bot real del taller):

    👩 "ok esa quiero, 1"
    🤖 "te preparo la Torta baja en carbohidratos DE 1KG"

La clienta nunca dijo el tamaño. Dijo "1", que ahí es la CANTIDAD. El bot eligió por ella — y
eligió **el más caro de los tres** (250g / 500g / 1kg, tres precios distintos).

**Es la fuga de la Kombucha otra vez.** Aquella (350ml $4 / 700ml $7, siempre cobraba $4) costó
una cirugía entera: migraciones 022/022b y el "código de barras". Pero aquella era el CÓDIGO
eligiendo mal; esta es el MODELO eligiendo por su cuenta, y el código de barras no la tapa: el
`variante_id` es válido… simplemente no es el que el cliente pidió.

**Se lo prohíbe el prompt DOS veces** —el catálogo ("PREGÚNTALE cuál quiere ANTES de registrar, y
NUNCA lo adivines") y el schema del `variante_id` ("cada tamaño tiene SU precio")— y lo hizo
igual. *El prompt SUGIERE, el código IMPIDE.*

⚠️ La mitad de este archivo son los casos que NO deben disparar, y aquí eso importa más que en
ninguna otra red: **frenar de más aquí no protege dinero, lo pierde.** Una red del cobro que
bloquea ventas buenas es una red que alguien acaba apagando.
"""

import json

import pytest

from app.agent import agent as ag
from app.agent.agent import (
    _formas_del_tamano,
    _menciona_tamano,
    _tamano_propuesto_por_el_bot,
)

# Los tres tamaños REALES de la torta que disparó el caso (verificado en la BD del taller
# el 2026-08-22: `producto_variantes` de "Torta baja en carbohidratos").
TORTA = {"producto": "Torta baja en carbohidratos", "elegido": "1kg",
         "tamanos": ["500g", "1kg", "250g"]}


# ══════════════════════════════════════════════════════════════════════════════════
#  LA PIEZA: ¿el cliente nombró ESE tamaño?
# ══════════════════════════════════════════════════════════════════════════════════

def test_el_cliente_lo_dice_como_quiere():
    """La clienta no escribe como el catálogo. Si esta lista se queda corta, la red bloquea
    ventas BUENAS — que es peor que el bug que arregla."""
    for dicho in ("quiero la de 1kg", "dame la de 1 kg", "la de un kilo", "el kilo porfa"):
        assert _menciona_tamano(dicho, "1kg") is True, dicho
    assert _menciona_tamano("la de 500", "500g") is True          # número distintivo, sin unidad
    assert _menciona_tamano("500 gramos esta bien", "500g") is True
    assert _menciona_tamano("medio kilo", "500g") is True
    assert _menciona_tamano("la de 350ml", "350ml") is True


def test_EL_BUG_un_numero_pelado_chico_es_la_CANTIDAD_no_el_tamano():
    """🔴 EL CASO MEDIDO, en una línea. "ok esa quiero, 1" NO dice el tamaño: dice cuántas.

    Si el '1' pelado contara como "1kg", esta red daría por elegido justo el tamaño que la
    clienta nunca pidió — y encima el más caro. Por eso el número suelto solo cuenta cuando es
    distintivo (≥100: 250, 500, 350, 700).
    """
    assert _menciona_tamano("ok esa quiero, 1", "1kg") is False
    assert _menciona_tamano("dame 2 porfa", "250g") is False
    assert _menciona_tamano("quiero 1", "1kg") is False


def test_las_formas_del_kilo_incluyen_la_palabra():
    assert any("kilo" in f for f in _formas_del_tamano("1kg"))
    # Un tamaño sin número ('familiar') se busca tal cual, sin inventarle equivalencias.
    assert _formas_del_tamano("familiar") == ["familiar"]
    assert _formas_del_tamano("") == []


def test_el_bot_propone_UNO_vale_pero_ofrecer_TRES_no():
    """Si el bot nombró UN tamaño y el cliente siguió, pudo estar diciéndole que sí. Si nombró
    los tres, está OFRECIENDO: un 'sí' no dice cuál, y ahí adivinar es exactamente el bug."""
    assert _tamano_propuesto_por_el_bot("te la dejo de 1kg?", TORTA["tamanos"]) == "1kg"
    assert _tamano_propuesto_por_el_bot(
        "la tenemos en 250g, 500g y 1kg. cual prefieres?", TORTA["tamanos"]
    ) is None
    assert _tamano_propuesto_por_el_bot("perfecto, te la preparo", TORTA["tamanos"]) is None


# ══════════════════════════════════════════════════════════════════════════════════
#  EL CARRIL: que `responder()` FRENE la herramienta (L22 — la pieza no es el carril)
# ══════════════════════════════════════════════════════════════════════════════════

def _msg(content: str = "", tools: list | None = None) -> dict:
    m: dict = {"role": "assistant", "content": content}
    if tools:
        m["tool_calls"] = [
            {"id": f"call_{i}", "type": "function",
             "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}}
            for i, (n, a) in enumerate(tools)
        ]
    return m


@pytest.fixture(autouse=True)
def sin_bd(monkeypatch):
    """Deja correr `responder()` sin Postgres. Todo el camino (bucle de tools, guardas, redes)
    es el CÓDIGO REAL: lo único falseado son las lecturas de configuración y del catálogo."""
    async def _activas():
        return frozenset({"info_producto", "registrar_pedido", "generar_datos_pago", "pedir_ayuda"})

    async def _partes(nombre_cliente, telefono, *, activas=None, quien="uno"):
        return ("Eres Alejandra.\nTorta baja en carbohidratos 1kg (id_para_pedir=77)", "Hoy es viernes.")

    async def _config_uno():
        return "uno", "modelo/x", "modelo/x"

    async def _no(*a, **kw):
        return None

    async def _falso(*a, **kw):
        return False

    async def _cero(*a, **kw):
        return 0.0

    async def _sin_productos(texto, tope=2):
        return []

    monkeypatch.setattr(ag, "leer_tools_activas", _activas)
    monkeypatch.setattr(ag, "construir_partes_prompt", _partes)
    monkeypatch.setattr(ag, "leer_config_agente", _config_uno)
    monkeypatch.setattr(ag, "productos_enfocados", _sin_productos)
    monkeypatch.setattr(ag, "media_ya_mostrada", _falso)
    monkeypatch.setattr(ag, "etiqueta_recordada", _no)
    monkeypatch.setattr(ag, "horas_de_silencio", _cero)
    monkeypatch.setattr(ag, "abrir_turno", lambda *a, **kw: None)
    monkeypatch.setattr(ag, "registrar", lambda *a, **kw: None)


@pytest.fixture
def catalogo(monkeypatch):
    """El catálogo de tamaños, falseado: el 77 es la torta de 1kg (tres tamaños); el 10, las
    Mini New York (un solo tamaño vendible → `tamanos_hermanos` devuelve None)."""
    async def _hermanos(variante_id):
        try:
            vid = int(variante_id)
        except (TypeError, ValueError):
            return None
        return {
            77: dict(TORTA),                       # 1kg — el más caro
            78: {**TORTA, "elegido": "250g"},      # el más barato
            79: {**TORTA, "elegido": "500g"},      # el PRIMERO de la lista (ver R7)
        }.get(vid)

    # 🔴 POR NOMBRE EN EL MÓDULO, no por parámetro: `_tamano_sin_elegir` resuelve el global en
    # cada llamada, así que este parche SÍ llega (no es el caso de L27, donde la referencia se
    # congelaba en un valor por defecto al importar).
    monkeypatch.setattr(ag, "tamanos_hermanos", _hermanos)


async def _correr(respuestas: list[dict], mensaje: str, historial: list):
    """Un turno real de `responder()` con un modelo GUIONADO.
    Devuelve (texto, tools EJECUTADAS de verdad, args con los que se llamó)."""
    pendientes = list(respuestas)
    ejecutadas: list[str] = []

    async def llm(messages, tools, model):
        return {"choices": [{"message": pendientes.pop(0) if pendientes else _msg("listo")}]}

    async def ejecutar(nombre, args, telefono, *a, **kw):
        ejecutadas.append(nombre)
        if nombre == "registrar_pedido":
            return {"ok": True, "pedido_id": 1300, "resumen": "Torta 1kg x1"}
        return {"ok": True}

    texto = await ag.responder(
        "584120000000", mensaje, list(historial), "Rosa", llm=llm, ejecutar=ejecutar,
    )
    return texto, ejecutadas


HIST_SIN_TAMANO = [
    {"role": "user", "content": "hola, que tortas tienen?"},
    {"role": "assistant", "content": "Tenemos la Torta baja en carbohidratos 💚"},
]


@pytest.mark.asyncio
async def test_EL_BUG_no_se_registra_el_tamano_que_nadie_pidio(catalogo):
    """🔴 EL CASO DEL BUG, de punta a punta: `registrar_pedido` NO llega a ejecutarse."""
    texto, ejecutadas = await _correr(
        [
            _msg("", tools=[("registrar_pedido",
                             {"items": [{"variante_id": 77, "cantidad": 1}]})]),
            _msg("La tenemos de 250g, 500g y 1kg. Cuál te provoca?"),
        ],
        "ok esa quiero, 1", HIST_SIN_TAMANO,
    )
    assert "registrar_pedido" not in ejecutadas, (
        f"se registró un tamaño que la clienta nunca pidió: {ejecutadas}"
    )
    assert texto.strip(), "el cliente tiene que recibir algo"
    assert "[SISTEMA]" not in texto


@pytest.mark.asyncio
async def test_el_modelo_recibe_el_porque_y_puede_corregir_en_el_MISMO_turno(catalogo):
    """El rechazo no es un muro: llega como `role: tool` con su `nota`, igual que los rechazos
    propios de la herramienta (zona inválida, agotado). Y el turno sigue."""
    pendientes = [
        _msg("", tools=[("registrar_pedido", {"items": [{"variante_id": 77, "cantidad": 1}]})]),
        _msg("De cuál tamaño te la preparo?"),
    ]
    respuestas_de_tool: list[str] = []
    ejecutadas: list[str] = []

    async def llm(messages, tools, model):
        # 🔴 LA FOTO DE LA ÚLTIMA LLAMADA, no un acumulado deduplicado (L34): `messages` ya trae
        # todo lo inyectado hasta ahora, así que leer aquí es exacto.
        respuestas_de_tool[:] = [
            str(m.get("content", "")) for m in messages if m.get("role") == "tool"
        ]
        return {"choices": [{"message": pendientes.pop(0) if pendientes else _msg("listo")}]}

    async def ejecutar(nombre, args, telefono, *a, **kw):
        ejecutadas.append(nombre)
        return {"ok": True}

    texto = await ag.responder(
        "584120000000", "ok esa quiero, 1", list(HIST_SIN_TAMANO), "Rosa",
        llm=llm, ejecutar=ejecutar,
    )
    assert "registrar_pedido" not in ejecutadas
    assert respuestas_de_tool, "el `tool_call` se quedó SIN respuesta: el proveedor daría un 400"
    rechazo = json.loads(respuestas_de_tool[0])
    assert rechazo["ok"] is False
    assert "NUNCA dijo cuál quiere" in rechazo["nota"]
    assert rechazo["tamanos"] == TORTA["tamanos"], "hay que decirle CUÁLES son los tamaños"
    assert "[SISTEMA]" not in texto


@pytest.mark.asyncio
async def test_si_el_CLIENTE_dijo_el_tamano_se_registra_normal(catalogo):
    """✅ NO DISPARA. Es la venta buena, y es el caso que más veces va a pasar."""
    texto, ejecutadas = await _correr(
        [
            _msg("", tools=[("registrar_pedido",
                             {"items": [{"variante_id": 77, "cantidad": 1}]})]),
            _msg("Listo Rosa, te la dejo agendada"),
        ],
        "quiero la de 1kg, 1", HIST_SIN_TAMANO,
    )
    assert "registrar_pedido" in ejecutadas, "se bloqueó una venta BUENA"


@pytest.mark.asyncio
async def test_si_lo_dijo_en_un_turno_ANTERIOR_tambien_vale(catalogo):
    """✅ NO DISPARA: el cliente no tiene que repetir el tamaño en cada mensaje."""
    hist = [
        {"role": "user", "content": "la torta de 1kg cuanto sale?"},
        {"role": "assistant", "content": "Te la puedo tener para mañana 💚"},
    ]
    _texto, ejecutadas = await _correr(
        [
            _msg("", tools=[("registrar_pedido",
                             {"items": [{"variante_id": 77, "cantidad": 1}]})]),
            _msg("Listo Rosa"),
        ],
        "dale, 1", hist,
    )
    assert "registrar_pedido" in ejecutadas


@pytest.mark.asyncio
async def test_si_el_BOT_propuso_UN_tamano_y_el_cliente_siguio_vale(catalogo):
    """✅ NO DISPARA: el bot propuso 1kg, la clienta lo tenía delante y dijo que sí."""
    hist = [
        {"role": "user", "content": "quiero una torta"},
        {"role": "assistant", "content": "Te la dejo de 1kg?"},
    ]
    _texto, ejecutadas = await _correr(
        [
            _msg("", tools=[("registrar_pedido",
                             {"items": [{"variante_id": 77, "cantidad": 1}]})]),
            _msg("Listo Rosa"),
        ],
        "si porfa", hist,
    )
    assert "registrar_pedido" in ejecutadas


@pytest.mark.asyncio
async def test_pero_si_el_bot_OFRECIO_LOS_TRES_un_si_no_dice_cual(catalogo):
    """🔴 SÍ DISPARA, y es el borde fino de la red: ofrecer no es proponer. Con los tres tamaños
    sobre la mesa, un 'sí' no elige ninguno — y el bot se quedaba con el más caro.

    🔴 Se registra el 79 (500g), que es el PRIMERO de la lista de tamaños, y eso NO es un detalle:
    con el 77 (1kg) este test pasaba aunque se rompiera `_tamano_propuesto_por_el_bot` — la
    reversión "ofrecer tres cuenta como proponer" devolvía el primero (500g), que no coincidía
    con el elegido (1kg), y el pedido quedaba bloqueado POR LA RAZÓN EQUIVOCADA. Un test que
    pasa por el orden de una lista no prueba nada (L35)."""
    hist = [
        {"role": "user", "content": "quiero una torta"},
        {"role": "assistant", "content": "La tenemos en 250g, 500g y 1kg. Cuál prefieres?"},
    ]
    _texto, ejecutadas = await _correr(
        [
            _msg("", tools=[("registrar_pedido",
                             {"items": [{"variante_id": 79, "cantidad": 1}]})]),
            _msg("Cuál de los tres te preparo?"),
        ],
        "si dale", hist,
    )
    assert "registrar_pedido" not in ejecutadas


@pytest.mark.asyncio
async def test_un_producto_de_UN_SOLO_tamano_nunca_se_toca(catalogo):
    """✅ NO DISPARA. Si no hay elección posible no se puede adivinar, y la inmensa mayoría del
    catálogo (29 de 32 productos el 08-22) es así: esta red no puede rozarlos."""
    _texto, ejecutadas = await _correr(
        [
            _msg("", tools=[("registrar_pedido",
                             {"items": [{"variante_id": 10, "cantidad": 1}]})]),
            _msg("Listo Rosa"),
        ],
        "quiero 1", HIST_SIN_TAMANO,
    )
    assert "registrar_pedido" in ejecutadas


@pytest.mark.asyncio
async def test_si_no_se_puede_leer_el_catalogo_la_venta_SIGUE(monkeypatch):
    """✅ NO DISPARA. Fail-open a propósito: una red del cobro que se cae no puede tumbar un
    pedido. `tamanos_hermanos` ya devuelve None ante cualquier fallo; esto lo prueba desde el
    carril, no desde su docstring."""
    async def _revienta(variante_id):
        return None

    monkeypatch.setattr(ag, "tamanos_hermanos", _revienta)
    _texto, ejecutadas = await _correr(
        [
            _msg("", tools=[("registrar_pedido",
                             {"items": [{"variante_id": 77, "cantidad": 1}]})]),
            _msg("Listo Rosa"),
        ],
        "ok esa quiero, 1", HIST_SIN_TAMANO,
    )
    assert "registrar_pedido" in ejecutadas
