"""La memoria de la conversación, con Postgres como RESPALDO de Redis.

🔴 EL PORQUÉ (fallo reportado por Maired el 2026-08-18, causa raíz hallada el 08-20):

El historial del agente vivía SOLO en Redis, con `conversacion_ttl` = 24 h. Postgres guardaba
los mensajes para siempre —la dueña los ve en el panel, la clienta los ve en su WhatsApp— pero
el agente NUNCA los leía. Pasadas 24 h de silencio el bot no perdía "algo de contexto": arrancaba
de CERO, convencido de no haber hablado nunca con esa persona.

Lo que costó: el 08-12 el bot ofreció empanadas de plátano con relleno de carne mechada, pollo o
queso de cabra. El 08-18 (5 días 15 h después) la clienta contestó *"De queso de cabra. Por favor.
Cuanto es?"* y el bot le vendió **Kéfir de Leche de cabra ($8)** con foto — "queso de cabra" es un
RELLENO, y el único producto del catálogo con "cabra" en el nombre es el Kéfir. En ese solo chat el
bot ya había arrancado sin memoria SEIS veces desde julio; era invisible porque los mensajes de
vuelta se explicaban solos ("empanadas de plátano" nombra el producto). Solo se ve cuando la
clienta contesta algo que únicamente tiene sentido con el turno anterior delante — o sea, el patrón
real de una clienta: pregunta hoy y decide en tres días.

Y lo que más pesa: el historial vacío **apaga cuatro redes de seguridad** que lo reciben por
parámetro (`_elige_entre_opciones` —la red del pitch, construida el 08-08 para exactamente este
caso—, `_es_inicio_conversacion`, `etiqueta_recordada` y `_pregunta_repetida`). Una red que mira el
historial no protege NADA tras 24 h de silencio.

REGLA QUE SALE DE AQUÍ: subir el TTL no arregla esto, solo mueve la frontera. El dato ya existe en
Postgres; lo que faltaba era leerlo.
"""
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select

from app.config import get_settings
from app.models import Mensaje
from app.services.db import get_session_factory
from app.services.redis_client import (
    MAX_TURNOS_HISTORIAL,
    obtener_historial,
    sembrar_historial,
)

logger = logging.getLogger(__name__)
settings = get_settings()


def _hay_conversacion(historial: list[dict]) -> bool:
    """True si en Redis hay una CONVERSACIÓN, no solo mensajes del cliente apilados.

    🔴 LA GUARDA NO PUEDE SER "¿hay algo en Redis?" (bug encontrado auditando el 2026-08-20, en
    este mismo fichero). Cuatro caminos dejan `hist:` con mensajes del cliente y NINGUNA respuesta:
    el bot apagado, el chat pausado porque la dueña lo tomó, el número fuera de la lista blanca, y
    `responder()` reventando (OpenRouter sin saldo — ya costó una semana de mensajes mudos). En los
    cuatro, la clave EXISTE pero no hay conversación viva, y preguntar solo si está vacía hacía que
    el bug original volviera por la puerta de atrás: el bot arrancaba sin memoria justo después de
    que la dueña interviene en un chat, que es el flujo central del producto.

    Sin un solo mensaje del bot no hay hilo que continuar: eso es la misma señal que usa
    `_es_inicio_conversacion` en `agent.py`.
    """
    return any((m or {}).get("role") == "assistant" for m in historial)


def _fusionar(rescatado: list[dict], vivo: list[dict]) -> list[dict]:
    """Lo de Postgres + lo que hubiera en Redis y NO esté ya ahí, en ese orden.

    Normalmente el rescate ya CONTIENE lo vivo (los cuatro caminos de arriba escriben también en
    Postgres, vía `_guardar_entrante`), así que esto no añade nada. Pero si esa escritura falló
    —el mismo hipo de Postgres que SIL-10 vino a cubrir—, el mensaje solo existe en Redis: tirarlo
    sería perder lo último que dijo el cliente para ganar historia vieja. Se compara por
    (role, content) porque es lo único que hay: el historial de Redis no lleva id ni fecha.
    """
    vistos = {(m.get("role"), m.get("content")) for m in rescatado}
    return rescatado + [m for m in vivo if (m.get("role"), m.get("content")) not in vistos]


async def historial_con_respaldo(telefono: str, *, sembrar: bool = False) -> list[dict]:
    """El historial del agente. Si Redis tiene una CONVERSACIÓN, manda Redis (es la fuente viva);
    si no, se reconstruye desde Postgres y se fusiona con lo que hubiera.

    `sembrar=True` deja lo reconstruido de vuelta en Redis. 🔴 NO es un lujo de rendimiento, es
    CORRECCIÓN: sin sembrar, el turno siguiente encuentra en Redis los 2 mensajes que acaba de
    escribir este turno (`hist:` ya NO está vacía), no vuelve a reconstruir, y el bot olvida otra
    vez todo lo anterior. Sembrar solo se pide desde los carriles que tienen el LOCK del teléfono
    (`_procesar`, `_responder_y_enviar`): así dos tareas no pueden sembrar a la vez y duplicar.

    Nunca lanza: si Postgres falla, devuelve lo que hubiera en Redis. Degradar, nunca bloquear
    (L16) — una memoria incompleta es el bug de siempre; una excepción aquí mataría el turno.
    """
    vivo = await obtener_historial(telefono)
    if _hay_conversacion(vivo):
        return vivo
    rescatado = await historial_desde_postgres(telefono)
    if not rescatado:
        # Postgres no tiene nada mejor que ofrecer (cliente nuevo, o la consulta falló): se sigue
        # con lo que hubiera en Redis. Nunca se devuelve MENOS memoria de la que ya había.
        return vivo
    fusionado = _fusionar(rescatado, vivo)
    if sembrar:
        try:
            # `reemplazar=True`: aquí `hist:` puede EXISTIR (los mensajes apilados del cliente) y
            # la guarda anti-duplicado la abandonaría, dejando al bot sin la memoria rescatada en
            # el turno siguiente — el bug de la puerta de atrás otra vez.
            await sembrar_historial(telefono, fusionado, reemplazar=True)
        except Exception:  # noqa: BLE001 — sin siembra el bot recuerda ESTE turno; con excepción, ninguno
            logger.exception("No se pudo sembrar en Redis el historial rescatado de %s", telefono)
    logger.info(
        "MEMORIA RESCATADA de Postgres para %s: %d mensajes (en Redis no había conversación%s)",
        telefono, len(fusionado), f", solo {len(vivo)} del cliente" if vivo else " ",
    )
    return fusionado


async def historial_desde_postgres(telefono: str) -> list[dict]:
    """Reconstruye el historial desde la tabla `mensajes`, en el MISMO formato que Redis
    (`{"role": ..., "content": ...}`) y con las MISMAS exclusiones. Los cuatro filtros no son
    cosmética: cada uno evita un bug concreto.

    1. 🔴 `estado != 'fallido'` — **SIL-8**. Postgres guarda los globos fallidos a propósito (se
       ven en ROJO en el panel), pero el bot NO puede recordar haber dicho algo que el cliente
       nunca recibió: si falla justo el globo con la cuenta y la cédula, el bot lo daría por dicho
       y no lo repetiría nunca. Se compara contra 'fallido' y NO por `== 'enviado'`: los mensajes
       que llegaron bien pasan a 'entregado'/'leido' cuando Meta avisa (hoy en el taller, 29 filas
       'entregado' contra 25 'enviado'), así que filtrar por 'enviado' habría tirado la mayoría del
       historial bueno.
    2. `tipo = 'text'` — la media NUNCA entró al historial de Redis (decisión del 08-08). Meter
       aquí las filas "(foto de X)" / "(catálogo en PDF)" le enseñaría al bot un formato que no
       existe en su memoria viva. Las notas de voz NO se pierden: se guardan ya transcritas y con
       tipo 'text' (`_responder_y_enviar`).
    3. `rol owner → assistant` — es lo que hace Redis con el eco de la dueña ("el bot HEREDA lo que
       ella dijo, una sola voz ante el cliente", `webhook/router.py`). Mandarlo como 'owner' metería
       un rol que el LLM no conoce; omitirlo dejaría un hueco donde alguien SÍ habló.
    4. **Ventana de días** — sin ella, el bot desentierra un pedido de hace dos meses y lo trata
       como vivo. Con ella cubre de sobra el patrón real (preguntar hoy, decidir en tres días).

    Los teléfonos internos (`__simulador__`, `__prueba_*`) quedan FUERA: no son un WhatsApp real,
    no sufren el problema de las 24 h, y los bancos dependen de arrancar con la memoria limpia.
    Misma frontera que usa `_numero_permitido`.
    """
    if not telefono or telefono.startswith("__"):
        return []
    desde = datetime.now(UTC) - timedelta(days=settings.historial_respaldo_dias)
    try:
        factory = get_session_factory()
        async with factory() as session:
            filas = (
                await session.execute(
                    select(Mensaje.rol, Mensaje.contenido)
                    .where(
                        Mensaje.cliente_telefono == telefono,
                        Mensaje.tipo == "text",
                        Mensaje.contenido != "",
                        Mensaje.created_at >= desde,
                        or_(Mensaje.estado.is_(None), Mensaje.estado != "fallido"),
                    )
                    # DESC + LIMIT para traer los ÚLTIMOS N (no los primeros) y se invierte
                    # después: mismo criterio que el `ltrim(-20, -1)` de Redis.
                    .order_by(Mensaje.id.desc())
                    .limit(MAX_TURNOS_HISTORIAL)
                )
            ).all()
    except Exception:  # noqa: BLE001 — el respaldo es una MEJORA: si falla, se sigue como antes
        logger.exception("No se pudo rescatar de Postgres el historial de %s", telefono)
        return []
    return [
        {"role": ("assistant" if rol == "owner" else rol), "content": contenido}
        for rol, contenido in reversed(filas)
    ]
