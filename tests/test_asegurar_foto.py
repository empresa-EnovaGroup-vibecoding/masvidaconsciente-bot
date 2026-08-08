"""LA RED DE LA FOTO — ver vende, y el modelo describe en vez de mostrar.

EL CASO REAL (smoke de 7 turnos contra el bot real, 2026-08-08, corrido DOS veces): CERO fotos
de producto en toda la conversación, con una clienta que llegó a decir *"ok esa quiero"*. La
regla de FOTOS/VIDEO del prompt (enorme, con 🔥 y "ÚSALA PROACTIVA") existe desde hace semanas
— y el modelo la ignora. Por eso `_asegurar_foto` es CÓDIGO, familia de `_asegurar_catalogo`:
cuando el turno queda enfocado en UN producto y no salió ninguna media, la foto la manda el
código por la misma puerta que usa el modelo (`enviar_fotos_producto`, que ya trae las guardas
de simulador y relevo — aquí no se duplican).

⚠️ La mitad de este archivo son los casos que NO deben disparar: el cliente todavía entre
varios productos, el producto ya mostrado, la media ya enviada, el saludo, la herramienta
apagada. Una red que bombardea fotos es spam que le baja la calidad al número — frenar de más
aquí no rompe nada (la foto es un extra), pero disparar de más sí.
"""

import pytest

from app.agent import agent as ag
from app.agent.agent import _asegurar_foto, _es_charla_pura
from app.agent.tools import _productos_nombrados_en

NOMBRES = ["Empanadas", "Empanadas Keto", "Pan de Sándwich", "Quesillo", "Galletas New York"]


# ══════════════════════════════════════════════════════════════════════════════════
# LA PIEZA: qué productos nombra un texto (semántica exacto-primero, la del bug $12/$14)
# ══════════════════════════════════════════════════════════════════════════════════

def test_un_solo_producto_nombrado():
    frase = "Buena elección 💚 el Quesillo es cremosito y rinde full."
    assert _productos_nombrados_en(frase, NOMBRES) == ["Quesillo"]


def test_el_mas_especifico_gana_su_trozo_de_texto():
    """'Empanadas Keto' NO cuenta también como 'Empanadas' — la lección del bug $12/$14:
    el nombre exacto manda y el más específico se queda con su trozo."""
    frase = "Las Empanadas Keto vienen 4 por paquete."
    assert _productos_nombrados_en(frase, NOMBRES) == ["Empanadas Keto"]


def test_tolera_el_singular_del_bot():
    """El bot escribe "la empanada keto" y el producto se llama 'Empanadas Keto'."""
    frase = "te recomiendo la empanada keto, es la mas pedida"
    assert _productos_nombrados_en(frase, NOMBRES) == ["Empanadas Keto"]


def test_dos_productos_son_dos_menciones():
    """"Empanadas y Empanadas Keto" ofrece DOS opciones: cada una tiene su trozo de texto."""
    frase = "tenemos Empanadas y también Empanadas Keto"
    assert sorted(_productos_nombrados_en(frase, NOMBRES)) == ["Empanadas", "Empanadas Keto"]


def test_palabra_completa_jamas_substring():
    """'Pan' no puede calzar dentro de em-PAN-adas (el bug del buscador viejo, otra vez)."""
    frase = "esas empanadas están buenísimas"
    assert _productos_nombrados_en(frase, ["Pan", "Empanadas"]) == ["Empanadas"]


def test_sin_menciones_lista_vacia():
    assert _productos_nombrados_en("no tenemos eso, mira el catálogo 💚", NOMBRES) == []
    assert _productos_nombrados_en("", NOMBRES) == []


@pytest.mark.parametrize(("mensaje", "es_charla"), [
    ("hola buenas tardes", True),
    ("gracias!! chao", True),
    ("👍", True),
    ("", True),
    ("ok esa quiero", False),      # el turno 4 del smoke: AHÍ la foto tenía que salir
    ("quiero el quesillo", False),
])
def test_charla_pura(mensaje: str, es_charla: bool):
    assert _es_charla_pura(mensaje) is es_charla


# ══════════════════════════════════════════════════════════════════════════════════
# LA RED, pieza a pieza (con las dos consultas de BD falseadas)
# ══════════════════════════════════════════════════════════════════════════════════

async def _correr_red(
    monkeypatch,
    *,
    texto: str = "Buena elección 💚 el Quesillo es cremosito y rinde full. te lo preparo?",
    mensaje: str = "ok esa quiero",
    puede_fotos: bool = True,
    hubo_media: bool = False,
    enfocado: str | None = "Quesillo",
    ya_mostrada: bool = False,
    resultado_tool: dict | Exception | None = None,
):
    """Corre `_asegurar_foto` y devuelve (tools_llamadas, veces_que_se_resolvio_producto)."""
    llamadas: list[tuple[str, dict]] = []
    resoluciones: list[str] = []

    async def _enfocado(t):
        resoluciones.append(t)
        return enfocado

    async def _mostrada(telefono, nombre):
        return ya_mostrada

    async def ejecutar(nombre, args, telefono):
        llamadas.append((nombre, args))
        if isinstance(resultado_tool, Exception):
            raise resultado_tool
        return resultado_tool if resultado_tool is not None else {"enviadas": 2, "producto": enfocado}

    monkeypatch.setattr(ag, "producto_enfocado", _enfocado)
    monkeypatch.setattr(ag, "media_ya_mostrada", _mostrada)
    await _asegurar_foto(
        texto, "584240000000", mensaje, ejecutar,
        puede_fotos=puede_fotos, hubo_media=hubo_media,
    )
    return llamadas, resoluciones


async def test_dispara_y_manda_la_foto_del_producto_exacto(monkeypatch):
    """EL CASO DEL SMOKE: turno enfocado en UN producto, cero media → la foto la manda el
    código, con el nombre EXACTO resuelto (jamás uno parecido)."""
    llamadas, _ = await _correr_red(monkeypatch)
    assert llamadas == [("enviar_fotos_producto", {"nombre": "Quesillo"})]


async def test_con_la_tool_apagada_la_red_no_existe(monkeypatch):
    """Apagada en la config de herramientas ⇒ ni siquiera se intenta resolver el producto."""
    llamadas, resoluciones = await _correr_red(monkeypatch, puede_fotos=False)
    assert llamadas == []
    assert resoluciones == [], "con la tool apagada no hay nada que resolver"


async def test_si_ya_salio_media_este_turno_no_bombardea(monkeypatch):
    """El modelo ya mandó fotos o el catálogo: sumarle más media es spam."""
    llamadas, resoluciones = await _correr_red(monkeypatch, hubo_media=True)
    assert llamadas == []
    assert resoluciones == []


async def test_el_mismo_producto_no_se_muestra_dos_veces(monkeypatch):
    """El candado de la conversación: si ya se le mostró (tabla mensajes), no se repite."""
    llamadas, _ = await _correr_red(monkeypatch, ya_mostrada=True)
    assert llamadas == []


async def test_ambiguo_o_sin_producto_no_dispara(monkeypatch):
    """El texto no nombra UN único producto resoluble: sin certeza, ninguna foto."""
    llamadas, _ = await _correr_red(
        monkeypatch,
        texto="tenemos empanadas de yuca y de plátano, las dos quedan brutales",
        enfocado=None,
    )
    assert llamadas == []


async def test_saludo_o_small_talk_no_dispara(monkeypatch):
    llamadas, resoluciones = await _correr_red(
        monkeypatch,
        mensaje="hola buenas tardes",
        texto="Hola Ana, buenas tardes 💚 el Quesillo está recién hecho hoy",
    )
    assert llamadas == []
    assert resoluciones == []


async def test_si_pidio_el_catalogo_ese_turno_no_es_de_fotos(monkeypatch):
    llamadas, resoluciones = await _correr_red(monkeypatch, mensaje="mandame el catalogo porfa")
    assert llamadas == []
    assert resoluciones == []


async def test_si_el_bot_ofrece_a_elegir_todavia_no_hay_foco(monkeypatch):
    """"¿cuál prefieres?" = el cliente sigue entre varios. Mandar la foto de uno sería
    decidir por él (y el prompt ordena UN producto a la vez)."""
    llamadas, resoluciones = await _correr_red(
        monkeypatch,
        texto="tengo pan de sándwich, de hamburguesa y keto 😊 cuál prefieres?",
    )
    assert llamadas == []
    assert resoluciones == []


async def test_si_no_hay_fotos_cargadas_no_pasa_nada(monkeypatch):
    """La tool dice "no hay fotos": el texto ya salió con la verdad y aquí no se toca nada.
    (No revienta, no reintenta, no promete.)"""
    llamadas, _ = await _correr_red(
        monkeypatch,
        resultado_tool={"enviadas": 0, "nota": "'Quesillo' no tiene fotos ni videos cargados."},
    )
    assert llamadas == [("enviar_fotos_producto", {"nombre": "Quesillo"})]


async def test_si_la_tool_revienta_el_turno_sigue_intacto(monkeypatch):
    """La foto es un empujón de venta: una excepción suya JAMÁS puede tumbar un turno bueno."""
    llamadas, _ = await _correr_red(monkeypatch, resultado_tool=RuntimeError("Meta caída"))
    assert llamadas == [("enviar_fotos_producto", {"nombre": "Quesillo"})]  # lo intentó y siguió


# ══════════════════════════════════════════════════════════════════════════════════
# POR LA PUERTA REAL (`responder`, modo uno): la red está CONECTADA
# ══════════════════════════════════════════════════════════════════════════════════

ACTIVAS = frozenset({
    "ver_catalogo", "info_producto", "enviar_catalogo", "pedir_ayuda", "enviar_fotos_producto",
})
HISTORIAL = [{"role": "assistant", "content": "hola 💚 dime, en que te puedo ayudar?"}]
TEXTO_ENFOCADO = "Buena elección 💚 el Quesillo es cremosito y rinde full. lo quieres para el sabado?"


@pytest.fixture(autouse=True)
def modo_uno(monkeypatch):
    """Solo se falsean las lecturas de configuración (molde de test_modo_dos.py)."""

    async def _config():
        return "uno", "modelo/uno", "modelo/uno"

    async def _modelo():
        return "modelo/uno"

    async def _activas():
        return ACTIVAS

    async def _partes(nombre_cliente, telefono, *, activas=None, quien="uno"):
        return ("Eres Alejandra. CATÁLOGO (para ti): Quesillo — $8.00", "Hoy es viernes.")

    monkeypatch.setattr(ag, "leer_config_agente", _config)
    monkeypatch.setattr(ag, "leer_modelo_ia", _modelo)
    monkeypatch.setattr(ag, "leer_tools_activas", _activas)
    monkeypatch.setattr(ag, "construir_partes_prompt", _partes)
    return None


async def _correr_turno(monkeypatch, *, activas=None, ya_mostrada=False, enfocado="Quesillo"):
    """Un turno real de modo uno cuyo texto final queda enfocado en el Quesillo."""
    llamadas: list[tuple[str, dict]] = []

    if activas is not None:
        activas_set = frozenset(activas)

        async def _activas():
            return activas_set

        monkeypatch.setattr(ag, "leer_tools_activas", _activas)

    async def _enfocado(texto):
        return enfocado

    async def _mostrada(telefono, nombre):
        return ya_mostrada

    monkeypatch.setattr(ag, "producto_enfocado", _enfocado)
    monkeypatch.setattr(ag, "media_ya_mostrada", _mostrada)

    async def llm(messages, tools, model):
        return {"choices": [{"message": {"role": "assistant", "content": TEXTO_ENFOCADO}}]}

    async def ejecutar(nombre, args, telefono):
        llamadas.append((nombre, args))
        return {"enviadas": 2, "producto": "Quesillo"}

    salida = await ag.responder(
        "584240000000", "ok esa quiero", list(HISTORIAL), "Ana",
        llm=llm, ejecutar=ejecutar,
    )
    return salida, llamadas


async def test_por_la_puerta_real_la_foto_sale_y_el_texto_no_se_toca(monkeypatch):
    salida, llamadas = await _correr_turno(monkeypatch)
    assert ("enviar_fotos_producto", {"nombre": "Quesillo"}) in llamadas
    assert salida == TEXTO_ENFOCADO, "la red suma una foto: el texto JAMÁS se toca"


async def test_por_la_puerta_real_apagada_no_existe(monkeypatch):
    """Con `enviar_fotos_producto` fuera de la config, la red no existe ese turno."""
    salida, llamadas = await _correr_turno(
        monkeypatch,
        activas={"ver_catalogo", "info_producto", "enviar_catalogo", "pedir_ayuda"},
    )
    assert not any(n == "enviar_fotos_producto" for n, _ in llamadas)
    assert salida == TEXTO_ENFOCADO


async def test_por_la_puerta_real_no_repite_el_producto(monkeypatch):
    salida, llamadas = await _correr_turno(monkeypatch, ya_mostrada=True)
    assert not any(n == "enviar_fotos_producto" for n, _ in llamadas)
    assert salida == TEXTO_ENFOCADO
