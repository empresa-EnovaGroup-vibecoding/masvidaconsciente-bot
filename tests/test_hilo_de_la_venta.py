"""EL HILO DE LA VENTA: lo que el cliente YA eligió viaja al prompt como ESTADO, cada turno.

🔴 EL CASO REAL (cazado por Maired, 2026-08-31 3:50-3:54pm, taller, chat "Enova"):
la clienta dijo "Me gustarían las empanadas de yucas" (3:51) y DOS turnos después, al pedir
los rellenos, el bot contestó "Y recuerda que puedes elegir la masa de yuca o de plátano.
¿Cuál prefieres?" (3:52). Ella tuvo que repetirse: "Carne mechada. Y será de yuca".

La autopsia (5 lectores sobre el código, vías descartadas una a una): la elección SÍ estaba
en el historial que recibió el modelo (a 4 renglones del final, dentro de la ventana de 20) y
la regla SIGUE EL HILO (system_prompt línea 83) SÍ viajaba en ese mismo turno — y el modelo
repreguntó igual, porque la ficha fresca de la herramienta (consultada por los rellenos) le
puso las dos masas delante SIN ninguna marca de que una ya estaba elegida. El hueco no es de
transporte: es que la elección NUNCA se convierte en estado antes de `registrar_pedido`.

El arreglo repite el patrón que ya funcionó dos veces (la cifra en Bs de `_estado_cliente_texto`
y la memoria de fotos): el CÓDIGO destila la elección (`hilo_de_la_venta`) y la inyecta en la
parte dinámica del prompt como HECHO. El prompt sugiere; el estado pesa.

⚠️ La mitad de este archivo son los casos que NO deben fijar elección: la duda, el token
ambiguo entre dos productos, la ventana agotada, el fallo de BD. Fijar de más es inventar —
doctrina $12/$14: ante la duda, no adivinar.
"""
import inspect

import pytest

from app.agent import agent as ag
from app.agent import tools as tl

TEL = "584240000000"
COMPUESTO = "Empanadas de masa de yuca o de masa de plátano"
CATALOGO = [COMPUESTO, "Empanadas Horneadas", "Empanadas Keto", "Pan Keto", "Quesillo"]

# La conversación literal del panel (los turnos user tal cual los guarda Redis).
HISTORIAL_ENOVA = [
    {"role": "user", "content": "¿Qué precio tienen esas empanadas?"},
    {"role": "assistant", "content": (
        "Las tres están a $12 el paquete:\nEmpanadas de yuca o plátano, 8 unidades "
        "Empanadas Horneadas, 4 unidades Empanadas Keto, 4 unidades\nCuál te llevo?"
    )},
    {"role": "user", "content": "Me gustarían las empanadas de yucas"},
    {"role": "assistant", "content": (
        "Perfectas para llevar y tener en el congelador, duran hasta 3 meses. "
        "Vienen en paquete de 8 a $12.\nCuántos paquetes quieres?"
    )},
    {"role": "user", "content": "Solo 1"},
    {"role": "assistant", "content": (
        "Listo! Y los rellenos, ya los tienes en mente o quieres que te cuente qué opciones hay?"
    )},
]
MENSAJE_352 = "Dime que opciones tienes?"


# ══════════════════════════════════════════════════════════════════════════════════
# LA PIEZA: `hilo_de_la_venta_en` (pura)
# ══════════════════════════════════════════════════════════════════════════════════

def test_el_caso_de_maired_la_eleccion_sigue_viva():
    """El turno exacto de las 3:52: la elección de las 3:51 tiene que salir como hecho."""
    assert tl.hilo_de_la_venta_en(MENSAJE_352, HISTORIAL_ENOVA, CATALOGO) == [
        (COMPUESTO, "yuca")
    ]


def test_lo_ultimo_que_diga_el_cliente_manda():
    """Cambió de opinión: la elección nueva tapa la vieja (la más reciente gana)."""
    historial = [
        {"role": "user", "content": "quiero las empanadas de yuca"},
        {"role": "assistant", "content": "Buenísima elección 💚"},
        {"role": "user", "content": "mejor la de platano"},
    ]
    assert tl.hilo_de_la_venta_en("dale", historial, CATALOGO) == [(COMPUESTO, "platano")]


def test_la_duda_no_fija_eleccion_ni_deja_pasar_la_vieja():
    """NO-disparo: nombró LAS DOS en un mensaje ⇒ dudó. La duda no se resuelve por mayoría,
    y además BLOQUEA la elección anterior (si dudó, lo viejo ya no vale como hecho)."""
    historial = [
        {"role": "user", "content": "quiero las empanadas de yuca"},
        {"role": "assistant", "content": "Buenísima 💚"},
        {"role": "user", "content": "mejor la de yuca... no no, la de platano... ay no sé"},
    ]
    assert tl.hilo_de_la_venta_en("cuánto es?", historial, CATALOGO) == []


def test_la_eleccion_pelada_se_atribuye_al_unico_compuesto():
    """El caso medido del 2026-08-09: el cliente dice "de platano" PELADO (sin nombrar el
    producto). Con UN solo compuesto en juego, la elección es suya."""
    historial = [{"role": "user", "content": "de platano"}]
    assert tl.hilo_de_la_venta_en("que relleno hay?", historial, CATALOGO) == [
        (COMPUESTO, "platano")
    ]


def test_dos_compuestos_con_el_mismo_token_no_se_adivina():
    """NO-disparo: si DOS productos compuestos tienen versión "yuca", un "de yuca" pelado no
    dice de cuál habla — fijar cualquiera sería inventar. Nombrándolo, sí se atribuye."""
    catalogo = [*CATALOGO, "Arepas de yuca o de maíz"]
    pelado = [{"role": "user", "content": "de yuca porfa"}]
    assert tl.hilo_de_la_venta_en("dale", pelado, catalogo) == []
    nombrado = [{"role": "user", "content": "quiero las arepas de yuca"}]
    assert tl.hilo_de_la_venta_en("dale", nombrado, catalogo) == [
        ("Arepas de yuca o de maíz", "yuca")
    ]


def test_el_pitch_del_bot_no_borra_la_eleccion():
    """LA DIFERENCIA con `etiqueta_recordada_en` (que corta al nombrarse otro producto): la
    elección es POR PRODUCTO. Que el bot ofrezca Pan Keto después no des-elige la masa."""
    historial = [
        {"role": "user", "content": "quiero las empanadas de yuca"},
        {"role": "assistant", "content": "Buenísima 💚 y también tenemos Pan Keto, buenazo"},
        {"role": "user", "content": "dale, y cuánto sale el pan?"},
    ]
    assert tl.hilo_de_la_venta_en("ok", historial, CATALOGO) == [(COMPUESTO, "yuca")]


def test_la_ventana_se_agota():
    """NO-disparo: una elección de hace más de `_TURNOS_HILO_VENTA` turnos del cliente ya no
    es memoria, es adivinar — y el historial de Redis (20 renglones) la habría botado igual."""
    historial = [{"role": "user", "content": "quiero las empanadas de yuca"}]
    for _ in range(10):
        historial.append({"role": "user", "content": "ok"})
    assert tl.hilo_de_la_venta_en("sigo aquí", historial, CATALOGO) == []


def test_sin_productos_compuestos_no_hay_hilo():
    """NO-disparo: sin nombres con ' o ' no hay nada que elegir ni que recordar."""
    historial = [{"role": "user", "content": "quiero el quesillo de yuca"}]
    assert tl.hilo_de_la_venta_en("dale", historial, ["Quesillo", "Pan Keto"]) == []


def test_sin_historial_el_turno_actual_tambien_dicta():
    """L20 al derecho: sin historial no revienta, y la elección dicha AHORA cuenta ya."""
    assert tl.hilo_de_la_venta_en("hola", None, CATALOGO) == []
    assert tl.hilo_de_la_venta_en("quiero las de yuca", None, CATALOGO) == [
        (COMPUESTO, "yuca")
    ]


# ══════════════════════════════════════════════════════════════════════════════════
# EL CABLEADO: el envoltorio con la BD (catálogo real, fallo real)
# ══════════════════════════════════════════════════════════════════════════════════

class _Resultado:
    def __init__(self, filas):
        self._filas = filas

    def scalars(self):
        return self

    def all(self):
        return list(self._filas)


class _Fabrica:
    """get_session_factory() → fábrica; fábrica() → sesión (async context manager)."""

    def __init__(self, nombres):
        self.nombres = nombres

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, consulta):
        return _Resultado(self.nombres)


async def test_el_envoltorio_pone_el_catalogo(monkeypatch):
    fabrica = _Fabrica(CATALOGO)
    monkeypatch.setattr(tl, "get_session_factory", lambda: fabrica)
    assert await tl.hilo_de_la_venta(MENSAJE_352, HISTORIAL_ENOVA) == [(COMPUESTO, "yuca")]


async def test_ante_fallo_de_bd_no_hay_linea_y_el_turno_vive(monkeypatch):
    """NO-disparo: la línea de estado es un empujón, jamás tumba el turno (patrón L20)."""

    def _reventada():
        raise RuntimeError("postgres tosiendo")

    monkeypatch.setattr(tl, "get_session_factory", _reventada)
    assert await tl.hilo_de_la_venta(MENSAJE_352, HISTORIAL_ENOVA) == []


# ══════════════════════════════════════════════════════════════════════════════════
# POR LA PUERTA REAL (`responder`, modo uno): la línea viaja en el prompt del turno
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
        return ("Eres Alejandra. CATÁLOGO (para ti): Quesillo — $8.00", "Hoy es viernes.")

    monkeypatch.setattr(ag, "leer_config_agente", _config)
    monkeypatch.setattr(ag, "leer_modelo_ia", _modelo)
    monkeypatch.setattr(ag, "leer_tools_activas", _activas)
    monkeypatch.setattr(ag, "construir_partes_prompt", _partes)
    return None


async def _correr_turno(monkeypatch, elecciones):
    """Un turno real de `responder` con el hilo controlado. Devuelve (salida, prompts): cada
    entrada de prompts es el texto DINÁMICO del system que recibió esa llamada al modelo."""
    prompts: list[str] = []

    async def _hilo(mensaje_usuario, historial):
        return elecciones

    monkeypatch.setattr(ag, "hilo_de_la_venta", _hilo)

    TEXTO = "Los rellenos son carne mechada, pollo y queso de cabra 💚 cuál te provoca?"

    async def llm(messages, tools, model):
        prompts.append(messages[0]["content"][1]["text"])
        return {"choices": [{"message": {"role": "assistant", "content": TEXTO}}]}

    async def ejecutar(nombre, args, telefono):
        return {"ok": True}

    salida = await ag.responder(
        TEL, MENSAJE_352, list(HISTORIAL_ENOVA), "Enova", llm=llm, ejecutar=ejecutar,
    )
    return salida, prompts, TEXTO


async def test_puerta_real_la_linea_viaja_como_estado(monkeypatch):
    """El turno de las 3:52, arreglado: el system dinámico lleva el hecho "YA eligió YUCA"."""
    salida, prompts, texto = await _correr_turno(monkeypatch, [(COMPUESTO, "yuca")])
    assert prompts, "el modelo tiene que haber sido llamado"
    assert "EL HILO DE LA VENTA" in prompts[0]
    assert "YUCA" in prompts[0]
    assert "NO le vuelvas a preguntar" in prompts[0]
    # La red del dinero lee este texto: la línea va SIN cifras ni marcas de dinero.
    bloque = prompts[0].split("EL HILO DE LA VENTA", 1)[1]
    assert "$" not in bloque and "Bs" not in bloque
    assert salida == texto, "la línea de estado jamás tumba ni reescribe el turno"


async def test_puerta_real_sin_eleccion_no_hay_linea(monkeypatch):
    """NO-disparo: cliente sin elecciones ⇒ el prompt queda EXACTAMENTE como hoy."""
    salida, prompts, texto = await _correr_turno(monkeypatch, [])
    assert prompts and "EL HILO DE LA VENTA" not in prompts[0]
    assert prompts[0] == "Hoy es viernes."
    assert salida == texto


# ══════════════════════════════════════════════════════════════════════════════════
# CONTRATOS: el aviso pegado a la ficha, y la ventana separada de la de fotos
# ══════════════════════════════════════════════════════════════════════════════════

def test_la_ficha_lleva_el_recordatorio_del_hilo():
    """La ficha fresca fue el disparador del caso real (reabrió las dos masas): el aviso
    SIGUE EL HILO viaja ahora PEGADO al dato que tienta, como ya hacía ver_catalogo."""
    assert "SIGUE EL HILO" in inspect.getsource(tl.info_producto)


def test_la_ventana_del_hilo_es_suya_y_no_toca_la_de_fotos():
    """La de fotos (3) está calibrada a su costo de error; compartir la constante acoplaría
    dos decisiones distintas y un ajuste de una rompería la otra en silencio."""
    assert tl._TURNOS_HILO_VENTA > tl._TURNOS_ETIQUETA_RECORDADA
    assert tl._TURNOS_ETIQUETA_RECORDADA == 3
