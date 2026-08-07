"""LA RED DEL BUCLE — el bot preguntando lo mismo hasta que el cliente se va.

Nació del simulacro con el bot REAL del 2026-08-06. Una clienta pidió 2 panes keto; el bot le
preguntó si era para el viernes 8 o el 15. Ella preguntó el TOTAL → el bot repitió la misma
pregunta. Ella preguntó CÓMO PAGAR → la repitió otra vez. Tres turnos, dos preguntas distintas de
ella, la misma evasiva. Y de paso quedó un "pedido fantasma": ella creía haber encargado porque el
bot le dijo "te dejo 2 panes keto", y el pedido nunca llegó a registrarse porque el bot seguía
atascado aquí.

⚠️ EL EQUILIBRIO ES TODO. Frenar de MENOS deja el bucle. Frenar de MÁS es peor: hay una regla del
prompt que ordena cerrar SIEMPRE con una pregunta, así que TODOS los turnos terminan preguntando
algo. Si la red confundiera "cerrar con pregunta" con "estar atascado", avisaría a la dueña en cada
conversación — y un detector que grita en falso se acaba ignorando (esa lección ya la pagó este
repo dos veces). Por eso la mitad de este archivo son los casos que NO deben disparar.
"""

import pytest

from app.agent.agent import _nucleo_pregunta, _pregunta_repetida, _preguntas_de


def _asis(*textos: str) -> list[dict]:
    return [{"role": "assistant", "content": t} for t in textos]


# ══════════════════════════════════════════════════════════════════════════════════
# LO QUE SÍ ES UN BUCLE
# ══════════════════════════════════════════════════════════════════════════════════

def test_el_caso_real_del_simulacro():
    """Los tres turnos exactos que se capturaron con el bot real."""
    t1 = "Listo, para el viernes te los tengo listos. ¿Es para mañana viernes 8 de agosto, o para el próximo viernes?"
    t2 = "Antes de darte el total, confirma: ¿es para mañana viernes 8 de agosto o para el próximo viernes 15?"
    t3 = "Primero confirma la fecha de entrega. ¿Es para mañana viernes 8 de agosto o para el próximo viernes 15?"
    assert _pregunta_repetida(t3, _asis(t1, t2)) is True


def test_dispara_aunque_el_modelo_reformule():
    """No se compara texto literal: el modelo nunca repite palabra por palabra."""
    previos = _asis(
        "¿Para cuándo te preparo el pedido, el viernes o el sábado?",
        "Necesito la fecha. ¿Lo preparo para el viernes o para el sábado?",
    )
    assert _pregunta_repetida("¿El viernes o el sábado te preparo el pedido?", previos) is True


def test_dos_veces_todavia_NO_es_bucle():
    """Preguntar dos veces es insistir; tres es un bucle. Frenar en la segunda sería frenar de más."""
    previos = _asis("¿Es para el viernes 8 o el viernes 15?")
    assert _pregunta_repetida("Confirma: ¿viernes 8 o viernes 15?", previos) is False


# ══════════════════════════════════════════════════════════════════════════════════
# LO QUE **NO** PUEDE DISPARAR (la mitad que protege la conversación)
# ══════════════════════════════════════════════════════════════════════════════════

def test_cerrar_siempre_con_pregunta_NO_es_un_bucle():
    """La regla del prompt ordena cerrar con pregunta. Si eso disparara, avisaría en cada chat."""
    previos = _asis(
        "El Pan Keto cuesta $25.00. ¿Cuántos paquetes te llevo?",
        "Tenemos panes, empanadas y tortillas. ¿Qué te provoca?",
    )
    assert _pregunta_repetida("Te lo tengo para el viernes. ¿Lo retiras o te lo enviamos?", previos) is False


def test_coletillas_cortas_no_cuentan():
    """'¿algo más?' turno tras turno es una muletilla de cierre, no un intento de sacar un dato."""
    previos = _asis("Listo 💚 ¿algo más?", "Perfecto. ¿algo más?")
    assert _pregunta_repetida("Anotado. ¿algo más?", previos) is False


def test_preguntas_distintas_no_disparan():
    previos = _asis(
        "¿Para cuándo te lo preparo?",
        "¿Lo retiras en La Mendera o prefieres delivery?",
    )
    assert _pregunta_repetida("¿Cuál es tu nombre completo para la nota?", previos) is False


def test_una_respuesta_sin_preguntas_nunca_dispara():
    previos = _asis("¿Es para el viernes 8 o el 15?", "¿Viernes 8 o viernes 15?")
    assert _pregunta_repetida("Listo, te lo dejo para el viernes 15 💚", previos) is False


def test_sin_historial_no_dispara():
    assert _pregunta_repetida("¿Es para el viernes 8 o el 15?", []) is False
    assert _pregunta_repetida("¿Es para el viernes 8 o el 15?", None) is False


def test_lo_que_escribe_el_CLIENTE_no_cuenta():
    """Solo se miran los turnos del bot. Si el cliente repite, el problema es otro."""
    hist = [
        {"role": "user", "content": "¿es para el viernes 8 o el 15?"},
        {"role": "user", "content": "¿viernes 8 o viernes 15?"},
    ]
    assert _pregunta_repetida("¿Es para el viernes 8 o el 15?", hist) is False


# ══════════════════════════════════════════════════════════════════════════════════
# LAS PIEZAS
# ══════════════════════════════════════════════════════════════════════════════════

def test_el_nucleo_ignora_tildes_y_relleno():
    a = _nucleo_pregunta("¿Es para el viernes 8 o para el próximo viernes 15?")
    b = _nucleo_pregunta("es para el viernes 8 o el proximo viernes 15")
    assert a == b
    assert "viernes" in a and "proximo" in a
    assert "para" not in a and "el" not in a   # relleno fuera


def test_solo_se_miran_las_preguntas():
    encontradas = _preguntas_de(
        "Te dejo 2 panes keto. ¿Para cuándo te los preparo y a qué zona te los llevo?"
    )
    assert len(encontradas) == 1
    assert "panes" not in encontradas[0]   # la afirmación no entra


@pytest.mark.parametrize("texto", ["", "   ", "Listo 💚", "¿?"])
def test_no_revienta_con_basura(texto: str):
    assert _pregunta_repetida(texto, _asis("¿hola?")) is False
