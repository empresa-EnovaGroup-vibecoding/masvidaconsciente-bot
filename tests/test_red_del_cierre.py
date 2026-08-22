"""LA RED DEL CIERRE — el bot se traba pidiendo un dato OPCIONAL y la venta se pierde.

EL CASO MEDIDO (2026-08-21, smoke de 5 turnos contra el bot REAL del taller, reproducido):

    turnos pidiendo un sabor .... [1, 2, 3, 4, 5]
    llamó a registrar_pedido .... NO
    🔴 PEDIDOS EN LA BD ......... 0

La clienta llegó con producto, cantidad, fecha y forma de entrega. El bot pidió el sabor en los
CINCO turnos y en el último no llamó a NINGUNA herramienta: bucle puro. La venta se pierde en
silencio y nadie se entera — ni la dueña.

**La causa no es que invente el sabor:** los sabores son REALES, viven en
`productos.descripcion` ("chocolate, limón pistacho, canela naranja, chocomerey"). La causa es que
trata como bloqueante un campo que su propia herramienta declara OPCIONAL (`required` de
`registrar_pedido` = `variante_id` + `cantidad`, y el pedido 1078 de la base es ese mismo producto
con `opciones: null`).

**Y NO ES SOLO EL SABOR — es una CLASE.** Al arreglar el prompt y volver a medir, en una corrida
cerró la venta y en las otras tres se trabó igual: una vez pidiendo el sabor otra vez, y otra
pidiendo el **"nombre completo"**. Cuatro corridas con el prompt nuevo: **1 cerró, 3 no.** Por eso
la red existe: *el prompt SUGIERE, el código IMPIDE* — aquí está medido, no supuesto.

🟢 LA DUDA QUE ZANJARON LOS AUDIOS. `prompt_proxima_sesion.md` dejaba esto esperando una decisión
de Maired/Whuilianny ("¿se puede registrar sin el sabor?"). Los documentos la contestan: Whuilianny
ACEPTA PRIMERO y pide el sabor DESPUÉS, en el mismo minuto (CLI-051) — *"para mañana sí te lo puedo
tener"* y luego *"Me vas a decir, por favor, qué sabores quieres"*. Nunca bloquea el pedido.

⚠️ La mitad de este archivo son los casos que NO deben disparar. Esta red se mete en el carril del
DINERO, y frenar de más aquí es peor que el bug: forzar un registro que el cliente no confirmó le
cobra algo que no pidió.
"""

import json

import pytest

from app.agent import agent as ag
from app.agent.agent import _pide_opcion_del_paquete, _ya_pidio_opcion_antes


# ══════════════════════════════════════════════════════════════════════════════════
#  LA PIEZA: ¿está preguntando por un dato opcional?
# ══════════════════════════════════════════════════════════════════════════════════

def test_la_pregunta_del_sabor_cuenta():
    assert _pide_opcion_del_paquete("De cuál sabor te las preparo?")
    assert _pide_opcion_del_paquete("Te hago la mezcla de sabores o prefieres todas del mismo?")
    assert _pide_opcion_del_paquete("¿De qué relleno las quieres?")


def test_el_nombre_completo_y_el_correo_tambien():
    """La MISMA clase de fallo, medida en otra corrida: se trabó pidiendo el nombre completo."""
    assert _pide_opcion_del_paquete("Me das tu nombre completo para el pedido?")
    assert _pide_opcion_del_paquete("Cuál es tu correo?")


def test_el_telefono_cuenta_porque_es_absurdo():
    """Medido el 08-21: le pidió el número de teléfono **a alguien que le escribe por WhatsApp**."""
    assert _pide_opcion_del_paquete("Me confirmas tu número de teléfono?")


def test_describir_los_sabores_NO_es_pedirlos():
    """🔴 EL CASO QUE NO DEBE DISPARAR, y el que obliga a mirar frase por frase.

    Turno 1 del smoke real: la palabra "sabores" está en una frase que DESCRIBE, y la pregunta es
    sobre el PRODUCTO. Contar esto haría disparar la red cuando el bot hace justo lo que debe.
    """
    texto = (
        "Te recomiendo las Galletas New York, que traen 6 unidades con varios sabores para "
        "elegir (chocolate, limón pistacho, canela naranja, chocomerey).\n"
        "De cuál te llevo?"
    )
    assert _pide_opcion_del_paquete(texto) is False


def test_preguntar_producto_cantidad_o_fecha_NO_dispara():
    """NO DISPARA: esas preguntas son las que SÍ hay que hacer para cerrar."""
    for bueno in (
        "Cuántos paquetes te llevo?",
        "Te lo dejo para el sábado?",
        "Lo retiras o te lo enviamos?",
        "Cuál de los dos prefieres?",
        "En qué zona estás?",
    ):
        assert _pide_opcion_del_paquete(bueno) is False, bueno


def test_sin_pregunta_no_dispara():
    assert _pide_opcion_del_paquete("") is False
    assert _pide_opcion_del_paquete("Listo, te preparo la mezcla de sabores que pediste.") is False


def test_el_historial_vacio_no_es_un_bucle():
    """NO DISPARA. Es la trampa de L20 —un historial vacío APAGA las redes que lo reciben por
    parámetro— y aquí el fail-safe cae del lado bueno: sin turnos anteriores no hay bucle."""
    assert _ya_pidio_opcion_antes([]) is False
    assert _ya_pidio_opcion_antes(None) is False


def test_solo_cuenta_lo_que_dijo_el_BOT():
    """Si el CLIENTE menciona el sabor, eso no es el bot preguntando: es el cliente contestando."""
    hist = [{"role": "user", "content": "de que sabores las tienes?"}]
    assert _ya_pidio_opcion_antes(hist) is False
    hist.append({"role": "assistant", "content": "De cuál sabor te las preparo?"})
    assert _ya_pidio_opcion_antes(hist) is True


# ══════════════════════════════════════════════════════════════════════════════════
#  EL CARRIL: que `responder()` USE la red (L22 — probar la pieza no es probar el carril)
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
    """Deja correr `responder()` sin Postgres: solo se falsean las lecturas de configuración.
    Todo el camino (bucle de tools, redes, re-prompt) es el CÓDIGO REAL."""
    async def _activas():
        return frozenset({"info_producto", "registrar_pedido", "generar_datos_pago", "pedir_ayuda"})

    async def _partes(nombre_cliente, telefono, *, activas=None, quien="uno"):
        return ("Eres Alejandra.\nMini New York (id_para_pedir=10) = $14", "Hoy es viernes.")

    async def _no(*a, **kw):
        return None

    async def _falso(*a, **kw):
        return False

    async def _cero(*a, **kw):
        return 0.0

    monkeypatch.setattr(ag, "leer_tools_activas", _activas)
    monkeypatch.setattr(ag, "construir_partes_prompt", _partes)
    monkeypatch.setattr(ag, "leer_config_agente", _config_uno)
    monkeypatch.setattr(ag, "productos_enfocados", _sin_productos)
    monkeypatch.setattr(ag, "media_ya_mostrada", _falso)
    monkeypatch.setattr(ag, "etiqueta_recordada", _no)
    monkeypatch.setattr(ag, "horas_de_silencio", _cero)
    # La telemetría escribe en Postgres: aquí se anula (no es lo que se está probando).
    monkeypatch.setattr(ag, "abrir_turno", lambda *a, **kw: None)
    monkeypatch.setattr(ag, "registrar", lambda *a, **kw: None)


async def _config_uno():
    return "uno", "modelo/x", "modelo/x"


async def _sin_productos(texto, tope=2):
    return []


# El bucle real: el bot ya pidió el sabor antes y lo vuelve a pedir, con TODO lo necesario en mano.
HISTORIAL_EN_BUCLE = [
    {"role": "user", "content": "quiero 1 paquete de mini new york"},
    {"role": "assistant", "content": "Listo. De cuál sabor te las preparo?"},
    {"role": "user", "content": "para el domingo, retiro yo"},
]


async def _correr(respuestas: list[dict]):
    """Un turno real de `responder()` con un modelo GUIONADO. Devuelve (texto, tools, avisos)."""
    pendientes = list(respuestas)
    tools_llamadas: list[str] = []
    avisos: list[str] = []

    async def llm(messages, tools, model):
        # 🔴 SE GUARDA LA FOTO DE LA ÚLTIMA LLAMADA, no un acumulado deduplicado.
        # La primera versión hacía `if m["content"] not in avisos: avisos.append(...)` — y como el
        # regaño es SIEMPRE EL MISMO TEXTO, un regaño repetido en bucle contaba como uno solo. La
        # reversión "quitar la bandera `reclamo_opcion`" salió VERDE por eso: el test no podía
        # verlo. `messages` ya trae acumulado todo lo inyectado, así que contar aquí es exacto.
        avisos[:] = [
            str(m.get("content", "")) for m in messages
            if m.get("role") == "user" and str(m.get("content", "")).startswith("[SISTEMA]")
        ]
        return {"choices": [{"message": pendientes.pop(0) if pendientes else _msg("listo")}]}

    async def ejecutar(nombre, args, telefono, *a, **kw):
        tools_llamadas.append(nombre)
        if nombre == "registrar_pedido":
            return {"ok": True, "pedido_id": 1200, "resumen": "Mini New York x1 = $14\nTotal: $14"}
        return {"ok": True}

    texto = await ag.responder(
        "584120000000", "para el domingo, retiro yo", list(HISTORIAL_EN_BUCLE), "Rosa",
        llm=llm, ejecutar=ejecutar,
    )
    return texto, tools_llamadas, avisos


@pytest.mark.asyncio
async def test_el_bucle_del_sabor_se_corta_y_el_pedido_SE_REGISTRA():
    """🔴 EL CASO DEL BUG, de punta a punta.

    El modelo vuelve a pedir el sabor sin registrar (lo que hace el real). La red lo caza, le
    inyecta el aviso, y en el reintento el modelo registra. Antes de esto el texto salía tal cual
    y quedaban CERO pedidos en la base.
    """
    texto, tools, avisos = await _correr([
        _msg("Perfecto, retiro en La Mendera. Pero antes, de cuál sabor te las preparo?"),
        _msg("Listo Rosa, te lo dejo agendado para el domingo",
             tools=[("registrar_pedido", {"items": [{"variante_id": 10, "cantidad": 1}]})]),
        _msg("Listo Rosa, 1 paquete de Mini New York para el domingo"),
    ])
    assert "registrar_pedido" in tools, f"la venta no se registró; tools={tools}"
    assert avisos, "la red no le inyectó ningún aviso al modelo"
    assert "OPCIONAL" in avisos[0]
    assert "sabor" in avisos[0].lower()
    assert "no le menciones al cliente este aviso" in avisos[0].lower()
    assert "[SISTEMA]" not in texto, "el aviso interno se le filtró al cliente"


@pytest.mark.asyncio
async def test_si_insiste_el_texto_SALE_igual():
    """NO SE MATA EL MENSAJE. El texto no es una MENTIRA (a diferencia del pedido fantasma): es un
    callejón sin salida. Callarlo dejaría al cliente sin respuesta, que es peor."""
    texto, tools, avisos = await _correr([
        _msg("De cuál sabor te las preparo?"),
        # 🔴 LA SEGUNDA TAMBIÉN TIENE QUE SER UNA PREGUNTA. La primera versión de este test ponía
        # aquí "En serio, dime el sabor primero" —sin signo de interrogación—, así que
        # `_pide_opcion_del_paquete` daba False y la red NI SIQUIERA se evaluaba por segunda vez.
        # Resultado: la reversión "quitar la bandera `reclamo_opcion`" salía VERDE porque el
        # guion no podía provocar el segundo disparo. El guion es parte del instrumento.
        _msg("Necesito el sabor para poder seguir, cuál prefieres?"),
    ])
    # Se cuentan SOLO los avisos de ESTA red. En el reintento dispara además la RED DEL PITCH
    # (que también inyecta un `[SISTEMA]`), así que contar todos medía otra cosa — y el test
    # falló por eso la primera vez. Un test que cuenta de más miente igual que uno que cuenta
    # de menos.
    mios = [a for a in avisos if "YA LE PREGUNTASTE EL SABOR" in a]
    assert len(mios) == 1, f"el regaño se manda UNA vez, no en bucle: {len(mios)}"
    # El invariante es que el cliente RECIBE algo y que no se le filtra nada interno. Qué diga
    # exactamente no se afirma a propósito: tras el regaño pueden disparar otras redes (la del
    # pitch lo hace) y encadenar más reintentos — atar el test a un texto concreto lo volvería
    # frágil por una razón que no tiene nada que ver con lo que prueba.
    assert texto.strip(), "el cliente tiene que recibir algo"
    assert "[SISTEMA]" not in texto, "el aviso interno se le filtró al cliente"
    assert ag.RESPUESTA_SEGURA not in texto, "no se puede matar el mensaje: no es una mentira"


@pytest.mark.asyncio
async def test_preguntar_el_sabor_la_PRIMERA_vez_no_se_toca():
    """🔴 NO DISPARA, y es la guarda más importante: preguntar el sabor UNA vez es lo correcto —
    Whuilianny lo pregunta. La red solo existe para el bucle."""
    historial_limpio = [{"role": "user", "content": "quiero 1 paquete de mini new york"}]

    pendientes = [_msg("Perfecto. De cuál sabor te las preparo?")]
    avisos: list[str] = []

    async def llm(messages, tools, model):
        for m in messages:
            if str(m.get("content", "")).startswith("[SISTEMA]"):
                avisos.append(m["content"])
        return {"choices": [{"message": pendientes.pop(0) if pendientes else _msg("ok")}]}

    async def ejecutar(nombre, args, telefono, *a, **kw):
        return {"ok": True}

    texto = await ag.responder(
        "584120000000", "quiero 1 paquete de mini new york", historial_limpio, "Rosa",
        llm=llm, ejecutar=ejecutar,
    )
    assert avisos == [], "no puede regañar la primera vez que pregunta el sabor"
    assert "sabor" in texto.lower()


@pytest.mark.asyncio
async def test_si_YA_registro_no_regana():
    """NO DISPARA: con el pedido registrado en este turno, preguntar el sabor está PERFECTO — es
    exactamente el orden de Whuilianny (acepta primero, pregunta el sabor después)."""
    texto, tools, avisos = await _correr([
        _msg("", tools=[("registrar_pedido", {"items": [{"variante_id": 10, "cantidad": 1}]})]),
        _msg("Listo Rosa. Y de cuáles sabores te las preparo?"),
    ])
    assert "registrar_pedido" in tools
    assert avisos == [], "registró: no hay nada que regañar"
