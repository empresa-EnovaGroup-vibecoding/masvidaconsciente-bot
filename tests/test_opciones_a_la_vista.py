"""LAS OPCIONES A LA VISTA — el modelo no puede nombrar lo que no tiene delante (6-sep).

EL CASO, medido TRES veces el mismo día con Sonnet 4.6: a "tienes galletas?" el bot respondía
"de cuál relleno te llevo?" sin nombrar ninguno. Primero se culpó a la personalidad ("no recites
sabores"), después se puso la regla del prompt ("una pregunta de elección lleva sus opciones")…
y siguió igual. La causa REAL era de DATOS: las Galletas New York tenían los rellenos SOLO en la
`descripcion` libre ("rellenos: chocolate, limón pistacho, canela naranja, chocomerey") —que la
ficha inline EXCLUYE a propósito— y el campo estructurado `sabores` del tamaño estaba VACÍO. El
modelo no los veía. Una regla del prompt no puede cumplirse con un dato ausente: el código lo pone.

Lección para la clase: antes de reescribir el prompt por tercera vez, mirar QUÉ ve el modelo.
"""
import inspect

from app.agent import system_prompt as sp
from app.agent.system_prompt import _opciones_en_descripcion

GALLETAS = (
    "Harina de almendra y coco, azúcar de coco o alulosa, vainilla natural, manteca de cerdo o "
    "mantequilla de merey, huevo, sal sin fluor, bicarbonato. \n"
    "rellenos: chocolate, limón pistacho, canela naranja, chocomerey."
)


def test_rescata_la_linea_de_rellenos_de_la_descripcion():
    """🔴 El literal de las Galletas New York (producto 7 en pruebas)."""
    assert _opciones_en_descripcion(GALLETAS) == "chocolate, limón pistacho, canela naranja, chocomerey"


def test_tambien_sabores_con_dos_puntos_y_mayusculas():
    assert _opciones_en_descripcion("Torta rica.\nSABORES: limón, chocolate") == "limón, chocolate"


def test_rescata_el_relleno_dicho_en_prosa():
    """El CHOCOLATE (producto 31): 'relleno de maca merey o de mantequilla de pistacho'."""
    r = _opciones_en_descripcion("CHOCOLATE 70% CACAO relleno de maca merey o de mantequilla de pistacho")
    assert r == "maca merey o de mantequilla de pistacho"


def test_sin_rellenos_en_la_descripcion_no_inventa_nada():
    assert _opciones_en_descripcion("Harina de almendra y coco, huevo, sal.") is None
    assert _opciones_en_descripcion(None) is None
    assert _opciones_en_descripcion("   ") is None


def test_la_ficha_del_catalogo_usa_el_rescate_cuando_sabores_esta_vacio():
    """Contrato de fuente: la ficha inline pinta `v.sabores or rescate` en las DOS ramas (un
    tamaño / varios tamaños), y la etiqueta le dice al modelo para qué es."""
    src = inspect.getsource(sp._catalogo_bloque)
    assert "_opciones_en_descripcion(p.descripcion)" in src
    assert src.count("v.sabores or rescate") >= 3
    assert "para elegir (nómbralos al preguntar)" in src


def test_la_regla_del_prompt_manda_a_consultar_antes_de_preguntar_a_ciegas():
    reglas = sp._REGLAS
    assert "jamás preguntes \"de cuál\" a ciegas" in reglas
    assert "sabores/rellenos para elegir" in reglas
