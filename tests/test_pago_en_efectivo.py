"""EL 20% ES SOBRE LOS PRODUCTOS Y EL DELIVERY SE COBRA — la cuenta del pago en DÓLARES
(efectivo, Zelle o Binance).

**Regla vigente** (Whuilianny, 7-sep-2026, transmitida por Maired: *"el único descuento es el 20%;
el delivery lo paga la persona"*): 20 % de descuento sobre los PRODUCTOS, pagando en dólares por
CUALQUIER vía —efectivo, Zelle o Binance (Maired, 24-ago: el 20% se ata a la MONEDA, no a la vía)—
y el flete COMPLETO encima. Los bolívares (Pago Móvil/transferencia) pagan el precio completo.

🔁 LA HISTORIA DEL FLETE, en tres actos, porque cada vuelta la defendió un test:
  · hasta el 22-ago: productos×0,80 + envío (la cuenta de HOY, por otra razón);
  · 22-ago → 7-sep: productos×0,80 y el flete GRATIS — "la casa asume el flete como palanca para
    cobrar en efectivo". Este fichero nació entonces para fijarlo, porque los 515 tests del CI no
    lo vigilaban y el único banco que sí (`probar_delivery`) no corre en el CI;
  · 7-sep: la propia dueña cierra el regalo. La cuenta vuelve a sumar el envío.

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

def test_el_caso_real_del_7_de_septiembre():
    """$14 de producto + $3 de envío ⇒ $14.20 en dólares (y NO $11.20, que era el flete regalado)."""
    assert monto_en_efectivo(GALLETAS + ENVIO_CENTRO, ENVIO_CENTRO) == Decimal("14.20")


def test_el_delivery_si_se_suma_y_la_zona_cambia_el_monto():
    """El flete lo paga el cliente: quien va al oeste ($5) paga más que quien va al centro ($3)."""
    centro = monto_en_efectivo(GALLETAS + ENVIO_CENTRO, ENVIO_CENTRO)
    oeste = monto_en_efectivo(GALLETAS + ENVIO_OESTE, ENVIO_OESTE)
    assert centro == Decimal("14.20")
    assert oeste == Decimal("16.20")
    assert oeste - centro == ENVIO_OESTE - ENVIO_CENTRO


def test_el_flete_no_se_descuenta():
    """El 20% es sobre los PRODUCTOS. El envío entra entero, sin rebaja: la diferencia entre
    cobrar con envío y sin envío es exactamente el envío."""
    for envio in (ENVIO_CENTRO, ENVIO_OESTE):
        con = monto_en_efectivo(GALLETAS + envio, envio)
        sin = monto_en_efectivo(GALLETAS, Decimal("0"))
        assert con - sin == envio


def test_sin_envio_es_solo_el_20_por_ciento():
    """Retiro en La Mendera: no hay flete, solo el descuento."""
    assert monto_en_efectivo(GALLETAS, Decimal("0")) == Decimal("11.20")


def test_envio_none_no_revienta():
    """Un pedido sin zona todavía tiene `costo_envio` en None. No puede tumbar el cobro."""
    assert monto_en_efectivo(GALLETAS, None) == Decimal("11.20")


@pytest.mark.parametrize("total,envio,esperado", [
    (Decimal("23"), Decimal("3"), Decimal("19.00")),
    (Decimal("17"), Decimal("3"), Decimal("14.20")),
    (Decimal("19"), Decimal("5"), Decimal("16.20")),
    (Decimal("10"), Decimal("0"), Decimal("8.00")),
    (Decimal("7.50"), Decimal("0"), Decimal("6.00")),
    # Redondeo a 2 decimales: 13.33 × 0,80 = 10.664 ⇒ 10.66
    (Decimal("13.33"), Decimal("0"), Decimal("10.66")),
    (Decimal("16.33"), Decimal("3"), Decimal("13.66")),
])
def test_la_cuenta_en_varios_montos(total, envio, esperado):
    assert monto_en_efectivo(total, envio) == esperado


def test_acepta_float_y_str_sin_perder_centavos():
    """`costo_envio` y `total` llegan como Decimal de SQLAlchemy, pero el carril del comprobante
    los pasa por float. La cuenta del dinero no puede depender del tipo con que la llamen."""
    assert monto_en_efectivo(17.0, 3.0) == Decimal("14.20")
    assert monto_en_efectivo("17", "3") == Decimal("14.20")


# ══════════════════════════════════════════════════════════════════════════════════
#  LO QUE YA NO VA — las dos cuentas que estuvieron en juego y perdieron
# ══════════════════════════════════════════════════════════════════════════════════

def test_ya_no_se_regala_el_flete():
    """La regla del 22-ago al 7-sep: productos×0,80 y el envío gratis = $11.20. Whuilianny la cerró."""
    total, envio = GALLETAS + ENVIO_CENTRO, ENVIO_CENTRO
    regalado = ((total - envio) * Decimal("0.80")).quantize(Decimal("0.01"))
    assert regalado == Decimal("11.20")
    assert monto_en_efectivo(total, envio) != regalado


def test_nunca_fue_el_20_por_ciento_del_total():
    """La lectura intuitiva —20% sobre los $17— da $13.60 y NO es la regla: el descuento es
    sobre los productos y el flete va entero."""
    total, envio = GALLETAS + ENVIO_CENTRO, ENVIO_CENTRO
    del_total = (total * Decimal("0.80")).quantize(Decimal("0.01"))
    assert del_total == Decimal("13.60")
    assert monto_en_efectivo(total, envio) != del_total


# ══════════════════════════════════════════════════════════════════════════════════
#  LAS DOS PUERTAS TIENEN QUE DAR LO MISMO
# ══════════════════════════════════════════════════════════════════════════════════

def test_cobrar_y_comprobar_usan_la_misma_funcion():
    """El bug que esto previene: si `generar_datos_pago` cobra $14.20 y `registrar_comprobante`
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
# $14.20 abajo sin que se vea que el 20% fue solo a los $14. El desglose es lo que lo explica.

def test_el_desglose_muestra_las_cuatro_lineas():
    """Con envío: productos, descuento, delivery (su costo real) y total. Cuatro líneas."""
    import inspect

    from app.agent import tools

    fuente = inspect.getsource(tools.generar_datos_pago)
    assert "desglose_efectivo" in fuente
    for linea in ("Productos:", "Descuento 20%:", "Delivery:", "Total en dólares:"):
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
    descuento = (productos * Decimal("0.20")).quantize(Decimal("0.01"))
    total = monto_en_efectivo(productos + envio, envio)
    assert productos - descuento + envio == total, (
        f"el desglose no cuadra: {productos} - {descuento} + {envio} != {total}"
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
    assert "el delivery se paga completo" in voz
