"""LA RED DE LA SALUD — no se dictamina sobre el cuerpo de alguien sin mirar la ficha.

EL CASO REAL (simulacro con el bot real, 2026-08-06). Una clienta preguntó *"¿es apta para
diabéticos la kombucha?"* y el bot respondió **"Sí, es apta"** sin llamar a ninguna herramienta,
razonando por su cuenta ("es fermentada y no lleva azúcar refinada"). La ficha dice
`apto_diabeticos = 'no'`. Le dijo que SÍ a una diabética sobre un producto marcado que NO.

Y la lección de cómo se llegó a esta red: el primer intento fue QUITAR `apto diabéticos` del
catálogo del prompt para forzar la consulta. **Salió peor** — sin el dato delante el modelo no
consultó, improvisó. Quitar información no obliga a buscarla. Por eso el dato volvió al prompt (así
el peor caso es una respuesta incompleta, nunca una FALSA) y quien obliga es esta red: en este repo
*"el prompt sugiere, el código impide"*.

⚠️ Como toda red, frenar de MÁS es tan malo como frenar de menos: si bloqueara una respuesta
honesta ("eso te lo confirmo") o una charla normal, se acabaría desactivando. La mitad de este
archivo son los casos que NO debe tocar.
"""

import pytest

from app.agent.agent import _dictamina_salud_sin_ficha

CONSULTO = True
NO_CONSULTO = False


# ══════════════════════════════════════════════════════════════════════════════════
# LO QUE SÍ TIENE QUE FRENAR
# ══════════════════════════════════════════════════════════════════════════════════

def test_el_caso_real_de_la_kombucha():
    """Palabra por palabra lo que pasó con el bot real."""
    assert _dictamina_salud_sin_ficha(
        "es apta para diabeticos la kombucha?",
        "Sí, Carmen, es apta para diabéticos. La kombucha es fermentada y no lleva azúcar refinada.",
        NO_CONSULTO,
    ) is True


@pytest.mark.parametrize("pregunta", [
    "es apto para diabeticos?",
    "mi mama es diabetica, puede comer eso?",
    "sirve para celiacos?",
    "esto es seguro para alergicos al mani?",
    "le conviene a un niño?",
    "puedo tomarlo estando embarazada?",
    "es bueno para hipertensos?",
])
def test_frena_ante_cualquier_pregunta_de_aptitud(pregunta: str):
    assert _dictamina_salud_sin_ficha(pregunta, "Sí, es apto, no hay problema.", NO_CONSULTO) is True


def test_frena_tambien_el_veredicto_negativo_sin_ficha():
    """Decir NO sin mirar también es dictaminar: podría estar negando una venta legítima."""
    assert _dictamina_salud_sin_ficha(
        "es apto para diabeticos?", "No, no es apto para diabéticos.", NO_CONSULTO
    ) is True


# ══════════════════════════════════════════════════════════════════════════════════
# LO QUE **NO** PUEDE FRENAR
# ══════════════════════════════════════════════════════════════════════════════════

def test_si_abrio_la_ficha_no_se_mete():
    """Es el camino BUENO: consultó y contesta. Frenar aquí sería frenar lo correcto."""
    assert _dictamina_salud_sin_ficha(
        "es apta para diabeticos la kombucha?",
        "No, la kombucha no es apta para diabéticos. Lleva azúcar en la fermentación.",
        CONSULTO,
    ) is False


def test_la_respuesta_honesta_no_se_frena():
    """Si NO dictamina, no hay nada que frenar: prefiere quedarse corta antes que
    bloquear al bot diciendo la verdad ('eso te lo confirmo')."""
    assert _dictamina_salud_sin_ficha(
        "es apto para diabeticos?",
        "Eso te lo confirmo enseguida y te aviso 💚",
        NO_CONSULTO,
    ) is False


@pytest.mark.parametrize(("pregunta", "respuesta"), [
    ("cuanto cuesta el pan keto?", "Cuesta $25.00. ¿Cuántos te llevo?"),
    ("tienen delivery?", "Sí, hacemos delivery a Barquisimeto centro por $3."),
    ("cuanto duran las empanadas?", "Duran 3 meses congeladas."),
    ("hola buenas", "¡Buenas tardes! ¿Qué te provoca hoy?"),
    ("me mandas el catalogo?", "Sí, ya te lo envié 💚"),
])
def test_una_conversacion_normal_nunca_la_dispara(pregunta: str, respuesta: str):
    """Si esto disparara en ventas normales, la red se acabaría desactivando."""
    assert _dictamina_salud_sin_ficha(pregunta, respuesta, NO_CONSULTO) is False


def test_hablar_de_diabetes_sin_pedir_veredicto_no_dispara():
    """'sin azúcar' es un HECHO del producto, no un dictamen sobre una persona."""
    assert _dictamina_salud_sin_ficha(
        "de que esta hecho?",
        "Está hecho con harina de almendra y azúcar de coco, sin azúcar refinada.",
        NO_CONSULTO,
    ) is False


@pytest.mark.parametrize("basura", ["", "   ", None])
def test_no_revienta_con_basura(basura):
    assert _dictamina_salud_sin_ficha(basura, basura, NO_CONSULTO) is False
