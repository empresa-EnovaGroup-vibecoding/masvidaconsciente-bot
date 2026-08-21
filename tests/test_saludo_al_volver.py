"""EL SALUDO SE DEVUELVE TAMBIÉN AL VOLVER — el caso que reportó Maired el 2026-08-21.

Ella escribió *"Buenas tardes, ¿cómo estás? Me gustaría saber si tienen empanadas de plátano"* UN
DÍA después de su último mensaje. El bot le contestó el "¿cómo estás?" y **NUNCA le devolvió las
buenas tardes**: `_asegurar_saludo` solo corría si `_es_inicio_conversacion`, y el historial de Redis
(TTL 24 h) traía el turno del día anterior, así que daba False. Su queja, textual:
*"Debería de decir buenas tardes. Espejear lo que yo estoy haciendo."*

🔴 ESTE FICHERO EXISTE PORQUE DOS REVERSIONES SALIERON VERDES (R36 y R40). Los tests de
`test_redes.py` prueban las piezas en AISLAMIENTO —`_falta_devolver_saludo`, `_asegurar_saludo`— así
que quitar la condición nueva de `responder`, o hacer que `horas_de_silencio` devolviera siempre 0,
dejaba la suite entera en verde con el bug de vuelta. Es la TERCERA vez en el día que aparece el
mismo hueco (R17, R29, R36): probar la pieza no es probar el carril.
"""
import pytest

from app.agent import agent as ag

pytestmark = pytest.mark.asyncio

MSG_MAIRED = "Buenas tardes, ¿cómo estás? Me gustaría saber si tienen empanadas de plátano"
# Lo que el modelo contesta: responde el "¿cómo estás?" pero NO saluda. Es el texto REAL del turno.
SIN_SALUDO = "Muy bien, gracias a Dios 💚\n\nClaro que sí, tenemos empanadas de plátano."
# El historial que había en Redis: el turno del día ANTERIOR (por eso no era "inicio").
HIST_AYER = [
    {"role": "user", "content": "Cuales son los horarios de atención"},
    {"role": "assistant", "content": "Buenas tardes, Enova 💚 Nuestro horario es de 8 a 6."},
]


@pytest.fixture(autouse=True)
def modo_uno(monkeypatch):
    """Los bordes pinchados (config, prompt, BD) y el resto del carril REAL."""
    async def _config():
        return "uno", "m/uno", "m/uno"

    async def _modelo():
        return "m/uno"

    async def _activas():
        return set()

    async def _partes(nombre_cliente, telefono, *, activas=None, quien="uno"):
        return ("Eres Alejandra.", "Hoy es viernes.")

    async def _nada(*_a, **_k):
        return None

    async def _si(*_a, **_k):
        return True

    monkeypatch.setattr(ag, "leer_config_agente", _config)
    monkeypatch.setattr(ag, "leer_modelo_ia", _modelo)
    monkeypatch.setattr(ag, "leer_tools_activas", _activas)
    monkeypatch.setattr(ag, "construir_partes_prompt", _partes)
    monkeypatch.setattr(ag, "producto_enfocado", _nada)
    monkeypatch.setattr(ag, "media_ya_mostrada", _si)


async def _turno(monkeypatch, *, horas, historial, texto=SIN_SALUDO, mensaje=MSG_MAIRED):
    """Un turno real de `responder`, con el silencio del cliente inyectado."""
    async def _silencio(_tel):
        return horas

    monkeypatch.setattr(ag, "horas_de_silencio", _silencio)

    async def llm(messages, tools, model):
        return {"choices": [{"message": {"role": "assistant", "content": texto}}]}

    async def ejecutar(nombre, args, telefono):
        return {"ok": True}

    return await ag.responder(
        "584264399792", mensaje, list(historial), "Enova", llm=llm, ejecutar=ejecutar
    )


# ── SÍ devuelve el saludo ─────────────────────────────────────────────────────────

async def test_el_caso_DE_MAIRED_ahora_devuelve_las_buenas_tardes(monkeypatch):
    """21,5 h de silencio, historial del día anterior: el bot TIENE que saludar."""
    salida = await _turno(monkeypatch, horas=21.5, historial=HIST_AYER)
    assert "buenas" in salida.lower() or "buenos" in salida.lower(), (
        f"no devolvió el saludo: {salida[:90]!r}"
    )
    # y no repite el estado que el modelo ya había dado
    assert salida.lower().count("gracias a dios") == 1


async def test_primer_contacto_sigue_saludando(monkeypatch):
    """El control de siempre: sin historial, la red actuaba y tiene que seguir actuando."""
    salida = await _turno(monkeypatch, horas=0.0, historial=[])
    assert "buenas" in salida.lower() or "buenos" in salida.lower()


# ── NO lo devuelve (los controles: frenar de más es tan malo como frenar de menos) ──

async def test_a_los_dos_minutos_NO_saluda_otra_vez(monkeypatch):
    """Un "hola" a mitad de conversación no se responde con otro saludo: eso se lee como un bot."""
    salida = await _turno(monkeypatch, horas=0.03, historial=HIST_AYER)
    assert salida == SIN_SALUDO, f"saludó a media conversación: {salida[:90]!r}"


async def test_si_el_bot_YA_saludo_y_contesto_el_estado_no_se_añade_nada(monkeypatch):
    """El texto tiene que cubrir LAS DOS cosas que el cliente hizo (saludar y preguntar cómo
    estás). Con una sola, la red completa la otra — y eso también es correcto: lo enseñó este
    test al fallar cuando solo saludaba."""
    ya = "Hola, Enova, buenas tardes 💚 Muy bien, gracias a Dios. Claro que sí, tenemos empanadas."
    salida = await _turno(monkeypatch, horas=48.0, historial=HIST_AYER, texto=ya)
    assert salida == ya


async def test_si_saluda_pero_NO_contesta_el_estado_la_red_lo_completa(monkeypatch):
    """El caso que destapó el test de arriba: media cortesía se completa, no se deja a medias."""
    solo_saludo = "Hola, Enova, buenas tardes 💚 Claro que sí, tenemos empanadas."
    salida = await _turno(monkeypatch, horas=48.0, historial=HIST_AYER, texto=solo_saludo)
    assert "gracias a Dios" in salida


async def test_si_el_cliente_NO_saluda_no_se_fuerza_nada(monkeypatch):
    """Aunque lleve dos días callado: si entra al grano, se le contesta al grano."""
    seco = "Tenemos empanadas de plátano en paquete de 8."
    salida = await _turno(
        monkeypatch, horas=48.0, historial=HIST_AYER,
        texto=seco, mensaje="tienen empanadas de platano?",
    )
    assert salida == seco


# ══════════════════════════════════════════════════════════════════════════════════
# `horas_de_silencio` DE VERDAD (sin mockearla)
#
# 🔴 R40 lo pidió: los tests de arriba INYECTAN el silencio para poder simular "21 h después", así
# que hacer que la función real devolviera siempre 0 dejaba la suite en verde y el bug de vuelta.
# Aquí se ejercita su cuerpo contra un doble de Postgres.
# ══════════════════════════════════════════════════════════════════════════════════

class _ResUno:
    def __init__(self, v):
        self._v = v

    def scalars(self):
        return self

    def first(self):
        return self._v


class _SesionUno:
    def __init__(self, v, espia):
        self._v, self._espia = v, espia

    async def __aenter__(self):
        return self

    async def __aexit__(self, *e):
        return False

    async def execute(self, _s):
        self._espia.append("consulta")
        return _ResUno(self._v)


def _falsear_cliente(monkeypatch, ultima, espia=None):
    espia = espia if espia is not None else []
    # 🔴 Se parchea `app.agent.tools`, NO `app.services.db`: `tools.py` importa
    # `get_session_factory` a NIVEL DE MÓDULO, así que tiene su propia referencia y parchear el
    # módulo origen no la toca — los tests se iban a buscar localhost:5432 y fallaban por
    # conexión, no por la aserción. (Es el espejo del caso de `_guardar_en_panel`, que hace el
    # import DENTRO de la función y sí hay que parchear el origen.)
    monkeypatch.setattr("app.agent.tools.get_session_factory",
                        lambda: (lambda: _SesionUno(ultima, espia)))
    return espia


async def test_horas_de_silencio_mide_el_hueco_de_verdad(monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.agent.tools import horas_de_silencio

    _falsear_cliente(monkeypatch, datetime.now(UTC) - timedelta(hours=21, minutes=30))
    h = await horas_de_silencio("584264399792")
    assert 21.0 < h < 22.0, f"midió {h}h en vez de ~21,5"


async def test_horas_de_silencio_con_fecha_SIN_zona(monkeypatch):
    """Postgres puede devolver un naive según el driver: sin el `replace(tzinfo=UTC)` esto
    revienta con "can't subtract offset-naive and offset-aware datetimes" y el turno se cae."""
    from datetime import UTC, datetime, timedelta

    from app.agent.tools import horas_de_silencio

    naive = (datetime.now(UTC) - timedelta(hours=10)).replace(tzinfo=None)
    _falsear_cliente(monkeypatch, naive)
    assert 9.0 < await horas_de_silencio("584264399792") < 11.0


async def test_cliente_nuevo_da_cero_y_no_fuerza_el_saludo(monkeypatch):
    """Sin fila en `clientes` ⇒ 0.0. El fallo seguro es NO forzar: saludar dos veces se lee
    como un bot, y el primer contacto ya lo cubre `_es_inicio_conversacion`."""
    from app.agent.tools import horas_de_silencio

    _falsear_cliente(monkeypatch, None)
    assert await horas_de_silencio("584111000222") == 0.0


async def test_si_la_BD_falla_da_cero_y_NO_lanza(monkeypatch):
    from app.agent.tools import horas_de_silencio

    def _explota():
        raise RuntimeError("Postgres no responde")

    monkeypatch.setattr("app.agent.tools.get_session_factory", _explota)
    assert await horas_de_silencio("584264399792") == 0.0


async def test_telefono_vacio_no_consulta(monkeypatch):
    from app.agent.tools import horas_de_silencio

    espia = _falsear_cliente(monkeypatch, None)
    assert await horas_de_silencio("") == 0.0
    assert espia == [], "un teléfono vacío no debería tocar la BD"
