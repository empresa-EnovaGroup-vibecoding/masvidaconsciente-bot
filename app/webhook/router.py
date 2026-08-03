import json
import logging
from datetime import UTC

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse

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
    # 🔴 SI ALGO SE ROMPIÓ, META TIENE QUE VOLVER A MANDARLO.
    #
    # Este `return` decía 200 SIEMPRE, y ahí empieza la semana muda del 10-17 de julio: con Redis
    # caído, `ya_procesado` (más abajo) revienta, el `except` de arriba lo atrapa —correcto, un
    # evento roto no puede tumbar el webhook entero— y Meta se lleva un **200 = "entregado y
    # cerrado"**. Meta no reintenta jamás. El mensaje del cliente desaparece sin dejar rastro: no
    # llega al buffer, no llega a la tabla `mensajes`, y el contenedor sigue en verde.
    #
    # Un 5xx hace que Meta lo reenvíe. Reenviarlo es SEGURO porque todo lo que sí pasó es
    # idempotente: `ya_procesado` (msg:), `comprobante_procesado` (comprob:), el UNIQUE de
    # `mensajes.message_id` y el candado del eco. Se repite el trabajo, no el efecto.
    #
    # Se devuelve 503 (no 500) a propósito: es "no pude ahora, vuelve", que es exactamente lo que
    # pasó, y los reintentos de Meta tienen backoff. (Auditoría 2026-08-02, F1.)
    if "error" in resultados:
        return JSONResponse(
            {"status": "parcial", "eventos": resultados}, status_code=503
        )
    return {"status": "ok", "eventos": resultados}


async def _procesar_entrante(mensaje) -> str:
    """Un CLIENTE escribió: lo de siempre."""
    # 🔴 …SALVO QUE QUIEN ESCRIBE SEA **LA DUEÑA** (auditoría 2026-08-02, META-11). `dueno_telefono`
    # solo se usaba como DESTINO; nunca se comparaba contra el remitente. Ella responde "ok, ya
    # voy" a uno de los avisos que el propio bot le manda → se le crea ficha de `Cliente`, entra
    # al carril de venta y el bot **le vende a ella**: *"¡Hola! 💚 ¿qué te gustaría pedir?"*.
    # Gasta tokens, ensucia el CRM y el reporte, y es la clase de cosa que se ve en una demo.
    #
    # Su mensaje SÍ sirve para una cosa, y es la que se aprovecha: abre SU ventana de 24h con el
    # número del negocio. Esa marca es la que después decide si tiene sentido intentar mandarle
    # un aviso por WhatsApp (META-15, `puede_escribirle_a_la_duena`).
    #
    # ⚠️ CONSECUENCIA ACEPTADA: si ella le escribe al número del negocio para "probar si el bot
    # funciona", ya NO le contesta. Para eso está el SIMULADOR del panel (teléfonos "__…"), que
    # se construyó justo para eso y no gasta ventana de Meta. Lo que dijo queda en el log del
    # contenedor, con su texto, para que nunca sea un silencio sin rastro.
    if await _es_la_duena(mensaje["telefono"]):
        from app.services.meta_client import abrir_ventana_de_la_duena

        logger.info(
            "Mensaje de LA DUEÑA al número del negocio (%s): el bot NO le contesta; se abre su "
            "ventana de 24h. Dijo: %r", mensaje["telefono"], (mensaje.get("texto") or "")[:200],
        )
        await abrir_ventana_de_la_duena()
        return "duena"

    # 🔴 …NI TAMPOCO SI QUIEN ESCRIBE ES UN **CONTACTO PRIVADO** (Capa 1, reporte de Maired §6).
    # Su familia, sus amigos y los clientes de su OTRO negocio entran por este mismo número, y
    # ELLOS TAMBIÉN ESCRIBEN PRIMERO — por eso la auto-pausa por eco no los cubre: esa solo
    # protege los chats donde ella ya intervino.
    #
    # VA ANTES DE `_marcar_entrante`, Y ESA ES LA DECISIÓN IMPORTANTE DE TODO EL ARREGLO. De aquí
    # para abajo, cada paso deja una huella; el mensaje tiene que morir ANTES de la primera:
    #   · No sube `no_leidos` ⇒ NO se repite SIL-7 (el globito de "3 no leídos" sobre un hilo
    #     VACÍO). Ojo con la lección: el error de SIL-7 fue el DESAJUSTE — el contador decía 3 y
    #     el hilo no tenía nada. Allí se arregló guardando el mensaje, porque el contador YA había
    #     subido. Aquí se arregla por la otra rama, que también es coherente: NI contador NI
    #     mensaje. Es exactamente lo que hace la dueña tres líneas más arriba, y nadie se queja de
    #     globitos huérfanos en su chat.
    #   · No se guarda en `mensajes` ⇒ sus conversaciones con la familia NO quedan archivadas en
    #     la base del NEGOCIO, que es lo que la palabra "privado" promete. Callar al bot pero
    #     seguir archivándolo todo sería media solución, y la peor mitad: da la sensación de
    #     privacidad sin darla. El mensaje no se pierde — está en su teléfono, en WhatsApp, que
    #     es su sitio; y el chat viejo sigue en el panel para poder DESmarcarlo.
    #   · No se marca leído ni sale "escribiendo…" (`_marcar_leido_si_vamos_a_responder` está más
    #     abajo desde META-9) ⇒ cero rastro de robot en un chat personal. El doble check azul lo
    #     pone WhatsApp cuando ELLA lo abre, que es como tiene que ser.
    #   · No cuenta para el tope anti-abuso (`_excede_tope`, más abajo) ⇒ una hermana habladora no
    #     dispara el aviso de "este cliente pasó el límite". El tope existe para no gastar IA con
    #     un troll, y por este camino se gasta CERO IA.
    #   · No se descarga ni se transcribe NADA: la foto familiar no pasa por la visión y la nota
    #     de voz no pasa por Gemini. Ahorra dinero, sí — pero sobre todo NO manda material privado
    #     a un proveedor de IA. Ese es el motivo por el que este freno NO puede vivir solo en
    #     `_estado_pausa`: allí se llega DESPUÉS de haber descargado y pagado.
    #
    # ⚠️ DEL TEXTO NO SE DEJA RASTRO, y aquí SÍ nos apartamos de la dueña a propósito: su línea de
    # log guarda lo que dijo para que nunca sea un silencio sin rastro, pero volcar el mensaje de
    # un familiar al log del contenedor contradice de frente lo que este freno defiende. Queda el
    # número y el tipo: suficiente para depurar "¿por qué no contestó?", sin archivar a nadie.
    if await _es_contacto_privado(mensaje["telefono"]):
        logger.info(
            "Mensaje (%s) de un CONTACTO PRIVADO (%s): el bot no lo atiende, no lo guarda y no "
            "gasta IA. El contenido NO se registra a propósito.",
            mensaje.get("tipo"), mensaje["telefono"],
        )
        return "privado"

    # EL RELOJ DE LAS 24 HORAS arranca AQUÍ y no en el worker: este es el único embudo por
    # el que pasan los CUATRO caminos (texto, voz, comprobante, sticker). El comprobante, por
    # ejemplo, nunca pasa por el worker de texto: si el reloj viviera allá, un cliente que solo
    # manda la captura del pago aparecería con la ventana CERRADA y la dueña no podría
    # responderle justo en el momento del dinero.
    #
    # ⚠️ Y solo se llama desde AQUÍ: un ECO es un mensaje SALIENTE y NO abre la ventana de 24h
    # (si la abriera, el panel dejaría escribir fuera de plazo y Meta rechazaría el envío).
    await _marcar_entrante(mensaje["telefono"], mensaje.get("nombre"))

    tipo = mensaje["tipo"]

    # Tope de gasto / anti-abuso: los comprobantes (image/document) SIEMPRE pasan
    # (es dinero); el resto cuenta para el limite diario por cliente.
    if tipo not in ("image", "document") and await _excede_tope(
        mensaje["telefono"], mensaje.get("nombre")
    ):
        # 🔴 EL TOPE FRENA LA RESPUESTA, NO EL MENSAJE (auditoría 2026-08-02, SIL-7). Esto era un
        # `return "limite"` a secas: el texto o la nota de voz del cliente no llegaban ni al
        # buffer ni a la tabla `mensajes`. Pero `_marcar_entrante` (arriba) YA le había subido el
        # contador de no leídos ⇒ el panel mostraba "3 no leídos" Y EL HILO VACÍO: la dueña
        # entraba a leer y no había NADA que leer. Es la peor forma del fallo mudo — el sistema
        # te DICE que hay algo y luego te enseña un vacío.
        # El tope existe para no gastar IA con un troll, no para borrarle los mensajes a un
        # cliente que habla mucho (con 80/día, los que más hablan suelen ser los que COMPRAN).
        # Lo que se frena es la RESPUESTA del bot; lo que dijo el cliente se guarda igual y ella
        # puede contestarle.
        await _guardar_sin_responder(mensaje)
        return "limite"

    # 🔴 "ESCRIBIENDO…" SOLO SI DE VERDAD VAMOS A ESCRIBIR (auditoría 2026-08-02, META-9).
    # Esta llamada estaba ARRIBA DEL TODO, antes del tope, del interruptor y de la lista blanca —
    # justo lo contrario de lo que promete su propio docstring ("SOLO se llama cuando SÍ vamos a
    # responder"). Dos daños, y ninguno es cosmético:
    #   · Con el bot APAGADO, el cliente veía el doble check azul y "escribiendo…" y después 20
    #     minutos de nada. Es peor que no leerlo: le enseña que lo estás ignorando a propósito.
    #   · Un cliente pasado del tope generaba **un POST a Meta por cada mensaje**, para siempre.
    #     Un troll con 500 mensajes son 500 llamadas a la API contra este número, gratis para él.
    # Ahora va después de todos los frenos. El único que queda por delante es el modelo (que puede
    # caerse), y ese caso ya tiene su propia red (`_avisar_turno_perdido`).
    await _marcar_leido_si_vamos_a_responder(mensaje)

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


async def _es_la_duena(telefono: str) -> bool:
    """¿Este mensaje lo escribió LA DUEÑA desde su teléfono personal? (META-11).

    🔴 YA NO SE MIRA SOLO EL ENTORNO (arreglo del cerebro partido, 2026-08-03). Antes esto decía
    que se comparaba contra `DUENO_TELEFONO` del ENTORNO a propósito, y que "si algún día se
    cambia solo desde el panel, lo peor que pasa es que volvemos al comportamiento de hoy (el bot
    le contesta), no que se rompa nada". Los dos supuestos resultaron falsos en la caja real:
      · no es "algún día": el taller lleva así desde siempre — entorno VACÍO y el número en la
        tabla — así que esto devolvía False SIEMPRE y el bot le vendía a la dueña;
      · y "el bot le contesta" no es inofensivo: le crea ficha de Cliente, la mete en el carril de
        venta, gasta tokens y ensucia el CRM y el reporte.

    El argumento del carril caliente SÍ seguía en pie, y por eso el resolvedor único memoriza el
    valor en el proceso (`services/dueno.py`, memo de 60 s): la consulta a Postgres se paga UNA
    vez por minuto y por proceso, no una por mensaje. Coste amortizado por mensaje: CERO.

    ⚠️ Vacío = nadie es la dueña. Un `dueno_telefono` sin configurar NO puede convertir a todos los
    clientes en 'la dueña' y dejar al bot mudo con el mundo entero. Esa regla NO cambió: vive
    ahora dentro de `dueno.es_la_duena`, y `probar_meta.py` caso 7 la vigila.
    """
    from app.services.dueno import es_la_duena

    return await es_la_duena(telefono)


async def _es_contacto_privado(telefono: str) -> bool:
    """¿Este número está marcado como CONTACTO PRIVADO? (familia, amigos, el otro negocio).

    🔴 POR QUÉ EXISTE (Capa 1 del reporte de Maired). El WhatsApp del negocio es TAMBIÉN el
    personal de Whuilianny. La auto-pausa por eco no cubre esto: solo se enciende cuando ELLA ya
    respondió en ese chat, y el familiar que escribe "hola cómo estás" es el que ESTRENA la
    conversación. Sin este freno, el bot le contesta con el catálogo de comida.

    ⚠️ Y no confundir con `_numero_permitido`: ESA es la lista blanca de PRUEBAS, que hoy tapa el
    problema por accidente (tiene dos números) y que desaparece el día que se abra el bot a
    clientes reales. Esto es otra cosa y vive aparte a propósito.

    FALLA ABIERTA, igual que `_cliente_pausado` (workers/tasks.py): si Postgres no contesta, el
    bot sigue atendiendo. Un error de lectura es GLOBAL y transitorio, y dejar mudo al bot con
    TODOS los clientes de verdad es la avería que ya costó una semana muda. Lo peor que pasa por
    fallar abierto es una respuesta de más a un familiar en plena caída de base — recuperable, y
    el segundo cinturón (`_estado_pausa`) todavía puede pillarla más adelante.

    Se pide SOLO la columna, no la fila entera: es una lectura por el UNIQUE de `telefono` y se
    paga una vez por mensaje entrante, detrás del filtro de la dueña. Los teléfonos internos
    ("__…", simulador del panel y bancos) se cortan antes de tocar la base: no son gente.
    """
    if (telefono or "").startswith("__"):
        return False

    from sqlalchemy import select

    from app.models import Cliente
    from app.services.db import get_session_factory

    try:
        factory = get_session_factory()
        async with factory() as session:
            privado = (
                await session.execute(
                    select(Cliente.privado).where(Cliente.telefono == telefono)
                )
            ).scalar_one_or_none()
        return bool(privado)
    except Exception:  # noqa: BLE001 — ante la duda, se atiende como siempre (ver docstring)
        logger.exception(
            "No pude saber si %s es un contacto privado; se atiende como hasta hoy", telefono
        )
        return False


async def _marcar_leido_si_vamos_a_responder(mensaje) -> None:
    """Doble check azul + "escribiendo…", pero solo si el bot va a contestar de verdad.

    Los tres frenos se preguntan aquí, en el webhook, y NO en el worker, porque el indicador de
    tipeo solo sirve si sale YA (el worker arranca 15 s después, con el buffer). Cada uno de los
    tres trae su propio lado seguro de casa y ninguno lanza; si algo raro pasara igual, se marca
    como hasta hoy: quedarse sin doble check azul no puede tumbar un webhook.
    """
    from app.services.meta_client import marcar_leido_y_escribiendo

    try:
        from app.workers.tasks import _bot_activo, _cliente_pausado, _numero_permitido

        telefono = mensaje["telefono"]
        if (
            not await _bot_activo()
            or await _cliente_pausado(telefono)
            or not await _numero_permitido(telefono)
        ):
            logger.info(
                "No se marca leído/escribiendo a %s: el bot no va a responderle (apagado, chat "
                "tomado o fuera de la lista blanca)", telefono,
            )
            return
    except Exception:  # noqa: BLE001 — ante la duda, se comporta como siempre
        logger.exception("No pude decidir si mostrar 'escribiendo…'; se muestra, como antes")
    await marcar_leido_y_escribiendo(mensaje["message_id"])


async def _procesar_eco(eco) -> str:
    """LA DUEÑA escribió desde SU CELULAR: el bot se calla en ese chat.

    ORDEN SAGRADO — la PAUSA primero, la burbuja después, cada una en su propia transacción:
    si fueran juntas y el INSERT fallara (una foto sin pie, un tipo raro, un ❤️), el rollback
    se llevaría también la pausa y el bot volvería a hablarle ENCIMA a la dueña, en medio de
    una venta. Perder una burbuja del historial es cosmético; perder la pausa, no.
    """
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models import Cliente, Intervencion, Mensaje, now_utc
    from app.services import redis_client as rc
    from app.services.db import get_session_factory
    from app.services.meta_client import es_mensaje_propio
    from app.webhook.parser import contenido_seguro

    telefono, wa_id = eco["telefono"], eco["message_id"]

    # 1) Candado barato: Meta REENTREGA el mismo evento si duda de nuestra respuesta.
    #
    # 🔴 SE MIRA AQUÍ, PERO SE MARCA AL FINAL (auditoría 2026-08-02, META-2). Antes esto era
    # `ya_procesado`, que MARCA AL LEER (`SET nx ex=86400`): si el `commit` de la PAUSA reventaba
    # tres líneas más abajo, Meta reintentaba el evento y el reintento se descartaba como
    # "eco_duplicado" ⇒ **la pausa no se ponía nunca** y el bot le hablaba encima a la dueña
    # delante del cliente, en medio de una venta. El "ORDEN SAGRADO" del docstring se respeta
    # DENTRO del handler; el candado que estaba ANTES lo anulaba.
    #
    # Marcar al final es seguro porque todo lo de aquí es idempotente: la pausa es un UPSERT con
    # los mismos valores, el `chat_tomado` mira si ya existe, la burbuja va con
    # `on_conflict_do_nothing(message_id)` y la memoria del bot solo se toca `if nueva`. Dos
    # entregas simultáneas repiten el TRABAJO, no el EFECTO — que es la misma regla con la que
    # F1 justificó devolverle 503 a Meta.
    if await rc.get_cache(f"cache:eco:{wa_id}"):
        return "eco_duplicado"

    factory = get_session_factory()

    # 2) ¿Es NUESTRO? Hoy está verificado que la Cloud API no genera eco, pero si Meta lo
    #    cambiara, el bot se pausaría a sí mismo tras cada respuesta y quedaría MUDO con todos
    #    los clientes. Este cinturón cuesta una consulta y evita el desastre.
    #
    #    🔴 Y AHORA LLEGA A TIEMPO (auditoría 2026-08-02, META-1). La fila de `mensajes` con este
    #    `wa_message_id` no existe hasta ~3-4 s después del envío (5 × `sleep(1)` + latencia +
    #    `_guardar_en_panel`), así que la prueba de "es mío" era un TOCTOU: llegaba TARDE. La
    #    marca de Redis se pone en el instante en que Meta devuelve el id, y se pregunta PRIMERO.
    #    La consulta a la BD se queda detrás como respaldo (si Redis se reinició, la fila sigue
    #    ahí): son dos cinturones, no uno sustituyendo al otro.
    if await es_mensaje_propio(wa_id):
        logger.info("Eco de un mensaje NUESTRO (%s, marca de Redis): se ignora, no se pausa", wa_id)
        return "eco_propio"
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

    # 3.5) EL AVISO SE CIERRA Y SE CONVIERTE EN "DEVUÉLVEME EL CHAT".
    #
    # 🔴 El aviso de `pedir_ayuda` le dice a la dueña, con estas palabras: "Entra al WhatsApp del
    # negocio y respóndele tú" (tools.py, `_avisar_intervencion`). Cuando ella OBEDECÍA, este
    # camino no cerraba nada — solo lo hacía el del panel (api/router.py). La Intervencion se
    # quedaba 'pendiente' para siempre y, con la regla de "un solo aviso vivo por chat", se
    # tragaba TODAS las escaladas futuras de ese cliente: el bot prometía "te confirmo enseguida"
    # y no había fila, ni WhatsApp, ni rastro. (Auditoría 2026-08-02, SIL-9.)
    #
    # ⚠️ Y CERRARLO A SECAS SERÍA PEOR: ese aviso pendiente es el ÚNICO botón de la bandeja que
    # reactiva el bot (`/intervenciones/{id}/resolver` con reactivar=True → `_disparar_retomar`).
    # Si el eco lo cierra y no deja nada en su lugar, desaparece de "Te esperan", nadie aprieta
    # nada y el chat queda MUDO PARA SIEMPRE — el síntoma exacto que se vino a matar. Por eso se
    # cierra el viejo (ya lo está atendiendo ELLA) y se deja UNO que dice la verdad de ahora: el
    # chat lo tienes tú. Ese es el que cierra al terminar, y ahí el bot vuelve.
    #
    # Transacción PROPIA y con todo tragado: la PAUSA de arriba es lo único que no se puede
    # perder (ORDEN SAGRADO). Y es idempotente frente a los 5 mensajes seguidos que ella mande:
    # el segundo eco encuentra el `chat_tomado` pendiente, no cierra nada más y no añade nada.
    # (El barredor NO lo toca: `cerrar_avisos_ya_atendidos` excluye este motivo EXACTO.)
    try:
        async with factory() as session:
            pendientes = (
                await session.execute(
                    select(Intervencion).where(
                        Intervencion.cliente_telefono == telefono,
                        Intervencion.estado == "pendiente",
                    )
                )
            ).scalars().all()
            ya_tomado = any(i.motivo == "chat_tomado" for i in pendientes)
            for aviso in pendientes:
                if aviso.motivo != "chat_tomado":
                    aviso.estado = "resuelta"
                    aviso.resuelta_at = ahora
            if not ya_tomado:
                session.add(Intervencion(
                    cliente_telefono=telefono,
                    motivo="chat_tomado",
                    # El texto nombra el botón REAL del panel, palabra por palabra
                    # (dashboard/src/app/(app)/bandeja/page.tsx): "Ya lo atendí (reactivar el
                    # bot)". Decirle que apriete algo que no existe sería dejarla igual de
                    # atascada, y el dashboard no se puede recompilar hasta que vuelva Coolify.
                    detalle=(
                        "Le respondiste tú desde tu teléfono, así que el bot se calló en ese "
                        "chat. Cuando termines con ese cliente, dale aquí a 'Ya lo atendí "
                        "(reactivar el bot)': si no, se queda sin bot para siempre."
                    ),
                ))
            await session.commit()
    except Exception:  # noqa: BLE001 — la pausa YA está puesta: eso es lo que no se puede perder
        logger.exception("No se pudo cerrar el aviso pendiente del eco de %s", telefono)

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

    # 6) AHORA SÍ: el candado (META-2). Se marca al FINAL, cuando la PAUSA —lo único que no se
    #    puede perder— ya está commiteada. Si algo revienta antes, la excepción sube, el webhook
    #    devuelve 503 y Meta reenvía el eco: esta vez se pone la pausa. Antes ese reenvío se
    #    tiraba a la basura por "duplicado".
    try:
        await rc.set_cache(f"cache:eco:{wa_id}", "1", 86400)
    except Exception:  # noqa: BLE001 — sin candado se repite el trabajo, no el efecto
        logger.warning("No se pudo marcar el eco %s como procesado", wa_id)
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
        await _telemetria_de_calidad(wa_id, ev.get("error"))
    return "estado" if res.rowcount else "estado_sin_dueño"


async def _telemetria_de_calidad(wa_id: str, error: str | None) -> None:
    """Un `failed` de Meta con un código de CALIDAD no se queda en un log (META-8).

    🔴 Por qué (auditoría 2026-08-02): los fallos se escribían en `mensajes.error`, el panel los
    pintaba en rojo, y ahí se acababa todo. **Sin Intervencion, sin aviso, sin contador.** Y los
    tres códigos que de verdad importan no son un dedazo de nadie:
      · `131047` — el envío iba FUERA DE LA VENTANA de 24h.
      · `131049` — "no entregado para mantener un ecosistema sano": Meta nos está FRENANDO. Es la
                   señal más directa que existe de que este número se está degradando.
      · `130472` — el usuario quedó fuera por una política/experimento de Meta.
    Enova es Tech Provider: esta es precisamente la telemetría que no puede quedar muda, porque
    lo que está en juego no es un mensaje, es la cuenta de Meta de TODOS sus clientes futuros.

    Deja el contador del día en Redis (para poder mirar "¿cuántos van?" sin abrir la BD) y UN
    aviso por código cada 6 h: si Meta empieza a rechazar en masa, la dueña recibe UN WhatsApp,
    no doscientos. Todo tragado: la telemetría jamás puede tumbar el webhook.
    """
    from sqlalchemy import select

    from app.models import Mensaje
    from app.services.db import get_session_factory
    from app.services.meta_client import CODIGOS_DE_CALIDAD, codigo_meta

    try:
        codigo = codigo_meta(error)
        if codigo not in CODIGOS_DE_CALIDAD:
            return

        # De quién era el mensaje. Hace falta para dos cosas: para no molestar por los teléfonos
        # internos (simulador y bancos, que fallan a propósito) y para que la fila de la bandeja
        # cuelgue del chat correcto, que es donde ella va a mirar.
        factory = get_session_factory()
        async with factory() as session:
            destino = (
                await session.execute(
                    select(Mensaje.cliente_telefono)
                    .where(Mensaje.wa_message_id == wa_id).limit(1)
                )
            ).scalars().first()
        if not destino or destino.startswith("__"):
            return

        from app.services import redis_client as rc
        from app.workers.tasks import _avisar_a_la_duena

        try:
            # Se reusa el contador diario del anti-abuso con una clave que NO es un teléfono
            # ("metafallo:131049"): es un INCR con caducidad de 26 h, que es exactamente lo que
            # hace falta, y no obliga a inventar otro contador que después nadie mantiene.
            n = await rc.contar_mensaje_dia(f"metafallo:{codigo}")
        except Exception:  # noqa: BLE001 — sin contador el aviso sale igual
            n = 0
        logger.error(
            "🔴 META RECHAZÓ UN ENVÍO POR CALIDAD — código %s, cliente %s, van %s hoy: %s",
            codigo, destino, n or "?", error,
        )

        titulo = {
            131047: "WhatsApp NO dejó escribirle: pasaron más de 24 h desde su último mensaje.",
            131049: (
                "WhatsApp decidió NO entregar el mensaje 'para mantener un ecosistema sano'. "
                "Eso es Meta frenando este número: hay que bajar el ritmo de mensajes."
            ),
            130472: "WhatsApp dejó a este usuario fuera por una política suya.",
        }.get(codigo, "WhatsApp rechazó el envío.")

        await _avisar_a_la_duena(
            destino,
            motivo="envio_rechazado",
            detalle=(
                f"{titulo} (código {codigo} de WhatsApp). El mensaje NO le llegó al cliente y en "
                "el chat se ve en rojo. Si esto sale seguido, avísale a Enova: la calidad del "
                "número está en juego."
            ),
            mensaje_cliente="(WhatsApp rechazó el mensaje del bot)",
            whatsapp=(
                f"📵 WhatsApp rechazó un mensaje del bot hacia {destino} (código {codigo}). "
                f"{titulo} Míralo en el chat: está en rojo."
            ),
            candado=(f"meta_fallo:{codigo}", 21600),
        )
    except Exception:  # noqa: BLE001 — la telemetría NUNCA puede tumbar el webhook
        logger.exception("No se pudo procesar el fallo de calidad de Meta (%s)", wa_id)


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
    """Sticker/video/ubicacion/etc.: el agente responde natural, sin frases roboticas.

    🔴 EL EVENTO VIAJA CON SUS DATOS (auditoría 2026-08-02, SIL-12). Aquí se encolaban solo
    (teléfono, tipo, nombre) y el worker construía la frase genérica "(el cliente envio un
    location, sin texto)". Para una UBICACIÓN eso es tirar LA DIRECCIÓN A DONDE HAY QUE LLEVAR EL
    PEDIDO: `latitude`/`longitude`/`name`/`address` no quedaban en `mensajes`, ni en Redis, ni en
    el log. En un negocio de delivery eso es una entrega que no se puede hacer. El `message_id`
    va también porque es el candado UNIQUE que hace idempotente la fila del hilo.

    ⚠️ ESTA LLAMADA Y LA FIRMA DE `procesar_evento` (tasks.py) SON UNA SOLA COSA: van juntas al
    contenedor del bot Y al del worker en el mismo despliegue. `procesar_evento` gana los dos
    parámetros AL FINAL y con default, así que las tareas YA encoladas con la tupla de 3 siguen
    ejecutándose; lo que no sobrevive es un worker VIEJO recibiendo esta tupla de 5.
    """
    from app.services import redis_client as rc
    from app.workers.tasks import procesar_evento

    if await rc.ya_procesado(mensaje["message_id"]):
        return "duplicado"
    procesar_evento.apply_async((
        mensaje["telefono"],
        mensaje["tipo"],
        mensaje.get("nombre"),
        mensaje.get("texto"),
        mensaje.get("message_id"),
    ))
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


async def _guardar_sin_responder(mensaje) -> None:
    """Mete en el hilo un mensaje que el bot NO va a contestar (tope del día alcanzado).

    Va con `message_id` (UNIQUE desde la 001) de candado, igual que la burbuja del eco: una
    reentrega de Meta no puede duplicarlo. Y de la nota de voz se guarda el `media_id`: el panel
    se la baja de Meta al vuelo (`/api/mensajes/{id}/media`), así que la dueña puede ESCUCHARLA
    aunque el bot no la haya transcrito (el tope corta antes de la transcripción).

    Si esto falla se loguea y ya: NO puede tumbar el webhook. (Ojo: desde F1, un `error` aquí
    haría que Meta reintentara el evento entero, y este carril no es el que hay que salvar.)
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models import Mensaje
    from app.services.db import get_session_factory
    from app.webhook.parser import contenido_seguro, tipo_valido

    tipo = tipo_valido(mensaje.get("tipo"))
    try:
        factory = get_session_factory()
        async with factory() as session:
            ins = pg_insert(Mensaje).values(
                message_id=mensaje["message_id"],
                cliente_telefono=mensaje["telefono"],
                rol="user",
                contenido=contenido_seguro(tipo, mensaje.get("texto"), mensaje.get("caption")),
                tipo=tipo,
                media_id=mensaje.get("media_id"),
                media_mime=mensaje.get("mime_type"),
            ).on_conflict_do_nothing(index_elements=[Mensaje.message_id])
            await session.execute(ins)
            await session.commit()
    except Exception:  # noqa: BLE001 — guardar jamás puede tumbar el webhook
        logger.exception(
            "No se pudo guardar el mensaje frenado por el tope de %s", mensaje["telefono"]
        )


async def _avisar_duena_abuso(telefono: str, nombre: str | None, n: int) -> None:
    """Un cliente pasó el tope del día: aviso a la dueña por BANDEJA + WhatsApp.

    🔴 Esta era la ÚNICA función de aviso del sistema que NO creaba Intervencion — las otras
    tres (`_avisar_ventana_cerrada`, `_avisar_pago_en_chat_pausado`, `_avisar_a_la_duena`) sí. Y
    sale UNA SOLA VEZ AL DÍA (`aviso_abuso_nuevo`, nx + ex=93600). O sea: si ese único WhatsApp
    se perdía —su ventana de 24h cerrada, o el chat enterrado entre 200—, ese cliente se quedaba
    sin respuesta el RESTO DEL DÍA y no quedaba ni un rastro en el sitio donde ella mira, que es
    la bandeja. Se reusa `_avisar_a_la_duena` (la función de aviso del proyecto) en vez de
    repetir aquí la mitad de su cuerpo. (Auditoría 2026-08-02, SIL-7.)

    El texto también dejó de mentir por omisión: antes decía "el bot pausó las respuestas
    automáticas" sin decir que además los mensajes se estaban TIRANDO. Ahora ya no se tiran
    (`_guardar_sin_responder`) y el aviso lo dice.

    Import perezoso de `tasks`, el mismo patrón que `_encolar_mensaje` / `_encolar_comprobante`
    / `_encolar_evento`: importar este router no puede exigir Celery.
    """
    from app.workers.tasks import _avisar_a_la_duena

    quien = nombre or telefono
    await _avisar_a_la_duena(
        telefono,
        motivo="tope_diario",
        detalle=(
            f"{quien} pasó el límite de mensajes de hoy ({n}). El bot dejó de contestarle hasta "
            "mañana para no dispararse el gasto de IA, pero SUS MENSAJES SIGUEN LLEGANDO y están "
            "guardados en el chat: entra y contéstale tú."
        ),
        mensaje_cliente="(pasó el tope de mensajes del día)",
        whatsapp=(
            f"⚠️ {quien} superó el límite de mensajes de hoy ({n}). El bot pausó las respuestas "
            "automáticas con ese cliente por hoy; lo que escriba se sigue guardando en el chat. "
            "Entra y contéstale tú."
        ),
    )
