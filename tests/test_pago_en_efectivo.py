"""EL 20% Y EL DELIVERY GRATIS — la cuenta del pago en EFECTIVO en dólares.

**Regla vigente** (plantilla de negocio de Maired, 2026-08-22, repetida en sus TRES apartados de
pago): *"20 % de descuento sobre los productos y delivery gratis en cualquier zona atendida"*,
pagando en **dólares físicos**.

🔴 POR QUÉ ESTE FICHERO EXISTE. Hasta hoy la regla era la CONTRARIA —el 20% no tocaba el flete—
y estaba defendida así en el código: *"si se aplicara al total, la dueña estaría pagando el
delivery de su bolsillo en CADA venta"*. La plantilla decide justo eso, a propósito: la casa
asume el flete como palanca para cobrar en efectivo (sin comisión, sin reverso, billete en mano).
Al cambiarla, **los 515 tests siguieron en verde**: nadie la fijaba. El único que la afirmaba era
`scripts/probar_delivery.py`, que necesita Postgres y por tanto **no corre en el CI, que es la
puerta que valida ANTES de desplegar**. O sea: se podía invertir la cuenta del dinero, empujar, y
que la puerta no dijera nada. Eso es lo que cierra este fichero.

⚠️ La mitad de los casos son de la cuenta que ya NO va, escritos como tal: si alguien "arregla"
esto de vuelta creyendo que es un bug, estos tests se ponen rojos y le explican por qué (L36 —
los bancos codifican decisiones de diseño; léelos antes de deshacerlas).
"""

from decimal import Decimal

import pytest

from app.agent.tools import monto_en_efectivo

# El pedido REAL que disparó la revisión (conversación del 2026-08-22, pedido 1483 del taller):
# Galletas New York $14 + envío a Barquisimeto centro $3.
GALLETAS, ENVIO_CENTRO, ENVIO_OESTE = Decimal("14"), Decimal("3"), Decimal("5")


# ══════════════════════════════════════════════════════════════════════════════════
#  LA CUENTA
# ══════════════════════════════════════════════════════════════════════════════════

def test_el_caso_real_del_22_de_agosto():
    """$14 de producto + $3 de envío ⇒ $11.20 en efectivo (y NO $14.20, que era lo de antes)."""
    assert monto_en_efectivo(GALLETAS + ENVIO_CENTRO, ENVIO_CENTRO) == Decimal("11.20")


def test_el_delivery_no_se_suma():
    """La zona no cambia lo que paga quien va en efectivo: el flete es de la casa."""
    centro = monto_en_efectivo(GALLETAS + ENVIO_CENTRO, ENVIO_CENTRO)
    oeste = monto_en_efectivo(GALLETAS + ENVIO_OESTE, ENVIO_OESTE)
    assert centro == oeste == Decimal("11.20")


def test_lo_que_le_cuesta_a_la_duena_esta_medido():
    """El flete regalado sale de su bolsillo: $3 en el centro, $5 en el oeste. Que quede escrito
    en un test y no solo en un comentario — es el número que Maired decidió asumir.

    ⚠️ La primera versión de este test comparaba `monto_en_efectivo(...) + envio` contra
    `monto_en_efectivo(...)` y afirmaba que difieren en `envio`: una TAUTOLOGÍA, cierta pase lo
    que pase. Se delató sola al revertir la fórmula — fue la única que **no** se puso roja. Ahora
    compara contra la regla vieja escrita a mano, que es lo que de verdad hay que vigilar."""
    for envio in (ENVIO_CENTRO, ENVIO_OESTE):
        total = GALLETAS + envio
        regla_vieja = ((total - envio) * Decimal("0.80")).quantize(Decimal("0.01")) + envio
        assert regla_vieja - monto_en_efectivo(total, envio) == envio


def test_sin_envio_es_solo_el_20_por_ciento():
    """Retiro en La Mendera: no hay flete que perdonar, solo el descuento."""
    assert monto_en_efectivo(GALLETAS, Decimal("0")) == Decimal("11.20")


def test_envio_none_no_revienta():
    """Un pedido sin zona todavía tiene `costo_envio` en None. No puede tumbar el cobro."""
    assert monto_en_efectivo(GALLETAS, None) == Decimal("11.20")


@pytest.mark.parametrize("total,envio,esperado", [
    (Decimal("23"), Decimal("3"), Decimal("16.00")),
    (Decimal("17"), Decimal("3"), Decimal("11.20")),
    (Decimal("10"), Decimal("0"), Decimal("8.00")),
    (Decimal("7.50"), Decimal("0"), Decimal("6.00")),
    # Redondeo a 2 decimales: 13.33 × 0,80 = 10.664 ⇒ 10.66
    (Decimal("13.33"), Decimal("0"), Decimal("10.66")),
])
def test_la_cuenta_en_varios_montos(total, envio, esperado):
    assert monto_en_efectivo(total, envio) == esperado


def test_acepta_float_y_str_sin_perder_centavos():
    """`costo_envio` y `total` llegan como Decimal de SQLAlchemy, pero el carril del comprobante
    los pasa por float. La cuenta del dinero no puede depender del tipo con que la llamen."""
    assert monto_en_efectivo(17.0, 3.0) == Decimal("11.20")
    assert monto_en_efectivo("17", "3") == Decimal("11.20")


# ══════════════════════════════════════════════════════════════════════════════════
#  LO QUE YA NO VA — las dos cuentas que estuvieron en juego y perdieron
# ══════════════════════════════════════════════════════════════════════════════════

def test_ya_no_se_cobra_el_flete_completo():
    """La regla vieja: productos×0,80 + envío = $14.20. Fue correcta hasta el 2026-08-22."""
    total, envio = GALLETAS + ENVIO_CENTRO, ENVIO_CENTRO
    vieja = ((total - envio) * Decimal("0.80")).quantize(Decimal("0.01")) + envio
    assert vieja == Decimal("14.20")
    assert monto_en_efectivo(total, envio) != vieja


def test_nunca_fue_el_20_por_ciento_del_total():
    """La lectura intuitiva —20% sobre los $17— da $13.60 y NO es la regla: el descuento es
    sobre los productos. Con el flete gratis encima, quien paga en efectivo paga menos aún."""
    total, envio = GALLETAS + ENVIO_CENTRO, ENVIO_CENTRO
    del_total = (total * Decimal("0.80")).quantize(Decimal("0.01"))
    assert del_total == Decimal("13.60")
    assert monto_en_efectivo(total, envio) != del_total


# ══════════════════════════════════════════════════════════════════════════════════
#  LAS DOS PUERTAS TIENEN QUE DAR LO MISMO
# ══════════════════════════════════════════════════════════════════════════════════

def test_cobrar_y_comprobar_usan_la_misma_funcion():
    """El bug que esto previene: si `generar_datos_pago` cobra $11.20 y `registrar_comprobante`
    espera $14.20, el cliente paga bien y su comprobante sale "no cuadra" — y el bot deja de
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
