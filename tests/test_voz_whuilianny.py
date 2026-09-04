"""EL BANCO DE LA VOZ — los dos documentos de Whuilianny contra lo que de verdad corre.

Fuentes (61 notas de voz + 42 conversaciones reales de una semana, ~/Downloads):
  · "MASVIDA - Como cierra la venta Whuilianny (los audios).docx"
  · "MASVIDA - Anexo conversaciones reales (para Erwin).docx"

🔴 POR QUÉ EXISTE ESTE FICHERO. El propio documento lo pide, con estas palabras:

    "PROBARLO, no confiar y ya. Aquí es donde vale el banco del closer: le ponemos al bot esas
     tres situaciones (un cliente cariñoso, uno neutro, uno molesto) como prueba, y medimos si
     hace lo correcto. Si falla, ajustamos la regla o cambiamos de modelo. No adivinamos:
     comprobamos."

Y también dice, con honestidad, dónde está el límite de lo que se puede garantizar:

    "El tono nunca va a estar garantizado al 100% como el dinero. Lo que debe estar garantizado
     (que no invente plata, que no mienta) ya lo fuerza el código."

⚠️ ASÍ QUE ESTE BANCO NO MIDE "¿ESTUVO CÁLIDA?" — eso no lo puede medir ninguna máquina, y un
test que lo pretendiera estaría mintiendo. Mide lo que SÍ es determinista y lo que de verdad se
rompe solo: **que cada conducta que los documentos pidieron siga en el prompt, sin contradecirse,
y que le llegue al agente que puede ejecutarla.** Es exactamente el hueco por el que se colaron
las dos regresiones del 2026-08-22 (una regla que se cae al editar de al lado y nadie lo nota).

El dinero, el orden de la media y las mentiras NO se prueban aquí: los fuerza el código y ya
tienen sus propios ficheros (`test_redes.py`, `test_orden_texto_antes_de_media.py`,
`test_red_del_cierre.py`, `test_red_del_tamano.py`). Repetirlos aquí sería medir dos veces lo
mismo y no medir esto.
"""

import pytest

from app.agent.system_prompt import (
    _REGLAS,
    _aplicar_marcas,
    _filtrar_por_agente,
)
from app.agent.tools import TOOL_SCHEMAS
from app.services.tools_config import BLINDADAS, DESACTIVABLES, TOOLS


def _prompt(quien: str = "uno", activas=None) -> str:
    """Las reglas TAL COMO le llegan a ese agente, con las marcas ya resueltas."""
    return _aplicar_marcas(
        _filtrar_por_agente(_REGLAS, quien), frozenset(activas or TOOLS)
    )


UNO = _prompt("uno")          # el modo que corre HOY
VOZ = _prompt("voz")          # quien ESCRIBE (modo dos)
OPERADOR = _prompt("operador")  # quien HACE (modo dos)


# ══════════════════════════════════════════════════════════════════════════════════
#  1. LA MATRIZ DE ALINEACIÓN: cada conducta del documento tiene su sitio
# ══════════════════════════════════════════════════════════════════════════════════
#
# Una fila por conducta de la sección "PARA ERWIN — qué necesita el bot para vender así".
# Si mañana se añade una conducta al documento, esto es UNA línea más — y si alguien borra
# la regla editando de al lado (lo que pasó dos veces el 08-22), esto se pone rojo.

CONDUCTAS = [
    # (id, la cita del documento, la marca que tiene que estar en el prompt)
    ("reencuadre",
     "no es comida de dieta, es comida para salud (celíacos, diabéticos, hipertensos, autismo)",
     "COMIDA PARA SALUD"),
    ("educar-no-rebajar",
     "ante duda del producto: educar con ingredientes y origen, no rebajar",
     "EDUCA, NO REBAJES"),
    ("nada-de-descuentos",
     "y jamás improvisar una rebaja para salvar la venta",
     "JAMÁS improvises un descuento"),
    ("asumir-la-venta",
     "asumir la venta + plazo natural 'bajo pedido / para mañana'",
     "ASUME EL SÍ"),
    ("upsell-con-valvula",
     "upsell suave con válvula de escape ('no sé si quieres aprovechar…, si no, bueno')",
     "EL EXTRA SE AVISA, NO SE EMPUJA"),
    ("honestidad",
     "poder decir 'esto se me acabó / esto salió distinto' como rasgo de confianza",
     "LA HONESTIDAD VENDE MÁS QUE LA PERFECCIÓN"),
    ("cliente-molesto",
     "si el cliente está molesto o serio, NO fuerces el cariño: sé amable y resuelve",
     "CLIENTE MOLESTO, SECO O SERIO"),
    ("espejeo",
     "espeja el largo y la energía del cliente",
     "ESPEJEA al cliente"),
    ("breve",
     "una persona real responde breve (lo reportó Maired)",
     "Una persona real responde BREVE"),
    ("saludo-antes-de-la-media",
     "saluda primero, antes de enviar imágenes (lo reportó Maired)",
     "PRIMERO SALUDAS Y HABLAS; LA MEDIA SALE DETRÁS"),
    ("nada-de-plantillas",
     "las frases de Whuilianny son su sabor, no plantillas que el bot copie",
     "TUS PALABRAS, NO PLANTILLAS"),
]


@pytest.mark.parametrize("ident,cita,marca", CONDUCTAS, ids=[c[0] for c in CONDUCTAS])
def test_la_conducta_del_documento_esta_en_el_prompt(ident, cita, marca):
    assert marca in UNO, f"se perdió del prompt la conducta «{ident}» — el documento pide: {cita}"


@pytest.mark.parametrize("ident,cita,marca", CONDUCTAS, ids=[c[0] for c in CONDUCTAS])
def test_y_sigue_ahi_con_las_5_herramientas_apagables_APAGADAS(ident, cita, marca):
    """🔴 Estas son conductas de VENTA, no de herramienta: apagar las fotos o el catálogo desde
    el panel no puede dejar al bot sin saber vender. Si alguna cuelga de un `{{tool|…}}`, este
    test la caza — es el fallo que documenta `_aplicar_marcas` (PRM-18)."""
    solo_blindadas = _prompt("uno", BLINDADAS)
    assert marca in solo_blindadas, (
        f"«{ident}» desaparece al apagar las {len(DESACTIVABLES)} tools apagables"
    )


# ══════════════════════════════════════════════════════════════════════════════════
#  2. LAS TRES SITUACIONES DEL ESPEJEO (lo que el documento pide probar)
# ══════════════════════════════════════════════════════════════════════════════════

def test_el_tercer_caso_del_espejeo_existe_y_dice_lo_contrario_de_espejear():
    """🔴 EL HUECO REAL que encontró la comparación con los documentos (2026-08-22).

    La personalidad de la BD cubre DOS casos: el cliente cariñoso ("devuélveselo con
    naturalidad") y el neutro ("cálida y respetuosa, sin apodos"). El tercero —el cliente
    MOLESTO— no estaba en ninguna parte. Y la regla de al lado, ESPEJEA, dice *"adapta tu largo
    y tu ENERGÍA a los suyos"*: leída con un cliente enojado delante, eso es espejearle el
    enojo. El documento pide justo lo contrario: *"si el cliente está molesto o serio, NO
    fuerces el cariño: sé amable y resuelve"*.
    """
    linea = next(ln for ln in UNO.split("\n") if "CLIENTE MOLESTO" in ln)
    assert "NO le fuerces el cariño" in linea
    assert "espejees el enojo" in linea
    assert "RESUELVE" in linea


def test_el_caso_del_molesto_NO_se_confunde_con_un_reclamo():
    """El reclamo de verdad tiene su propio carril desde julio (`pedir_ayuda`, motivo 'reclamo'),
    y ese SÍ calla al bot. Mezclarlos haría una de dos cosas malas: escalar a la dueña cada
    cliente apurado, o dejar sin escalar un reclamo real."""
    linea = next(ln for ln in UNO.split("\n") if "CLIENTE MOLESTO" in ln)
    assert "pedir_ayuda" in linea, "el borde con el RECLAMO tiene que estar dicho en la regla"
    assert "el cliente RECLAMA de verdad" in UNO, "la regla del reclamo real sigue en su sitio"


def test_las_tres_reglas_del_espejeo_le_llegan_a_quien_ESCRIBE():
    """🔴 Es una regla de TONO, así que le toca a la VOZ. Si viviera solo en el Operador, en modo
    dos el bot escribiría igual de mal: el Operador no le escribe al cliente."""
    assert "CLIENTE MOLESTO" in VOZ
    assert "ESPEJEA al cliente" in VOZ
    assert "CLIENTE MOLESTO" not in OPERADOR, "el Operador no escribe: esto solo lo diluye"


# ══════════════════════════════════════════════════════════════════════════════════
#  3. LO QUE **NO** SE DUPLICA (L37 — el prompt ya sufre de duplicación)
# ══════════════════════════════════════════════════════════════════════════════════

def test_el_cariño_que_YA_vive_en_la_personalidad_no_se_repite_en_las_REGLAS():
    """🔴 L37, convertida en test. Al comparar el documento con la BD se vio que la mitad de lo
    que pedía YA estaba en la personalidad, palabra por palabra: *"El cariño NO lo inicias tú…
    si el cliente te habla con cariño, devuélveselo"* y *"LO VOY A PENSAR: no insistas"*.
    Escribirlo otra vez aquí metería reglas duplicadas en un prompt cuyo problema ES la
    duplicación — y encima le quitaría a Whuilianny una decisión que es SUYA (cuánto cariño).

    Lo que sí está aquí es el tercer caso, el que no es cuestión de gusto sino de no empeorar
    una conversación que ya viene mal.
    """
    bajo = _REGLAS.lower()
    assert "lo voy a pensar" not in bajo, "eso vive en la personalidad (BD), no aquí"
    assert bajo.count("mi amor") <= 1, (
        "la dosis de cariño la decide Whuilianny en la personalidad; aquí solo se dice "
        "cuándo NO lo uses"
    )


def test_la_regla_del_molesto_dice_de_donde_salen_los_otros_dos_casos():
    """Para que quien lea el prompt vea los tres casos juntos sin que estén escritos dos veces."""
    linea = next(ln for ln in UNO.split("\n") if "CLIENTE MOLESTO" in ln)
    assert "personalidad" in linea


# ══════════════════════════════════════════════════════════════════════════════════
#  4. LOS FRENOS: cada conducta nueva de venta trae el suyo
# ══════════════════════════════════════════════════════════════════════════════════

def test_el_reencuadre_no_puede_cruzar_la_raya_medica():
    """🔴 El reencuadre ("comida para salud… celíacos, diabéticos, hipertensos") roza de frente
    la regla médica. El documento lo pide y el freno va PEGADO: se puede decir para quién cocina
    el negocio y qué ES la comida; JAMÁS prometer un efecto en el cuerpo de quien escribe."""
    assert "COMIDA PARA SALUD" in UNO
    assert "EL BORDE QUE NO SE CRUZA" in UNO
    assert "NUNCA prometer un efecto en el cuerpo" in UNO
    assert "NADA DE CONSEJO MÉDICO" in UNO


def test_antiinflamatoria_sigue_apareciendo_UNA_vez_y_condicionada():
    """🔴 PRM-17, y una de las dos regresiones que cazaron los bancos el 08-22: al fusionar las
    reglas médicas se cayó el *"si la personalidad lo indica"*. No es adorno — "antiinflamatorio"
    NO es campo de ninguna ficha, así que ninguna red lo caza, y sin la condición la
    contradicción con ANTIINVENCIÓN se resuelve siempre a favor de AFIRMAR.

    (`probar_prompt_coherente` ya lo comprueba, pero ese banco necesita un contenedor vivo y
    corre DESPUÉS de desplegar; esto corre en el CI, ANTES. La regresión del 08-22 llegó a
    desplegarse justo por ese hueco.)
    """
    assert _REGLAS.lower().count("antiinflamator") == 1
    assert "si la personalidad lo indica" in _REGLAS


def test_el_upsell_va_UNA_vez_y_con_puerta_de_salida():
    """La textura propia de Whuilianny, y lo que hace que no suene a vendedora insistente:
    "no sé si quieres aprovechar…", "si no, bueno". Sin el freno, un bot que "ofrece el extra"
    lo ofrece en cada mensaje."""
    linea = next(ln for ln in UNO.split("\n") if "EL EXTRA SE AVISA" in ln)
    assert "UNA sola vez" in linea
    assert "no vuelve" in linea, "si dice que no, el tema se cae ahí mismo"


def test_la_brevedad_es_una_regla_CONCRETA_no_una_idea_vaga():
    """*"Una persona real responde breve"* (Maired). Una regla de tono solo se obedece si se
    puede comprobar contra algo: por eso el prompt lleva el umbral escrito."""
    linea = next(ln for ln in UNO.split("\n") if "BREVEDAD ante todo" in ln)
    assert "pasa de 3 líneas" in linea, "sin umbral, 'sé breve' es una idea, no una regla"
    assert "BREVEDAD ante todo" in VOZ, "la brevedad la ejecuta quien ESCRIBE"


# ══════════════════════════════════════════════════════════════════════════════════
#  5. LA REGLA DE ORO DEL DOCUMENTO: la calidez la pone el modelo, los NÚMEROS la herramienta
# ══════════════════════════════════════════════════════════════════════════════════

def test_la_voz_no_recibe_las_reglas_que_le_permitirian_inventar_dinero():
    """*"La voz vende, el texto cobra"* — el hallazgo central de los audios. Traducido al
    reparto: la calidez y el porqué los redacta el modelo; el precio, el total y el banco salen
    SIEMPRE de la herramienta. Por eso las reglas de DINERO son de ACCIÓN (`!a`, del Operador),
    no de la Voz: la Voz ni siquiera ve el catálogo."""
    assert "DINERO (regla de oro)" in OPERADOR
    assert "DINERO (regla de oro)" not in VOZ
    assert "LOS DATOS DE PAGO" in OPERADOR
    assert "LOS DATOS DE PAGO" not in VOZ
    # …pero las dos mitades sí le llegan al modo que corre hoy.
    assert "DINERO (regla de oro)" in UNO


def test_los_datos_de_pago_se_entregan_ETIQUETADOS_y_con_su_metodo():
    """Del análisis: Whuilianny los manda secos ("Datos. [cédula] [teléfono] Banesco") porque al
    otro lado hay alguien que ya sabe qué es cada número. El bot le escribe a gente que NO lo
    sabe: tres números pegados es la forma más fácil de que el cliente pague mal."""
    from app.agent.tools import _nota_cobro_metodo_elegido

    # 🔴 SE MIRA LA `nota` QUE DEVUELVE LA HERRAMIENTA, no la descripción del schema. La primera
    # versión de este test miraba el schema y pasaba… midiendo otra cosa: el schema dice de dónde
    # salen los datos, y la instrucción de ETIQUETARLOS vive en la `nota` del resultado, que es
    # lo que el modelo lee en el turno del cobro.
    nota = _nota_cobro_metodo_elegido("Pago Móvil", "pago_movil", ["Zelle"])
    assert "DI QUÉ MÉTODO ES por su nombre" in nota
    assert "cada dato con su etiqueta en su propia línea" in nota
    assert "no tres números pegados" in nota

    esquema = next(
        t for t in TOOL_SCHEMAS if t["function"]["name"] == "generar_datos_pago"
    )["function"]["description"]
    assert "UNICA fuente de los datos de pago" in esquema or "ÚNICA fuente" in esquema


# ══════════════════════════════════════════════════════════════════════════════════
#  6. LA CONTRADICCIÓN CONOCIDA CON LA PERSONALIDAD: resuelta EN VOZ ALTA
# ══════════════════════════════════════════════════════════════════════════════════

def test_la_contradiccion_de_las_FOTOS_esta_dicha_explicita():
    """🟡 P4. La personalidad de la BD dice *"# FOTOS — Solo cuando el cliente pida ver el
    producto. No mandes fotos que nadie pidió"*; `_REGLAS` dice *"ÚSALA PROACTIVA"*. Gana
    `_REGLAS` (es la capa blindada y es la decisión medida de Erwin del 08-21), pero el modelo
    recibe LAS DOS en el mismo mensaje: si no se le dice cuál manda, queda partido — y un modelo
    partido hace lo que le da la gana.

    ⚠️ Esto NO cierra el pendiente: el texto es de Whuilianny y hay que pedirle que lo alinee.
    Lo que este test garantiza es que, mientras tanto, la contradicción esté resuelta por escrito.
    """
    assert "MANDA ESTA REGLA" in UNO
    assert "solo cuando el cliente las pida" in UNO


def test_ninguna_regla_le_ordena_al_bot_algo_que_no_puede_hacer_hoy():
    """Los envíos proactivos (Whuilianny escribe primero: "ya tengo tus quesillos") están en el
    documento como ROADMAP, no como conducta: hoy necesitan plantilla aprobada de Meta. Si se
    colara al prompt, el bot prometería seguimientos que el sistema no hace."""
    bajo = UNO.lower()
    for prohibida in ("te escribo mañana", "te aviso apenas esté", "yo te escribo cuando"):
        assert prohibida not in bajo, f"el prompt promete un proactivo que Meta no permite: {prohibida}"
