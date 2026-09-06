"""UNA PREGUNTA DE ELECCIÓN LLEVA SUS OPCIONES (6-sep, lo señaló Maired en pruebas).

EL CASO (dos veces en el mismo día, con Sonnet 4.6): a "¿tienes galletas?" el bot respondió con la
foto y "¿de cuál relleno te llevo?" — SIN nombrar los rellenos. La clienta tuvo que preguntar
"¿de qué tienes?". Maired: *"¿por qué me pregunta de cuál relleno te llevo si yo no lo sé?"*.

LA CAUSA no era el modelo: la personalidad viva dice "No recites ingredientes, sabores… salvo que el
cliente lo pregunte específicamente", y las reglas del flujo le piden que el cliente ELIJA el
relleno. El modelo obedeció a las dos a la vez: pidió la elección y se calló las opciones. Esta
regla desempata: preguntar por una elección obliga a nombrar las opciones reales de ESE producto.

Es una regla del PROMPT (conducta), no una red de estilo: la frontera del 24-ago sigue en pie.
"""
from app.agent import system_prompt


def _reglas() -> str:
    return system_prompt._REGLAS if isinstance(system_prompt._REGLAS, str) else "\n".join(system_prompt._REGLAS)


def test_la_regla_existe_y_nombra_el_caso():
    reglas = _reglas()
    assert "UNA PREGUNTA DE ELECCIÓN LLEVA SUS OPCIONES" in reglas
    assert "de cuál relleno" in reglas
    assert "no se puede contestar" in reglas


def test_la_regla_vive_pegada_al_flujo_de_un_solo_paso():
    """Va dentro de 'ERES UNA CERRADORA', después de 'UN SOLO PASO A LA VEZ' y antes de 'ASUME EL
    SÍ': es parte del mismo flujo (qué producto → cuántos → …), no una regla suelta."""
    reglas = _reglas()
    i_paso = reglas.index("UN SOLO PASO A LA VEZ")
    i_opciones = reglas.index("UNA PREGUNTA DE ELECCIÓN LLEVA SUS OPCIONES")
    i_si = reglas.index("ASUME EL SÍ")
    assert i_paso < i_opciones < i_si


def test_la_regla_no_reabre_la_recitacion_de_la_ficha():
    """Nombrar las opciones que se piden elegir ≠ recitar ingredientes y duración."""
    reglas = _reglas()
    i = reglas.index("UNA PREGUNTA DE ELECCIÓN LLEVA SUS OPCIONES")
    trozo = reglas[i:i + 900]
    assert "NO contradice" in trozo and "recitar" in trozo.lower()
