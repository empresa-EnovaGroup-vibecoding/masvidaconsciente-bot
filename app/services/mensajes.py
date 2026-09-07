"""Guías editables de los mensajes automáticos.

La dueña edita la INTENCIÓN de cada momento desde el panel; el agente redacta el
mensaje natural (no son plantillas fijas — respeta "agente, no bot"). Si no hay
guía editada, se usa el default. Cualquier fallo cae al default.

⚠️ CAMBIAR UN DEFAULT DE AQUÍ NO LE CAMBIA NADA A LA DUEÑA, y por eso los HECHOS del
momento NO viajan dentro de la guía. `GET /api/mensajes` rellena las tres cajas del
panel con ESTOS textos, y `PUT /api/mensajes` guarda las tres tal como estén: basta con
que ella toque "Guardar cambios" UNA vez —aunque no haya escrito una letra— para que el
default quede COPIADO en `configuracion`, y desde ese momento `leer_guia` devuelve SU
copia, no esta. Conclusión de diseño: la guía pone el TONO (es suya, puede estar
editada), y el CÓDIGO le pega los DATOS por fuera — ver `contexto_entrega`, que llega
igual esté la guía editada o no.
"""
import logging
from datetime import date

from sqlalchemy import select

from app.models import Configuracion, ZonaEntrega
from app.services.db import get_session_factory

logger = logging.getLogger(__name__)

MENSAJES_DEFAULT = {
    "msg_guia_confirmado": (
        "el pago del cliente acaba de quedar CONFIRMADO; cierra la venta con calidez, "
        "agradécele su compra y CIERRA LA ENTREGA: si te digo cómo y cuándo la recibe, "
        "díselo con tus palabras y pregúntale en cuál FRANJA le queda mejor (nunca una hora "
        "exacta: esa la confirma la dueña según su ruta); si no lo sabes, dile que coordinan "
        "la entrega"
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


def _frase_entrega(
    zona: str | None,
    es_retiro: bool | None,
    fecha: date | None,
    franjas: list[str] | None = None,
    franja_elegida: str | None = None,
    falta_referencia: bool = False,
) -> str:
    """La frase que se le PEGA a la situación del pago confirmado. PURA (sin BD): la prueba el CI.

    🗓️ LA HORA YA NO SE PREGUNTA (6-sep, regla de negocio de Maired): el cliente elige una FRANJA
    de la lista cerrada de la dueña (`franjas`) y la hora exacta la confirma Whuilianny según su
    ruta. Con `franja_elegida` ya guardada, se le recuerda y no se repregunta. Sin franjas (los
    llamadores viejos), sale la redacción de siempre. `falta_referencia`: es delivery y no hay
    dirección — se pide en el mismo cierre.

    🔴 NI UNA CIFRA DE DINERO SALE DE AQUÍ, Y NO ES UN ESCRÚPULO: `redactar_mensaje` arma la lista
    blanca del carril del dinero con `autorizados_por_moneda(situacion)`, así que **todo monto que
    viaje en la situación queda DECIBLE ese turno**. Por eso van la zona, el retiro/delivery y la
    fecha — y JAMÁS el flete ni el total.

    🔴 Y SON DOS PAREDES, NO UNA. Lo que entra en la situación queda decible por las DOS vías:
    `autorizados_por_moneda(situacion)` para el dinero **y** `_datos_sensibles(situacion)` para
    cuentas, cédulas y teléfonos. La primera versión de esta guardia solo miraba el dinero — y
    `_CORRIDA_DIGITOS_RE` junta dígitos a través de espacios y guiones, así que una zona bautizada
    "Retiro — llamar al 0412-123 4567" habría AUTORIZADO ese número para el turno. Es exactamente
    la fuga que cerró `agent.py` ("los datos bancarios de la personalidad NO autorizan"). Ante la
    duda se cae el NOMBRE, nunca la pared: preferimos decir menos.

    La FECHA sí va, y no es dinero. Aquí es un `date` de Postgres y `_fecha_larga` la escribe en
    palabras ("sábado 8 de agosto"): no hay ni un huso horario de por medio. (El bug de la
    medianoche UTC que pinta el día ANTERIOR es del PANEL —`formatFecha` en TypeScript, que hace
    `new Date("2026-08-08")`— y allí ya se resuelve con `formatFechaSola`. Por este camino no pasa.)

    `es_retiro=None` = no se pudo saber (pedido viejo sin zona, o la dueña borró la zona y solo
    quedó el nombre congelado por el ON DELETE SET NULL de la 023): entonces se nombra el sitio
    sin afirmar retiro ni delivery. Antes que mentir, decir menos.
    """
    # Perezosos a propósito: `mensajes` no tiene por qué arrastrar el agente entero al importarse.
    from app.agent.agent import _datos_sensibles, autorizados_por_moneda
    from app.agent.tools import _fecha_larga

    nombre = (zona or "").strip()
    if nombre:
        usd, bs = autorizados_por_moneda(nombre)
        if usd or bs or _datos_sensibles(nombre):
            logger.warning(
                "La zona %r lleva una cifra de DINERO o un dato sensible en el nombre: no viaja "
                "en el aviso", nombre,
            )
            nombre = ""
    if es_retiro is True:
        como = f"es RETIRO — «{nombre}»" if nombre else "lo RETIRA él"
    elif es_retiro is False:
        como = f"es DELIVERY a «{nombre}»" if nombre else "se lo LLEVAN a su casa"
    else:
        como = f"la entrega quedó en «{nombre}»" if nombre else ""
    cuando = f"el {_fecha_larga(fecha)}" if fecha else ""
    partes = [p for p in (como, cuando) if p]
    if not partes:
        return ""  # no sabemos nada de la entrega: la situación queda EXACTAMENTE como hoy
    texto = " Y la entrega ya está acordada: " + " · ".join(partes) + ". Díselo con tus palabras"
    franja = " ".join((franja_elegida or "").split())
    if franja:
        texto += (
            f" y recuérdale que la eligió {franja}: la hora exacta se la confirma la dueña "
            "según su ruta (NO prometas una hora)."
        )
    elif franjas:
        texto += (
            " y dile cuándo hay espacio para llevárselo, con tus palabras y sin la palabra "
            "'franja' — los momentos, tal cual están escritos: " + " · ".join(franjas)
            + " (NO preguntes una hora exacta: esa la confirma la dueña según su ruta). Cuando "
            "elija, guárdalo con anotar_entrega."
        )
    else:
        texto += " y pregúntale a qué hora le queda bien."
    if falta_referencia:
        texto += (
            " Y pídele un punto de referencia de la dirección (una línea) y guárdalo con "
            "anotar_entrega: sin eso no hay a dónde llevarlo."
        )
    return texto


async def contexto_entrega(pedido) -> str:
    """LOS HECHOS DE LA ENTREGA, para pegárselos a la situación del pago confirmado.

    🔴 POR QUÉ EXISTE: el cliente pagaba, la dueña confirmaba, y el bot cerraba con "gracias,
    coordinamos la entrega" — cuando el pedido YA sabía si era retiro o delivery, en qué zona y
    para qué día. El dato estaba EN MEMORIA en `confirmar_pago`, dos líneas antes del envío, y no
    se usaba. Whuilianny no cierra así: cierra coordinando la HORA.

    Va POR FUERA de la guía a propósito (ver el aviso del principio del módulo): la guía es de la
    dueña y puede estar editada desde el panel; los hechos los pone el código y tienen que llegar
    siempre. El flujo del cobro NO se toca: esto solo cambia lo que el mensaje SABE.

    ⚠️ `pedido.entrega` (el texto libre con las palabras del cliente) NO se usa a propósito: lo
    escribe el modelo, y si algún día trae un número con marca de dinero lo estaría autorizando
    para ese turno. Aquí solo entran campos de listas CERRADAS: la zona, su `es_retiro` y la fecha.

    `pedido` llega DESPRENDIDO de su sesión (el endpoint ya cerró el bloque): solo se leen columnas
    simples, que con `expire_on_commit=False` siguen cargadas. Nunca lanza: si algo falla devuelve
    "" y el aviso sale exactamente como salía antes.
    """
    if pedido is None:
        return ""
    try:
        from app.agent.tools import _franjas_de_entrega

        es_retiro = None
        factory = get_session_factory()
        async with factory() as session:
            if pedido.zona_id is not None:
                es_retiro = (
                    await session.execute(
                        select(ZonaEntrega.es_retiro).where(ZonaEntrega.id == pedido.zona_id)
                    )
                ).scalars().first()
            franjas = await _franjas_de_entrega(session)
        # `getattr` con default: filas anteriores a la 038 y dobles de tests sin esos campos.
        franja_elegida = getattr(pedido, "entrega_franja", None)
        referencia = str(getattr(pedido, "entrega_referencia", None) or "").strip()
        falta_referencia = es_retiro is False and not referencia
        return _frase_entrega(
            pedido.zona_nombre, es_retiro, pedido.entrega_fecha,
            franjas=franjas, franja_elegida=franja_elegida, falta_referencia=falta_referencia,
        )
    except Exception:  # noqa: BLE001 — el aviso del PAGO no se cae por un adorno de la entrega
        logger.exception(
            "No se pudo armar el contexto de entrega del pedido %s", getattr(pedido, "id", None)
        )
        return ""
