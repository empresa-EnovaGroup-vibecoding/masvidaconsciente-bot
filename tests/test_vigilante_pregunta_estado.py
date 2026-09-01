"""EL VIGILANTE PREGUNTA-vs-ESTADO (rama D) — lo que IMPIDE, no lo que sugiere.

La última pieza del plan "que no repregunte": las ramas #6 y C INYECTAN lo ya elegido como
estado, pero eso es prompt y el prompt SUGIERE. Si el modelo repregunta u ofrece la alternativa
igual, este vigilante lo IMPIDE antes de que el mensaje salga — regaño [SISTEMA] + una redacción
nueva. No mata el texto (a la segunda sale; insistir no es mentir), y hereda la regla de los
guardias del 1-sep: si el cliente acaba de PEDIR o CAMBIAR ese dato, el bot RESPONDE y no dispara.

Dos capas de prueba: la PIEZA (`_reabre_eleccion_ya_hecha`, pura) y el CARRIL (`responder()`
real, con el vigilante en su sitio del bucle).
"""
import pytest

import app.agent.agent as ag
from app.agent.agent import _reabre_eleccion_ya_hecha

# Estado del hilo: el cliente ya eligió estas cosas.
HILO_MASA = [("Empanadas de masa de yuca o de masa de plátano", "yuca")]
VAR_SABOR = [("Empanadas de masa de yuca o de masa de plátano", "sabor", "carne mechada")]
VAR_TAMANO = [("Torta baja en carbohidratos", "tamaño", "250g")]


# ══════════════════════════════════════════════════════════════════════════════════
#  1. LA PIEZA — SABOR ya elegido
# ══════════════════════════════════════════════════════════════════════════════════

def test_repreguntar_el_sabor_ya_elegido_dispara():
    assert _reabre_eleccion_ya_hecha(
        "¿De qué relleno la quieres?", [], VAR_SABOR, "para el sábado"
    ) == "el relleno"
    assert _reabre_eleccion_ya_hecha(
        "listo. ¿de qué sabor?", [], VAR_SABOR, "ok"
    ) == "el sabor"


def test_confirmar_el_sabor_elegido_NO_dispara():
    """Mencionar SOLO lo elegido es confirmación legítima, no reapertura."""
    assert _reabre_eleccion_ya_hecha(
        "Perfecto, las de carne mechada entonces. ¿Para cuándo?", [], VAR_SABOR, "para el sábado"
    ) is None


def test_si_el_cliente_pregunto_el_sabor_el_bot_RESPONDE_no_reabre():
    """Regla de los guardias: si el cliente pidió el dato, contestarlo no es reabrir. El
    borrador ES una pregunta de relleno (la detecta `_dato_opcional_pedido`); lo único que lo
    absuelve es que el cliente acaba de preguntar por los rellenos."""
    borrador = "Claro, ¿de qué relleno la preparo?"
    # Sin que el cliente lo pida, ese borrador SÍ dispara (control del test):
    assert _reabre_eleccion_ya_hecha(borrador, [], VAR_SABOR, "para el sábado") == "el relleno"
    # Pero si el cliente acaba de preguntar los rellenos, es RESPUESTA, no reapertura:
    assert _reabre_eleccion_ya_hecha(
        borrador, [], VAR_SABOR, "¿qué rellenos tienes?"
    ) is None


def test_sin_sabor_elegido_no_hay_nada_que_reabrir():
    """La 1ª pregunta de un dato faltante NO se toca (no hay elección vigente)."""
    assert _reabre_eleccion_ya_hecha("¿De qué relleno?", [], [], "quiero empanadas") is None


# ══════════════════════════════════════════════════════════════════════════════════
#  2. LA PIEZA — VERSIÓN (masa) ya elegida
# ══════════════════════════════════════════════════════════════════════════════════

def test_ofrecer_la_otra_masa_dispara():
    """El caso real de la masa: ya eligió yuca y el bot ofrece 'yuca o plátano'."""
    assert _reabre_eleccion_ya_hecha(
        "Puedes elegir la masa de yuca o de plátano, ¿cuál prefieres?",
        HILO_MASA, [], "para el sábado",
    ) == "la versión (masa) ya elegida"


def test_confirmar_la_masa_elegida_NO_dispara():
    assert _reabre_eleccion_ya_hecha(
        "Perfecto, las de yuca. ¿Cuántos paquetes?", HILO_MASA, [], "para el sábado"
    ) is None


def test_si_el_cliente_cambio_la_masa_el_bot_responde():
    """El cliente cambió a plátano en su mensaje: el bot lo confirma, no reabre."""
    assert _reabre_eleccion_ya_hecha(
        "Listo, mejor las de plátano entonces.", HILO_MASA, [], "no, mejor de plátano",
    ) is None


# ══════════════════════════════════════════════════════════════════════════════════
#  3. LA PIEZA — TAMAÑO ya elegido
# ══════════════════════════════════════════════════════════════════════════════════

def test_preguntar_el_tamano_ya_elegido_dispara():
    assert _reabre_eleccion_ya_hecha(
        "¿De qué tamaño la quieres?", [], VAR_TAMANO, "para el sábado"
    ) == "el tamaño ya elegido"


def test_confirmar_el_tamano_NO_dispara():
    assert _reabre_eleccion_ya_hecha(
        "Perfecto, la de 250g. ¿Para cuándo?", [], VAR_TAMANO, "para el sábado"
    ) is None


def test_si_el_cliente_dijo_el_tamano_el_bot_responde():
    assert _reabre_eleccion_ya_hecha(
        "¿De qué tamaño? tenemos 250, 500 y 1kg", [], VAR_TAMANO, "de 250 porfa"
    ) is None


# ══════════════════════════════════════════════════════════════════════════════════
#  4. EL CARRIL: responder() con el vigilante en su sitio
# ══════════════════════════════════════════════════════════════════════════════════

def _msg(content: str = "") -> dict:
    return {"role": "assistant", "content": content}


@pytest.fixture(autouse=True)
def sin_bd(monkeypatch):
    async def _activas():
        return frozenset({"info_producto", "registrar_pedido", "generar_datos_pago", "pedir_ayuda"})

    async def _partes(nombre_cliente, telefono, *, activas=None, quien="uno"):
        return ("Eres Alejandra.", "Hoy es viernes.")

    async def _config_uno():
        return "uno", "modelo/x", "modelo/x"

    async def _sin_productos(texto, tope=2):
        return []

    async def _no(*a, **kw):
        return None

    async def _falso(*a, **kw):
        return False

    async def _cero(*a, **kw):
        return 0.0

    # El hilo ya eligió la masa de yuca; sin elección de tamaño/sabor por variante.
    async def _hilo(mensaje, historial):
        return [("Empanadas de masa de yuca o de masa de plátano", "yuca")]

    async def _var(mensaje, historial):
        return []

    monkeypatch.setattr(ag, "leer_tools_activas", _activas)
    monkeypatch.setattr(ag, "construir_partes_prompt", _partes)
    monkeypatch.setattr(ag, "leer_config_agente", _config_uno)
    monkeypatch.setattr(ag, "hilo_de_la_venta", _hilo)
    monkeypatch.setattr(ag, "elecciones_de_variante", _var)
    monkeypatch.setattr(ag, "productos_enfocados", _sin_productos)
    monkeypatch.setattr(ag, "media_ya_mostrada", _falso)
    monkeypatch.setattr(ag, "etiqueta_recordada", _no)
    monkeypatch.setattr(ag, "horas_de_silencio", _cero)
    monkeypatch.setattr(ag, "abrir_turno", lambda *a, **kw: None)
    monkeypatch.setattr(ag, "registrar", lambda *a, **kw: None)


HIST = [
    {"role": "user", "content": "quiero las empanadas de yuca"},
    {"role": "assistant", "content": "Perfecto, las de yuca. ¿De qué relleno?"},
    {"role": "user", "content": "de carne mechada"},
]


async def _correr(respuestas: list[dict], mensaje="para el sábado"):
    pendientes = list(respuestas)
    avisos: list[str] = []

    async def llm(messages, tools, model):
        avisos[:] = [
            str(m.get("content", "")) for m in messages
            if m.get("role") == "user" and str(m.get("content", "")).startswith("[SISTEMA]")
        ]
        return {"choices": [{"message": pendientes.pop(0) if pendientes else _msg("listo 💚")}]}

    async def ejecutar(nombre, args, telefono, *a, **kw):
        return {"ok": True}

    texto = await ag.responder("584120000000", mensaje, list(HIST), "Rosa", llm=llm, ejecutar=ejecutar)
    return texto, avisos


@pytest.mark.asyncio
async def test_carril_reabrir_la_masa_se_corrige_una_vez():
    """🔴 De punta a punta: el borrador ofrece 'yuca o plátano' con la yuca ya elegida → el
    vigilante lo corrige, y en el reintento el bot da la elección por hecha."""
    texto, avisos = await _correr([
        _msg("Puedes elegir la masa de yuca o de plátano, ¿cuál prefieres?"),
        _msg("Perfecto, las de yuca con carne mechada. ¿Cuántos paquetes?"),
    ])
    reabre = [a for a in avisos if "VOLVIENDO A PREGUNTAR" in a]
    assert len(reabre) == 1, f"el vigilante no corrigió una vez: {avisos}"
    assert "yuca" in reabre[0].lower()
    assert "[SISTEMA]" not in texto
    assert "cuántos" in texto.lower() or "paquete" in texto.lower()


@pytest.mark.asyncio
async def test_carril_si_insiste_el_texto_SALE():
    """No se mata el mensaje: si el modelo insiste en reabrir, sale igual (una pasada)."""
    texto, avisos = await _correr([
        _msg("¿La quieres de yuca o de plátano?"),
        _msg("En serio, ¿de yuca o de plátano?"),
    ])
    reabre = [a for a in avisos if "VOLVIENDO A PREGUNTAR" in a]
    assert len(reabre) == 1, "el regaño se manda UNA vez, no en bucle"
    assert texto.strip()
    assert ag.RESPUESTA_SEGURA not in texto


@pytest.mark.asyncio
async def test_carril_confirmar_no_dispara():
    """Dar la elección por hecha NO activa al vigilante: sale a la primera, sin regaño."""
    texto, avisos = await _correr([
        _msg("Perfecto, las de yuca. ¿Para cuándo las necesitas?"),
    ])
    assert [a for a in avisos if "VOLVIENDO A PREGUNTAR" in a] == []
    assert "cuándo" in texto.lower()
