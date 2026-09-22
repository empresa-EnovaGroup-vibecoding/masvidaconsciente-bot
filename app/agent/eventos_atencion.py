"""Mensajes de pago a partir de un evento tipado y su estado persistido, sin IA."""
import logging
from typing import Literal

from pydantic import ValidationError

from app.agent.contratos_atencion import Cerrado, MensajeConfirmado
from app.agent.resolver_atencion import elegir_frase
from app.models import Pago, Pedido
from app.services.db import get_session_factory

logger = logging.getLogger(__name__)


class EventoPago(Cerrado):
    tipo: Literal["captura", "revision", "confirmado", "rechazado", "revision_importe"]
    pago_id: int | None = None


async def leer_pago(telefono, pago_id):
    factory = get_session_factory()
    async with factory() as session:
        pago = await session.get(Pago, pago_id)
        pedido = await session.get(Pedido, pago.pedido_id) if pago else None
        if not pedido or pedido.cliente_telefono != telefono:
            return None
        return {"estado": pago.estado, "pedido_id": pedido.id}


async def redactar_evento(evento, telefono, historial=None, *, leer=leer_pago):
    try:
        e = EventoPago.model_validate(evento)
        if e.tipo == "captura":
            return MensajeConfirmado(elegir_frase(
                ("Puedes enviarme una captura clara del comprobante, por favor?", "Envíame el comprobante donde se vea el monto y la referencia, por favor."), historial))
        if e.pago_id is None:
            return MensajeConfirmado("")
        pago = await leer(telefono, e.pago_id)
        if not pago:
            return MensajeConfirmado("")
        if e.tipo == "confirmado" and pago["estado"] == "confirmado":
            return MensajeConfirmado("Tu pago está aprobado. Gracias por tu compra 💚")
        if e.tipo == "rechazado" and pago["estado"] == "rechazado":
            return MensajeConfirmado("El comprobante no quedó aprobado. Vamos a revisar contigo lo que pasó.")
        if e.tipo in {"revision", "revision_importe"}:
            return MensajeConfirmado(elegir_frase(
                ("Gracias, recibí tu comprobante. Ya te confirmo.", "Ya tengo tu comprobante, déjame revisarlo."), historial))
        return MensajeConfirmado("")
    except (ValidationError, TypeError, ValueError):
        return MensajeConfirmado("")
    except Exception:  # noqa: BLE001 — un fallo no permite afirmar que el pago se aprobó
        logger.exception("No se pudo confirmar el evento de pago")
        return MensajeConfirmado("")
