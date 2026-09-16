"""EL 20% ES SOBRE LA CUENTA COMPLETA CON DELIVERY — pago en DÓLARES.

**Regla vigente** (Whuilianny, 16-sep-2026, transmitida por Maired: *"total de la cuenta con el
delivery y descuento del 20%"*): primero se suman productos + delivery y después se descuenta 20%
al total, pagando en dólares por CUALQUIER vía —efectivo, Zelle o Binance—. Los bolívares (Pago
Móvil/transferencia) pagan el precio completo.

🔁 LA HISTORIA DEL FLETE, en cuatro actos, porque cada vuelta la defendió un test:
  · hasta el 22-ago: productos×0,80 + envío;
  · 22-ago → 7-sep: productos×0,80 y el flete GRATIS — "la casa asume el flete como palanca para
    cobrar en efectivo". Este fichero nació entonces para fijarlo, porque los 515 tests del CI no
    lo vigilaban y el único banco que sí (`probar_delivery`) no corre en el CI;
  · 7-sep: se descuenta solo el producto y se suma el envío completo;
  · 16-sep: Whuilianny aclara la fórmula definitiva: (productos + envío)×0,80.

⚠️ La mitad de los casos son de las cuentas que ya NO van, escritas como tal: si alguien "arregla"
esto de vuelta creyendo que es un bug, estos tests se ponen rojos y le explican por qué (L36 — los
bancos codifican decisiones de negocio; léelos antes de deshacerlas).
"""

from decimal import Decimal

import pytest

from app.agent.tools import monto_en_efectivo

# El pedido REAL de las dos revisiones (22-ago, pedido 1483 del taller; 7-sep, pedido de Maired en
# pruebas): Galletas New York $14 + envío a Barquisimeto centro $3.
GALLETAS, ENVIO_CENTRO, ENVIO_OESTE = Decimal("14"), Decimal("3"), Decimal("5")


# ══════════════════════════════════════════════════════════════════════════════════
#  LA CUENTA
# ══════════════════════════════════════════════════════════════════════════════════

def test_la_regla_aclarada_el_16_de_septiembre():
    """$14 de producto + $3 de envío = $17; menos 20% = $13.60."""
    assert monto_en_efectivo(GALLETAS + ENVIO_CENTRO, ENVIO_CENTRO) == Decimal("13.60")


def test_el_delivery_si_se_suma_y_la_zona_cambia_el_monto():
    """El delivery entra en la cuenta: una zona más cara todavía aumenta el total final."""
    centro = monto_en_efectivo(GALLETAS + ENVIO_CENTRO, ENVIO_CENTRO)
    oeste = monto_en_efectivo(GALLETAS + ENVIO_OESTE, ENVIO_OESTE)
    assert centro == Decimal("13.60")
    assert oeste == Decimal("15.20")
    assert oeste - centro == (ENVIO_OESTE - ENVIO_CENTRO) * Decimal("0.80")


def test_el_delivery_participa_en_el_descuento():
    """Whuilianny indicó que se suma el delivery y luego se descuenta 20% a toda la cuenta."""
    for envio in (ENVIO_CENTRO, ENVIO_OESTE):
        con = monto_en_efectivo(GALLETAS + envio, envio)
        sin = monto_en_efectivo(GALLETAS, Decimal("0"))
        assert con - sin == envio * Decimal("0.80")


def test_sin_envio_es_solo_el_20_por_ciento():
    """Retiro en La Mendera: no hay flete, solo el descuento."""
    assert monto_en_efectivo(GALLETAS, Decimal("0")) == Decimal("11.20")


def test_envio_none_no_revienta():
    """Un pedido sin zona todavía tiene `costo_envio` en None. No puede tumbar el cobro."""
    assert monto_en_efectivo(GALLETAS, None) == Decimal("11.20")


@pytest.mark.parametrize("total,envio,esperado", [
    # Caso que Maired usó para pedir la aclaración: $18 de productos + $2 de delivery.
    (Decimal("20"), Decimal("2"), Decimal("16.00")),
    (Decimal("23"), Decimal("3"), Decimal("18.40")),
    (Decimal("17"), Decimal("3"), Decimal("13.60")),
    (Decimal("19"), Decimal("5"), Decimal("15.20")),
    (Decimal("10"), Decimal("0"), Decimal("8.00")),
    (Decimal("7.50"), Decimal("0"), Decimal("6.00")),
    # Redondeo a 2 decimales: 13.33 × 0,80 = 10.664 ⇒ 10.66
    (Decimal("13.33"), Decimal("0"), Decimal("10.66")),
    (Decimal("16.33"), Decimal("3"), Decimal("13.06")),
])
def test_la_cuenta_en_varios_montos(total, envio, esperado):
    assert monto_en_efectivo(total, envio) == esperado


def test_acepta_float_y_str_sin_perder_centavos():
    """`costo_envio` y `total` llegan como Decimal de SQLAlchemy, pero el carril del comprobante
    los pasa por float. La cuenta del dinero no puede depender del tipo con que la llamen."""
    assert monto_en_efectivo(17.0, 3.0) == Decimal("13.60")
    assert monto_en_efectivo("17", "3") == Decimal("13.60")


# ══════════════════════════════════════════════════════════════════════════════════
#  LO QUE YA NO VA — las dos cuentas que estuvieron en juego y perdieron
# ══════════════════════════════════════════════════════════════════════════════════

def test_ya_no_se_regala_el_flete():
    """La regla del 22-ago al 7-sep: productos×0,80 y el envío gratis = $11.20. Whuilianny la cerró."""
    total, envio = GALLETAS + ENVIO_CENTRO, ENVIO_CENTRO
    regalado = ((total - envio) * Decimal("0.80")).quantize(Decimal("0.01"))
    assert regalado == Decimal("11.20")
    assert monto_en_efectivo(total, envio) != regalado


def test_la_regla_anterior_ya_no_aplica():
    """Del 7 al 16-sep se cobraban productos×0,80 + delivery; Whuilianny aclaró otra fórmula."""
    total, envio = GALLETAS + ENVIO_CENTRO, ENVIO_CENTRO
    anterior = ((total - envio) * Decimal("0.80") + envio).quantize(Decimal("0.01"))
    assert anterior == Decimal("14.20")
    assert monto_en_efectivo(total, envio) != anterior


# ══════════════════════════════════════════════════════════════════════════════════
#  LAS DOS PUERTAS TIENEN QUE DAR LO MISMO
# ══════════════════════════════════════════════════════════════════════════════════

def test_cobrar_y_comprobar_usan_la_misma_funcion():
    """El bug que esto previene: si `generar_datos_pago` cobra $13.60 y `registrar_comprobante`
    espera $11.20, el cliente paga bien y su comprobante sale "no cuadra" — y el bot deja de
    decirle que recibió su pago. Las dos llaman a `monto_en_efectivo`; esto lo comprueba leyendo
    el código, que es lo único que no se puede desincronizar sin que este test lo vea."""
    import inspect

    from app.agent import tools

    for funcion in (tools.generar_datos_pago, tools.registrar_comprobante):
        fuente = inspect.getsource(funcion)
        assert "monto_en_efectivo(" in fuente, (
            f"{funcion.__name__} dejó de usar la función común: la cuenta del efectivo volvió a "
            "estar duplicada y las dos puertas pueden desincronizarse"
        )
        assert 'Decimal("0.80")' not in fuente, (
            f"{funcion.__name__} volvió a escribir el 0,80 a mano en vez de usar "
            "`monto_en_efectivo`"
        )


# ══════════════════════════════════════════════════════════════════════════════════
#  EL DESGLOSE — "subtotal, descuento, delivery y total final"
# ══════════════════════════════════════════════════════════════════════════════════
#
# Maired reportó DOS VECES que el bot "saca mal la cuenta" porque veía $17 arriba y $11.20 abajo
# **sin el paso intermedio**. Con el flete cobrado el riesgo es el mismo al revés: $17 arriba y
# otro monto abajo sin que se vea la operación. El desglose es lo que lo explica.

def test_el_desglose_muestra_la_operacion_completa():
    """Con envío: productos, delivery, subtotal, descuento y total."""
    import inspect

    from app.agent import tools

    fuente = inspect.getsource(tools.generar_datos_pago)
    assert "desglose_efectivo" in fuente
    for linea in ("Productos:", "Delivery:", "Subtotal:", "Descuento 20%:", "Total en dólares:"):
        assert linea in fuente, f"falta la línea del desglose: {linea!r}"


def test_el_desglose_sale_en_el_resultado_de_la_tool():
    """Si no viaja en el return, el modelo no puede copiarlo."""
    import inspect

    from app.agent import tools

    fuente = inspect.getsource(tools.generar_datos_pago)
    assert '"desglose_efectivo": desglose_efectivo' in fuente


def test_el_descuento_del_desglose_cuadra_con_lo_que_se_cobra():
    """🔴 Lo que este test impide: que el desglose y el total se desincronicen. Un desglose que
    no suma el total es peor que no tenerlo — le da al cliente motivos para desconfiar."""
    productos, envio = Decimal("14"), Decimal("3")
    subtotal = productos + envio
    descuento = (subtotal * Decimal("0.20")).quantize(Decimal("0.01"))
    total = monto_en_efectivo(productos + envio, envio)
    assert subtotal - descuento == total, (
        f"el desglose no cuadra: {productos} + {envio} - {descuento} != {total}"
    )


# ══════════════════════════════════════════════════════════════════════════════════
#  NADA QUE LEA EL MODELO SIGUE DICIENDO QUE EL DELIVERY ES GRATIS
# ══════════════════════════════════════════════════════════════════════════════════
#
# La regla vieja no vivía en un solo sitio: estaba en la cuenta, en el pitch del cobro, en el
# desglose ("Delivery: $0 … va por nuestra cuenta") y en la voz. Misma lección que "la hora es
# muda" (SESIONES (27)): se borra de TODAS las capas, y un test lo vigila.

FRASES_DEL_REGALO = ("por nuestra cuenta", "delivery gratis", "envío gratis", "envio gratis",
                     "flete gratis", "corre por nuestra", "Delivery: $0")


def test_el_cobro_no_promete_el_delivery_gratis():
    import inspect

    from app.agent import tools

    fuente = inspect.getsource(tools.generar_datos_pago)
    halladas = [f for f in FRASES_DEL_REGALO if f.lower() in fuente.lower()]
    assert halladas == [], f"generar_datos_pago sigue regalando el flete: {halladas}"


def test_las_reglas_no_prometen_el_delivery_gratis():
    from app.agent.system_prompt import _REGLAS

    halladas = [f for f in FRASES_DEL_REGALO if f.lower() in _REGLAS.lower()]
    assert halladas == [], f"las reglas siguen regalando el flete: {halladas}"


def test_la_voz_no_promete_el_delivery_gratis():
    """El BRIEF no viaja en el repo (.gitignore, repo público): en el CI se salta; en la máquina
    de Maired vigila que la personalidad que se promueve diga la regla nueva."""
    import pathlib

    brief = pathlib.Path(__file__).resolve().parents[1] / "BRIEF-personalidad-alejandra-2026-09-06.md"
    if not brief.exists():
        pytest.skip("el BRIEF no viaja en el repo; la voz viva se revisa en pruebas")
    voz = brief.read_text(encoding="utf-8").lower()
    halladas = [f for f in FRASES_DEL_REGALO if f.lower() in voz]
    assert halladas == [], f"la voz sigue regalando el flete: {halladas}"
    assert "total de la cuenta con el delivery" in voz
