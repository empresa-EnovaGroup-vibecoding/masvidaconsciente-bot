"""Mensajes de pago a partir de un evento tipado y su estado persistido, sin IA."""
import logging
from decimal import Decimal
from typing import Literal

from pydantic import ValidationError

from app.agent.contratos_atencion import Cerrado, MensajeConfirmado
from app.agent.resolver_atencion import elegir_frase
from app.agent.tools import _fmt_bs, _fmt_usd
from app.models import Pago, Pedido
from app.services.db import get_session_factory

logger = logging.getLogger(__name__)


class EventoPago(Cerrado):
    tipo: Literal[
        "captura", "revision", "confirmado", "rechazado", "revision_importe",
        "parcial", "sin_pedido",
    ]
    pago_id: int | None = None


async def leer_pago(telefono, pago_id):
    factory = get_session_factory()
    async with factory() as session:
        pago = await session.get(Pago, pago_id)
        pedido = await session.get(Pedido, pago.pedido_id) if pago else None
        if not pedido or pedido.cliente_telefono != telefono:
            return None
        return {
            "estado": pago.estado,
            "pedido_id": pedido.id,
            "monto_bs": pago.monto_bs,
            "monto_usd": pago.monto_usd,
            "monto_recibido": pago.monto_recibido,
        }


def _monto_texto(valor, *, en_bs: bool) -> str:
    return f"Bs {_fmt_bs(valor)}" if en_bs else _fmt_usd(valor)


async def redactar_evento(evento, telefono, historial=None, *, leer=leer_pago):
    try:
        e = EventoPago.model_validate(evento)
        if e.tipo == "captura":
            return MensajeConfirmado(elegir_frase(
                ("Puedes enviarme una captura clara del comprobante, por favor?", "Envíame el comprobante donde se vea el monto y la referencia, por favor."), historial))
        if e.tipo == "sin_pedido":
            return MensajeConfirmado(
                "Recibí tu imagen. Es el comprobante de un pago? Cuéntame en qué te ayudo."
            )
        if e.pago_id is None:
            return MensajeConfirmado("")
        pago = await leer(telefono, e.pago_id)
        if not pago:
            return MensajeConfirmado("")
        if e.tipo == "confirmado" and pago["estado"] == "confirmado":
            texto = "Tu pago está aprobado. Gracias por tu compra 💚"
            en_bs = pago.get("monto_bs") is not None
            total = pago.get("monto_bs") if en_bs else pago.get("monto_usd")
            recibido = pago.get("monto_recibido")
            if total is not None and recibido is not None:
                sobrante = Decimal(str(recibido)) - Decimal(str(total))
                if sobrante > 0:
                    texto += (
                        f" Pagaste {_monto_texto(sobrante, en_bs=en_bs)} de más: te queda ese "
                        "saldo a favor para tu próxima compra."
                    )
            return MensajeConfirmado(texto)
        if e.tipo == "rechazado" and pago["estado"] == "rechazado":
            return MensajeConfirmado("El comprobante no quedó aprobado. Vamos a revisar contigo lo que pasó.")
        if e.tipo == "parcial" and pago["estado"] == "parcial":
            en_bs = pago.get("monto_bs") is not None
            total = pago.get("monto_bs") if en_bs else pago.get("monto_usd")
            recibido = pago.get("monto_recibido")
            if total is None or recibido is None:
                return MensajeConfirmado("")
            falta = max(Decimal("0"), Decimal(str(total)) - Decimal(str(recibido)))
            return MensajeConfirmado(
                f"Recibí {_monto_texto(recibido, en_bs=en_bs)}; el total es "
                f"{_monto_texto(total, en_bs=en_bs)} y faltan "
                f"{_monto_texto(falta, en_bs=en_bs)}."
            )
        if e.tipo in {"revision", "revision_importe"} and pago["estado"] in {"reportado", "parcial"}:
            return MensajeConfirmado(elegir_frase(
                ("Gracias, recibí tu comprobante. Ya te confirmo.", "Ya tengo tu comprobante, déjame revisarlo."), historial))
        return MensajeConfirmado("")
    except (ValidationError, TypeError, ValueError):
        return MensajeConfirmado("")
    except Exception:  # noqa: BLE001 — un fallo no permite afirmar que el pago se aprobó
        logger.exception("No se pudo confirmar el evento de pago")
        return MensajeConfirmado("")
