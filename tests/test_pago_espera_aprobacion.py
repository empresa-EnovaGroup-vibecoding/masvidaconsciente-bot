"""EL BOT ESPERA EL «PAGO APROBADO» DE LA DUEÑA — pasos 8 y 9 de la plantilla.

**Lo pidió Maired el 2026-08-22:** *"lo de que ella confirma el pago y luego él sigue el proceso
tampoco estaba"*. Y su plantilla lo detalla:

  · Paso 8 — *"Al recibirlo, indicar que está en revisión y avisar a la dueña."*
  · Paso 9 — *"El estado «Pago aprobado» reactiva al agente. Debe confirmar con naturalidad y
    continuar la coordinación sin pedirle al cliente que repita información ya entregada."*

⚠️ **Esto INVIERTE una decisión que estaba escrita como regla dura en `CLAUDE.md` §3**: hasta hoy
el bot registraba el comprobante y **seguía la venta** de una (agradecía y coordinaba la entrega).
El cambio tiene un costo real y asumido: el cliente pasa un rato sin coordinación después de haber
pagado, hasta que la dueña entre al panel.

🔴 **POR ESO PAUSAR Y AVISAR VAN JUNTOS, y es lo más importante de este fichero.** Si el bot se
detiene y a ella no le llega el aviso, el cliente queda colgado indefinidamente — que es peor que
el comportamiento que estamos cambiando. Un test aquí vigila que el aviso no se caiga nunca.
"""

import inspect

from app.workers import tasks


def _carril_del_comprobante() -> str:
    """El código del carril, sin los comentarios (que citan el texto viejo al documentarlo)."""
    return "\n".join(
        ln for ln in inspect.getsource(tasks._procesar_comprobante).splitlines()
        if not ln.lstrip().startswith("#")
    )


def test_ya_NO_coordina_la_entrega_al_recibir_el_comprobante():
    """Lo que cambió: antes decía "coordinas la entrega/envío" en cuanto el monto cuadraba."""
    codigo = _carril_del_comprobante()
    assert "coordinas la entrega" not in codigo, (
        "volvió a coordinar la entrega al recibir el comprobante, sin esperar a la dueña"
    )


def test_le_dice_al_cliente_que_lo_esta_REVISANDO():
    """El cliente no puede quedarse sin respuesta: acaba de pagar."""
    codigo = " ".join(_carril_del_comprobante().split())
    assert "lo estás revisando" in codigo or "lo estas revisando" in codigo


def test_NO_coordines_todavia_esta_dicho_explicito():
    """El modelo necesita la prohibición explícita, no solo la ausencia de la orden."""
    codigo = " ".join(_carril_del_comprobante().split())
    assert "NO coordines todavía la entrega" in codigo


def test_AVISA_A_LA_DUENA_o_el_cliente_queda_colgado():
    """EL TEST QUE MÁS IMPORTA. Si el bot espera y ella no se entera, nadie destraba la venta."""
    codigo = _carril_del_comprobante()
    assert "_avisar_a_la_duena" in codigo, (
        "el bot se detiene a esperar la aprobación PERO no le avisa a la dueña: el cliente "
        "quedaría esperando para siempre después de haber pagado"
    )
    assert "pago_por_aprobar" in codigo, "falta el motivo con el que ella lo ve en la bandeja"


def test_el_aviso_le_dice_a_la_duena_QUE_HACER():
    """Un aviso que no dice qué hacer es un aviso que se ignora."""
    codigo = " ".join(_carril_del_comprobante().split())
    assert "Pago aprobado" in codigo, "el aviso no nombra el botón que ella tiene que pulsar"


def test_el_clic_de_la_duena_sigue_coordinando_la_entrega():
    """La otra mitad del flujo: `confirmar_pago` es lo que reactiva al bot. Si esto se rompe, el
    cliente se queda sin entrega Y sin aviso — el peor de los dos mundos."""
    from app.api import router

    fuente = inspect.getsource(router.confirmar_pago)
    assert "notificar_cliente_pago" in fuente, "el clic de la dueña ya no avisa al cliente"
    assert "contexto_entrega" in fuente, (
        "el mensaje de confirmación perdió los hechos de la entrega (zona, retiro, fecha)"
    )
