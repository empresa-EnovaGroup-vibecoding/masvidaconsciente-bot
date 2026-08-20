import asyncio
import json
import logging
import os
import re
from datetime import datetime

from celery.exceptions import MaxRetriesExceededError

from app.agent.agent import (
    leer_comprobante,
    redactar_mensaje,
    responder,
    transcribir_audio,
)
from app.config import get_settings
from app.services import redis_client as rc
from app.services.db import get_session_factory
from app.services.memoria import historial_con_respaldo
from app.services.meta_client import (
    MAX_MEDIA_BYTES,
    MediaDemasiadoGrande,
    descargar_media,
    enviar_texto,
    marcar_mensaje_propio,
)
from app.services.telemetria import abrir_turno
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)
settings = get_settings()

# Loop de asyncio persistente POR PROCESO del worker.
#
# Celery (prefork) corre tareas sincronas. Usar asyncio.run() en cada tarea
# crea y CIERRA un loop nuevo cada vez, dejando invalidas las conexiones async
# cacheadas (redis / engine de la BD) -> a partir de la 2da tarea explota con
# "RuntimeError: Event loop is closed". Reusar UN solo loop por proceso mantiene
# esas conexiones vivas entre tareas. Cada proceso de Celery tiene el suyo.
_LOOP = None


def _run(coro):
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_LOOP)
    return _LOOP.run_until_complete(coro)


# ─── EL ÚNICO PORTÓN DE LOS AVISOS A LA DUEÑA ────────────────────────


async def _whatsapp_a_la_duena(destino: str, cuerpo: str, *, que: str) -> bool:
    """Le manda el WhatsApp a la DUEÑA y DEJA CONSTANCIA si no salió. Nunca lanza.

    🔴 Por qué existe (auditoría 2026-08-02, META-15): los seis carriles de aviso a la dueña
    hacían el POST a ciegas y se tragaban el error con un `logger.exception` genérico. De los 16
    puntos de salida del sistema solo 3 miran la ventana de 24h, y ninguno de estos — que son los
    ÚNICOS envíos verdaderamente proactivos que hay, los que nadie aprueba a mano. No era
    fail-closed: era *fail-silent después de intentar*, que es peor, porque el intento fuera de
    ventana SÍ se ejecuta y Meta lo apunta contra este número.

    ⚠️ LA COMPROBACIÓN DE LA VENTANA **NO** ESTÁ AQUÍ, y es a propósito: vive en
    `meta_client.enviar_texto`, la única puerta por la que salen los seis carriles (y los que se
    escriban mañana). Repartirla por los call sites es exactamente lo que produjo este hallazgo:
    seis sitios donde olvidarla. Lo que sí vive aquí es lo otro que faltaba — que cuando el aviso
    NO sale, quede escrito en el log con su motivo y con lo que se quería decir, en vez de un
    traceback anónimo. El aviso nunca se pierde: quien llama deja SIEMPRE la fila en la bandeja
    ANTES de llegar aquí, y la bandeja es lo único de esta casa que nunca falló.

    ⚠️ Usa el `enviar_texto` DE ESTE MÓDULO (el de arriba del todo) y no uno importado dentro de
    la función: los bancos sustituyen `tasks.enviar_texto` por un doble, y un import local se lo
    saltaría — mandando WhatsApps de verdad en cada corrida de pruebas.
    """
    if not destino:
        return False
    try:
        await enviar_texto(destino, cuerpo)
        return True
    except Exception as exc:  # noqa: BLE001 — un aviso que no sale no puede tumbar al worker
        logger.error(
            "AVISO A LA DUEÑA NO ENTREGADO (%s): %s — decía: %r. La fila SÍ está en la bandeja.",
            que, exc, cuerpo[:200],
        )
        return False


# ─── Escrituras del PANEL: si fallan, alguien se entera ──────────────

_INTENTOS_PANEL = 2  # el original + UN reintento


async def _escribir_en_panel(escribir, telefono: str, que: str) -> bool:
    """Ejecuta una escritura del panel y, si revienta, la reintenta UNA vez tras un segundo.

    🔴 Por qué (auditoría 2026-08-02, SIL-15): esto era `except: logger.exception(...)` y punto.
    El fallo típico aquí NO es "Postgres está muerto": es una conexión del pool que el servidor
    cerró por su lado y explota al usarla, o el pestañeo de un reinicio de la base. El segundo
    intento pasa. Con el trago-y-log, ese pestañeo borraba del panel el intercambio ENTERO —lo
    que preguntó el cliente Y lo que contestó el bot— y la dueña seguía atendiendo a ciegas,
    sin ninguna señal de que le faltaban mensajes.
    """
    for intento in range(1, _INTENTOS_PANEL + 1):
        try:
            await escribir()
            return True
        except Exception:  # noqa: BLE001
            logger.exception(
                "No se pudo guardar %s de %s en el panel (intento %s/%s)",
                que, telefono, intento, _INTENTOS_PANEL,
            )
            if intento < _INTENTOS_PANEL:
                await asyncio.sleep(1.0)
    return False


async def _hueco_en_el_panel(telefono: str, texto_usuario: str, dichos: list) -> None:
    """La base no aceptó el intercambio ni al segundo intento: el hilo del panel queda con un
    HUECO. Que no sea silencioso — el silencio es LA falla de esta casa.

    El orden importa: (1) log en ERROR con el contenido íntegro, porque los logs del contenedor
    son el último respaldo que queda cuando la BD no está; (2) WhatsApp a la dueña, con el
    destino sacado de la VARIABLE DE ENTORNO y no de la tabla `configuracion` —la tabla vive en
    el Postgres que acaba de fallar, que es como preguntarle la hora al reloj roto—; (3) candado
    de 15 min, para que una caída de la base no se convierta en 200 WhatsApps. La clave del
    candado va SIN teléfono a propósito: la avería es UNA (la base), no una por cliente.
    """
    logger.error(
        "HUECO EN EL PANEL de %s — cliente=%r bot=%r", telefono, texto_usuario, dichos
    )
    try:
        if settings.dueno_telefono and await rc.aviso_unico("panel_incompleto", 900):
            await _whatsapp_a_la_duena(
                settings.dueno_telefono,
                "⚠️ No estoy pudiendo guardar las conversaciones en el panel (falla la base de "
                "datos). El bot sigue contestando por WhatsApp, pero el panel está INCOMPLETO: "
                "hay mensajes de tus clientes y respuestas mías que no vas a ver ahí. "
                "Avísale a Enova.",
                que="el panel está perdiendo mensajes",
            )
    except Exception:  # noqa: BLE001 — el aviso es lo último que hay; que no tumbe al worker
        logger.exception("Tampoco se pudo avisar de que el panel está perdiendo mensajes")


async def _guardar_en_panel(
    telefono: str, nombre: str | None, texto_usuario: str, partes: list[dict],
    ts_usuario: datetime | None = None,
) -> bool:
    """Persiste la conversacion en Postgres para que aparezca en el panel.

    El historial en Redis es para el contexto del agente; el PANEL lee de Postgres
    (tablas clientes + mensajes). Sin esto, las charlas no se ven en el panel.

    🔴 UNA FILA POR GLOBO. El bot responde en varios mensajitos (hasta 6), y Meta devuelve un
    id por cada uno. Antes se guardaba UNA sola fila con todo el texto junto y se TIRABAN los
    ids: cuando Meta avisaba de que un globo había FALLADO, ese aviso no casaba con ninguna
    fila y se perdía. O sea: si fallaba justo el globo con LOS DATOS BANCARIOS, en el panel se
    veía todo verde y nadie se enteraba de que el cliente nunca supo dónde pagar.

    Devuelve `True` si el intercambio QUEDÓ ESCRITO. El que llama lo necesita: el comentario
    viejo decía "no critico: la respuesta ya se envio" —cierto para el CLIENTE, falso para la
    DUEÑA—, y ahora hay una red (SIL-10) que escribe el turno del cliente cuando nadie más pudo.
    Esa red solo funciona si aquí se dice la VERDAD sobre si se guardó o no.

    🔴 `ts_usuario` (2026-08-20): CUÁNDO escribió el cliente, no cuándo terminamos de guardar.
    Esta función corre al FINAL del turno y los `default=now_utc` se evalúan todos en el mismo
    flush, mientras que la media saliente se escribe DURANTE el turno, al enviarla. Resultado: en
    el hilo del panel —que ordena por `created_at` (`detalle_conversacion`)— la foto aparecía
    ANTES de la pregunta que la provocó. Pasaba en todos los turnos con media: el 08-18 la foto
    quedó a las 15:04:06 y la pregunta del cliente a las 15:04:10. Sin este dato la dueña lee el
    hilo al revés justo en los turnos donde el bot mandó algo.
    """
    from sqlalchemy import select

    from app.models import Cliente, Mensaje, now_utc
    from app.services.db import get_session_factory

    async def _escribir() -> None:
        factory = get_session_factory()
        async with factory() as session:
            cliente = (
                await session.execute(select(Cliente).where(Cliente.telefono == telefono))
            ).scalar_one_or_none()
            if cliente is None:
                session.add(Cliente(telefono=telefono, nombre=nombre, ultima_interaccion=now_utc()))
            else:
                cliente.ultima_interaccion = now_utc()
                if nombre and not cliente.nombre:
                    cliente.nombre = nombre
            if texto_usuario:
                fila = Mensaje(cliente_telefono=telefono, rol="user", contenido=texto_usuario)
                if ts_usuario is not None:
                    fila.created_at = ts_usuario
                session.add(fila)
            for p in partes:
                session.add(Mensaje(
                    cliente_telefono=telefono,
                    rol="assistant",
                    contenido=p["texto"],
                    wa_message_id=p.get("wa_message_id"),
                    estado=p.get("estado"),
                    error=p.get("error"),
                ))
            await session.commit()

    if await _escribir_en_panel(_escribir, telefono, "la conversación"):
        return True
    await _hueco_en_el_panel(telefono, texto_usuario, [p.get("texto") for p in partes])
    return False


# ─── Cinturon de seguridad del DINERO (anti-alucinacion) ─────────────
# Solo la duena confirma un pago, desde el panel (eso dispara notificar_cliente_pago).
# Si el AGENTE, en una charla normal, afirma que un pago quedo confirmado, es una
# alucinacion: el bot NUNCA debe confirmar dinero por su cuenta. Lo interceptamos.
_FRASES_PAGO_CONFIRMADO = (
    "pago confirmado",
    "pago fue confirmado",
    "pago ya confirmado",
    "pago quedo confirmado",
    "pago esta confirmado",
    "confirmado tu pago",
    "confirme tu pago",
    "pago verificado",
    "verifique tu pago",
    "tu pago ya esta listo",
    "ya quedo confirmado tu pago",
)
_RESPUESTA_PAGO_SEGURA = "¡Recibido! Estoy revisando tu pago y te confirmo en un ratito 😊"


def _proteger_afirmacion_de_pago(respuesta: str) -> str:
    """Si el agente afirma que un pago quedo confirmado (cosa que SOLO la duena
    puede hacer desde el panel), lo reemplaza por un mensaje seguro de 'revisando'.
    Compara sin acentos para atrapar 'confirmo'/'confirmo', etc."""
    import unicodedata

    t = unicodedata.normalize("NFKD", respuesta.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    if any(frase in t for frase in _FRASES_PAGO_CONFIRMADO):
        logger.warning(
            "Anti-alucinacion dinero: el agente afirmo un pago confirmado en charla; reemplazado"
        )
        return _RESPUESTA_PAGO_SEGURA
    return respuesta


# ─── Envío humano: plano + varios mensajitos cortos (no un mensajote) ─

def _aplanar(texto: str) -> str:
    """Quita el formato que delata a un bot y lo deja como se escribe en WhatsApp: viñetas
    (* - •) al inicio de línea, negritas/cursivas markdown (*texto*), los decimales .00 de los
    precios y —clave para que suene NATURAL— los signos de APERTURA '¿' y '¡'. Nadie en un chat
    escribe "¿Cómo estás?": escribe "como estas?". La dueña escribe PLANO e informal; esto es una
    red de seguridad por si el modelo igual mete formato o puntuación de más (a veces la ignora)."""
    lineas = [re.sub(r"^[ \t]*[\*\-•]+[ \t]+", "", ln) for ln in texto.split("\n")]
    t = "\n".join(lineas)
    t = t.replace("*", "")  # negritas / asteriscos sueltos
    t = t.replace(" — ", ", ").replace("—", ", ")  # raya larga (em-dash) -> coma (suena a folleto)
    # Fuera los signos de APERTURA: en WhatsApp nadie los usa. La pregunta queda "como estas?"
    # (solo el cierre) y la exclamación "que rico" — más humano, menos acartonado.
    t = t.replace("¿", "").replace("¡", "")
    t = re.sub(r"\$\s?(\d+)\.00(?!\d)", r"$\1", t)  # $18.00 -> $18
    return t


async def _enviar_en_partes(telefono: str, texto: str) -> list[dict]:
    """Envía la respuesta PLANA y como VARIOS mensajes cortos (como una persona real
    en WhatsApp), no un mensajote. El agente separa cada globo con una línea en blanco;
    aquí aplanamos el formato, partimos por las líneas en blanco y enviamos cada parte
    por separado, con una pausa breve. Tope de globos para proteger la calidad del número.

    Devuelve UNA ENTRADA POR GLOBO: {texto, wa_message_id, estado, error}. El `wa_message_id`
    es el id que devuelve Meta, y es lo ÚNICO con lo que después se puede casar el aviso de
    "entregado / leído / FALLÓ". Antes ese id se tiraba a la basura: si fallaba el globo con
    los datos bancarios, el aviso de Meta no casaba con nada y en el panel se veía todo verde.

    Lista VACÍA = no se envió nada (texto vacío, o la dueña tomó el chat) → el que llama NO
    debe guardar nada en el historial: el bot no puede "recordar" algo que el cliente no vio.
    """
    if not texto or not texto.strip():
        return []

    # ÚLTIMA MIRADA AL FRENO, ya con la respuesta en la mano.
    # El bot tarda ~20s en contestar (15s de buffer + lo que piensa). En ese rato la dueña
    # pudo haber tomado el chat desde el panel. Si solo se mirara la pausa AL EMPEZAR, el bot
    # soltaría su respuesta ENCIMA de la de ella y el cliente vería a dos personas hablándole
    # a la vez. Este es el único embudo por el que salen las 4 respuestas del bot.
    #
    # OJO: se pregunta si lo pausó UNA PERSONA, no si está pausado a secas. El propio bot se
    # pausa al escalar (pedir_ayuda), y en ESE caso su mensaje de despedida al cliente ("dame
    # un momentito, te confirmo") TIENE que salir. Confundir los dos casos dejaba al cliente
    # con silencio total. Ver migración 020.
    if await _lo_paso_una_persona(telefono):
        logger.info(
            "No envío: la dueña tomó el chat de %s mientras el bot pensaba (relevo)", telefono
        )
        return []

    texto = _aplanar(texto)
    partes = [p.strip() for p in re.split(r"\n\s*\n", texto.strip()) if p.strip()]
    if not partes:
        partes = [texto.strip()]
    if len(partes) > 6:  # tope anti-spam: junta el exceso en el último globo
        partes = partes[:5] + ["\n\n".join(partes[5:])]

    enviados: list[dict] = []
    for i, parte in enumerate(partes):
        if i:
            await asyncio.sleep(1.0)  # pausa breve entre globos, como una persona
        try:
            resp = await enviar_texto(telefono, parte)
            wa_id = ((resp.get("messages") or [{}])[0] or {}).get("id")
            await marcar_mensaje_propio(wa_id)
            enviados.append(
                {"texto": parte, "wa_message_id": wa_id, "estado": "enviado", "error": None}
            )
        except Exception as exc:  # noqa: BLE001 — Meta lo rechazó: queda ESCRITO, no perdido
            logger.exception("Meta rechazó un globo para %s", telefono)
            enviados.append(
                {"texto": parte, "wa_message_id": None, "estado": "fallido",
                 "error": str(exc)[:400]}
            )
            # 🔴 LOS GLOBOS QUE NUNCA SE INTENTARON TAMBIÉN DEJAN FILA (auditoría 2026-08-02,
            # SIL-8). Aquí había un `break` seco: si fallaba el globo 2 de 4, el 3 y el 4 se
            # descartaban SIN rastro. En el panel se veía UN globo rojo y nadie podía saber que
            # al cliente le faltaban DOS mensajes — el peor caso es que uno de ellos lleve la
            # cuenta y la cédula. Se sigue sin insistir (si Meta rechazó uno, los siguientes
            # también), pero lo que no sale queda ESCRITO. Van con estado 'fallido' y NO con un
            # estado nuevo a propósito: 'fallido' es el único valor que el panel pinta en ROJO
            # (conversaciones/page.tsx:394 y :426, "· no se envió"), y el panel HOY no se puede
            # recompilar. El `wa_message_id` en NULL hace que ningún aviso de Meta las pise.
            for resto in partes[i + 1:]:
                enviados.append({
                    "texto": resto, "wa_message_id": None, "estado": "fallido",
                    "error": "no se intentó: falló el globo anterior de este mismo mensaje",
                })
            break  # si el primero no pasó, los siguientes tampoco: no se insiste
    return enviados


def _algo_llego(partes: list[dict]) -> bool:
    """True si al menos un globo LLEGÓ. Un globo 'fallido' se guarda (se ve en rojo en el
    panel) pero NO cuenta como dicho: el bot no puede recordar lo que el cliente no recibió."""
    return any(p.get("estado") == "enviado" for p in partes)


def _lo_que_llego(partes: list[dict], respuesta: str) -> str:
    """Lo que el cliente REALMENTE recibió, para la memoria del bot.

    🔴 El docstring de `_algo_llego` (justo arriba) promete que un globo 'fallido' NO cuenta como
    dicho… y los seis sitios que lo llaman guardaban la `respuesta` ENTERA (auditoría 2026-08-02,
    SIL-8). O sea: llegaba el globo 1 ("perfecto, te paso los datos"), fallaba el 2 —EL DE LA
    CUENTA Y LA CÉDULA— y el bot creía haberlos dado. Como ya está "dicho", no lo repite nunca: el
    cliente se queda sin saber dónde pagar y la venta sigue como si nada.

    En el camino feliz devuelve la `respuesta` TAL CUAL, sin tocar ni un carácter: este arreglo es
    para el turno ROTO, no para cambiarle la memoria a los turnos que funcionan. (Devolver siempre
    el texto aplanado y unido por \\n\\n sería un cambio de comportamiento en el 100% de los turnos
    para arreglar un caso raro.)
    """
    if all(p.get("estado") == "enviado" for p in partes):
        return respuesta
    return "\n\n".join(p["texto"] for p in partes if p.get("estado") == "enviado")


async def _guardar_media_en_hilo(
    *, telefono: str, message_id: str | None, media_id: str, ruta: str | None,
    mime: str, caption: str | None, es_imagen: bool,
) -> None:
    """Mete la FOTO DEL CLIENTE (el comprobante) en el hilo del panel.

    Va en sesión PROPIA y con todo tragado: si esto falla, el pago se registra igual. Nunca
    al revés. Y con `message_id` (que tiene UNIQUE desde la 001) como candado: un reintento de
    Meta no puede duplicar la burbuja.

    `ruta=None` es un caso REAL y válido (auditoría 2026-08-02, SIL-5): el disco falló, o ni
    siquiera pudimos bajar el archivo. La burbuja entra IGUAL con el `media_id` a secas — el
    panel se la baja de Meta al vuelo (/api/mensajes/{id}/media, caso 3), así que la dueña VE la
    captura del pago aunque nosotros no la tengamos guardada.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models import Mensaje
    from app.services.db import get_session_factory

    try:
        factory = get_session_factory()
        async with factory() as session:
            ins = pg_insert(Mensaje).values(
                message_id=message_id,
                cliente_telefono=telefono,
                rol="user",
                # El tipo REAL: un PDF no es una imagen (si se guardara como 'image', el panel
                # intentaría pintarlo con <img> y saldría roto).
                tipo="image" if es_imagen else "document",
                contenido=(caption or "").strip() or "(comprobante)",
                media_id=media_id,
                media_url=ruta,
                media_mime=mime or None,
            ).on_conflict_do_nothing(index_elements=[Mensaje.message_id])
            await session.execute(ins)
            await session.commit()
    except Exception:  # noqa: BLE001 — la burbuja es cosmética; el DINERO no puede caerse
        logger.exception("No se pudo meter el comprobante de %s en el hilo", telefono)


# ─── Interruptor del bot (encender / apagar) ─────────────────────────

async def _bot_activo() -> bool:
    """Lee el interruptor del bot (config 'bot_activo'). Por defecto ENCENDIDO.
    Si falla la lectura, deja el bot encendido (no se queda mudo por un error de BD)."""
    from sqlalchemy import select

    from app.models import Configuracion
    from app.services.db import get_session_factory

    try:
        factory = get_session_factory()
        async with factory() as session:
            fila = (
                await session.execute(
                    select(Configuracion).where(Configuracion.clave == "bot_activo")
                )
            ).scalar_one_or_none()
        if fila and fila.valor is not None:
            return fila.valor.strip().lower() not in ("0", "false", "no", "off")
    except Exception:  # noqa: BLE001
        pass
    return True


async def _cliente_pausado(telefono: str) -> bool:
    """True si el bot está pausado en ESE chat (lo pausara quien lo pausara).
    Si la BD falla, devuelve False: un error de lectura no puede dejar MUDO al bot entero."""
    try:
        return (await _estado_pausa(telefono))[0]
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo leer la pausa de %s (sigue respondiendo)", telefono)
        return False


async def _lo_paso_una_persona(telefono: str) -> bool:
    """True SOLO si el freno lo apretó UNA PERSONA (la dueña tomó ese chat).

    🔴 Por qué existe (bug cazado en vivo el 2026-07-12): la red anti-atropello miraba si el
    chat estaba pausado, pero no QUIÉN lo pausó — y hay dos casos OPUESTOS:
      · La DUEÑA tomó el chat → el bot debe CALLARSE (si no, le habla encima al cliente).
      · El BOT se pausó SOLO (pedir_ayuda: está escalando) → su último mensaje al cliente
        ("dame un momentito, te confirmo") SÍ tiene que salir.
    Al confundirlos, el bot se tragaba su propio mensaje de despedida y el cliente se quedaba
    con SILENCIO TOTAL: escribía "Hola" y no recibía absolutamente nada.

    Ante cualquier duda o error, devuelve True (el bot se CALLA): es el lado seguro. Callarse
    de más cuesta un mensaje; hablarle encima a la dueña delante de un cliente, en medio de un
    cobro, cuesta la venta y la confianza. OJO: esto es lo CONTRARIO de `_cliente_pausado`, que
    ante un error deja hablar al bot — son dos preguntas distintas con dos lados seguros
    distintos, y por eso NO comparten el except.
    """
    try:
        pausado, por = await _estado_pausa(telefono)
    except Exception:  # noqa: BLE001
        logger.exception("No sé quién pausó a %s → el bot se CALLA (lado seguro)", telefono)
        return True
    if not pausado:
        return False
    return por != "bot"


async def _estado_pausa(telefono: str) -> tuple[bool, str | None]:
    """(¿pausado?, ¿quién lo pausó?) — 'dueña' | 'bot' | 'privado' | None.

    PROPAGA la excepción a propósito: cada quien tiene su lado seguro (el bot sigue hablando
    si no sabemos si está pausado; el bot se CALLA si no sabemos QUIÉN lo pausó). Tragarse el
    error aquí obligaba a los dos a compartir el mismo, y uno de los dos quedaba mal.

    🔴 'privado' ES EL **SEGUNDO CINTURÓN** DE LOS CONTACTOS PRIVADOS (migración 031). El freno de
    verdad está en el webhook (`_es_contacto_privado`), que corta antes de guardar nada y antes de
    gastar un céntimo de IA. Este de aquí atrapa lo que YA venía en vuelo cuando ella apretó el
    interruptor: el mensaje sentado en el buffer de 15 s, un `retomar_chat` ya encolado, un
    comprobante a medio camino. Sin él, marcar a alguien como privado no callaría al bot hasta el
    mensaje SIGUIENTE — y el que está en vuelo es justo el que la hizo darse cuenta.
    Son dos cinturones, no uno sustituyendo al otro (misma doctrina que META-1).

    Devuelve 'privado' y no 'dueña' para no mentir en el log ni en el motivo. Los DOS llamadores
    ya lo tratan bien sin tocarlos: `_cliente_pausado` solo mira el booleano, y
    `_lo_paso_una_persona` hace `por != "bot"` ⇒ True ⇒ el bot se CALLA, que es lo que queremos.

    ⚠️ 'privado' NUNCA SE ESCRIBE EN `clientes.pausado_por`: el CHECK `ck_cliente_pausado_por`
    (migración 020) solo admite 'dueña' | 'bot' | NULL. Esto es un valor de RETORNO en memoria,
    no una fila. Un INSERT/UPDATE con 'privado' reventaría — y no lo hay.
    """
    from sqlalchemy import select

    from app.models import Cliente
    from app.services.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        cliente = (
            await session.execute(select(Cliente).where(Cliente.telefono == telefono))
        ).scalar_one_or_none()
    if cliente is None:
        return False, None
    # Va ANTES de mirar `bot_pausado`: un contacto privado calla al bot AUNQUE su chat no esté
    # pausado. Son dos cosas distintas — la pausa es de un rato, esto es de siempre.
    if cliente.privado:
        return True, "privado"
    if not cliente.bot_pausado:
        return False, None
    return True, cliente.pausado_por


async def _guardar_entrante(telefono: str, nombre: str | None, texto: str) -> bool:
    """Guarda SOLO el mensaje entrante del cliente (sin respuesta), para que la
    dueña lo vea en Conversaciones cuando el bot está apagado y responda ella.

    Devuelve `True` si quedó escrito. Es la escritura MÁS importante de las dos (SIL-15): cubre
    justo los casos en los que ELLA es la única que va a contestar —bot apagado, chat tomado, o
    el bot se cayó sin poder responder—, así que un fallo silencioso aquí le deja el globito de
    "no leído" sobre una conversación en la que no hay nada que leer.
    """
    from sqlalchemy import select

    from app.models import Cliente, Mensaje, now_utc
    from app.services.db import get_session_factory

    async def _escribir() -> None:
        factory = get_session_factory()
        async with factory() as session:
            cliente = (
                await session.execute(select(Cliente).where(Cliente.telefono == telefono))
            ).scalar_one_or_none()
            if cliente is None:
                session.add(Cliente(telefono=telefono, nombre=nombre, ultima_interaccion=now_utc()))
            else:
                cliente.ultima_interaccion = now_utc()
                if nombre and not cliente.nombre:
                    cliente.nombre = nombre
            session.add(Mensaje(cliente_telefono=telefono, rol="user", contenido=texto))
            await session.commit()

    if await _escribir_en_panel(_escribir, telefono, "el mensaje entrante"):
        return True
    await _hueco_en_el_panel(telefono, texto, [])
    return False


async def _numero_permitido(telefono: str) -> bool:
    """LISTA BLANCA de pruebas: si la lista NO esta vacia, el bot SOLO responde a esos numeros;
    a los demas les guarda el mensaje pero NO responde (probar en produccion sin contestarle a
    clientes reales). Vacia = responde a todos. Compara por la COLA de 10 digitos, asi tolera el
    codigo de pais (+57, 0058, +593...).

    La lista sale de DOS sitios que se SUMAN:
      · settings.numeros_permitidos (variable de entorno, la fija) y
      · el config `numeros_permitidos_extra` (editable SIN redeploy: para añadir un numero de
        prueba al vuelo sin tocar Coolify — que reiniciaria el contenedor).
    """
    from sqlalchemy import select

    from app.models import Configuracion
    from app.services.db import get_session_factory

    # Los teléfonos INTERNOS (simulador del panel, bancos de prueba) empiezan por "__" y NUNCA
    # son un WhatsApp real: pasan siempre, la lista blanca es solo para números de verdad. Sin
    # esto, poner un número real en `numeros_permitidos_extra` volvía la lista NO vacía y de
    # rebote bloqueaba a `__prueba_dinero__` / `__simulador__` (rompía los bancos).
    if (telefono or "").startswith("__"):
        return True

    permitidos = (settings.numeros_permitidos or "").strip()
    extra = ""
    try:
        factory = get_session_factory()
        async with factory() as s:
            extra = (
                await s.execute(
                    select(Configuracion.valor).where(
                        Configuracion.clave == "numeros_permitidos_extra"
                    )
                )
            ).scalars().first() or ""
    except Exception:  # noqa: BLE001 — si la BD falla, manda solo la lista de entorno
        pass

    juntos = ",".join(x for x in (permitidos, extra.strip()) if x)
    if not juntos:
        return True  # sin lista blanca = responde a todos (produccion normal)

    def _cola(s: str) -> str:
        d = "".join(c for c in (s or "") if c.isdigit())
        return d[-10:] if len(d) >= 10 else d

    objetivo = _cola(telefono)
    return any(_cola(n) == objetivo for n in juntos.split(",") if n.strip())


# Cuántas veces se reintenta el turno cuando el lock está tomado, y cada cuánto.
# 8 × 20 s = 160 s de cobertura, A PROPÓSITO por encima de los 120 s que dura el lock
# (`adquirir_lock`, redis_client.py): aunque el worker que lo tenía se muera sin soltarlo, el
# lock caduca solo y el siguiente reintento entra. NO se sube más: cada reintento con countdown
# queda RESERVADO en la memoria del worker (Celery incrementa la ventana QoS), y con
# --concurrency=2 esa ventana es de 8 mensajes. Diez clientes × 15 reintentos serían ~150
# mensajes reservados contra una ventana de 8 ⇒ el carril del COMPROBANTE (el del DINERO)
# haría cola detrás de la basura de reintentos. Cobertura de sobra con la mitad de mensajes.
_REINTENTOS_BUFFER = 8
_ESPERA_BUFFER = 20


# ─── EL DEBOUNCE DEL BUFFER (la ráfaga se contesta UNA vez) ──────────
#
# 🔴 EL BUG (medido en el taller el 2026-08-08, tel …9792, logs del worker + tabla `mensajes`):
# la ventana de 15s estaba anclada al PRIMER mensaje y NO se reiniciaba, así que una tarea vieja
# barría los mensajes recién llegados. 22:25:40 "Como son las que tienes? Variada." (tarea para
# 22:25:55) · 22:25:47 "Tienes tortas?" (tarea para 22:26:02) · 22:25:55 la 1ª tarea consolida
# esos dos (`mensajes.id 4009`) ✅ · 22:25:57 "De chocolate" · 22:26:02 la 2ª tarea se lo lleva
# con solo 5s de espera ❌ → turno aparte (id 4013) y respuesta propia (id 4014). Tres mensajes
# en ráfaga, dos respuestas: el cliente ve al bot contestándole a trozos.
#
# EL ARREGLO es un debounce de verdad: se procesa cuando el cliente lleva `buffer_segundos`
# CALLADO, y cada mensaje nuevo reinicia la cuenta (`agregar_a_buffer` pisa la marca `ultimo`).
# La tarea que llega antes de tiempo NO vacía el buffer: se reprograma para lo que falte.


def _espera_restante(primero: float, ultimo: float, ahora: float) -> float:
    """Segundos que faltan para contestar. 0 = procesar YA. Pura a propósito (tests sin reloj).

    Dos relojes a la vez: el SILENCIO desde el último mensaje (la ventana que se reinicia) y el
    TOPE desde el primero (`buffer_max_segundos`), que manda siempre — un cliente que escribe sin
    parar no puede quedarse sin respuesta jamás. La espera se recorta para no pisar el tope, así
    que la respuesta nunca sale más tarde que `primero + buffer_max_segundos`.
    """
    falta_para_el_tope = (primero + settings.buffer_max_segundos) - ahora
    if falta_para_el_tope <= 0:
        return 0.0  # TOPE: lleva demasiado rato escribiendo, se le contesta aunque siga
    silencio = ahora - ultimo
    restante = settings.buffer_segundos - silencio
    if restante <= 0:
        return 0.0  # ya lleva callado la ventana entera
    # El `min` con `buffer_segundos` es cinturón contra una marca del FUTURO (relojes torcidos
    # entre contenedores): un dato raro puede hacer esperar, nunca colgar.
    return min(restante, falta_para_el_tope, float(settings.buffer_segundos))


async def _espera_del_buffer(telefono: str) -> float:
    """Lo mismo pero leyendo las marcas de Redis. SIN MARCA ⇒ 0 (procesar ya, nunca colgar)."""
    marcas = await rc.marcas_de_buffer(telefono)
    if marcas is None:
        return 0.0
    return _espera_restante(marcas[0], marcas[1], rc._ahora())


@celery_app.task(name="procesar_buffer", bind=True, max_retries=_REINTENTOS_BUFFER)
def procesar_buffer(self, telefono: str, nombre: str | None = None):
    """Tarea Celery: procesa los mensajes acumulados de un cliente y responde.

    🔴 OCUPADO NO ES "LISTO" (auditoría 2026-08-02, SIL-1). Antes, cuando no conseguía el lock,
    la tarea se iba sin hacer nada y SIN dejar otra programada. El caso real: t=0 llega "hola"
    → tarea a t+15; t=15 esa tarea toma el lock y se pone a pensar; t=20 llega "sí, dale, lo
    quiero" → tarea a t=35; a t=35 el lock sigue tomado ⇒ esa tarea se iba muda y el "sí, lo
    quiero" se pudría en el buffer hasta expirar (1 h). Ni el cliente, ni la dueña, ni el log.
    Reintentar aquí es GRATIS y no duplica nada: en esa rama no se vació el buffer, no se llamó
    al modelo y no se envió un solo globo.

    ⚠️ "ESPERANDO" NO ES "OCUPADO" (debounce, 2026-08-09). El camino nuevo —el cliente todavía
    está escribiendo— se reprograma DENTRO de `_procesar` con `apply_async` y sale por este mismo
    `return`. A propósito no pasa por `self.retry`: gastaría el presupuesto de 8 reintentos que
    está reservado para el lock tomado y, al agotarlo, dispararía la falsa alarma de
    `_avisar_turno_perdido` sobre un turno que no se ha perdido — sencillamente aún no toca.
    """
    if _run(_procesar(telefono, nombre)) != "ocupado":
        return
    try:
        self.retry(countdown=_ESPERA_BUFFER)
    except MaxRetriesExceededError:
        # 160 s con el lock tomado no pasa ni en el peor turno: algo está muy roto. El texto
        # sigue en el buffer (TTL 1 h) y el próximo mensaje del cliente lo arrastrará, pero eso
        # NO puede ser el plan: alguien tiene que enterarse.
        logger.error(
            "Buffer de %s: %s reintentos y el lock sigue tomado; se avisa",
            telefono, _REINTENTOS_BUFFER,
        )
        try:
            _run(_avisar_turno_perdido(telefono, nombre, "(el bot no logró tomar el turno)"))
        except Exception:  # noqa: BLE001 — con Redis caído el aviso también revienta; que no
            # se lleve por delante la tarea entera: el log de arriba ya dejó el rastro.
            logger.exception("Y el aviso del turno perdido de %s tampoco pudo", telefono)


async def _procesar(telefono: str, nombre: str | None) -> str:
    """Un turno de texto de punta a punta. Devuelve el veredicto para que el que llama decida:
    "ocupado" (hay que REENCOLAR), "esperando" (ya se reprogramó sola), "vacio", "apagado",
    "sin_envio", "error", "ok"."""
    # Solo un worker procesa el buffer de este cliente a la vez. Ver SIL-1: "ocupado" NO es
    # "listo" — el que llama REENCOLA.
    if not await rc.adquirir_lock(telefono):
        logger.info("Buffer de %s: hay un turno en curso; se reencola", telefono)
        return "ocupado"

    texto = ""
    guardado = False  # ¿el turno del cliente ya quedó ESCRITO en Postgres?
    try:
        # DEBOUNCE: ¿el cliente sigue escribiendo? Va DESPUÉS del lock (si hay un turno en curso
        # manda SIL-1, que es otra cosa) y ANTES de `vaciar_buffer` (que es atómico: una vez
        # vaciado ya no hay marcha atrás). El `finally` suelta el lock también por aquí — si no,
        # la siguiente tarea vería "ocupado" contra un turno que no existe.
        espera = await _espera_del_buffer(telefono)
        if espera > 0:
            logger.info("Buffer de %s: sigue escribiendo; se reprograma en %.1fs", telefono, espera)
            procesar_buffer.apply_async((telefono, nombre), countdown=espera)
            return "esperando"

        mensajes = await rc.vaciar_buffer(telefono)
        if not mensajes:
            return "vacio"  # otra tarea ya lo procesó

        texto = "\n".join(mensajes)
        # CUÁNDO escribió el cliente (para el orden del hilo en el panel, ver `_guardar_en_panel`).
        # Se toma aquí, antes de pensar y antes de enviar nada: cualquier media de este turno sale
        # después, así que en el hilo la pregunta queda por delante de la foto que la provocó.
        from app.models import now_utc

        ts_turno = now_utc()

        if not await _bot_activo() or await _cliente_pausado(telefono) or not await _numero_permitido(telefono):
            # Bot apagado (global o solo en este chat): guarda lo que escribió el
            # cliente para que la dueña lo vea en Conversaciones y responda ella.
            await rc.guardar_historial(telefono, "user", texto)
            guardado = await _guardar_entrante(telefono, nombre, texto)
            return "apagado"

        # 🔴 CON RESPALDO EN POSTGRES (fallo del 2026-08-18): si el historial de Redis ya expiró
        # (24 h de `conversacion_ttl`), se reconstruye desde la tabla `mensajes`. Sin esto el bot
        # arranca de cero con una clienta que le escribió hace tres días — y con cuatro redes
        # ciegas, porque reciben `historial` por parámetro. Ver `services/memoria.py`.
        # `sembrar=True`: aquí tenemos el LOCK del teléfono, y sin dejarlo en Redis el turno
        # SIGUIENTE volvería a olvidarlo todo (ya no encontraría `hist:` vacía).
        historial = await historial_con_respaldo(telefono, sembrar=True)

        # 🔴 EL TURNO DEL CLIENTE SE ANOTA ANTES DE PENSAR (auditoría 2026-08-02, SIL-10).
        # `vaciar_buffer` es LRANGE+DELETE atómico: desde esa línea, lo que el cliente escribió
        # no existe en NINGÚN otro sitio. Esta línea vivía DESPUÉS de `responder()`, así que un
        # 402 de OpenRouter (sin saldo — ya nos costó una semana de mensajes mudos) lo borraba
        # del mapa: ni en Redis, ni en la tabla `mensajes`. Y el webhook ya le había sumado 1 a
        # `no_leidos`, o sea que el panel mostraba el globito de no leído sobre una conversación
        # sin nada nuevo dentro. VA DESPUÉS DE LEER `historial` A PROPÓSITO: `responder()` recibe
        # el turno aparte, y si además ya estuviera en la lista el modelo lo leería DOS VECES.
        await rc.guardar_historial(telefono, "user", texto)

        respuesta = await responder(telefono, texto, historial, nombre)
        respuesta = _proteger_afirmacion_de_pago(respuesta)

        partes = await _enviar_en_partes(telefono, respuesta)
        if not partes:
            # La dueña tomó el chat mientras el bot pensaba: su respuesta se DESCARTA
            # (no se envía ni se recuerda). Lo que sí se guarda es lo que dijo el cliente,
            # para que ella lo vea y le conteste.
            guardado = await _guardar_entrante(telefono, nombre, texto)
            return "sin_envio"
        # Los globos FALLIDOS también se guardan (se ven en ROJO en el panel), pero el bot no
        # "recuerda" haber dicho algo que el cliente nunca recibió (`_lo_que_llego`, SIL-8).
        if _algo_llego(partes):
            await rc.guardar_historial(telefono, "assistant", _lo_que_llego(partes, respuesta))
        guardado = await _guardar_en_panel(telefono, nombre, texto, partes, ts_usuario=ts_turno)
        await _avisar_turno_a_medias(telefono, nombre, partes)
    except Exception:  # noqa: BLE001
        logger.exception("Error procesando el buffer de %s (texto=%r)", telefono, texto[:200])
        # SIL-10, la segunda mitad: el buffer YA está vacío. Si nadie alcanzó a escribir el
        # turno en Postgres, se escribe AQUÍ aunque el bot no haya podido contestar. Un mensaje
        # sin respuesta se ve y se atiende; un mensaje que no existe, no.
        try:
            if texto and not guardado:
                await _guardar_entrante(telefono, nombre, texto)
                await _avisar_turno_perdido(telefono, nombre, texto)
        except Exception:  # noqa: BLE001 — el RESCATE no puede tumbar el turno que YA se cayó.
            # Sin este try: con Redis caído (que es UNA de las averías que traen aquí), la avería
            # típica no viene sola — `_guardar_entrante` falla contra Postgres y `aviso_unico`
            # falla contra Redis, DENTRO del except. La excepción se escaparía de `_procesar`,
            # se llevaría el `return "error"` por delante y la tarea moriría con un traceback en
            # vez de con un veredicto. Que el rescate falle es aceptable; que se lleve el turno
            # entero, no.
            logger.exception("Y el rescate del turno de %s tampoco pudo", telefono)
        return "error"
    finally:
        await rc.liberar_lock(telefono)
    return "ok"


# ─── RETOMAR: la dueña devolvió el chat y el cliente quedó esperando ─
#
# EL HUECO (lo reportó Maired con una captura real): mientras la dueña tiene el chat tomado, el
# cliente sigue escribiendo ("¿cuánto sería en Bs?", "quedo pendiente del monto"). Al devolverle
# el chat al bot, el bot NO contestaba: "Devolver al bot" solo apagaba la bandera de pausa, y el
# bot únicamente habla cuando ENTRA un mensaje nuevo por el webhook. Esos pendientes YA habían
# entrado ⇒ nadie disparaba nada ⇒ silencio, y la venta se moría ahí. Faltaba el DISPARADOR.
#
# Esto es RESPUESTA, no envío proactivo: el cliente escribió y está esperando, y el botón que
# aprieta la dueña ES la aprobación humana. Por eso es seguro con Meta.

# 🔥 AUTO-BLINDAJE (ensayo general del 2026-07-13): la PRIMERA versión de esta instrucción decía
# "la dueña te devolvió el chat, RESPÓNDELE TÚ" — y el modelo lo leyó como "ahora la dueña eres
# tú". Al cliente que pidió *"quiero hablar con una persona de verdad, no con una máquina"* le
# contestó: **"Soy Whuilianny, la dueña de masvidaconsciente"**. MINTIÓ SOBRE SER HUMANA — la falla
# más grave que hay, y por el camino normal el bot NO la comete (ahí escala con `pedir_ayuda`).
# Lección: al devolverle el turno al modelo hay que RE-ANCLAR quién es; una orden ambigua sobre el
# relevo se lee como un cambio de identidad. La regla vive en `_REGLAS`, pero esta orden la pisaba.
# 🔥 Y LA SEGUNDA VERSIÓN TAMBIÉN FALLÓ, POR OTRO LADO (mismo ensayo). Decía "lee LO ÚLTIMO que
# escribió el cliente" — y el modelo se ancló en la ÚLTIMA LÍNEA, perdiendo lo que el cliente había
# pedido antes. Al cliente que escribió "quiero hablar con una PERSONA de verdad" y luego "¿sigue
# ahí alguien?", el bot le contestó "Sí, aquí estoy 💚 ¿En qué te puedo ayudar?": cero herramientas,
# cero `pedir_ayuda`, cero aviso. Se comió la petición y NADIE se enteró. (Por el camino normal el
# bot SÍ escala: la anteojera la puse yo.) Lo pendiente casi nunca es UN mensaje: es un BLOQUE.
_INSTRUCCION_RETOMAR = (
    "[SISTEMA] Vuelves a atender este chat (la dueña te lo devolvió). Mira TODO lo que el cliente "
    "escribió y quedó SIN RESPONDER —puede ser más de un mensaje— y contéstale lo que pidió, "
    "retomando donde quedó. Incluye lo que pidió ANTES de que la dueña entrara si todavía está sin "
    "resolver: el cliente sigue esperando eso. "
    "SIGUES SIENDO LA MISMA DE SIEMPRE: la asistente virtual del negocio. NO eres la dueña ni una "
    "persona; si el cliente pide hablar con una persona, llama a `pedir_ayuda` (motivo "
    "'pide_persona') como siempre. "
    "No repitas lo que ya se dijo, no vuelvas a saludar ni a presentarte, y no menciones este "
    "aviso ni que estuviste ausente. Si te falta un dato, pídeselo al cliente o llama a la "
    "herramienta que lo dé: jamás inventes un precio ni un monto."
)

# El caso ESTRELLA: el bot escaló (no sabía el precio del día), la dueña lo cargó y le devolvió el
# chat. Lo que le faltaba YA ESTÁ en el sistema — pero solo lo verá si vuelve a preguntárselo a la
# herramienta. Si no se le dice esto, el modelo "recuerda" que no lo sabía y se queda ahí.
_INSTRUCCION_RETOMAR_ESCALADO = (
    "[SISTEMA] Le pediste ayuda a la dueña sobre este chat y ella YA la resolvió: el dato que te "
    "faltaba (por ejemplo, el precio del día) ya está cargado en el sistema. VUELVE A CONSULTARLO "
    "con tus herramientas —no des por hecho que sigue faltando— y dale al cliente la respuesta que "
    "le prometiste, retomando la venta donde quedó. "
    "SIGUES SIENDO LA MISMA DE SIEMPRE: la asistente virtual del negocio. NO eres la dueña ni una "
    "persona. No vuelvas a saludar ni a presentarte, no repitas lo que ya se dijo y no menciones "
    "este aviso. Y si el dato SIGUE sin estar, NO lo inventes: llama otra vez a `pedir_ayuda`."
)


async def _ventana_abierta(telefono: str) -> bool:
    """¿Se le puede escribir texto libre a este cliente AHORA? (la regla de las 24h de Meta).

    FAIL-CLOSED: ante cualquier duda (el cliente no existe, no hay fecha, falla la BD) devuelve
    False y el bot NO envía. Un envío fuera de ventana lo rechaza Meta y le baja la calidad al
    número; siendo Enova Tech Provider, eso arriesga la cuenta de Meta de TODOS los clientes.

    El flujo normal (webhook) nunca necesita esto: el cliente ACABA de escribir, así que la
    ventana está abierta por definición. Aquí sí: entre el último mensaje del cliente y el
    momento en que la dueña devuelve el chat pueden haber pasado horas o días. Y `_enviar_en_partes`
    no la valida por su cuenta.

    Se reusa `_ventana` del panel a propósito: la regla de las 24h vive en UN solo sitio.
    """
    from sqlalchemy import select

    from app.api.router import _ventana
    from app.models import Cliente

    try:
        factory = get_session_factory()
        async with factory() as session:
            cliente = (
                await session.execute(select(Cliente).where(Cliente.telefono == telefono))
            ).scalar_one_or_none()
        if cliente is None:
            return False
        return bool(_ventana(cliente)["abierta"])
    except Exception:  # noqa: BLE001
        logger.exception("No sé si la ventana de %s está abierta → NO se envía (lado seguro)", telefono)
        return False


async def _avisar_ventana_cerrada(telefono: str, nombre: str | None) -> None:
    """La dueña devolvió el chat, pero pasaron +24h desde el último mensaje del cliente: WhatsApp
    no deja escribirle texto libre. El bot NO envía nada (lado seguro) — y se lo dice a ELLA, o el
    silencio se vería exactamente igual que el bug que vinimos a arreglar."""
    from sqlalchemy import select

    from app.models import Configuracion, Intervencion

    quien = nombre or telefono
    try:
        factory = get_session_factory()
        async with factory() as session:
            session.add(Intervencion(
                cliente_telefono=telefono,
                motivo="ventana_cerrada",
                detalle=(
                    f"Le devolviste el chat de {quien} al bot, pero pasaron más de 24 horas desde "
                    "su último mensaje: WhatsApp NO deja escribirle texto libre hasta que él "
                    "vuelva a escribir. El bot no le mandó nada."
                ),
                mensaje_cliente="(quedó esperando respuesta)",
            ))
            fila = (
                await session.execute(
                    select(Configuracion).where(Configuracion.clave == "dueno_telefono")
                )
            ).scalar_one_or_none()
            await session.commit()
        destino = (fila.valor if fila else None) or settings.dueno_telefono
        if destino:
            await _whatsapp_a_la_duena(
                destino,
                f"⏰ Le devolviste el chat de {quien} al bot, pero pasaron más de 24 horas desde "
                "su último mensaje: WhatsApp no deja escribirle. El bot NO le respondió nada.",
                que=f"ventana cerrada de {telefono}",
            )
    except Exception:  # noqa: BLE001 — el aviso es lo único que hay aquí; que no tumbe al worker
        logger.exception("No se pudo avisar de la ventana cerrada de %s", telefono)


# 🔴🔴 EL CASO ESTRELLA, QUE NO FUNCIONABA (auditoría de arquitectura, 2026-07-13).
#
# El ROADMAP promete esto: *"pon el precio del día y devuelve el chat: el bot lo venderá solo"*.
# Probado con el bot vivo, hacía ESTO:
#     cliente: "¿cuánto la torta keto de 1kg?"  →  el bot NO lo sabe (precio del día) → escala:
#     le deja el aviso a la dueña y le dice al cliente "te lo confirmo enseguida".
#     La dueña pone el precio y aprieta "Ya lo atendí (reactivar el bot)".
#     El bot… SE QUEDA MUDO. El cliente nunca se entera del precio. Se pierde la venta.
#     Y la dueña se queda creyendo que el bot le contestó.
#
# La causa era MI guard: preguntaba "¿el último mensaje es del cliente?" — y NO lo es: el último
# es el del propio bot ("te lo confirmo enseguida"). Así que concluía "aquí no hay nada pendiente".
#
# El error de fondo: **el mensaje del bot al escalar NO es una respuesta, es un pagaré.** La
# pregunta del cliente sigue viva. Por eso ahora el disparador trae la FIRMA de la pausa:
#   · pausado_por='bot'   → el bot escaló y NADIE le ha contestado al cliente ⇒ el bot habla.
#   · pausado_por='dueña' → ella tomó el chat ⇒ solo habla si el cliente escribió DESPUÉS.
# (Si ella contesta —por el panel o desde su celular— la firma pasa a 'dueña' sola, así que el
#  bot nunca le habla encima.)

@celery_app.task(name="retomar_chat")
def retomar_chat(telefono: str, nombre: str | None = None, pausado_por: str | None = None):
    """Tarea Celery: la dueña devolvió el chat → el bot contesta lo que quedó pendiente."""
    _run(_retomar(telefono, nombre, pausado_por))


async def _retomar(telefono: str, nombre: str | None, pausado_por: str | None = None) -> None:
    # EL MISMO lock por-teléfono que usa el buffer: si justo ahora el bot ya está contestando un
    # mensaje nuevo de ese cliente, no hay nada que retomar (ese turno ya arrastra los pendientes).
    # Se sale ANTES de gastar el candado, para que la dueña pueda volver a apretar.
    if not await rc.adquirir_lock(telefono):
        logger.info("Retomar %s: ya hay un turno en curso; no hace falta disparar", telefono)
        return
    try:
        # Doble click (o los dos caminos de resume a la vez) ⇒ UNA sola respuesta.
        if not await rc.candado_retomar(telefono):
            logger.info("Retomar %s: ya se disparó hace un momento (doble click)", telefono)
            return

        if not await _bot_activo() or not await _numero_permitido(telefono):
            return

        # Entre el click y esta tarea, la dueña pudo volver a tomar el chat (o el bot pudo
        # pausarse solo al escalar algo). Si está pausado, el bot no habla: punto.
        if await _cliente_pausado(telefono):
            logger.info("Retomar %s: el chat está pausado otra vez; el bot no habla", telefono)
            return

        # 🔴 ESTE CARRIL SE QUEDA SIN RESPALDO DE POSTGRES, A PROPÓSITO (decisión del 08-20).
        # Aquí el historial no es contexto: es el GUARD DE HONESTIDAD de tres líneas más abajo
        # (`if not historial: return`). Hoy, con la memoria expirada, el bot se CALLA — que es el
        # fallo seguro. Rescatando de Postgres empezaría a hablarle a quien escribió hace días
        # porque su último turno "quedó pendiente", y eso es un envío PROACTIVO: la regla dura de
        # Tech Provider con Meta (ningún proactivo sin aprobación humana) manda por encima de la
        # comodidad de recordar. Si algún día se quiere, va con su propio diseño y su propio A/B.
        historial = await rc.obtener_historial(telefono)

        # ¿El bot había ESCALADO? Entonces su último mensaje ("te lo confirmo enseguida") NO es una
        # respuesta: es un PAGARÉ. La pregunta del cliente sigue viva y hay que pagarla.
        venia_de_escalada = pausado_por == "bot"

        # GUARD DE HONESTIDAD: el bot solo habla si el cliente quedó ESPERANDO. Si la dueña ya le
        # contestó a mano (el último turno es de ella), abrir la boca sería un envío PROACTIVO
        # —lo que Meta prohíbe sin aprobación humana— y encima le hablaría encima.
        if not historial:
            return
        if historial[-1].get("role") != "user" and not venia_de_escalada:
            logger.info(
                "Retomar %s: no hay nada pendiente (el último turno no es del cliente)", telefono
            )
            return

        # LA VENTANA DE 24H, FAIL-CLOSED. Va DESPUÉS del guard a propósito: si no había nada
        # pendiente, no hay por qué molestar a la dueña con un aviso de ventana cerrada.
        if not await _ventana_abierta(telefono):
            logger.warning("Retomar %s: ventana de 24h CERRADA → el bot NO escribe", telefono)
            await _avisar_ventana_cerrada(telefono, nombre)
            return

        # Lo que escribió el cliente YA está en el historial: NO se reinyecta como mensaje (se
        # duplicaría el turno). Lo que va en su lugar es una orden EFÍMERA de sistema, que el bot
        # lee y NO se guarda en ningún lado: en la memoria solo queda su respuesta.
        #
        # `pregunta_cliente`: lo que el cliente preguntó DE VERDAD. Sin esto, si el bot vuelve a
        # escalar, el aviso de la bandeja le decía a la dueña: *El cliente preguntó: "[SISTEMA]
        # Vuelves a atender este chat…"*. Basura, justo donde ella mira para entender qué pasa.
        ultima_del_cliente = next(
            (h.get("content") for h in reversed(historial) if h.get("role") == "user"), ""
        )
        instruccion = _INSTRUCCION_RETOMAR_ESCALADO if venia_de_escalada else _INSTRUCCION_RETOMAR
        respuesta = await responder(
            telefono, instruccion, historial, nombre, pregunta_cliente=ultima_del_cliente
        )
        respuesta = _proteger_afirmacion_de_pago(respuesta)

        partes = await _enviar_en_partes(telefono, respuesta)
        if not partes:
            # La dueña volvió a tomar el chat mientras el bot pensaba (~20s): su respuesta se
            # DESCARTA (ni se envía ni se recuerda). Lo que dijo el cliente ya está guardado.
            return
        if _algo_llego(partes):
            await rc.guardar_historial(telefono, "assistant", _lo_que_llego(partes, respuesta))
        # texto_usuario="" a propósito: lo que dijo el cliente YA está en `mensajes` (se guardó
        # cuando llegó, durante la pausa). Volver a insertarlo lo duplicaría en el hilo del panel.
        await _guardar_en_panel(telefono, nombre, "", partes)
    except Exception:  # noqa: BLE001
        logger.exception("Error retomando el chat de %s", telefono)
    finally:
        await rc.liberar_lock(telefono)


# ─── Comprobantes de pago (imagenes / PDF) ───────────────────────────

_EXT_POR_MIME = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "application/pdf": "pdf",
}


def _guardar_comprobante(media_id: str, contenido: bytes, mime: str) -> str:
    """Guarda el binario del comprobante en COMPROBANTES_DIR y devuelve la ruta."""
    os.makedirs(settings.comprobantes_dir, exist_ok=True)
    base_mime = (mime or "").split(";")[0].strip().lower()
    ext = _EXT_POR_MIME.get(base_mime, "bin")
    ruta = os.path.join(settings.comprobantes_dir, f"{media_id}.{ext}")
    with open(ruta, "wb") as f:
        f.write(contenido)
    return ruta


# Cuánto disco libre hace falta para dormir tranquilo. Por debajo de esto, la próxima captura
# de pago puede no caber — y un comprobante que no se guarda es una discusión con un cliente.
_DISCO_MINIMO_BYTES = 500 * 1024 * 1024


async def _vigilar_disco_comprobantes() -> None:
    """Avisa si a `/data/comprobantes` se le está acabando el sitio. NUNCA borra nada.

    🔴 La otra mitad de META-13 (auditoría 2026-08-02) pedía "limpieza": cada comprobante se
    escribe ahí PARA SIEMPRE, sin cuota ni rotación. **Y aun así aquí no se borra nada, a
    propósito.** Estos archivos son el respaldo de PAGOS de un negocio real: son la prueba que
    la dueña abre cuando un cliente dice "yo ya te pagué". Un barrido automático que se lleve
    por delante el comprobante equivocado es un daño que no se puede deshacer, y el ahorro (unos
    megas) no se le parece ni de lejos. Lo que sí puede hacer el sistema es lo que nunca hizo:
    DECIRLO a tiempo, para que se archive o se agrande el volumen con calma. Un aviso al día.
    """
    try:
        import shutil

        libre = shutil.disk_usage(settings.comprobantes_dir).free
    except Exception:  # noqa: BLE001 — mirar el disco jamás puede tumbar el carril del dinero
        return
    if libre >= _DISCO_MINIMO_BYTES:
        return
    megas = libre // (1024 * 1024)
    logger.error(
        "DISCO DE COMPROBANTES BAJO: quedan %s MB libres en %s", megas, settings.comprobantes_dir
    )
    await _avisar_a_la_duena(
        "__sistema__",
        motivo="disco_lleno",
        detalle=(
            f"Al servidor le quedan {megas} MB libres donde se guardan los comprobantes de pago. "
            "Si se llena, las próximas capturas no se van a poder guardar (el pago se registra "
            "igual, pero la imagen se pierde). Avísale a Enova para agrandar el espacio."
        ),
        mensaje_cliente="(aviso del sistema)",
        whatsapp=(
            f"💾 Al servidor le quedan {megas} MB libres para guardar comprobantes. Avísale a "
            "Enova antes de que se llene."
        ),
        candado=("disco_comprobantes", 86400),
    )


async def _leer_comprobante_seguro(telefono, contenido, base_mime) -> dict:
    """Lee el comprobante con visión, dándole TODAS las cuentas de pago de la dueña
    (tabla metodos_pago) para reconocer SOLO pagos hacia alguna de ellas. Nunca lanza."""
    # 📊 El turno del COMPROBANTE empieza aquí (telemetría, 032): `leer_comprobante` recibe bytes,
    # no sabe de quién es el pago. Y el mensaje que la Voz le escriba después al cliente cae en
    # este mismo turno (`redactar_mensaje` no pisa un turno abierto), así que "lo que cuesta
    # procesar un pago" queda medido de punta a punta.
    abrir_turno(telefono, "comprobante")
    from sqlalchemy import select

    from app.models import MetodoPago

    cuentas: list[dict] = []
    try:
        factory = get_session_factory()
        async with factory() as session:
            metodos = (
                await session.execute(select(MetodoPago).where(MetodoPago.activo.is_(True)))
            ).scalars().all()
        cuentas = [
            {
                "titular": m.titular,
                "banco": m.banco,
                "telefono": m.telefono,
                "cedula": m.cedula,
                "cuenta": m.cuenta,
                "correo": m.correo,
                "wallet": m.wallet,
            }
            for m in metodos
        ]
    except Exception:  # noqa: BLE001
        pass
    try:
        return await leer_comprobante(contenido, base_mime, cuentas=cuentas)
    except Exception:  # noqa: BLE001 — defensa extra: nunca tumbar el worker
        logger.exception("Fallo leyendo el comprobante de %s", telefono)
        return {"es_comprobante": None, "leido": False}


async def _responder_situacion(
    telefono: str, situacion: str, nombre: str | None
) -> list[dict]:
    """Whuilianny REDACTA un mensaje para el cliente según la situación (no plantilla),
    lo protege contra afirmaciones de pago, lo envía en partes y lo guarda en historial.

    🔴 El INTERRUPTOR no cubría este carril (auditoría 2026-07-13): con el bot APAGADO desde el
    panel, un cliente que mandaba su comprobante RECIBÍA respuesta igual. El pago se registra
    siempre (el dinero nunca se pierde), pero si la dueña apagó el bot, el bot NO habla.
    """
    if not await _numero_permitido(telefono) or not await _bot_activo():
        return []  # bot apagado o fuera de la lista blanca: se registra el pago, pero no se habla
    try:
        # Con respaldo (fallo del 08-18), pero `sembrar=False`: este carril lo dispara el worker
        # de VISIÓN y no siempre tiene el lock del teléfono. Aquí el historial es solo CONTEXTO
        # para redactar — los montos viajan aparte, en `montos_usd`/`montos_bs`—, así que leer de
        # más no toca el camino del dinero; sembrar sin lock sí podría duplicar.
        historial = await historial_con_respaldo(telefono)
        _usd, _bs = await _montos_decibles(telefono)
        mensaje = await redactar_mensaje(
            situacion, historial, nombre, telefono, montos_usd=_usd, montos_bs=_bs
        )
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo redactar el mensaje al cliente %s", telefono)
        return []
    mensaje = _proteger_afirmacion_de_pago(mensaje or "")
    if not mensaje.strip():
        # La red del dinero tumbó el mensaje (montos inventados o una frase prohibida) y el modelo
        # insistió. NO se le manda una mentira al cliente — pero tampoco se le deja en silencio
        # justo cuando acaba de pagar: acuse sobrio + la dueña se entera.
        logger.error("Carril del dinero: no salió un mensaje limpio para %s; acuse seguro", telefono)
        partes = await _enviar_en_partes(telefono, _RESPUESTA_PAGO_SEGURA)
        if _algo_llego(partes):
            await rc.guardar_historial(
                telefono, "assistant", _lo_que_llego(partes, _RESPUESTA_PAGO_SEGURA)
            )
        if partes:
            await _guardar_en_panel(telefono, nombre, "", partes)
        await _avisar_mensaje_frenado(telefono, nombre)
        return partes
    partes = await _enviar_en_partes(telefono, mensaje)
    # Si no se envió (la dueña tomó el chat), NO se guarda en el historial: el bot no puede
    # "recordar" haber dicho algo que el cliente nunca vio. Y si salió A MEDIAS, solo se recuerda
    # lo que SÍ llegó (`_lo_que_llego`, SIL-8) — en el carril del dinero eso es la diferencia
    # entre repetir los datos de la cuenta o no volver a darlos nunca.
    if _algo_llego(partes):
        await rc.guardar_historial(telefono, "assistant", _lo_que_llego(partes, mensaje))
    # Y AHORA SÍ se guarda en el panel: hasta hoy, TODO este carril (el del comprobante)
    # NO escribía una sola línea en `mensajes` — en el hilo del panel ese tramo estaba EN
    # BLANCO, y la dueña tenía que responder a ciegas justo en el momento del dinero.
    if partes:
        await _guardar_en_panel(telefono, nombre, "", partes)
    return partes


def _a_float(x):
    """Convierte un monto leído ('39.480,47' o '39480.47') a float. None si no se puede."""
    s = "".join(c for c in str(x or "") if c.isdigit() or c in ".,")
    if not s:
        return None
    if "," in s:  # formato venezolano: 39.480,47 -> 39480.47
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


async def _montos_decibles(telefono: str) -> tuple[set[float], set[float]]:
    """LA LISTA CERRADA de montos que el bot puede decir en el carril del DINERO: (dólares, bolívares).

    Son los que el CÓDIGO cobró de verdad en esta conversación (la cotización que guardó
    `generar_datos_pago`). Nada más. El catálogo NO entra: aquí el bot no está cotizando productos,
    está hablando de UN pago — y autorizar el catálogo entero fue justo lo que dejaba pasar el "$12"
    inventado (12 = precio de otro producto).

    🔴 Y van SEPARADOS POR MONEDA. Devolverlos en un solo saco era repetir el bug del "$23": el bot
    llamaba "bolívares" a una cifra en dólares y la red la daba por buena porque el número estaba
    en la lista. Un dólar solo autoriza dólares; un bolívar solo autoriza bolívares.
    """
    bs, usd, divisas = await _montos_cobrados(telefono)
    return (
        {m for m in (usd, divisas) if m is not None},   # dólares
        {m for m in (bs,) if m is not None},            # bolívares
    )


async def _montos_cobrados(telefono: str):
    """Devuelve (monto_bs, monto_usd, monto_usd_divisas) que el bot le cobró (de la
    cotización en Redis). El comprobante puede venir en Bs (Pago Móvil/transferencia),
    en USD pleno, o en USD con 20% de descuento (divisas: Zelle/Binance/efectivo)."""
    try:
        guardado = await rc.get_cache(f"cobro:{telefono}")
        if guardado:
            d = json.loads(guardado)
            return (
                _a_float(d.get("monto_bs")),
                _a_float(d.get("monto_usd")),
                _a_float(d.get("monto_usd_divisas")),
            )
    except Exception:  # noqa: BLE001
        pass
    # 🔁 RESPALDO DURADERO (migración 027). La clave de Redis dura 24h, y aquí cotizar un día y
    # pagar al siguiente es lo NORMAL: los pedidos van con días de anticipación. Sin este respaldo
    # no había NADA con que comparar el comprobante, y eso —con el fail-open que también se corrigió
    # hoy— hacía que un pago de Bs 5.000 sobre una venta de Bs 16.591 disparara el mensaje feliz.
    try:
        from sqlalchemy import select

        from app.models import Pedido

        factory = get_session_factory()
        async with factory() as s:
            ped = (
                await s.execute(
                    select(Pedido)
                    .where(
                        Pedido.cliente_telefono == telefono,
                        Pedido.cotizado_at.is_not(None),
                    )
                    .order_by(Pedido.cotizado_at.desc())
                )
            ).scalars().first()
            if ped is not None:
                return (
                    _a_float(ped.cotizado_bs),
                    _a_float(ped.cotizado_usd),
                    _a_float(ped.cotizado_usd_divisas),
                )
    except Exception:  # noqa: BLE001 — nunca tumbar el carril del comprobante por el respaldo
        logger.exception("No se pudo leer la cotización de respaldo de %s", telefono)
    return None, None, None


def _monto_cuadra(leido: float | None, esperados) -> bool:
    """¿El monto que la visión leyó en el comprobante calza con ALGUNO de los que se le cobraron?

    🔴 FAIL-CLOSED, y esa es toda la gracia (auditoría 2026-08-02, DIN-5). Antes esta decisión
    vivía inline y arrancaba en `True`, reevaluándose solo `if leido is not None and candidatos`.
    O sea: **cuando no había con qué comparar, el sistema daba el monto por bueno**. Y quedarse sin
    comparación es lo normal, no una rareza — la cotización vivía solo en Redis con TTL de 24h y
    aquí los pedidos van con días de anticipación. Ante un comprobante de Bs 5.000 sobre una venta
    de Bs 16.591, el bot soltaba "recibí tu pago y coordino la entrega".

    **No poder comprobar no es comprobar bien.** Si no hay monto leído, o no hay cotización con que
    contrastarlo, la respuesta es NO: el pago se registra igual (esa red no se toca) pero el bot no
    afirma que esté completo y la dueña lo ve en la bandeja.

    Vive aparte para que se pueda probar: era un `if` enterrado en 200 líneas de carril de
    comprobante, imposible de cubrir sin montar media visión.
    """
    candidatos = [c for c in esperados if c is not None]
    if leido is None or not candidatos:
        return False
    return any(abs(leido - c) <= max(1.0, c * 0.02) for c in candidatos)


# Cuántas veces se reintenta el comprobante antes de rendirse, y con qué espera.
# Se PUEDE reintentar porque `descargar_media` PIDE UNA URL NUEVA en cada intento (meta_client,
# paso 1: GET /{media_id}): lo que caduca a los ~5 min es la URL firmada, NO el media_id (ese
# vive ~30 días). El comentario viejo usaba ese dato justo al revés, como excusa para no hacer
# nada. Y es SEGURO repetir: el archivo se sobrescribe, la burbuja va con
# on_conflict_do_nothing(message_id) y `registrar_comprobante` está blindado por el UNIQUE de
# comprobante_media_id + el índice ux_pago_reportado_por_pedido (migración 026).
_REINTENTOS_COMPROBANTE = 3
_ESPERA_COMPROBANTE = (20, 60, 180)  # segundos entre intentos


@celery_app.task(name="procesar_comprobante", bind=True, max_retries=_REINTENTOS_COMPROBANTE)
def procesar_comprobante(self, telefono, message_id, media_id, caption=None, nombre=None, mime_type=None):
    """Tarea dedicada (fuera del buffer de texto): descarga y guarda el comprobante.

    🔴 EL REINTENTO VIVE AQUÍ, NO EN META (auditoría 2026-08-02, SIL-5). Los `except` de abajo
    decían "NO marcar, dejar reintentar a Meta". Meta NO puede reintentar: el webhook ya
    devolvió 200 AL ENCOLAR esta tarea (`_encolar_comprobante` → `recibir` → 200), y para Meta
    ese evento está entregado y cerrado. Y Celery tampoco reintentaba: `celery_app.conf` no
    tiene retry ni autoretry_for, y el decorador era `@task(name=...)` a secas. O sea: el
    "reintento" al que se le dejaba el pago del cliente NO EXISTÍA. Un 5xx de Meta o un parpadeo
    de la BD y el pago se perdía PARA SIEMPRE: sin fila, sin respuesta al cliente —que ACABA de
    pagar— y sin que la dueña se enterara. Es el único carril donde el silencio cuesta dinero.
    """
    resultado = _run(_procesar_comprobante(
        telefono, message_id, media_id, caption, nombre, mime_type,
        ultimo_intento=self.request.retries >= _REINTENTOS_COMPROBANTE,
    ))
    if resultado == "reintentar":
        espera = _ESPERA_COMPROBANTE[min(self.request.retries, len(_ESPERA_COMPROBANTE) - 1)]
        raise self.retry(countdown=espera)


async def _procesar_comprobante(
    telefono, message_id, media_id, caption, nombre, mime_type, ultimo_intento: bool = False
) -> str:
    """Devuelve el VEREDICTO para que el wrapper decida si reintenta: "duplicado" / "reintentar"
    / "rendido" / "no_es_comprobante" / "ok". Se devuelve un string (y no se lanza la excepción)
    para que un banco pueda probar los tres finales sin montar Celery."""
    # Idempotencia del carril de DINERO: se marca SOLO tras un registro exitoso (o tras avisarle
    # a una persona). Si la descarga o el registro fallan, NO se marca: reintentamos nosotros.
    if message_id and await rc.comprobante_procesado(message_id):
        return "duplicado"

    try:
        contenido, mime = await descargar_media(media_id)
    except MediaDemasiadoGrande:
        # 🔴 UN ARCHIVO DE 100 MB NO SE REINTENTA (auditoría 2026-08-02, META-13). WhatsApp los
        # acepta y `descargar_media` los cargaba ENTEROS en la RAM del worker — el del DINERO.
        # Ahora se corta antes, y aquí se sale del bucle de reintentos: volver a intentarlo es
        # volver a pedir lo mismo y recibir el mismo "no". Se va DIRECTO al camino de la rendición,
        # que es el bueno: la burbuja entra al hilo con su `media_id` (el panel se lo baja de Meta
        # al vuelo, así que la dueña LO VE), ella recibe el aviso y el cliente su acuse sobrio.
        logger.error(
            "Comprobante %s de %s: el archivo pasa del tope de %s bytes; no se descarga y NO se "
            "reintenta (reintentarlo da el mismo resultado y el mismo riesgo de RAM)",
            media_id, telefono, MAX_MEDIA_BYTES,
        )
        await _comprobante_a_ciegas(
            telefono, message_id, media_id, caption, nombre, mime_type,
            porque="el archivo es demasiado grande para poder abrirlo",
        )
        return "rendido"
    except Exception:  # noqa: BLE001 — fallo transitorio: NO marcar, el reintento es NUESTRO
        logger.exception("No se pudo descargar el comprobante %s de %s", media_id, telefono)
        if not ultimo_intento:
            return "reintentar"
        await _comprobante_a_ciegas(
            telefono, message_id, media_id, caption, nombre, mime_type,
            porque="WhatsApp no me entregó la imagen",
        )
        return "rendido"

    base_mime = (mime or mime_type or "").split(";")[0].strip().lower()
    es_imagen = base_mime.startswith("image/")

    # EL DISCO NO PUEDE TUMBAR EL COBRO (SIL-5). Esta línea vivía FUERA de todo try: con /data
    # lleno, sin montar o de solo lectura saltaba OSError, la tarea moría con el traceback en el
    # log y el pago del cliente no dejaba NI UN RASTRO — ni burbuja en el hilo, ni Pago, ni
    # respuesta, ni aviso. Un problema de DISCO borraba el rastro de un PAGO. Los bytes YA están
    # en memoria: la visión y el registro siguen igual. Lo único que se pierde es la copia local,
    # y la burbuja se guarda con `media_id` a secas — el panel se la baja de Meta al vuelo.
    ruta = None
    try:
        ruta = _guardar_comprobante(media_id, contenido, mime or mime_type or "")
        logger.info("Comprobante de %s guardado en %s (%s bytes)", telefono, ruta, len(contenido))
    except Exception:  # noqa: BLE001
        logger.exception("Disco: no se pudo escribir el comprobante de %s (el cobro sigue)", telefono)
        await _avisar_a_la_duena(
            telefono,
            motivo="comprobante_sin_archivo",
            detalle=(
                "No pude guardar el archivo del comprobante en el servidor (disco lleno o sin "
                "montar). El pago SÍ se registró y la captura se ve en el chat, pero si el "
                "problema sigue, avisa a soporte."
            ),
            mensaje_cliente="(comprobante de pago)",
            whatsapp=(
                "⚠️ No pude guardar el archivo de un comprobante en el servidor. El pago sí "
                "quedó registrado."
            ),
        )
    if ruta:
        # Fuera del `try` de arriba a propósito: si esto avisara desde dentro, un fallo suyo se
        # leería como "falló el disco" y le mandaría a la dueña el aviso equivocado.
        await _vigilar_disco_comprobantes()

    # LA FOTO ENTRA AL HILO **AQUÍ**, apenas se descarga y ANTES de que la visión la juzgue.
    # Si se insertara junto al registro del pago, la imagen que la visión RECHAZA (la captura
    # borrosa, el reflejo, el PDF) NUNCA aparecería en el chat — y es justo la que la dueña
    # necesita ver con sus ojos para decidir. Va en SESIÓN PROPIA: jamás puede compartir
    # transacción con el dinero (un fallo al guardar la burbuja no puede tumbar el Pago).
    await _guardar_media_en_hilo(
        telefono=telefono,
        message_id=message_id,
        media_id=media_id,
        ruta=ruta,
        mime=base_mime,
        caption=caption,
        es_imagen=es_imagen,
    )

    # VISIÓN: extrae los datos y valida EN CÓDIGO que el pago sea A LA CUENTA de la
    # dueña. Solo para imágenes; un PDF no se analiza por visión.
    #
    # 🔴 EL DINERO SE JUZGA UNA VEZ. La visión NO es determinista, y desde que el reintento
    # existe de verdad (arriba) el MISMO comprobante puede pasar por aquí varias veces: el
    # intento 1 podría leer "es comprobante, $28" y fallar al registrar, y el intento 2 leer "no
    # es un comprobante" y cerrar el caso pidiéndole al cliente la captura otra vez. El veredicto
    # sobre un pago no puede cambiar entre intentos. Se congela por `media_id` durante la ventana
    # de reintentos (15 min > 20+60+180 s), y SOLO si se pudo leer: una lectura fallida no vale
    # la pena congelarla — si la visión se recupera, que el siguiente intento la aproveche.
    lectura = {}
    if es_imagen:
        try:
            _cacheado = await rc.get_cache(f"cache:vision:{media_id}")
        except Exception:  # noqa: BLE001 — sin caché se lee de nuevo; nunca tumbar el dinero
            logger.exception("No se pudo leer la caché de visión de %s", media_id)
            _cacheado = None
        if _cacheado:
            lectura = json.loads(_cacheado)
            logger.info("Visión de %s: se reusa la lectura ya hecha (mismo comprobante)", media_id)
        else:
            lectura = await _leer_comprobante_seguro(telefono, contenido, base_mime)
            if lectura.get("leido"):
                try:
                    await rc.set_cache(f"cache:vision:{media_id}", json.dumps(lectura), 900)
                except Exception:  # noqa: BLE001
                    logger.exception("No se pudo cachear la lectura de visión de %s", media_id)
    es_comprobante = lectura.get("es_comprobante")
    monto = lectura.get("monto")
    monto_ok = bool(monto) and str(monto).strip().lower() not in ("", "null", "none", "0")
    logger.info(
        "Visión comprobante de %s: imagen=%s leido=%s es_comprobante=%s pantalla=%s beneficiario=%r monto=%s",
        telefono, es_imagen, lectura.get("leido"), es_comprobante,
        lectura.get("es_pantalla_bancaria"), lectura.get("beneficiario_nombre"), monto,
    )

    # IMÁGENES = ESTRICTO: solo es un pago si la visión RECONOCIÓ un comprobante a la
    # cuenta de la dueña (es_comprobante True + monto). Si NO lo reconoció —no es
    # comprobante, es de otra cuenta, o la visión no pudo leer— se PIDE la captura y
    # NO se registra. (Antes la red de seguridad registraba ante la duda, y por eso
    # se colaban fotos cualquiera.)
    # 🔴 "NO PUDE LEER" NO ES "NO ES UN COMPROBANTE" (auditoría 2026-08-02, SIL-6).
    # `leer_comprobante` distingue TRES estados y hasta hoy los tres caían en el mismo `if`:
    # `leido` se logueaba (línea de arriba) y NUNCA se ramificaba sobre él. Con la visión caída
    # —402 de OpenRouter sin saldo (pasó el 2026-07-15), 429, timeout de 45 s, JSON ilegible, o
    # un image/gif que ni llega a la API porque no está en _FORMATOS_IMAGEN— el cliente recibía
    # "ahí no veo el comprobante, mándame la captura clara" CON CADA CAPTURA, y encima se marcaba
    # el mensaje como atendido: no había vuelta atrás ni cuando la visión volviera. EL NEGOCIO
    # DEJABA DE COBRAR y el único testigo era un logger.info. Ahora un comprobante ilegible se
    # trata como un PDF, que es lo que es: no puedo juzgarlo ⇒ se registra 'reportado', lo mira
    # la dueña, y el bot NO afirma nada (con monto=None, `_monto_cuadra` da False por el
    # fail-closed de DIN-5 y sale el mensaje "ya lo recibí, lo estoy revisando" — la verdad).
    vision_leyo = lectura.get("leido") is True
    if es_imagen and not vision_leyo:
        logger.error(
            "Visión CAÍDA con el comprobante de %s (media %s, mime %s): no se pudo leer. Se "
            "registra igual (antes se pedía la captura otra vez y el pago se perdía).",
            telefono, media_id, base_mime,
        )
        # CANDADO ANTIINUNDACIÓN (15 min por cliente). Sin esto, con la visión caída cada imagen
        # de cada cliente abre una Intervencion y manda un WhatsApp. Y `leido=False` NO solo pasa
        # con la visión caída: un image/gif o un image/bmp tampoco llegan a la API, así que un
        # cliente mandando GIFs graciosos abriría un aviso por cada uno. El log de arriba deja el
        # rastro COMPLETO aunque el aviso no salga. El registro del Pago sí va SIEMPRE: esa parte
        # es el corazón del arreglo y no lleva candado.
        if await rc.aviso_unico(f"vision_caida:{telefono}", 900):
            await _avisar_a_la_duena(
                telefono,
                motivo="comprobante_ilegible",
                detalle=(
                    f"{nombre or telefono} mandó un comprobante y no lo pude leer (puede ser la "
                    "foto, o que el lector de imágenes esté caído). Lo registré para que no se "
                    "pierda, pero NO le dije que su pago esté completo. Ábrelo en el chat y "
                    "confírmalo tú."
                ),
                mensaje_cliente="(comprobante de pago)",
                whatsapp=(
                    f"👁️ No pude LEER el comprobante de {nombre or telefono}. Lo registré igual "
                    "(no se pierde) y al cliente solo le dije que lo estoy revisando. Míralo tú "
                    "en el panel."
                ),
            )

    if es_imagen and vision_leyo and not (es_comprobante is True and monto_ok):
        logger.info("Imagen de %s NO reconocida como comprobante de la dueña; no se registra", telefono)
        if message_id:
            await rc.marcar_comprobante(message_id)  # atendido: no reprocesar
        _pant = lectura.get("es_pantalla_bancaria")
        es_pantalla = _pant is True or str(_pant).strip().lower() in ("true", "si", "sí", "yes", "1")
        if es_pantalla:
            # SÍ es la pantalla de un pago/transferencia, pero NO a la cuenta de la dueña.
            # OJO: método-NEUTRAL a propósito. Antes decía "verifica que lo envió a tu Pago
            # Móvil" aunque el cliente hubiera pagado por Zelle o Binance — confundía justo
            # en el momento del dinero.
            situacion = (
                "El cliente te mandó una imagen de un pago o transferencia, pero ese pago NO te "
                "aparece hecho a TU cuenta (parece que fue a otra cuenta). Contéstale "
                "con calidez y con TUS PROPIAS PALABRAS, natural y DISTINTA cada vez (JAMÁS repitas "
                "la misma frase ni suenes a plantilla o robot): dile con cariño que ese pago no te "
                "aparece a tu cuenta, pídele que verifique que lo envió a los datos exactos que le "
                "diste (del método que él eligió) y que te reenvíe la captura. No lo acuses ni des "
                "el pago por hecho; solo pídele que confirme."
            )
        else:
            # No parece un comprobante (foto cualquiera, o no se ve el pago).
            situacion = (
                "El cliente te envió una imagen que no parece un comprobante de pago (parece otra "
                "cosa o no se alcanza a ver el pago). Contéstale con calidez y con TUS PROPIAS "
                "PALABRAS, natural y DISTINTA cada vez (JAMÁS repitas la misma frase ni suenes a "
                "plantilla): dile con cariño que ahí no ves el comprobante y pídele que te reenvíe "
                "la captura clara del pago (donde se vea el monto y la referencia)."
            )
        await _responder_situacion(telefono, situacion, nombre)
        return "no_es_comprobante"

    # ¿El MONTO del comprobante cuadra con lo cobrado? Comparamos contra el monto en
    # Bs (Pago Móvil/Transferencia) Y en USD (Binance/Zelle): basta que coincida con UNO.
    #
    # 🔴 FAIL-CLOSED: NO PODER COMPROBAR NO ES COMPROBAR BIEN (auditoría 2026-08-02, DIN-5).
    # Antes esto arrancaba en `True` y solo se reevaluaba `if leido_monto is not None and
    # candidatos`. O sea: cuando NO había con qué comparar, el sistema daba el monto por bueno.
    # Y quedarse sin comparación es facilísimo — la cotización vive SOLO en Redis con TTL de 24h
    # (`cobro:{telefono}`), así que basta con que el cliente cotice el viernes y pague el domingo.
    # Resultado: ante un comprobante de Bs 5.000 sobre una venta de Bs 16.591, el bot soltaba
    # "recibí tu pago y coordino la entrega". El pago se registra igual (eso está bien: es la red
    # de seguridad), pero el bot ya NO afirma que está completo y la dueña lo ve en la bandeja.
    # 🔴 Y UN PDF NO ES UN PAGO COMPROBADO (auditoría 2026-08-02, META-7). Esta línea decía
    # `monto_cuadra = True` para todo lo que no fuera imagen, y `True` aquí significa una cosa muy
    # concreta 60 líneas más abajo: el bot le dice al cliente **"recibí tu pago y coordino la
    # entrega"**. O sea, el guard estricto se aplicaba SOLO a `es_imagen` y CUALQUIER documento
    # entraba por la puerta de atrás: un cliente con un pedido abierto manda una receta médica en
    # PDF por error → se crea un `Pago` en 'reportado' amarrado a su pedido Y el bot le confirma
    # un pago que nadie ha visto. El PDF no se puede leer por visión: afirmar que el pago llegó
    # es, literalmente, inventarse el dinero — lo único que este proyecto no perdona.
    #
    # El registro NO se toca (esa es la red de seguridad: un comprobante de banco en PDF es un
    # caso REAL y frecuente, y el dinero jamás se descarta). Lo que cambia es lo que el bot DICE:
    # cae en el mismo carril que un monto que no cuadra —"ya lo recibí, lo estoy revisando y te
    # confirmo en un momentito"—, que es exactamente la verdad, y la dueña lo ve en la bandeja.
    monto_cuadra = False
    if es_imagen:
        esperado_bs, esperado_usd, esperado_div = await _montos_cobrados(telefono)
        leido_monto = _a_float(monto)
        esperados = (esperado_bs, esperado_usd, esperado_div)
        monto_cuadra = _monto_cuadra(leido_monto, esperados)
        logger.info(
            "Monto comprobante de %s: leido=%s bs=%s usd=%s divisa=%s cuadra=%s%s",
            telefono, leido_monto, esperado_bs, esperado_usd, esperado_div, monto_cuadra,
            "" if any(e is not None for e in esperados)
            else "  ← SIN COTIZACIÓN con la que comparar (¿caché vencida?)",
        )

    # Aquí: la visión reconoció el comprobante (imagen), O es un PDF/otro -> red de
    # seguridad: se registra como 'reportado' para que la dueña lo revise.
    from app.agent.tools import registrar_comprobante

    # La referencia leída por visión solo se confía si reconoció un comprobante.
    referencia = lectura.get("referencia") if es_comprobante is True else None
    if not isinstance(referencia, str) or not referencia.strip():
        referencia = None

    factory = get_session_factory()
    try:
        async with factory() as session:
            resultado = await registrar_comprobante(
                session,
                telefono,
                referencia=referencia,
                comprobante_media_id=media_id,
                comprobante_url=ruta,
                # El monto que la visión leyó: sirve para saber si pagó en divisas (con el
                # 20% de descuento) o en bolívares (precio completo).
                monto_leido=_a_float(monto) if es_comprobante is True else None,
            )
    except Exception:  # noqa: BLE001 — error de BD: NO marcar, el reintento es NUESTRO
        logger.exception("No se pudo registrar el comprobante de %s", telefono)
        if not ultimo_intento:
            return "reintentar"   # Meta no va a volver; volvemos nosotros (SIL-5)
        await _comprobante_a_ciegas(
            telefono, message_id, media_id, caption, nombre, mime_type,
            porque="la base de datos me falló al registrar el pago",
        )
        return "rendido"
    logger.info("Comprobante de %s registrado: %s", telefono, resultado)

    # LA OTRA MITAD DE META-7: si entró un DOCUMENTO, nadie lo ha mirado todavía. Ni la visión
    # (no lee PDFs) ni una persona. El pago queda registrado —bien— pero alguien tiene que abrirlo
    # con los ojos antes de despachar, y hasta hoy nada se lo decía a la dueña. Candado por cliente
    # de 15 min: un cliente que manda tres PDFs seguidos es UN aviso, no tres.
    if resultado.get("ok") and not es_imagen:
        await _avisar_a_la_duena(
            telefono,
            motivo="comprobante_sin_leer",
            detalle=(
                f"{nombre or telefono} mandó un ARCHIVO (no una foto) como comprobante. No lo "
                "puedo leer, así que NO le dije que su pago esté completo: solo que lo estoy "
                "revisando. Lo registré para que no se pierda — ábrelo en el chat y confírmalo tú."
            ),
            mensaje_cliente="(comprobante en archivo)",
            whatsapp=(
                f"📎 {nombre or telefono} mandó un archivo como comprobante. Yo no puedo leerlo: "
                "lo registré y al cliente solo le dije que lo estoy revisando. Míralo tú."
            ),
            candado=(f"comprobante_doc:{telefono}", 900),
        )

    # Registro exitoso: marcar para que un reintento de Meta no repita el cierre.
    if message_id:
        await rc.marcar_comprobante(message_id)

    # Cierre del CLOSER: si se registró el pago, SIGUE la venta (agradece, coordina,
    # ofrece más). No avisa a la dueña (su banco ya le avisa) ni afirma verificación.
    if resultado.get("ok") and monto_cuadra:
        situacion = (
            "el cliente acaba de mandar el comprobante de su pago y ya lo registraste. "
            "Agradécele con calidez, dile que recibiste su pago y que coordinas la "
            "entrega/envío, y déjale la puerta abierta por si quiere algo más. "
            "NO digas que verificaste el pago en el banco ni que está 'confirmado'."
        )
    elif resultado.get("ok"):
        # Registrado, pero el monto NO cuadra con lo cobrado: no afirmar que está completo.
        situacion = (
            "el cliente mandó el comprobante pero el MONTO no cuadra con el de su pedido. "
            "Con calidez dile que ya lo recibiste y lo estás revisando, y que le confirmas "
            "en un momentito. NO afirmes que el pago está completo ni confirmado."
        )
    else:
        situacion = (
            "el cliente te envió una imagen pero no hay un pedido esperando pago; "
            "pregúntale con calidez si es un comprobante y en qué lo puedes ayudar"
        )
    partes = await _responder_situacion(telefono, situacion, nombre)

    # 🔴 EL CARRIL DEL DINERO NUNCA ES SILENCIOSO.
    # Si la dueña tiene ese chat tomado, el bot se calla (correcto) — pero el cliente ACABA DE
    # PAGAR y no recibiría absolutamente nada, y ella no se enteraría porque el aviso del bot
    # tampoco salió. Aquí se le avisa a ella, sí o sí.
    if resultado.get("ok") and not partes:
        await _avisar_pago_en_chat_pausado(telefono, nombre)
    return "ok"


async def _comprobante_a_ciegas(
    telefono, message_id, media_id, caption, nombre, mime_type, *, porque: str
) -> None:
    """Se agotaron los reintentos: el comprobante NO se pudo procesar. Aquí NO se pierde.

    Tres cosas, en este orden y ninguna opcional:
      1. La burbuja entra al hilo IGUAL, aunque no tengamos el archivo. Con el `media_id` a
         secas basta: el panel se lo baja de Meta al vuelo (/api/mensajes/{id}/media, caso 3),
         así que la dueña VE la captura del pago con sus ojos aunque nosotros no la hayamos
         podido bajar. Es lo más valioso que queda cuando todo lo demás falló.
      2. Le llega el aviso a ELLA: bandeja + WhatsApp. Un pago no se puede quedar sin dueño.
      3. El cliente recibe el acuse sobrio de siempre. Acaba de pagar: el silencio no es opción.

    Lo que NO se hace: afirmar nada. No hay Pago registrado, así que el bot no puede decir
    "recibí tu pago y coordino la entrega". Dice "lo estoy revisando", que es la verdad.
    """
    base_mime = (mime_type or "").split(";")[0].strip().lower()
    await _guardar_media_en_hilo(
        telefono=telefono,
        message_id=message_id,
        media_id=media_id,
        ruta=None,
        mime=base_mime,
        caption=caption,
        es_imagen=base_mime.startswith("image/"),
    )
    quien = nombre or telefono
    await _avisar_a_la_duena(
        telefono,
        motivo="comprobante_sin_procesar",
        detalle=(
            f"{quien} te mandó un comprobante y NO lo pude procesar: {porque}. El pago NO quedó "
            "registrado. Ábrelo en el chat, míralo con tus ojos y regístralo tú. Al cliente solo "
            "le mandé un acuse de 'lo estoy revisando'."
        ),
        mensaje_cliente="(comprobante de pago)",
        whatsapp=(
            f"💰⚠️ {quien} te mandó un comprobante y NO lo pude procesar ({porque}). El pago NO "
            "está registrado. Entra al chat, míralo y regístralo tú."
        ),
    )
    # El acuse al cliente respeta el interruptor y la lista blanca, igual que `_responder_situacion`.
    if await _bot_activo() and await _numero_permitido(telefono):
        partes = await _enviar_en_partes(telefono, _RESPUESTA_PAGO_SEGURA)
        if _algo_llego(partes):
            await rc.guardar_historial(
                telefono, "assistant", _lo_que_llego(partes, _RESPUESTA_PAGO_SEGURA)
            )
        if partes:
            await _guardar_en_panel(telefono, nombre, "", partes)
    # Ahora SÍ se marca: ya hay una persona enterada; una reentrega no debe repetir el aviso.
    if message_id:
        await rc.marcar_comprobante(message_id)


async def _avisar_pago_en_chat_pausado(telefono: str, nombre: str | None) -> None:
    """Entró un comprobante en un chat que la dueña tiene tomado: el bot no responde, así que
    hay que avisarle a ELLA. Un pago no se puede quedar sin acuse."""
    from sqlalchemy import select

    from app.models import Configuracion, Intervencion

    quien = nombre or telefono
    try:
        factory = get_session_factory()
        async with factory() as session:
            session.add(Intervencion(
                cliente_telefono=telefono,
                motivo="reclamo",
                detalle=(
                    f"{quien} MANDÓ UN COMPROBANTE y tú tienes ese chat tomado, así que el bot "
                    "no le respondió nada. Contéstale tú."
                ),
                mensaje_cliente="(comprobante de pago)",
            ))
            fila = (
                await session.execute(
                    select(Configuracion).where(Configuracion.clave == "dueno_telefono")
                )
            ).scalar_one_or_none()
            await session.commit()
        destino = (fila.valor if fila else None) or settings.dueno_telefono
        if destino:
            await _whatsapp_a_la_duena(
                destino,
                f"💰 {quien} te mandó un comprobante, pero ese chat lo estás atendiendo tú: "
                "el bot NO le respondió nada. Entra y contéstale.",
                que=f"pago en chat pausado de {telefono}",
            )
    except Exception:  # noqa: BLE001 — el pago YA está registrado; esto es el aviso
        logger.exception("No se pudo avisar del pago en chat pausado de %s", telefono)


# ─── Notas de voz y otros eventos (respuesta humana) ─────────────────

async def _responder_y_enviar(telefono: str, texto: str, nombre: str | None) -> str:
    """Pasa un texto por el agente y envia la respuesta. Comparte el lock por
    cliente para no responder en paralelo con el flujo de texto.

    Devuelve el mismo veredicto que `_procesar` ("ocupado" / "apagado" / "sin_envio" /
    "error" / "ok").
    """
    # 🔴 ESTE CARRIL NO TIENE BUFFER (auditoría 2026-08-02, SIL-1b). El texto que llega aquí no
    # está en Redis: es una variable local (la transcripción de la nota de voz, ya descargada y
    # ya PAGADA a Gemini). Antes, si el lock estaba tomado, este `return` la borraba del mundo —
    # y el reintento de Meta tampoco la salvaba, porque el webhook ya había quemado el
    # message_id con `ya_procesado` (webhook/router.py, `_encolar_audio`). Ahora, en vez de
    # reintentar la tarea entera (que volvería a descargar un media cuya URL caduca a ~5 min y a
    # pagar otra transcripción), el texto se DERRAMA al buffer de texto y lo atiende el carril
    # normal, que sí tiene red. De regalo sale mejor UX: la nota de voz y el "y también quiero
    # dos" que el cliente escribe justo después se contestan JUNTOS, en un solo turno.
    if not await rc.adquirir_lock(telefono):
        await rc.agregar_a_buffer(telefono, texto)
        procesar_buffer.apply_async((telefono, nombre), countdown=settings.buffer_segundos)
        logger.info("Turno ocupado en %s: la voz/evento se derrama al buffer de texto", telefono)
        return "ocupado"

    guardado = False  # ¿el turno del cliente ya quedó ESCRITO en Postgres?
    # CUÁNDO habló el cliente, para el orden del hilo (el gemelo de `_procesar`): una nota de voz
    # que provoca una foto tenía el mismo hilo invertido en el panel.
    from app.models import now_utc

    ts_turno = now_utc()
    try:
        if not await _bot_activo() or await _cliente_pausado(telefono) or not await _numero_permitido(telefono):
            await rc.guardar_historial(telefono, "user", texto)
            guardado = await _guardar_entrante(telefono, nombre, texto)
            return "apagado"
        # Con respaldo en Postgres, igual que `_procesar` (fallo del 08-18): una nota de voz
        # después de tres días de silencio tenía el mismo agujero de memoria que un texto.
        historial = await historial_con_respaldo(telefono, sembrar=True)
        # EL TURNO DEL CLIENTE SE ANOTA ANTES DE PENSAR — el gemelo de `_procesar` (SIL-10).
        # Aquí es todavía más grave: lo que se pierde es una transcripción que NO está en ningún
        # buffer ni en ningún webhook. Si `responder()` revienta, esto es lo único que queda.
        await rc.guardar_historial(telefono, "user", texto)
        respuesta = await responder(telefono, texto, historial, nombre)
        respuesta = _proteger_afirmacion_de_pago(respuesta)
        partes = await _enviar_en_partes(telefono, respuesta)
        if not partes:
            # La dueña tomó el chat mientras el bot pensaba: su respuesta se DESCARTA
            # (no se envía ni se recuerda). Lo que sí se guarda es lo que dijo el cliente,
            # para que ella lo vea y le conteste.
            guardado = await _guardar_entrante(telefono, nombre, texto)
            return "sin_envio"
        # Los globos FALLIDOS también se guardan (se ven en ROJO en el panel), pero el bot no
        # "recuerda" haber dicho algo que el cliente nunca recibió (`_lo_que_llego`, SIL-8).
        if _algo_llego(partes):
            await rc.guardar_historial(telefono, "assistant", _lo_que_llego(partes, respuesta))
        guardado = await _guardar_en_panel(telefono, nombre, texto, partes, ts_usuario=ts_turno)
        await _avisar_turno_a_medias(telefono, nombre, partes)
    except Exception:  # noqa: BLE001
        logger.exception("Error respondiendo a %s (texto=%r)", telefono, texto[:200])
        try:
            if not guardado:
                await _guardar_entrante(telefono, nombre, texto)
                await _avisar_turno_perdido(telefono, nombre, texto)
        except Exception:  # noqa: BLE001 — el RESCATE no puede tumbar el turno que ya se cayó
            logger.exception("Y el rescate del turno de %s tampoco pudo", telefono)
        return "error"
    finally:
        await rc.liberar_lock(telefono)
    return "ok"


@celery_app.task(name="procesar_audio")
def procesar_audio(telefono, message_id, media_id, nombre=None, mime_type=None):
    """Tarea: descarga la nota de voz, la transcribe y responde como a un texto."""
    _run(_procesar_audio(telefono, media_id, nombre, mime_type))


async def _procesar_audio(telefono, media_id, nombre, mime_type) -> None:
    # 📊 El turno de la NOTA DE VOZ empieza aquí, antes de transcribir (telemetría, 032). Así la
    # transcripción y las vueltas del bucle que vienen detrás quedan bajo UN solo `turno_id`: una
    # nota de voz cuesta las dos cosas, y medirlas por separado no diría lo que cuesta atenderla.
    abrir_turno(telefono, "audio")
    transcripcion = ""
    try:
        contenido, mime = await descargar_media(media_id)
        transcripcion = await transcribir_audio(contenido, mime or mime_type or "audio/ogg")
    except Exception:  # noqa: BLE001 — escuchar el audio nunca debe tumbar al worker
        logger.exception("No se pudo escuchar la nota de voz de %s", telefono)
    if transcripcion.strip():
        await _responder_y_enviar(telefono, transcripcion, nombre)
    else:
        # No se pudo entender el audio: el agente responde con naturalidad.
        await _responder_y_enviar(
            telefono, "(el cliente envio una nota de voz que no se pudo escuchar bien)", nombre
        )


@celery_app.task(name="procesar_evento")
def procesar_evento(telefono, tipo, nombre=None, texto=None, message_id=None):
    """Tarea: sticker/video/ubicacion/etc. El agente responde natural, sin robotismos.

    Los dos parámetros nuevos van AL FINAL y con default, a propósito: las tareas que YA estén
    encoladas con la tupla de 3 siguen ejecutándose sin reventar durante el despliegue.
    (`message_id` todavía no se usa: viaja ya para no volver a tocar la firma cuando llegue el
    candado del audio.)
    """
    _run(_procesar_evento(telefono, tipo, nombre, texto, message_id))


async def _procesar_evento(telefono, tipo, nombre, texto, message_id) -> None:
    """🔴 SI EL EVENTO TRAE DATOS, SE LE PASAN AL AGENTE (auditoría 2026-08-02, SIL-12).

    Hoy: la UBICACIÓN. Se resumía a "(el cliente envio un location, sin texto)" y la DIRECCIÓN DE
    ENTREGA del cliente no quedaba en ningún sitio del sistema: ni en el hilo del panel, ni en la
    memoria del bot. Se perdía el dato por el que el bot pregunta en cada venta con delivery.
    Ahora entra en el texto del turno, así que `_guardar_en_panel` lo escribe en
    `mensajes.contenido` —la dueña lo ve y lo puede copiar— y el bot puede confirmarlo con el
    cliente en vez de volver a preguntarlo.
    """
    dato = (texto or "").strip()
    if tipo == "location" and dato:
        await _responder_y_enviar(telefono, f"(el cliente envio su ubicacion: {dato})", nombre)
        return
    await _responder_y_enviar(telefono, f"(el cliente envio un {tipo}, sin texto)", nombre)


@celery_app.task(name="notificar_cliente_pago")
def notificar_cliente_pago(telefono, situacion):
    """Tarea: avisa al cliente (pago confirmado/rechazado) con un mensaje redactado
    al momento por Whuilianny, en su voz y con contexto — no una plantilla."""
    _run(_notificar_cliente_pago(telefono, situacion))


async def _avisar_a_la_duena(
    telefono: str, *, motivo: str, detalle: str, mensaje_cliente: str, whatsapp: str,
    candado: tuple[str, int] | None = None,
) -> None:
    """Deja el aviso en la BANDEJA y le manda un WhatsApp a la dueña.

    Se usa cuando el bot NO pudo hacer su trabajo y alguien tiene que enterarse. Si esto fallara
    en silencio, el cliente se quedaría esperando a nadie — que es el peor final posible.

    `candado=(clave, ttl)` frena SOLO el WhatsApp, nunca la fila de la bandeja. Es la diferencia
    entre las dos cosas: la bandeja tiene que tener UNA fila por cliente (cada cliente importa
    y ella necesita saber cuál), pero si la avería es UNA sola —el bot apagado, la base caída—
    no puede convertirse en un WhatsApp por cada cliente afectado. Sin candado se comporta
    exactamente como siempre.
    """
    from sqlalchemy import select

    from app.models import Configuracion, Intervencion

    try:
        factory = get_session_factory()
        async with factory() as session:
            session.add(Intervencion(
                cliente_telefono=telefono,
                motivo=motivo,
                detalle=detalle,
                mensaje_cliente=mensaje_cliente,
            ))
            fila = (
                await session.execute(
                    select(Configuracion).where(Configuracion.clave == "dueno_telefono")
                )
            ).scalar_one_or_none()
            await session.commit()
        destino = (fila.valor if fila else None) or settings.dueno_telefono
        if candado is not None and not await rc.aviso_unico(*candado):
            logger.info(
                "Aviso '%s' de %s: la fila queda en la bandeja, pero el WhatsApp no se repite "
                "(candado '%s')", motivo, telefono, candado[0],
            )
            return
        if destino:
            await _whatsapp_a_la_duena(destino, whatsapp, que=f"{motivo} de {telefono}")
    except Exception:  # noqa: BLE001 — el aviso es lo último que hay; que no tumbe al worker
        logger.exception("No se pudo avisar a la dueña sobre %s", telefono)


async def _avisar_mensaje_frenado(telefono: str, nombre: str | None) -> None:
    """La red del dinero tumbó lo que el bot iba a decir (dos veces). El cliente recibió un acuse
    sobrio, pero la conversación la tiene que terminar una persona."""
    quien = nombre or telefono
    await _avisar_a_la_duena(
        telefono,
        motivo="bot_frenado",
        detalle=(
            f"Frené un mensaje del bot a {quien}: iba a decir un monto que NO salió del sistema, o "
            "una frase que tiene prohibida (el banco, ser una persona, un tema de salud). Al cliente "
            "solo le llegó un acuse. Entra tú al chat y termínalo."
        ),
        mensaje_cliente="(el bot se frenó solo)",
        whatsapp=(
            f"⚠️ Frené un mensaje del bot a {quien}: iba a decir algo que no puede. Solo le mandé un "
            "acuse. Entra al chat y contéstale tú."
        ),
    )


async def _avisar_turno_perdido(telefono: str, nombre: str | None, texto: str) -> None:
    """El cliente escribió y el bot NO pudo contestarle (se cayó la IA, Redis, lo que sea).

    Su mensaje queda escrito en el panel — pero eso solo sirve si ALGUIEN lo mira. Este es el
    mismo principio que ya rige el carril del dinero (`_avisar_pago_en_chat_pausado`): en esta
    casa nada se queda en silencio. Candado de 15 min POR CLIENTE: si OpenRouter se cae una hora
    (el 402 del 2026-07-15), la dueña recibe un aviso por persona, no uno por mensaje.
    """
    quien = nombre or telefono
    corto = (texto or "").strip().replace("\n", " ")[:120]
    if not await rc.aviso_unico(f"sin_respuesta:{telefono}", 900):
        logger.error("Turno perdido de %s (ya avisado hace poco): %r", telefono, corto)
        return
    await _avisar_a_la_duena(
        telefono,
        motivo="sin_respuesta",
        detalle=(
            f"{quien} te escribió y el bot NO pudo contestarle (falló la IA o un servicio). Su "
            f"mensaje quedó guardado en la conversación: «{corto}». Entra tú y contéstale."
        ),
        mensaje_cliente=texto[:500],
        whatsapp=(
            f"⚠️ {quien} te escribió y el bot no pudo contestarle: «{corto}». "
            "Está en el panel; contéstale tú."
        ),
    )


async def _avisar_turno_a_medias(telefono: str, nombre: str | None, partes: list[dict]) -> None:
    """Un turno que salió A MEDIAS tiene que llegarle a la dueña.

    Es el caso más caro que hay: el cliente recibió "perfecto, te paso los datos" y NO recibió la
    cuenta, y el bot ya no lo repite (ver `_lo_que_llego`). Solo dispara si algo SÍ llegó: es la
    MEDIA VERDAD lo peligroso — una caída total de Meta no manda nada y no confunde a nadie.
    """
    perdidos = [p for p in partes if p.get("estado") != "enviado"]
    if not perdidos or not _algo_llego(partes):
        return
    quien = nombre or telefono
    await _avisar_a_la_duena(
        telefono,
        motivo="mensaje_a_medias",
        detalle=(
            f"A {quien} el mensaje del bot le salió A MEDIAS: WhatsApp aceptó "
            f"{len(partes) - len(perdidos)} y rechazó {len(perdidos)}. NO recibió esto: "
            f"«{perdidos[0]['texto'][:180]}». Está en rojo en el chat: mándaselo tú."
        ),
        mensaje_cliente="(el mensaje del bot salió a medias)",
        whatsapp=(
            f"⚠️ A {quien} el mensaje del bot le salió A MEDIAS ({len(perdidos)} de {len(partes)} "
            "no llegaron). Entra al chat: lo que falta está en rojo."
        ),
    )


async def _avisar_pago_sin_confirmar(telefono: str, mensaje: str, *, tomado: bool) -> None:
    """La dueña tocó confirmar/rechazar un pago y al cliente NO le llegó NADA. Que se entere.

    Son DOS finales distintos y le piden cosas distintas, por eso no es un aviso solo:
      · `tomado=True` → ella tiene ESE chat en las manos (`pausado_por='dueña'`) y el bot se calló
        para no hablarle encima. NO es una avería: es el caso NORMAL, porque si está confirmando
        el pago es justo porque pidió el comprobante a mano o contestó desde el celular. Lo único
        que falta es que escriba la confirmación en el chat que YA tiene abierto. Candado POR
        CLIENTE (como `sin_respuesta`): cada cliente importa, pero el panel puede disparar esta
        tarea dos veces por el mismo (verificar monto ⇒ parcial ⇒ confirmar) y eso no puede
        costarle dos WhatsApp.
      · `tomado=False` → Meta RECHAZÓ todos los globos. Eso sí es una avería, y es UNA sola (el
        número, o Meta). Candado SIN teléfono, mismo criterio que `pago_con_bot_apagado`: si
        confirma cinco pagos con Meta caído recibe CINCO filas en la bandeja —una por cliente,
        que es lo que necesita para no dejarse a ninguno— y UN solo WhatsApp.

    Es primo de `_avisar_pago_en_chat_pausado` (el carril del COMPROBANTE, más arriba), que cubre
    el caso gemelo cuando entra la captura. Aquel no lleva candado; este sí.

    ⚠️ NI UNA CIFRA PROPIA. Lo único con montos que se cita es `mensaje`, y se cita porque ES el
    texto que YA pasó la red del dinero en ESTE mismo turno: `redactar_mensaje` devuelve "" si no
    la pasa, y el que llama lo comprueba antes de llegar aquí. Sacar el monto de otro sitio (la
    cotización de Redis, el pedido) sería decirle una cifra que esta vuelta no autorizó nadie — y
    en un pago parcial esa cifra es OTRA que la que ella acaba de tocar en el panel. Mismo patrón
    que `_avisar_turno_a_medias`: cita el texto que se perdió, nunca un número suyo.
    """
    logger.warning(
        "Aviso de pago a %s: NO le llegó nada (%s) → lo pasa la dueña",
        telefono, "la dueña tiene el chat tomado" if tomado else "Meta rechazó todos los globos",
    )
    recado = (mensaje or "").strip().replace("\n", " ")[:180]
    if tomado:
        motivo = "pago_en_chat_tomado"
        candado = (f"pago_en_chat_tomado:{telefono}", 900)
        detalle = (
            f"Tocaste confirmar/rechazar el pago de {telefono}, pero ese chat lo tienes tomado "
            "TÚ: el bot se calló para no hablarte encima y el cliente NO recibió nada. Sigue sin "
            f"saber en qué quedó su pago — díselo tú. Le iba a decir: «{recado}»."
        )
        whatsapp = (
            f"💰 Tocaste lo del pago de {telefono}, pero ese chat lo tienes tomado tú: el bot NO "
            "le avisó nada. Entra al chat y díselo tú."
        )
    else:
        motivo = "pago_no_entregado"
        candado = ("pago_no_entregado", 900)
        detalle = (
            f"Tocaste confirmar/rechazar el pago de {telefono} y WhatsApp RECHAZÓ el mensaje: al "
            "cliente NO le llegó nada (está en rojo en su chat). Escríbele tú desde tu teléfono, "
            f"y si se repite avísale a Enova. Le iba a decir: «{recado}»."
        )
        whatsapp = (
            f"⚠️ Tocaste lo del pago de {telefono} y WhatsApp rechazó el mensaje: al cliente NO le "
            "llegó nada. Escríbele tú."
        )
    await _avisar_a_la_duena(
        telefono,
        motivo=motivo,
        detalle=detalle,
        mensaje_cliente="(esperando el resultado de su pago)",
        whatsapp=whatsapp,
        candado=candado,
    )


async def _notificar_cliente_pago(telefono, situacion) -> None:
    """La dueña confirmó o rechazó un pago desde el panel: hay que decírselo al cliente.

    🔴 ES EL ÚNICO CAMINO QUE LE HABLA AL CLIENTE **DÍAS DESPUÉS** (auditoría 2026-07-13). Todos
    los demás contestan a un mensaje que el cliente ACABA de mandar, así que la ventana de 24h de
    Meta está abierta por definición. Aquí NO: la dueña confirma el pago cuando puede (esa misma
    noche, al día siguiente…). Sin esta comprobación, Meta RECHAZA el envío y le BAJA LA CALIDAD
    AL NÚMERO — y siendo Enova Tech Provider, eso arriesga la cuenta de Meta de TODOS los clientes.
    Falla CERRADA: si no se le puede escribir, se le avisa a ELLA para que lo haga desde su
    teléfono. Un pago confirmado no se puede quedar sin avisar.
    """
    if not await _numero_permitido(telefono):
        return

    if not await _ventana_abierta(telefono):
        logger.warning("Aviso de pago a %s: ventana de 24h CERRADA → el bot NO escribe", telefono)
        await _avisar_a_la_duena(
            telefono,
            motivo="ventana_cerrada",
            detalle=(
                "Tocaste confirmar/rechazar el pago de este cliente, pero pasaron más de 24 horas "
                "desde su último mensaje: WhatsApp NO deja escribirle. El bot no le avisó nada — "
                "escríbele tú desde tu teléfono."
            ),
            mensaje_cliente="(esperando el resultado de su pago)",
            whatsapp=(
                f"⏰ No pude avisarle a {telefono} lo de su pago: pasaron más de 24 horas desde su "
                "último mensaje y WhatsApp no deja escribirle. Escríbele tú."
            ),
        )
        return

    # 🔴 EL INTERRUPTOR TAMPOCO CUBRÍA ESTE CARRIL (auditoría 2026-08-02, META-3). Los cuatro
    # carriles hermanos SÍ lo comprueban (`_procesar`, `_retomar`, `_responder_y_enviar` y
    # `_responder_situacion` — el comentario de este último documenta el bug EXACTO, cazado el
    # 2026-07-13). Este quedó sin cerrar, y es el peor sitio donde podía quedar: es el ÚNICO
    # camino que le habla al cliente DÍAS DESPUÉS, con texto redactado por el LLM y sin que
    # nadie lea ese texto antes de que salga.
    #
    # El caso real: la dueña APAGA el bot (porque está diciendo algo raro, o porque se va de
    # viaje), entra al panel a ponerse al día y confirma tres pagos ⇒ el bot le escribía a TRES
    # clientes, 12-48 h después, sin supervisión. Apagar el bot tiene que significar apagar el bot.
    #
    # Va DESPUÉS de la ventana a propósito: si a ese cliente no se le puede escribir, eso es lo
    # que ella necesita leer (y el consejo es el mismo, "escríbele tú"). El pago NO se pierde por
    # esto: la confirmación ya está escrita en la BD y la bandeja le dice a quién le toca. El
    # WhatsApp lleva candado de 15 min —la avería es UNA, el bot apagado— pero la FILA de la
    # bandeja se abre para CADA cliente, que es lo que ella necesita para no dejarse a ninguno.
    if not await _bot_activo():
        logger.warning(
            "Aviso de pago a %s: el BOT ESTÁ APAGADO → no le escribe. Queda en la bandeja.",
            telefono,
        )
        await _avisar_a_la_duena(
            telefono,
            motivo="bot_apagado",
            detalle=(
                "Confirmaste o rechazaste el pago de este cliente, pero el BOT ESTÁ APAGADO, así "
                "que no le avisó nada (apagado es apagado). Escríbele tú, o enciende el bot y "
                "vuelve a tocar el botón."
            ),
            mensaje_cliente="(esperando el resultado de su pago)",
            whatsapp=(
                "🔌 Tocaste confirmar/rechazar un pago, pero el bot está APAGADO: no le avisó al "
                "cliente. Míralo en 'Te esperan' y escríbele tú, o enciende el bot y vuelve a "
                "tocarlo."
            ),
            candado=("pago_con_bot_apagado", 900),
        )
        return
    try:
        # Con respaldo, `sembrar=False` por el mismo motivo que el carril del comprobante.
        historial = await historial_con_respaldo(telefono)
        _usd, _bs = await _montos_decibles(telefono)
        mensaje = await redactar_mensaje(
            situacion, historial, None, telefono, montos_usd=_usd, montos_bs=_bs
        )
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo redactar el aviso de pago para %s", telefono)
        return
    if not mensaje.strip():
        # La red del dinero lo tumbó dos veces: NO se le manda una mentira sobre su pago.
        logger.error("Aviso de pago a %s: no salió un mensaje limpio; lo pasa la dueña", telefono)
        await _avisar_mensaje_frenado(telefono, None)
        return
    partes = await _enviar_en_partes(telefono, mensaje)
    if _algo_llego(partes):
        await rc.guardar_historial(telefono, "assistant", _lo_que_llego(partes, mensaje))
    if partes:
        await _guardar_en_panel(telefono, None, "", partes)
    # 🔴 AQUÍ NO HABÍA `else`, Y ESE ES EL BUG 1 DEL REPORTE DE MAIRED. Los dos carriles de arriba
    # (ventana cerrada, bot apagado) SÍ avisan; este se iba MUDO. Y no por un caso raro:
    # `_enviar_en_partes` devuelve lista VACÍA cuando la dueña tiene el chat tomado — que es EL
    # CASO NORMAL, porque si ella está confirmando el pago es justo porque pidió el comprobante a
    # mano o contestó desde el celular. Resultado: el cliente pagó, NADIE le confirmó, y no quedaba
    # ni un rastro de que el aviso no había salido: ni fila en la bandeja, ni WhatsApp, ni error.
    #
    # Los tres finales van SEPARADOS porque le piden cosas distintas: entrar al chat que ya tiene
    # abierto (tomado), escribirle desde su teléfono porque Meta nos rechazó todo (no llegó nada),
    # o completar lo que falta (a medias). El de a medias es el más caro de los tres —el cliente
    # vio MEDIA confirmación de su pago y el bot ya no la repite, ver `_lo_que_llego`— y no
    # estrena red: se reusa la MISMA que ya usa `_procesar`, que solo dispara si algo llegó.
    if not partes:
        await _avisar_pago_sin_confirmar(telefono, mensaje, tomado=True)
    elif not _algo_llego(partes):
        await _avisar_pago_sin_confirmar(telefono, mensaje, tomado=False)
    else:
        await _avisar_turno_a_medias(telefono, None, partes)
