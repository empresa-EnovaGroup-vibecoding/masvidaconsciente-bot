"""LA VERSIÓN QUE EL CLIENTE ELIGIÓ SE RECUERDA — pero solo mientras siga valiendo.

EL CASO REAL (medido contra el bot del taller, 2026-08-09; producto "Empanadas de masa de yuca
o de masa de plátano", dos fotos etiquetadas "de yuca" / "de plátano"):

    turno A — cliente: "de platano"
    turno B — cliente: "que relleno hay?"
    turno C — cliente: "de carne mechada, 1 paquete"  → aquí dispara la RED DE LA FOTO

En el turno C salían LAS DOS fotos (yuca y plátano) porque `etiqueta_del_cliente` solo mira el
mensaje de ESE turno, y ahí ya no aparecía "platano". No es una mentira —la herramienta avisa al
modelo de que mostró las generales— pero el cliente eligió plátano y recibía también la de yuca.

⚠️ MÁS DE LA MITAD de este archivo son los casos que NO deben recordar: cambio de producto,
cambio de variante, ventana vencida, turno actual que ya eligió, etiqueta que no es de este
producto. Una memoria que se equivoca manda la foto EQUIVOCADA — y eso sí es peor que hoy
(doctrina del bug $12/$14: exacto primero, ambigüedad real ⇒ no adivinar).
"""

import pytest

from app.agent import agent as ag
from app.agent.agent import _asegurar_foto
from app.agent.tools import (
    _elegir_medios,
    etiqueta_del_cliente,
    etiqueta_recordada,
    etiqueta_recordada_en,
)

# El producto REAL del taller: UNO solo, dos versiones, dos fotos etiquetadas.
COMPUESTO = "Empanadas de masa de yuca o de masa de plátano"
CATALOGO = [COMPUESTO, "Pan Keto", "Quesillo", "Kombucha", "Galletas New York"]

# Los textos LITERALES de la conversación medida (los del bot son los que dio el bot real).
OFRECE = "Tengo Empanadas de masa de plátano y Empanadas de masa de yuca — ambas sin gluten. ¿De cuál prefieres?"
CONFIRMA = "Listo. Las Empanadas de masa de plátano vienen en paquete de 8 unidades. ¿Cuántos paquetes quieres y de qué relleno?"
RELLENOS = "Tenemos de carne mechada, de pollo desmechado y de queso 💚"
CIERRE = "Perfecto 💚 te dejo 1 paquete de Empanadas de masa de plátano con relleno de carne mechada."

# El historial tal y como llega del turno C: la elección de masa quedó DOS turnos atrás.
HISTORIAL_REAL = [
    {"role": "assistant", "content": OFRECE},
    {"role": "user", "content": "de platano"},
    {"role": "assistant", "content": CONFIRMA},
    {"role": "user", "content": "que relleno hay?"},
    {"role": "assistant", "content": RELLENOS},
]
MENSAJE_C = "de carne mechada, 1 paquete"


# ══════════════════════════════════════════════════════════════════════════════════
# LA PIEZA PURA: qué se recuerda (`etiqueta_recordada_en`, con el catálogo puesto a mano)
# ══════════════════════════════════════════════════════════════════════════════════

def test_el_caso_real_medido_se_recuerda():
    """EL FALLO QUE SE ARREGLA: en el turno del cierre ya no se dice "platano", y la foto que
    tiene que salir sigue siendo la de plátano."""
    assert etiqueta_del_cliente(COMPUESTO, MENSAJE_C) is None, "el turno actual no dice la masa"
    assert etiqueta_recordada_en(COMPUESTO, MENSAJE_C, HISTORIAL_REAL, CATALOGO) == "platano"


def test_en_el_borde_de_la_ventana_todavia_se_recuerda():
    """Tres turnos del cliente ES la ventana: al tercero todavía se recuerda. Este test existe
    para que un off-by-one al acortarla no mate el caso real de un turno para otro."""
    historial = [
        {"role": "user", "content": "de platano"},
        {"role": "assistant", "content": CONFIRMA},
        {"role": "user", "content": "que relleno hay?"},
        {"role": "assistant", "content": RELLENOS},
        {"role": "user", "content": "y vienen congeladas?"},
        {"role": "assistant", "content": "Sí, y duran 3 meses en el congelador 💚"},
    ]
    assert etiqueta_recordada_en(COMPUESTO, "dame 1 paquete", historial, CATALOGO) == "platano"


def test_la_eleccion_mas_reciente_tapa_a_la_vieja():
    """CAMBIO DE VARIANTE: dijo plátano y después se arrepintió. Gana la última."""
    historial = [
        *HISTORIAL_REAL,
        {"role": "user", "content": "mejor la de yuca"},
        {"role": "assistant", "content": "Claro 💚 de masa de yuca entonces."},
    ]
    assert etiqueta_recordada_en(COMPUESTO, MENSAJE_C, historial, CATALOGO) == "yuca"


# ── LOS QUE NO DEBEN RECORDAR ─────────────────────────────────────────────────────

def test_el_turno_actual_manda_sobre_lo_recordado():
    """Si en ESTE mensaje eligió, la memoria no se consulta siquiera: decide `etiqueta_del_cliente`
    (y decide "yuca", no el "platano" viejo)."""
    assert etiqueta_recordada_en(COMPUESTO, "mejor la de yuca", HISTORIAL_REAL, CATALOGO) is None
    assert etiqueta_del_cliente(COMPUESTO, "mejor la de yuca") == "yuca"


def test_el_turno_actual_ambiguo_tampoco_se_pisa_con_memoria():
    """Nombró LAS DOS ahora mismo: van las generales A PROPÓSITO. Rellenar esa duda con lo que
    dijo hace dos turnos es exactamente adivinar."""
    assert etiqueta_recordada_en(COMPUESTO, "yuca o platano?", HISTORIAL_REAL, CATALOGO) is None


def test_no_cruza_un_cambio_de_producto_del_cliente():
    """CAMBIO DE PRODUCTO: una etiqueta solo vale para SU producto. El "de platano" de las
    empanadas no puede viajar al producto siguiente."""
    historial = [
        *HISTORIAL_REAL,
        {"role": "user", "content": "y tienen Quesillo?"},
        {"role": "assistant", "content": "Sí 💚"},
    ]
    assert etiqueta_recordada_en(COMPUESTO, "dame 1", historial, CATALOGO) is None


def test_no_cruza_un_cambio_de_producto_del_bot():
    """El desvío también corta cuando lo mete el BOT ("también tenemos Pan Keto"): entre medias
    la conversación fue de otra cosa y la elección vieja dejó de ser la de este turno."""
    historial = [
        *HISTORIAL_REAL,
        {"role": "assistant", "content": "Ah, y también tenemos Pan Keto recién horneado 😊"},
        {"role": "user", "content": "ah ok"},
    ]
    assert etiqueta_recordada_en(COMPUESTO, "dame 1", historial, CATALOGO) is None


def test_fuera_de_la_ventana_no_se_recuerda():
    """Ventana corta a propósito: recordar la masa de hace media conversación no es memoria,
    es adivinar. Pasada la ventana se hace lo de hoy (las generales)."""
    historial = [
        *HISTORIAL_REAL,
        {"role": "user", "content": "vale"},
        {"role": "assistant", "content": "💚"},
        {"role": "user", "content": "una pregunta"},
        {"role": "assistant", "content": "dime 😊"},
        {"role": "user", "content": "hacen delivery?"},
        {"role": "assistant", "content": "Sí, en Cabudare 💚"},
    ]
    assert etiqueta_recordada_en(COMPUESTO, "dame 1 paquete", historial, CATALOGO) is None


def test_una_duda_vieja_no_se_resuelve_por_mayoria():
    """En su turno el cliente nombró las dos masas: eso no fue una elección entonces y tampoco
    lo es ahora. Se para ahí (no se sigue buscando hacia atrás una elección más vieja)."""
    historial = [
        {"role": "user", "content": "de platano"},
        {"role": "assistant", "content": CONFIRMA},
        {"role": "user", "content": "mejor la de yuca no la de platano"},
        {"role": "assistant", "content": OFRECE},
    ]
    assert etiqueta_recordada_en(COMPUESTO, "dame 1", historial, CATALOGO) is None


def test_producto_simple_nunca_recuerda():
    """Sin versiones no hay nada que elegir — ni aunque el historial hable de plátanos."""
    assert etiqueta_recordada_en("Pan Keto", "dame 1", HISTORIAL_REAL, CATALOGO) is None


def test_jamas_inventa_una_etiqueta_que_no_es_de_este_producto():
    """El cliente pidió "de trigo" y ESTE producto solo se hace de yuca o de plátano: no se
    fuerza nada, van las generales (nunca se inventa una etiqueta)."""
    historial = [
        {"role": "user", "content": "la de trigo"},
        {"role": "assistant", "content": CONFIRMA},
    ]
    assert etiqueta_recordada_en(COMPUESTO, "dame 1", historial, CATALOGO) is None


def test_la_cabeza_del_nombre_no_es_una_eleccion():
    """"las empanadas" nombra el producto, no elige masa."""
    historial = [
        {"role": "user", "content": "las empanadas"},
        {"role": "assistant", "content": OFRECE},
    ]
    assert etiqueta_recordada_en(COMPUESTO, "dame 1", historial, CATALOGO) is None


@pytest.mark.parametrize("historial", [None, [], [{"role": "user"}], ["basura"]])
def test_sin_historial_o_con_basura_no_hay_memoria(historial):
    assert etiqueta_recordada_en(COMPUESTO, MENSAJE_C, historial, CATALOGO) is None


# ══════════════════════════════════════════════════════════════════════════════════
# LO RECORDADO QUE NO CALZA CON NINGUNA FOTO REAL ⇒ LAS GENERALES (`_elegir_medios`)
# ══════════════════════════════════════════════════════════════════════════════════

class _Media:
    def __init__(self, etiqueta=None, variante_id=None):
        self.etiqueta = etiqueta
        self.variante_id = variante_id


def test_una_etiqueta_recordada_sin_foto_que_calce_cae_en_las_generales():
    """Última regla de precedencia: si la dueña no etiquetó ninguna foto así, no se fuerza —
    salen las generales y `etiqueta_enviada` viene None para que el bot no diga que es esa."""
    generales = [_Media(), _Media()]
    medios, enviada, _ = _elegir_medios(generales, None, "platano")
    assert medios == generales
    assert enviada is None, "sin foto de esa masa NO se puede decir que se mandó esa masa"


def test_con_la_foto_etiquetada_sale_solo_esa():
    """Y cuando SÍ existe, la de la otra masa jamás sale."""
    yuca, platano = _Media("de yuca"), _Media("de plátano")
    medios, enviada, _ = _elegir_medios([yuca, platano], None, "platano")
    assert medios == [platano]
    assert enviada == "de plátano"


# ══════════════════════════════════════════════════════════════════════════════════
# EL CABLEADO: la RED DE LA FOTO le pasa el historial y usa lo recordado
# ══════════════════════════════════════════════════════════════════════════════════

async def _correr_red(monkeypatch, *, mensaje, historial, texto=CIERRE, enfocado=COMPUESTO):
    """Corre `_asegurar_foto` con la memoria REAL (la pura) atada a un catálogo de prueba.
    Devuelve (llamadas a tools, argumentos con los que se consultó la memoria)."""
    llamadas: list[tuple[str, dict]] = []
    consultas: list[tuple] = []

    async def _enfocado(t):
        return enfocado

    async def _mostrada(telefono, nombre):
        return False

    async def _recordada(nombre, mensaje_actual, hist):
        consultas.append((nombre, mensaje_actual, hist))
        return etiqueta_recordada_en(nombre, mensaje_actual, hist, CATALOGO)

    async def ejecutar(nombre, args, telefono):
        llamadas.append((nombre, args))
        return {"enviadas": 1, "producto": enfocado}

    monkeypatch.setattr(ag, "producto_enfocado", _enfocado)
    monkeypatch.setattr(ag, "media_ya_mostrada", _mostrada)
    monkeypatch.setattr(ag, "etiqueta_recordada", _recordada)
    await _asegurar_foto(
        texto, "584240000000", mensaje, ejecutar,
        puede_fotos=True, hubo_media=False, historial=historial,
    )
    return llamadas, consultas


async def test_la_red_manda_la_foto_de_la_masa_recordada(monkeypatch):
    """EL CASO REAL POR LA PUERTA DE LA RED: turno C, y la etiqueta viaja igual."""
    llamadas, consultas = await _correr_red(
        monkeypatch, mensaje=MENSAJE_C, historial=HISTORIAL_REAL
    )
    assert llamadas == [
        ("enviar_fotos_producto", {"nombre": COMPUESTO, "etiqueta": "platano"})
    ]
    assert consultas and consultas[0][2] == HISTORIAL_REAL, "el historial tiene que llegar"


async def test_la_red_no_consulta_la_memoria_si_el_turno_actual_ya_eligio(monkeypatch):
    """Precedencia, a nivel de cableado: con la versión en el mensaje de ahora, la memoria ni
    se toca (y el resultado es el del turno actual, no el del historial)."""
    llamadas, consultas = await _correr_red(
        monkeypatch, mensaje="mejor la de yuca", historial=HISTORIAL_REAL
    )
    assert llamadas == [("enviar_fotos_producto", {"nombre": COMPUESTO, "etiqueta": "yuca"})]
    assert consultas == [], "el turno actual manda: no hay nada que recordar"


async def test_la_red_sin_memoria_llama_como_siempre(monkeypatch):
    """Sin elección que recordar, la llamada sale SIN `etiqueta`: exactamente lo de hoy."""
    llamadas, _ = await _correr_red(
        monkeypatch, mensaje="dame 1 paquete", historial=[{"role": "user", "content": "hola"}]
    )
    assert llamadas == [("enviar_fotos_producto", {"nombre": COMPUESTO})]


# ══════════════════════════════════════════════════════════════════════════════════
# EL ENVOLTORIO CON BD (`etiqueta_recordada`): atajos sin consultar, y a prueba de fallos
# ══════════════════════════════════════════════════════════════════════════════════

class _Resultado:
    def __init__(self, filas):
        self._filas = filas

    def scalars(self):
        return self

    def all(self):
        return self._filas


class _Sesion:
    def __init__(self, filas):
        self._filas = filas

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _stmt):
        return _Resultado(self._filas)


def _factory_de(filas):
    """Lo que se falsea es `get_session_factory` — que devuelve la FÁBRICA, no la sesión."""
    return lambda: (lambda: _Sesion(filas))


def _espiar_bd(monkeypatch):
    """Falsea `get_session_factory` para CONTAR los intentos de consulta, no solo para romperla.

    🔴 La primera versión de esto solo hacía `raise` y miraba el resultado — y NO servía: el
    `except Exception` del envoltorio se traga la excepción y devuelve None igual, así que el
    test pasaba con los atajos anulados. Se descubrió al revertir. Lo que hay que mirar es
    CUÁNTAS veces se abrió la puerta de la BD.
    """
    intentos: list[str] = []

    def _explota():
        intentos.append("consulta")
        raise RuntimeError("la BD no se toca en este caso")

    monkeypatch.setattr("app.agent.tools.get_session_factory", _explota)
    return intentos


@pytest.mark.parametrize(("nombre", "mensaje", "historial"), [
    (COMPUESTO, MENSAJE_C, None),              # sin historial no hay nada que mirar
    (COMPUESTO, MENSAJE_C, []),
    ("Pan Keto", MENSAJE_C, HISTORIAL_REAL),   # producto simple: no tiene versiones
    (COMPUESTO, "mejor la de yuca", HISTORIAL_REAL),   # el turno actual ya eligió
    (COMPUESTO, "yuca o platano?", HISTORIAL_REAL),    # …o ya dudó
])
async def test_los_atajos_no_consultan_la_bd(monkeypatch, nombre, mensaje, historial):
    """El caso normal (producto sin versiones) no puede pagar una consulta por esta mejora."""
    intentos = _espiar_bd(monkeypatch)
    assert await etiqueta_recordada(nombre, mensaje, historial) is None
    assert intentos == [], "no hay nada que recordar: la BD ni se toca"


async def test_el_envoltorio_lee_el_catalogo_y_recuerda(monkeypatch):
    monkeypatch.setattr("app.agent.tools.get_session_factory", _factory_de(CATALOGO))
    assert await etiqueta_recordada(COMPUESTO, MENSAJE_C, HISTORIAL_REAL) == "platano"


async def test_si_la_bd_falla_se_sigue_como_hoy(monkeypatch):
    """Sin catálogo no se puede saber si hubo cambio de producto ⇒ no se recuerda nada. Las
    fotos generales nunca son una mentira; una foto equivocada sí."""
    intentos = _espiar_bd(monkeypatch)
    assert await etiqueta_recordada(COMPUESTO, MENSAJE_C, HISTORIAL_REAL) is None
    assert intentos == ["consulta"], "aquí SÍ tenía que intentarlo (y tragarse el fallo)"


# ══════════════════════════════════════════════════════════════════════════════════
# POR LA PUERTA REAL (`responder`, modo uno): los tres turnos del caso medido
# ══════════════════════════════════════════════════════════════════════════════════

ACTIVAS = frozenset({
    "ver_catalogo", "info_producto", "enviar_catalogo", "pedir_ayuda", "enviar_fotos_producto",
})


@pytest.fixture(autouse=True)
def modo_uno(monkeypatch):
    """Solo se falsean las lecturas de configuración (molde de test_asegurar_foto.py)."""

    async def _config():
        return "uno", "modelo/uno", "modelo/uno"

    async def _modelo():
        return "modelo/uno"

    async def _activas():
        return ACTIVAS

    async def _partes(nombre_cliente, telefono, *, activas=None, quien="uno"):
        return ("Eres Alejandra. CATÁLOGO (para ti): Empanadas — $14.00", "Hoy es domingo.")

    monkeypatch.setattr(ag, "leer_config_agente", _config)
    monkeypatch.setattr(ag, "leer_modelo_ia", _modelo)
    monkeypatch.setattr(ag, "leer_tools_activas", _activas)
    monkeypatch.setattr(ag, "construir_partes_prompt", _partes)
    return None


async def _correr_turno(monkeypatch, *, mensaje, historial):
    llamadas: list[tuple[str, dict]] = []

    async def _enfocado(texto):
        return COMPUESTO

    async def _mostrada(telefono, nombre):
        return False

    async def _recordada(nombre, mensaje_actual, hist):
        return etiqueta_recordada_en(nombre, mensaje_actual, hist, CATALOGO)

    monkeypatch.setattr(ag, "producto_enfocado", _enfocado)
    monkeypatch.setattr(ag, "media_ya_mostrada", _mostrada)
    monkeypatch.setattr(ag, "etiqueta_recordada", _recordada)

    async def llm(messages, tools, model):
        return {"choices": [{"message": {"role": "assistant", "content": CIERRE}}]}

    async def ejecutar(nombre, args, telefono):
        llamadas.append((nombre, args))
        return {"enviadas": 1, "producto": COMPUESTO}

    salida = await ag.responder(
        "584240000000", mensaje, list(historial), "Ana", llm=llm, ejecutar=ejecutar,
    )
    return salida, llamadas


async def test_por_la_puerta_real_el_turno_C_manda_solo_la_de_platano(monkeypatch):
    """La conversación medida entera: "de platano" → "que relleno hay?" → "de carne mechada,
    1 paquete" ⇒ sale la foto etiquetada de plátano, y el texto no se toca."""
    salida, llamadas = await _correr_turno(
        monkeypatch, mensaje=MENSAJE_C, historial=HISTORIAL_REAL
    )
    assert ("enviar_fotos_producto", {"nombre": COMPUESTO, "etiqueta": "platano"}) in llamadas
    assert salida == CIERRE, "la red suma una foto: el texto JAMÁS se toca"


async def test_por_la_puerta_real_tras_cambiar_de_producto_no_se_arrastra(monkeypatch):
    """El mismo turno, pero con un Quesillo por medio: sin etiqueta, como hoy."""
    historial = [
        *HISTORIAL_REAL,
        {"role": "user", "content": "y tienen Quesillo?"},
        {"role": "assistant", "content": "Sí 💚"},
    ]
    _, llamadas = await _correr_turno(monkeypatch, mensaje="dame 1", historial=historial)
    assert ("enviar_fotos_producto", {"nombre": COMPUESTO}) in llamadas
