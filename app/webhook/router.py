import json
import logging
from datetime import UTC

from fastapi import APIRouter, Query, Request, Response

from app.config import get_settings
from app.webhook.parser import extraer_eventos
from app.webhook.signature import verificar_firma

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhook", tags=["webhook"])
settings = get_settings()


@router.get("/whatsapp")
def verificar(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
):
    """Handshake de verificación que Meta hace una sola vez al registrar el webhook."""
    if hub_mode == "subscribe" and hub_verify_token == settings.meta_verify_token:
        return Response(content=hub_challenge or "", media_type="text/plain")
    return Response(status_code=403)


@router.post("/whatsapp")
async def recibir(request: Request):
    """Recibe TODO lo que manda Meta y lo reparte a su carril.

    Tres cosas distintas entran por aquí:
      · un CLIENTE escribió            → el bot le responde (por Celery).
      · LA DUEÑA escribió desde su CELULAR (eco) → el bot se CALLA en ese chat.
      · un mensaje NUESTRO fue entregado / leído / FALLÓ → se anota en el hilo.

    Responde 200 SIEMPRE (salvo firma inválida): si devolviéramos un error, Meta reintenta el
    mismo evento una y otra vez, y los reintentos fallidos le bajan la calidad al número —
    siendo Tech Provider, eso arriesga la cuenta de Meta de TODOS los clientes. El
    procesamiento pesado va en background para no pasarnos de los ~5s que Meta espera.
    """
    raw = await request.body()
    firma = request.headers.get("x-hub-signature-256")
    if not verificar_firma(settings.meta_app_secret, raw, firma):
        logger.warning("Webhook con firma inválida — descartado")
        return Response(status_code=401)

    payload = json.loads(raw)
    _testigo(payload)

    # TODOS los eventos del POST, no solo el primero: Meta AGRUPA. Antes se leía únicamente
    # entry[0].changes[0].messages[0] → si venía un lote de estados y detrás el mensaje de un
    # cliente, el mensaje se perdía, respondíamos 200 y Meta no reintentaba: "quiero 8
    # empanadas" desaparecía PARA SIEMPRE.
    eventos = extraer_eventos(payload)
    if not eventos:
        return {"status": "ignored"}

    resultados = []
    for ev in eventos:
        try:
            if ev["clase"] == "mensaje":
                resultados.append(await _procesar_entrante(ev))
            elif ev["clase"] == "eco":
                resultados.append(await _procesar_eco(ev))
            elif ev["clase"] == "estado":
                resultados.append(await _aplicar_estado(ev))
        except Exception:  # noqa: BLE001 — un evento roto NUNCA tumba el webhook entero
            logger.exception("Fallo procesando un evento del webhook: %s", ev.get("clase"))
            resultados.append("error")
    return {"status": "ok", "eventos": resultados}


async def _procesar_entrante(mensaje) -> str:
    """Un CLIENTE escribió: lo de siempre."""
    # EL RELOJ DE LAS 24 HORAS arranca AQUÍ y no en el worker: este es el único embudo por
    # el que pasan los CUATRO caminos (texto, voz, comprobante, sticker). El comprobante, por
    # ejemplo, nunca pasa por el worker de texto: si el reloj viviera allá, un cliente que solo
    # manda la captura del pago aparecería con la ventana CERRADA y la dueña no podría
    # responderle justo en el momento del dinero.
    #
    # ⚠️ Y solo se llama desde AQUÍ: un ECO es un mensaje SALIENTE y NO abre la ventana de 24h
    # (si la abriera, el panel dejaría escribir fuera de plazo y Meta rechazaría el envío).
    await _marcar_entrante(mensaje["telefono"], mensaje.get("nombre"))

    # Mostrar "escribiendo…" de inmediato: el cliente ve que lo estamos atendiendo
    # (se siente humano, no robotico). Import perezoso; no critico si falla.
    from app.services.meta_client import marcar_leido_y_escribiendo
    await marcar_leido_y_escribiendo(mensaje["message_id"])

    tipo = mensaje["tipo"]

    # Tope de gasto / anti-abuso: los comprobantes (image/document) SIEMPRE pasan
    # (es dinero); el resto cuenta para el limite diario por cliente.
    if tipo not in ("image", "document") and await _excede_tope(
        mensaje["telefono"], mensaje.get("nombre")
    ):
        return "limite"

    if tipo == "text":
        logger.info("Mensaje de %s: %s", mensaje["telefono"], mensaje["texto"])
        return await _encolar_mensaje(mensaje)

    if tipo in ("image", "document"):
        logger.info("Comprobante (%s) de %s", tipo, mensaje["telefono"])
        return await _encolar_comprobante(mensaje)

    if tipo == "audio":
        logger.info("Nota de voz de %s", mensaje["telefono"])
        return await _encolar_audio(mensaje)

    # sticker / video / ubicacion / contactos / etc.: el agente responde como humano.
    logger.info("Evento %s de %s", tipo, mensaje["telefono"])
    return await _encolar_evento(mensaje)


async def _procesar_eco(eco) -> str:
    """LA DUEÑA escribió desde SU CELULAR: el bot se calla en ese chat.

    ORDEN SAGRADO — la PAUSA primero, la burbuja después, cada una en su propia transacción:
    si fueran juntas y el INSERT fallara (una foto sin pie, un tipo raro, un ❤️), el rollback
    se llevaría también la pausa y el bot volvería a hablarle ENCIMA a la dueña, en medio de
    una venta. Perder una burbuja del historial es cosmético; perder la pausa, no.
    """
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models import Cliente, Mensaje, now_utc
    from app.services import redis_client as rc
    from app.services.db import get_session_factory
    from app.webhook.parser import contenido_seguro

    telefono, wa_id = eco["telefono"], eco["message_id"]

    # 1) Candado barato: Meta REENTREGA el mismo evento si duda de nuestra respuesta.
    if await rc.ya_procesado(f"eco:{wa_id}"):
        return "eco_duplicado"

    factory = get_session_factory()

    # 2) ¿Es NUESTRO? Hoy está verificado que la Cloud API no genera eco, pero si Meta lo
    #    cambiara, el bot se pausaría a sí mismo tras cada respuesta y quedaría MUDO con todos
    #    los clientes. Este cinturón cuesta una consulta y evita el desastre.
    async with factory() as session:
        mio = (
            await session.execute(
                select(Mensaje.id).where(Mensaje.wa_message_id == wa_id).limit(1)
            )
        ).scalar_one_or_none()
    if mio is not None:
        logger.info("Eco de un mensaje NUESTRO (%s): se ignora, no se pausa", wa_id)
        return "eco_propio"

    # 3) LA PAUSA (transacción propia). Con UPSERT: si la dueña le escribe PRIMERO a alguien
    #    que nunca le escribió, ese cliente todavía no existe en la BD — con un UPDATE simple
    #    no se guardaría nada y el bot se metería encima de la conversación que ella empezó.
    #    ⚠️ NO se toca `ultimo_entrante_at`: un mensaje SALIENTE no abre la ventana de 24h.
    ahora = now_utc()
    async with factory() as session:
        stmt = pg_insert(Cliente).values(
            telefono=telefono,
            bot_pausado=True,
            pausado_por="dueña",
            no_leidos=0,
            ultima_interaccion=ahora,
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[Cliente.telefono],
                set_={
                    "bot_pausado": True,
                    "pausado_por": "dueña",
                    "no_leidos": 0,
                    "ultima_interaccion": ahora,
                },
            )
        )
        await session.commit()
    logger.info("ECO de la dueña → el bot queda CALLADO con %s", telefono)

    # 4) LA BURBUJA (otra transacción, y si falla NO se lleva la pausa).
    texto = contenido_seguro(eco["tipo"], eco.get("texto"), eco.get("caption"))
    nueva = False
    try:
        async with factory() as session:
            ins = pg_insert(Mensaje).values(
                # `message_id` tiene UNIQUE desde la 001 y hasta hoy NADIE lo usaba: es el
                # candado que impide que un reintento de Meta duplique la burbuja Y meta dos
                # veces el mismo mensaje en la memoria del bot (empujando fuera lo que el
                # cliente realmente pidió).
                message_id=wa_id,
                wa_message_id=wa_id,
                cliente_telefono=telefono,
                rol="owner",
                contenido=texto,
                tipo=eco["tipo"],
                media_id=eco.get("media_id"),
                estado="enviado",
                created_at=_fecha_meta(eco.get("timestamp")) or ahora,
            ).on_conflict_do_nothing(index_elements=[Mensaje.message_id])
            res = await session.execute(ins)
            await session.commit()
            nueva = bool(res.rowcount)
    except Exception:  # noqa: BLE001 — la pausa YA está puesta: eso es lo que no se puede perder
        logger.exception("No se pudo guardar la burbuja del eco de %s", telefono)

    # 5) El bot HEREDA lo que ella dijo (una sola voz ante el cliente). Solo si la burbuja es
    #    NUEVA: si no, un reintento de Meta duplicaría el mensaje en la memoria del agente.
    if nueva:
        try:
            await rc.guardar_historial(telefono, "assistant", texto)
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo meter el eco en la memoria del bot (%s)", telefono)
    return "eco"


# Un estado NUNCA retrocede: si el "entregado" llega tarde, no puede pisar un "leído".
_RANGO = {"enviado": 1, "entregado": 2, "leido": 3}


async def _aplicar_estado(ev) -> str:
    """Meta dice qué pasó con un mensaje NUESTRO. El FALLO se ve en rojo, no se pierde."""
    from sqlalchemy import or_, update

    from app.models import Mensaje
    from app.services.db import get_session_factory

    estado, wa_id = ev["estado"], ev["wa_message_id"]
    inferiores = [e for e, r in _RANGO.items() if r < _RANGO.get(estado, 0)]

    condiciones = [Mensaje.wa_message_id == wa_id]
    if estado == "fallido":
        pass  # el fallo SIEMPRE gana: es lo único que la dueña tiene que ver sí o sí
    else:
        condiciones.append(
            or_(Mensaje.estado.is_(None), Mensaje.estado.in_(inferiores or ["__nunca__"]))
        )

    factory = get_session_factory()
    async with factory() as session:
        res = await session.execute(
            update(Mensaje).where(*condiciones).values(estado=estado, error=ev.get("error"))
        )
        await session.commit()
    if estado == "fallido":
        logger.error("ENVÍO FALLIDO (%s): %s", wa_id, ev.get("error"))
    return "estado" if res.rowcount else "estado_sin_dueño"


def _fecha_meta(ts: str | None):
    """El timestamp de Meta (segundos epoch) → datetime UTC. None si no se puede."""
    from datetime import datetime

    try:
        return datetime.fromtimestamp(int(ts), tz=UTC)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _testigo(payload: dict) -> None:
    """SOLO MIRA Y ANOTA. No cambia nada, no responde nada, no toca la BD.

    Existe para poder responder CON PRUEBAS a la única pregunta peligrosa de la Fase 2:
    cuando se active `smb_message_echoes` en Meta, ¿el eco se dispara TAMBIÉN con los
    mensajes que manda el BOT? Si así fuera, el bot se pausaría a sí mismo después de cada
    respuesta y se quedaría MUDO con todos los clientes.

    Anota, de cada evento: qué campo llegó (`field`), qué claves trae (`messages`,
    `message_echoes`, `statuses`…) y, si es un eco, de quién es y qué dice. Con eso se
    decide si la Fase 2 se construye o no. Cualquier fallo aquí se traga: un testigo JAMÁS
    puede tumbar el webhook.
    """
    try:
        for entry in (payload.get("entry") or []):
            for cambio in (entry.get("changes") or []):
                campo = cambio.get("field")
                value = cambio.get("value") or {}
                claves = sorted(
                    k for k in value if k not in ("messaging_product", "metadata")
                )
                if "messages" in claves:
                    continue  # el camino normal: ya se registra en otro lado
                detalle = ""
                ecos = value.get("message_echoes")
                if ecos and isinstance(ecos, list):
                    e = ecos[0] if isinstance(ecos[0], dict) else {}
                    detalle = (
                        f" | eco: from={e.get('from')} to={e.get('to')}"
                        f" tipo={e.get('type')} id={e.get('id')}"
                    )
                logger.info("TESTIGO webhook: field=%s claves=%s%s", campo, claves, detalle)
    except Exception:  # noqa: BLE001 — un testigo nunca puede tumbar el webhook
        pass


async def _marcar_entrante(telefono: str, nombre: str | None) -> None:
    """El cliente ESCRIBIÓ: se abre su ventana de 24h y sube su contador de no leídos.

    Va con UPSERT a propósito: si el cliente es NUEVO, la fila todavía no existe (la crea el
    worker, después). Con un UPDATE simple no se guardaría nada y el cliente estrenaría con la
    ventana en NULL = CERRADA — o sea, la dueña no podría contestarle a quien le escribe por
    primera vez, que es justo el que más importa.

    Si esto falla, se loguea pero NO se rompe el webhook: perder el reloj es malo, pero
    devolverle un error a Meta (y que reintente el mensaje) es peor.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models import Cliente, now_utc
    from app.services.db import get_session_factory

    ahora = now_utc()
    try:
        factory = get_session_factory()
        async with factory() as session:
            stmt = pg_insert(Cliente).values(
                telefono=telefono,
                nombre=nombre,
                ultima_interaccion=ahora,
                ultimo_entrante_at=ahora,
                no_leidos=1,
            )
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[Cliente.telefono],
                    set_={
                        "ultima_interaccion": ahora,
                        "ultimo_entrante_at": ahora,
                        # El nombre NO se pisa: el que ya tenemos (o el que puso la dueña a mano)
                        # vale más que el del perfil de WhatsApp.
                        "no_leidos": Cliente.no_leidos + 1,
                    },
                )
            )
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo marcar el mensaje entrante de %s", telefono)


async def _encolar_mensaje(mensaje) -> str:
    """Idempotencia + buffer + encolado en Celery.

    Imports perezosos: así importar el router no exige Redis/Celery, y los
    tests pueden sustituir esta función sin esos servicios.
    """
    from app.services import redis_client as rc
    from app.workers.tasks import procesar_buffer

    if await rc.ya_procesado(mensaje["message_id"]):
        return "duplicado"

    await rc.agregar_a_buffer(mensaje["telefono"], mensaje["texto"])
    procesar_buffer.apply_async(
        (mensaje["telefono"], mensaje["nombre"]),
        countdown=settings.buffer_segundos,
    )
    return "ok"


async def _encolar_comprobante(mensaje) -> str:
    """Encola un comprobante (imagen/PDF) en su carril propio.

    NO pasa por el buffer de texto de 15s. La idempotencia NO se marca aqui:
    se consolida en el worker SOLO tras un registro exitoso (y la BD la blinda
    con el UNIQUE de comprobante_media_id), para que un fallo transitorio de
    descarga no descarte el reintento legitimo de Meta y se pierda el pago.
    """
    from app.workers.tasks import procesar_comprobante

    if not mensaje.get("media_id"):
        logger.warning("Comprobante sin media_id de %s", mensaje["telefono"])
        return "sin_media"

    procesar_comprobante.apply_async((
        mensaje["telefono"],
        mensaje["message_id"],
        mensaje["media_id"],
        mensaje.get("caption"),
        mensaje.get("nombre"),
        mensaje.get("mime_type"),
    ))
    return "ok"


async def _encolar_audio(mensaje) -> str:
    """Nota de voz: se descarga, se transcribe y el agente responde como a un texto."""
    from app.services import redis_client as rc
    from app.workers.tasks import procesar_audio

    if not mensaje.get("media_id"):
        return "sin_media"
    if await rc.ya_procesado(mensaje["message_id"]):
        return "duplicado"
    procesar_audio.apply_async((
        mensaje["telefono"],
        mensaje["message_id"],
        mensaje["media_id"],
        mensaje.get("nombre"),
        mensaje.get("mime_type"),
    ))
    return "ok"


async def _encolar_evento(mensaje) -> str:
    """Sticker/video/ubicacion/etc.: el agente responde natural, sin frases roboticas."""
    from app.services import redis_client as rc
    from app.workers.tasks import procesar_evento

    if await rc.ya_procesado(mensaje["message_id"]):
        return "duplicado"
    procesar_evento.apply_async((mensaje["telefono"], mensaje["tipo"], mensaje.get("nombre")))
    return "ok"


async def _excede_tope(telefono: str, nombre: str | None) -> bool:
    """True si el cliente supero el tope de mensajes del dia: se frena la respuesta
    automatica y se avisa a la duena (una vez). limite<=0 = sin tope.
    Cualquier fallo del contador deja pasar el mensaje (no frena el bot)."""
    limite = settings.limite_mensajes_cliente_dia
    if limite <= 0:
        return False
    from app.services import redis_client as rc

    try:
        n = await rc.contar_mensaje_dia(telefono)
    except Exception:  # noqa: BLE001 — un fallo del contador no debe frenar el bot
        logger.exception("No se pudo contar mensajes de %s", telefono)
        return False
    if n <= limite:
        return False
    logger.warning("Cliente %s supero el tope diario (%s > %s)", telefono, n, limite)
    try:
        if await rc.aviso_abuso_nuevo(telefono):
            await _avisar_duena_abuso(telefono, nombre, n)
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo avisar del abuso de %s", telefono)
    return True


async def _avisar_duena_abuso(telefono: str, nombre: str | None, n: int) -> None:
    """Avisa a la duena (su WhatsApp) que un cliente supero el tope del dia."""
    from sqlalchemy import select

    from app.models import Configuracion
    from app.services.db import get_session_factory
    from app.services.meta_client import enviar_texto

    factory = get_session_factory()
    async with factory() as session:
        fila = (
            await session.execute(
                select(Configuracion).where(Configuracion.clave == "dueno_telefono")
            )
        ).scalar_one_or_none()
    destino = (fila.valor if fila else None) or settings.dueno_telefono
    if not destino:
        return
    quien = nombre or telefono
    await enviar_texto(
        destino,
        f"⚠️ {quien} superó el límite de mensajes de hoy ({n}). El bot pausó las "
        f"respuestas automáticas con ese cliente por hoy; si quieres, escríbele tú.",
    )
