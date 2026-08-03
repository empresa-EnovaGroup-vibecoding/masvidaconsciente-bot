"""EL CONTEXTO DE LA ENTREGA — que cierre la venta SIN abrirle un agujero a la pared del dinero.

El mensaje del pago confirmado ya no dice "coordinamos la entrega": dice si es retiro o delivery,
en qué zona y para qué día, y pregunta la HORA. Ese contexto lo escribe el CÓDIGO y se le pega a
la situación… y ahí está el peligro que vigila este fichero:

🔴 TODO MONTO QUE VIAJE EN LA SITUACIÓN QUEDA DECIBLE ESE TURNO. `redactar_mensaje` construye la
lista blanca del carril del dinero con `autorizados_por_moneda(situacion)`. Si el contexto de
entrega colara el flete —o cualquier cifra— el bot quedaría autorizado a soltarla en el mensaje
del pago. Por eso aquí no se comprueba "que el texto se vea bien": se le pasa el texto a las
MISMAS funciones que son la pared (`autorizados_por_moneda`, `_datos_sensibles`) y se exige que
no autoricen NADA.

Y la FECHA: es un `date` y se escribe en palabras, así que el bug del panel (`new Date("2026-08-08")`
= medianoche UTC = en Venezuela pinta el día ANTERIOR) no puede repetirse por este camino. Se
comprueba con una fecha cuyo día de la semana se sabe: el 8 de agosto de 2026 es SÁBADO.
"""

from datetime import date

import pytest

from app.agent.agent import _datos_sensibles, autorizados_por_moneda
from app.services.mensajes import _frase_entrega

SABADO = date(2026, 8, 8)


def test_retiro_nombra_la_zona_y_el_dia_y_pide_la_hora():
    texto = _frase_entrega("Retiro en La Mendera", True, SABADO)
    assert "RETIRO" in texto
    assert "La Mendera" in texto
    assert "sábado 8 de agosto" in texto  # ni viernes 7: la fecha NO se corre de día
    assert "hora" in texto


def test_delivery_dice_que_se_lo_llevan_y_a_donde():
    texto = _frase_entrega("Barquisimeto oeste", False, SABADO)
    assert "DELIVERY" in texto
    assert "Barquisimeto oeste" in texto


def test_sin_zona_pero_con_fecha_igual_sirve():
    """Un pedido viejo sin zona no puede dejar mudo el cierre: el día sí se sabe."""
    assert "sábado 8 de agosto" in _frase_entrega(None, None, SABADO)


def test_sin_nada_no_dice_nada():
    """Si no sabemos de la entrega, la situación queda EXACTAMENTE como estaba (ADITIVO)."""
    assert _frase_entrega(None, None, None) == ""


@pytest.mark.parametrize("es_retiro", [True, False, None])
@pytest.mark.parametrize(
    "zona", ["Retiro en La Mendera", "Barquisimeto oeste", "Cabudare", "Zona 2 (oeste)", None]
)
def test_la_pared_del_dinero_no_se_mueve(zona, es_retiro):
    """🔴 EL TEST QUE IMPORTA: el contexto NO autoriza ni un monto ni un dato sensible."""
    texto = _frase_entrega(zona, es_retiro, SABADO)
    assert autorizados_por_moneda(texto) == (set(), set())
    assert _datos_sensibles(texto) == set()


def test_una_zona_con_precio_en_el_nombre_pierde_el_nombre_no_la_pared():
    """Si la dueña bautiza una zona "Cabudare $3", el $3 NO puede viajar: se cae la zona.

    ⚠️ Al caerse el nombre cambia la REDACCIÓN, y eso es a propósito: sin sitio que nombrar,
    "es DELIVERY a «Cabudare»" no se puede escribir, así que sale "se lo LLEVAN a su casa". El
    HECHO (que se lo llevan) sigue llegando; lo único que se pierde es el nombre contaminado.
    """
    texto = _frase_entrega("Cabudare $3", False, SABADO)
    assert "$3" not in texto
    assert "Cabudare" not in texto
    assert autorizados_por_moneda(texto) == (set(), set())
    assert "LLEVAN" in texto  # el hecho sigue llegando, con la otra redacción


def test_una_zona_con_un_telefono_en_el_nombre_pierde_el_nombre():
    """🔴 LA SEGUNDA PARED, la que la primera versión de la guardia no miraba.

    `_CORRIDA_DIGITOS_RE` junta dígitos a través de espacios y guiones, así que una zona bautizada
    con un teléfono habría AUTORIZADO ese número para el turno — la misma fuga que ya cerró
    `agent.py` con los datos bancarios de la personalidad. La zona se cae; el hecho sobrevive.
    """
    texto = _frase_entrega("Retiro — llamar al 0412-123 4567", True, SABADO)
    assert "4567" not in texto
    assert "0412" not in texto
    assert _datos_sensibles(texto) == set()
    assert "RETIRA" in texto  # "lo RETIRA él": sin sitio que nombrar, pero el hecho llega
