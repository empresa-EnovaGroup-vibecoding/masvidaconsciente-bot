"""Guías editables de los mensajes automáticos.

La dueña edita la INTENCIÓN de cada momento desde el panel; el agente redacta el
mensaje natural (no son plantillas fijas — respeta "agente, no bot"). Si no hay
guía editada, se usa el default. Cualquier fallo cae al default.
"""
from sqlalchemy import select

from app.models import Configuracion
from app.services.db import get_session_factory

MENSAJES_DEFAULT = {
    "msg_guia_confirmado": (
        "el pago del cliente acaba de quedar CONFIRMADO; cierra la venta con calidez, "
        "agradécele su compra y dile que coordinan la entrega"
    ),
    "msg_guia_rechazado": (
        "no se pudo verificar el pago del cliente; pídele con suavidad y sin alarmar que "
        "reenvíe el comprobante o la referencia correcta"
    ),
    "msg_guia_comprobante": (
        "el cliente acaba de enviarte el comprobante de su pago; confírmale con calidez que "
        "lo recibiste y que lo estás verificando, SIN afirmar que el pago ya quedó confirmado"
    ),
}

CLAVES_MENSAJES = list(MENSAJES_DEFAULT.keys())


async def leer_guia(clave: str) -> str:
    """Guía editada por la dueña (config) o el default. Nunca lanza."""
    default = MENSAJES_DEFAULT.get(clave, "")
    try:
        factory = get_session_factory()
        async with factory() as session:
            fila = (
                await session.execute(
                    select(Configuracion).where(Configuracion.clave == clave)
                )
            ).scalar_one_or_none()
        if fila and fila.valor and fila.valor.strip():
            return fila.valor
    except Exception:  # noqa: BLE001
        pass
    return default
