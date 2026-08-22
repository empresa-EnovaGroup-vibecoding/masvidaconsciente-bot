"""LA FICHA CUATRO VECES — lo que más repitió Maired en sus reportes.

**MEDIDO** (2026-08-22, prueba de Maired, y ya CON la regla del prompt puesta esa misma tarde):
*"son versión mini de las galletas New York, duran 2 semanas y son aptas para diabéticos"*
apareció en los turnos de las **22:50, 22:55, 22:56 y 23:04**. Cuatro veces en una conversación.

Ella lo reportó dos veces, con dos días de diferencia:
  · *"repite mucho… me lo repitió tres veces, donde es demasiado texto innecesario"*
  · *"Repite mucho son las galletas mini Nueva York y duran dos semanas y son para diabéticos"*

**La regla del prompt no bastó** — igual que con el domingo. Por eso esto es código: se parte el
mensaje en frases y se tira la que ya se dijo, literal, antes en la conversación.

⚠️ La mitad de este fichero son los casos que NO debe tocar. Una red que recorta de más deja al
bot mudo o le borra un precio, y eso es peor que repetirse.
"""

from app.agent.agent import _sin_ficha_repetida

FICHA = "Son versión mini de las galletas New York, duran 2 semanas y son aptas para diabéticos"
HIST = [
    {"role": "user", "content": "Las mini."},
    {"role": "assistant", "content": f"Perfecto, te preparo las Mini New York. {FICHA}."},
    {"role": "user", "content": "Dame 1"},
]


def test_la_ficha_ya_dicha_se_quita():
    texto = f"Listo, 1 paquete de Mini New York. {FICHA}.\nTe lo dejo para el lunes."
    out = _sin_ficha_repetida(texto, HIST)
    assert "duran 2 semanas" not in out, "repitió la ficha por segunda vez"
    assert "Te lo dejo para el lunes" in out, "se llevó por delante lo que sí era nuevo"


def test_lo_nuevo_se_respeta():
    texto = "Listo. Te lo dejo para el lunes.\nEn qué zona estás?"
    assert _sin_ficha_repetida(texto, HIST) == texto


def test_sin_historial_no_toca_nada():
    texto = f"Perfecto. {FICHA}."
    assert _sin_ficha_repetida(texto, []) == texto
    assert _sin_ficha_repetida(texto, None) == texto


# ══════════════════════════════════════════════════════════════════════════════════
#  LOS TRES FRENOS
# ══════════════════════════════════════════════════════════════════════════════════

def test_las_frases_cortas_se_repiten_con_naturalidad():
    """"Claro que sí" o "Cuántos quieres?" son conversación normal, no ficha."""
    hist = [{"role": "assistant", "content": "Claro que sí. Cuántos quieres?"}]
    texto = "Claro que sí. Cuántos quieres?"
    assert _sin_ficha_repetida(texto, hist) == texto


def test_NUNCA_toca_una_frase_con_cifras():
    """🔴 El carril del dinero es intocable: un precio o un total repetido es CORRECTO, y
    borrarlo dejaría al cliente sin saber cuánto paga."""
    hist = [{"role": "assistant", "content": "El paquete de Mini New York te queda en 14 dolares."}]
    texto = "El paquete de Mini New York te queda en 14 dolares."
    assert _sin_ficha_repetida(texto, hist) == texto


def test_si_al_limpiar_no_queda_nada_se_devuelve_el_original():
    """Callar al bot es peor que repetirse: si el mensaje entero era la ficha, sale igual."""
    texto = f"{FICHA}."
    assert _sin_ficha_repetida(texto, HIST) == texto


def test_fail_open_ante_cualquier_fallo():
    """Un historial con la forma rara no puede tumbar la respuesta."""
    texto = "Te lo dejo para el lunes."
    assert _sin_ficha_repetida(texto, [{"rol": "malo"}, None]) == texto
