"""LA IDENTIDAD: Alejandra, la asesora — y las dos capas que se contradecían.

**LO ENCONTRÓ UNA AUDITORÍA EXHAUSTIVA** (2026-08-22, 200 requisitos de la plantilla cruzados
contra el bot vivo). Se había cambiado la personalidad de la BD para que dijera *"Alejandra, la
asesora"* —porque el documento prohíbe la palabra "asistente"— pero **`_REGLAS` seguía diciendo
"eres la asistente virtual del negocio"**.

Dos capas del mismo prompt dándole al modelo dos identidades distintas. Se escapó de la auditoría
anterior porque esa solo buscó lo que el documento pedía; nadie cruzó las capas entre sí.

⚠️ **DÓNDE ESTÁ EL LÍMITE, Y NO SE MUEVE.** El punto 7 del documento pide además que el bot
*"responda que es Whuilianny"* si le preguntan. Eso **no se aplica**: como Tech Provider oficial de
Meta, un bot que jura ser humano arriesga la cuenta de todos los clientes — y el propio documento
marca esa línea como *"debe validarse con Erwin"*. Lo que sí se aplicó es la parte compatible: que
no se autodenomine "asistente". Dice su ROL (asesora), que es verdad, y nunca niega lo que es.
"""

from app.agent.agent import frase_prohibida_siempre
from app.agent.system_prompt import _REGLAS


def test_las_dos_capas_dicen_lo_MISMO():
    """El bug: la BD decía "asesora" y el código "asistente virtual"."""
    assert "asesora" in _REGLAS.lower(), "el código dejó de nombrar su rol real"
    assert "asistente" not in _REGLAS.lower(), (
        "volvió la palabra que el documento prohíbe — y contradice a la personalidad de la BD"
    )


def test_sigue_PROHIBIDO_jurar_que_es_humana():
    """🔴 Irrenunciable: es la regla de Meta, no una preferencia de estilo. Si esto cae, el
    número del negocio (y el de todos los clientes futuros) queda expuesto."""
    assert "PROHIBIDO jurar que eres humana" in _REGLAS


def test_sigue_prohibido_decir_que_es_Whuilianny():
    """El punto 7 del documento pide justo esto y NO se aplicó. Si alguien lo cambia sin pensar,
    este test se lo recuerda: la decisión está documentada, no olvidada."""
    assert "decir que eres Whuilianny" in _REGLAS
    # Y la red de código lo frena aunque el prompt fallara
    assert frase_prohibida_siempre("Soy Whuilianny, la dueña de masvidaconsciente") is not None


def test_la_red_sigue_frenando_las_mentiras_de_identidad():
    for mentira in (
        "Soy Whuilianny, la dueña de masvidaconsciente",
        "Sí, soy una persona real",
        "No soy un bot, soy humana",
    ):
        assert frase_prohibida_siempre(mentira) is not None, f"se escapó: {mentira!r}"


def test_lo_que_SI_puede_decir_no_se_frena():
    """Decir su rol es la respuesta correcta: no miente y no se autodenomina asistente."""
    for buena in (
        "Soy Alejandra, la asesora de masvidaconsciente 💚 En qué te ayudo?",
        "Soy Alejandra 💚 quieres que te pase con Whuilianny?",
    ):
        assert frase_prohibida_siempre(buena) is None, f"frenó un mensaje correcto: {buena!r}"
