"""EL RIESGO DE CUENTA META — banco de pruebas del BLOQUE 3 (auditoría 2026-08-02).

Enova es **Tech Provider oficial de Meta**. Un envío mal calibrado no cuesta un mensaje: le quema
la calidad a ESTE número y arriesga la cuenta de Meta de TODOS sus clientes futuros (CLAUDE.md §3).
Este banco vigila los nueve sitios por donde el sistema podía hacer justo eso.

Lo que se prueba, en orden de gravedad:

  1. **META-3** — el INTERRUPTOR cubre `notificar_cliente_pago`, el ÚNICO carril que le habla al
     cliente DÍAS después. Con el bot apagado no le escribe, y la dueña se entera por la bandeja.
  2. **META-15** — los avisos a la DUEÑA comprueban antes de intentar: si consta que su ventana de
     24h está cerrada (Meta nos rechazó con un 131047 hace menos de una hora) NO se vuelve a
     intentar, y el aviso sigue quedando en la bandeja. Y el sistema APRENDE del rechazo solo.
  3. **META-8** — un `failed` con código de CALIDAD (131047 / **131049** / 130472) deja fila,
     aviso y contador. Ya no muere en un log.
  4. **META-7** — un DOCUMENTO no es un pago comprobado: se registra (el dinero no se descarta)
     pero el bot NO dice "recibí tu pago", y la dueña recibe el aviso de que hay que mirarlo.
  5. **META-5** — la media (fotos y catálogo) pasa por el freno anti-atropello: con el chat
     tomado por la dueña, NO salen.
  6. **META-9** — "escribiendo…" solo si el bot va a responder de verdad.
  7. **META-11** — la dueña NO entra al carril de cliente: el bot no le vende a su propio número,
     y su mensaje abre su ventana de 24h.
  8. **META-10** — el freno de tasa por número del negocio existe, y el `429` de Meta se respeta.
  9. **META-13** — `descargar_media` tiene tope de tamaño (RAM del worker) y el comprobante
     demasiado grande NO se reintenta: va directo a manos de la dueña.
 10. **META-1 / META-2** — el cinturón anti-eco llega A TIEMPO (marca en Redis, no fila en la BD),
     y el candado del eco se marca DESPUÉS de que la pausa esté commiteada.

Se corre DENTRO del contenedor del bot, contra la BD del taller:
  docker exec -w /app -e PYTHONPATH=/app <bot> python scripts/probar_meta.py

⚠️ NADA sale al mundo: `enviar_texto` y las funciones de media se sustituyen por espías en los
tres módulos que las usan (tasks, tools y meta_client). Los teléfonos de prueba empiezan por
"58999100…" (imposible como número venezolano real) para reconocer un residuo de un vistazo, y el
`finally` los borra — igual que hace `probar_vigilante`.
"""
import asyncio
import inspect
import sys
import time

from sqlalchemy import delete, select

import app.agent.tools as tl
import app.services.db as dbmod
import app.services.dueno as dn
import app.services.meta_client as mc
import app.webhook.router as W
from app.models import Cliente, Intervencion, Mensaje, now_utc
from app.services import redis_client as rc
from app.services.db import get_session_factory
from app.workers import tasks as T

TEL = "58999100001"          # el cliente de siempre
TEL_DOC = "58999100002"      # el que manda un PDF
TEL_MEDIA = "58999100003"    # el que pide fotos con el chat tomado
DUENA = "58999100999"        # el teléfono "personal" de la dueña, para las pruebas
TEL_INTERNO = "__prueba_meta__"
TODOS = [TEL, TEL_DOC, TEL_MEDIA, DUENA, TEL_INTERNO]

# ⚠️ Ids ÚNICOS por corrida, igual que en `probar_fase2`: los candados de idempotencia
# (`comprob:`, `cache:eco:`) recuerdan cada id en Redis durante 24 h. Con ids fijos, la segunda
# corrida del día se saltaría medio banco — y con razón, porque el código estaría haciendo
# exactamente su trabajo. Reutilizarlos da un rojo FALSO.
RUN = str(int(time.time()))

# El envío de verdad, guardado ANTES de poner los espías: hay un caso (META-15) que necesita la
# función real para comprobar que se corta en la puerta y no llega ni a intentar el POST.
_ENVIAR_REAL = mc.enviar_texto

fallos: list[str] = []
enviados: list[tuple[str, str]] = []  # (destino, texto) — lo que HABRÍA salido por WhatsApp


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"   {'[OK ]' if ok else '[MAL]'} {nombre}{('  → ' + str(detalle)) if detalle and not ok else ''}")
    if not ok:
        fallos.append(nombre)


async def _espia(telefono, texto, **_):
    # `**_`: el carril de EMERGENCIA (`avisar_relevo_caido`) manda `forzar=True`, y este espía
    # está puesto a la vez en `T`, `tl` y `mc` (main, :664-666).
    enviados.append((telefono, texto))
    return {"messages": [{"id": f"wamid.META{len(enviados)}"}]}


async def _fijo(v):
    return v


async def _si():
    return True


async def _no():
    return False


async def _limpiar() -> None:
    factory = get_session_factory()
    async with factory() as s:
        await s.execute(delete(Mensaje).where(Mensaje.cliente_telefono.in_(TODOS)))
        await s.execute(delete(Intervencion).where(Intervencion.cliente_telefono.in_(TODOS)))
        await s.execute(delete(Cliente).where(Cliente.telefono.in_(TODOS)))
        await s.commit()
    # Las marcas de Redis de esta corrida (la ventana de la dueña y los candados de aviso).
    c = rc._client()
    await c.delete(
        mc._MARCA_VENTANA,
        "aviso:pago_con_bot_apagado",
        f"aviso:comprobante_doc:{TEL_DOC}",
        "aviso:meta_fallo:131049",
        "aviso:media_fallo:131053",  # el candado del aviso del catálogo fantasma (caso 3b)
        # Los candados del aviso nuevo (bug 1): sin soltarlos, la SEGUNDA corrida del día no
        # vería ningún WhatsApp y el banco saldría rojo por estar el código haciendo su trabajo.
        f"aviso:pago_en_chat_tomado:{TEL}",
        "aviso:pago_no_entregado",
    )


async def _motivos(telefono: str) -> list[str]:
    factory = get_session_factory()
    async with factory() as s:
        return list((await s.execute(
            select(Intervencion.motivo).where(Intervencion.cliente_telefono == telefono)
        )).scalars().all())


# ─── 1) META-3: EL INTERRUPTOR CUBRE EL AVISO DE PAGO ────────────────

async def caso_interruptor_del_aviso_de_pago() -> None:
    print("\n1) 🔌 META-3: el interruptor cubre `notificar_cliente_pago` (el que habla DÍAS después)")
    await _limpiar()
    factory = get_session_factory()
    async with factory() as s:
        s.add(Cliente(telefono=TEL, nombre="Rosa", ultimo_entrante_at=now_utc()))  # ventana ABIERTA
        await s.commit()

    real_activo = T._bot_activo
    T._bot_activo = _no
    enviados.clear()
    try:
        await T._notificar_cliente_pago(TEL, "su pago quedó confirmado")
    finally:
        T._bot_activo = real_activo

    al_cliente = [t for t, _ in enviados if t == TEL]
    check("🔴 con el bot APAGADO no le escribe al cliente (aunque la ventana esté abierta)",
          not al_cliente, str(enviados))
    check("🔴 y la dueña lo ve en la bandeja ('bot_apagado'), para que le escriba ella",
          "bot_apagado" in await _motivos(TEL), str(await _motivos(TEL)))

    # ANTI-REGRESIÓN: con el bot encendido y la ventana abierta, el aviso SÍ sale. Este banco no
    # puede convertirse en la excusa para que el cliente no se entere de su pago.
    await _limpiar()
    async with factory() as s:
        s.add(Cliente(telefono=TEL, nombre="Rosa", ultimo_entrante_at=now_utc()))
        await s.commit()
    real_redactar = T.redactar_mensaje

    async def _redacta(*a, **k):
        return "listo Rosa, ya me llegó 💚"

    T._bot_activo, T.redactar_mensaje = _si, _redacta
    enviados.clear()
    try:
        await T._notificar_cliente_pago(TEL, "su pago quedó confirmado")
    finally:
        T._bot_activo, T.redactar_mensaje = real_activo, real_redactar
    check("✅ con el bot ENCENDIDO y la ventana abierta, el cliente SÍ recibe su aviso",
          any(t == TEL for t, _ in enviados), str(enviados))
    check("✅ y no se le abre ningún aviso a la dueña (no avisamos de lo que funcionó)",
          not await _motivos(TEL), str(await _motivos(TEL)))

    # Y el candado del WhatsApp NO se lleva por delante la fila de la bandeja: tres pagos
    # confirmados con el bot apagado son TRES filas (una por cliente) y UN solo WhatsApp.
    await _limpiar()
    async with factory() as s:
        s.add_all([
            Cliente(telefono=t, nombre="Prueba", ultimo_entrante_at=now_utc())
            for t in (TEL, TEL_DOC, TEL_MEDIA)
        ])
        await s.commit()
    T._bot_activo = _no
    enviados.clear()
    try:
        for t in (TEL, TEL_DOC, TEL_MEDIA):
            await T._notificar_cliente_pago(t, "su pago quedó confirmado")
    finally:
        T._bot_activo = real_activo
    # `sum(... await ... for ...)` NO funciona: la comprensión con `await` produce un
    # async_generator, y `sum()` no lo sabe iterar. Bucle explícito.
    filas = 0
    for t in (TEL, TEL_DOC, TEL_MEDIA):
        filas += len(await _motivos(t))
    check("🔴 tres pagos con el bot apagado ⇒ TRES filas en la bandeja (una por cliente)",
          filas == 3, str(filas))
    check("   ...y UN solo WhatsApp (la avería es una: el bot está apagado)",
          len(enviados) == 1, str(enviados))


# ─── 2) META-15: LOS AVISOS A LA DUEÑA MIRAN ANTES DE SALTAR ─────────

async def caso_ventana_de_la_duena() -> None:
    print("\n2) ⏰ META-15: el aviso a la DUEÑA comprueba ANTES de intentar (y aprende del rechazo)")
    await _limpiar()
    guardado = mc.settings.dueno_telefono
    mc.settings.dueno_telefono = DUENA
    # 🔴 Y SE LE FIJA EL MEMO AL RESOLVEDOR ÚNICO. Desde el arreglo del cerebro partido, el número
    # de la dueña sale de la tabla `configuracion` ANTES que del entorno — y en el taller la tabla
    # SÍ trae un número real. Sin esto, el banco compararía contra la dueña DE VERDAD y se pondría
    # rojo por el motivo equivocado. Se fija el MEMO (que es lo primero que mira `dueno.py`) en vez
    # de tocar la tabla: un banco no le escribe a la base.
    dn._memo_valor, dn._memo_hasta = DUENA, time.monotonic() + 3600
    try:
        check("un aviso a la dueña se reconoce como tal (no se confunde con un cliente)",
              await mc.es_numero_de_la_duena(f"+{DUENA}")
              and not await mc.es_numero_de_la_duena(TEL))

        check("sin saber nada, se intenta igual que siempre (nunca se apaga el canal a ciegas)",
              await mc.puede_escribirle_a_la_duena(DUENA))

        # Meta nos dice que la ventana está cerrada: el sistema lo APRENDE solo.
        await mc._cerrar_ventana_de_la_duena(DUENA, mc.CODIGO_FUERA_DE_VENTANA)
        check("🔴 tras un 131047, CONSTA que está cerrada",
              not await mc.puede_escribirle_a_la_duena(DUENA))

        # Y con eso, `enviar_texto` ya NO intenta el POST: lanza sin tocar la red. Se usa la
        # función REAL (no el espía) y se le pone una trampa a `_enviar`: si el POST se intentara
        # igual, el banco se pone rojo AQUÍ en vez de mandarle basura a Meta.
        async def _trampa(*a, **k):
            raise AssertionError("¡el POST hacia Meta se intentó igual!")

        real_enviar = mc._enviar
        mc._enviar = _trampa
        salto = ""
        try:
            await _ENVIAR_REAL(DUENA, "aviso de prueba")
        except mc.MetaRechazo as e:
            salto = str(e) if e.codigo == mc.CODIGO_FUERA_DE_VENTANA else ""
        except AssertionError as e:
            salto = f"FALLÓ: {e}"
        finally:
            mc._enviar = real_enviar
        check("🔴 y el envío NO se intenta: se corta en la puerta, con su motivo escrito",
              "no se intentó" in salto, salto or "no lanzó nada")

        # Al CLIENTE no le afecta: su ventana es otra cosa y no se toca.
        #
        # ⚠️ Se comprueba el COMPORTAMIENTO (que el POST sí se intenta), no el helper.
        # `puede_escribirle_a_la_duena(destino)` responde sobre LA MARCA, no sobre el destino:
        # con la marca cerrada devuelve False para cualquier teléfono. Quien discrimina es
        # `enviar_texto`, que solo la consulta si `es_numero_de_la_duena(...)`. La primera versión
        # de este banco preguntaba al helper y se ponía ROJO con el código CORRECTO — un test que
        # mira la pieza en vez de la puerta acusa de un bug que no existe.
        intentos_cliente: list[int] = []

        async def _espia_post(*a, **k):
            intentos_cliente.append(1)
            return {"messages": [{"id": "wamid.CLIENTE_OK"}]}

        real_enviar2 = mc._enviar
        mc._enviar = _espia_post
        try:
            await _ENVIAR_REAL(TEL, "hola, tu pedido va en camino")
        except Exception:  # noqa: BLE001 — lo que importa es si llegó a intentarse
            pass
        finally:
            mc._enviar = real_enviar2
        check("✅ con la marca CERRADA, un envío a un CLIENTE sí se intenta (su ventana es otra)",
              bool(intentos_cliente), "el portón de la dueña estaría frenando a los clientes")

        # 🔴 LA PUERTA DE EMERGENCIA (2026-08-03). `avisar_relevo_caido` existe porque la BASE se
        # cayó: no hay fila en la bandeja donde su aviso pueda esperar. Si el portón lo frena por
        # un 131047 rutinario de hace un rato, la dueña no se entera de NADA.
        forzados: list[int] = []

        async def _espia_forzado(*a, **k):
            forzados.append(1)
            return {"messages": [{"id": "wamid.FORZADO"}]}

        await mc._cerrar_ventana_de_la_duena(DUENA, mc.CODIGO_FUERA_DE_VENTANA)
        real_enviar3 = mc._enviar
        mc._enviar = _espia_forzado
        try:
            await _ENVIAR_REAL(DUENA, "el bot no pudo dejarte el aviso", forzar=True)
        except Exception:  # noqa: BLE001 — lo que importa es si llegó a intentarse
            pass
        finally:
            mc._enviar = real_enviar3
        check("🔴 con la ventana CERRADA, un aviso de EMERGENCIA (`forzar`) SÍ se intenta",
              bool(forzados), "el portón dejó mudo al único canal que quedaba")

        # Y el blindaje se comprueba en el CÓDIGO, no en el comportamiento: hacia un cliente no
        # hay nada que saltar, y no puede haberlo mañana por un copiar-pegar. `forzar` tiene que
        # leerse DENTRO del guardia de la dueña.
        # ⚠️ `_ENVIAR_REAL`, NO `mc.enviar_texto`: `main()` ya sustituyó el atributo del módulo por
        # el espía (:831), así que `getsource(mc.enviar_texto)` leería el ESPÍA y este check se
        # pondría rojo sin que nadie hubiera roto nada. Es el mismo motivo por el que la función
        # de verdad se guarda arriba (:65-69).
        fuente = inspect.getsource(_ENVIAR_REAL)
        check("🔴 `forzar` vive DENTRO del guardia de la dueña (no hay puerta hacia el CLIENTE)",
              "if await es_numero_de_la_duena" in fuente
              and fuente.index("if await es_numero_de_la_duena") < fuente.index("if not forzar"),
              "la bandera se lee fuera del guardia: un carril de cliente podría usarla")

        # La dueña escribe al número del negocio ⇒ su ventana se abre y el canal vuelve entero.
        await mc.abrir_ventana_de_la_duena()
        check("🔴 en cuanto ella escribe, el canal vuelve (la marca se cura sola)",
              await mc.puede_escribirle_a_la_duena(DUENA))

        # Un error que NO es la ventana no puede apagar el canal.
        await mc._cerrar_ventana_de_la_duena(DUENA, 500)
        check("✅ un 500 de Meta NO se confunde con la ventana cerrada",
              await mc.puede_escribirle_a_la_duena(DUENA))

        check("un teléfono interno (__) nunca se frena (simulador y bancos)",
              await mc.puede_escribirle_a_la_duena("__duena_prueba__"))
    finally:
        mc.settings.dueno_telefono = guardado
        dn._memo_valor, dn._memo_hasta = "", 0.0
        await rc._client().delete(mc._MARCA_VENTANA)

    # Y la fila de la bandeja sale SIEMPRE, aunque el WhatsApp no salga: es lo que nunca falla.
    real_envio = T.enviar_texto

    async def _revienta(destino, texto):
        raise mc.MetaRechazo(400, mc.CODIGO_FUERA_DE_VENTANA, "fuera de ventana")

    T.enviar_texto = _revienta
    try:
        await T._avisar_a_la_duena(
            TEL, motivo="sin_respuesta", detalle="prueba", mensaje_cliente="prueba",
            whatsapp="prueba",
        )
    finally:
        T.enviar_texto = real_envio
    check("🔴 con el WhatsApp rechazado, el aviso SIGUE en la bandeja (nada se pierde)",
          "sin_respuesta" in await _motivos(TEL), str(await _motivos(TEL)))


# ─── 3) META-8: LOS `failed` DE CALIDAD NO MUEREN EN UN LOG ──────────

async def caso_telemetria_de_calidad() -> None:
    print("\n3) 📵 META-8: un `failed` con código de CALIDAD deja fila, aviso y contador")
    await _limpiar()
    factory = get_session_factory()
    async with factory() as s:
        s.add(Cliente(telefono=TEL, nombre="Rosa"))
        s.add(Mensaje(cliente_telefono=TEL, rol="assistant", contenido="hola",
                      wa_message_id="wamid.CAL1", estado="enviado"))
        s.add(Mensaje(cliente_telefono=TEL_INTERNO, rol="assistant", contenido="hola",
                      wa_message_id="wamid.CAL2", estado="enviado"))
        await s.commit()

    check("el código se lee igual de una excepción que de un `mensajes.error`",
          mc.codigo_meta("131049: no entregado — ecosistema sano") == 131049
          and mc.codigo_meta(mc.MetaRechazo(400, 131047, "x")) == 131047)

    enviados.clear()
    await W._aplicar_estado({"clase": "estado", "wa_message_id": "wamid.CAL1",
                             "estado": "fallido",
                             "error": '131049: Message not delivered — {"code":131049}'})
    check("🔴 el 131049 ('ecosistema sano' = degradación del número) abre fila en la bandeja",
          "envio_rechazado" in await _motivos(TEL), str(await _motivos(TEL)))
    check("   ...y le sale el WhatsApp a la dueña", bool(enviados), str(enviados))

    # No inunda: cien rechazos seguidos son UN aviso, no cien.
    enviados.clear()
    await W._aplicar_estado({"clase": "estado", "wa_message_id": "wamid.CAL1",
                             "estado": "fallido", "error": '{"code":131049}'})
    check("🔴 el segundo rechazo del mismo código NO manda otro WhatsApp (candado de 6 h)",
          not enviados, str(enviados))

    # Un código que no es de calidad no molesta a nadie.
    await _limpiar()
    async with factory() as s:
        s.add(Mensaje(cliente_telefono=TEL, rol="assistant", contenido="hola",
                      wa_message_id="wamid.CAL3", estado="enviado"))
        await s.commit()
    enviados.clear()
    await W._aplicar_estado({"clase": "estado", "wa_message_id": "wamid.CAL3",
                             "estado": "fallido", "error": '{"code":470}'})
    check("✅ un fallo corriente NO abre aviso (un detector que grita siempre se acaba ignorando)",
          not await _motivos(TEL) and not enviados, str(await _motivos(TEL)))

    # Y los teléfonos internos (bancos, simulador) jamás disparan un aviso real.
    enviados.clear()
    await W._aplicar_estado({"clase": "estado", "wa_message_id": "wamid.CAL2",
                             "estado": "fallido", "error": '{"code":131047}'})
    check("✅ un fallo de un teléfono interno (__) no molesta a la dueña", not enviados, str(enviados))
    async with factory() as s:
        await s.execute(delete(Mensaje).where(Mensaje.cliente_telefono == TEL_INTERNO))
        await s.commit()


# ─── 3b) EL CATÁLOGO FANTASMA: UN `failed 131053` LE AVISA A LA DUEÑA ─

async def caso_media_no_entregada() -> None:
    print("\n3b) 📎 EL CATÁLOGO FANTASMA: un `failed 131053` (link muerto) le avisa a la dueña")
    await _limpiar()
    factory = get_session_factory()
    async with factory() as s:
        s.add(Cliente(telefono=TEL, nombre="Rosa"))
        s.add(Mensaje(cliente_telefono=TEL, rol="assistant", contenido="(catálogo en PDF)",
                      wa_message_id="wamid.MEDIA1", estado="enviado"))
        s.add(Mensaje(cliente_telefono=TEL_INTERNO, rol="assistant", contenido="(catálogo en PDF)",
                      wa_message_id="wamid.MEDIA2", estado="enviado"))
        await s.commit()

    check("el 131053 se lee igual de un `mensajes.error` que de un cuerpo de Graph",
          mc.codigo_meta("131053: Media upload error") == 131053
          and mc.codigo_meta('{"code":131053}') == 131053)

    # El caso EXACTO del 2-sep: Meta aceptó el envío (200 + wa_message_id), y SEGUNDOS después
    # mandó el `failed` porque no pudo bajar el PDF del link muerto. Hasta hoy eso moría en un log.
    enviados.clear()
    await W._aplicar_estado({"clase": "estado", "wa_message_id": "wamid.MEDIA1",
                             "estado": "fallido",
                             "error": '131053: Media upload error — {"code":131053}'})
    check("🔴 el 131053 (Meta no pudo BAJAR el PDF del link) abre fila en la bandeja",
          "media_no_entregada" in await _motivos(TEL), str(await _motivos(TEL)))
    check("   ...y le sale el WhatsApp a la dueña (ya no hace falta una autopsia)",
          bool(enviados), str(enviados))

    # No inunda: el mismo código otra vez dentro de las 6 h es UN aviso, no dos.
    enviados.clear()
    await W._aplicar_estado({"clase": "estado", "wa_message_id": "wamid.MEDIA1",
                             "estado": "fallido", "error": '{"code":131053}'})
    check("🔴 el segundo 131053 seguido NO manda otro WhatsApp (candado de 6 h)",
          not enviados, str(enviados))

    # Un código de CALIDAD no entra por aquí (los conjuntos son disjuntos: cada `failed` dispara
    # como mucho UNA de las dos telemetrías), y un teléfono interno jamás molesta a la dueña.
    await _limpiar()
    async with factory() as s:
        s.add(Mensaje(cliente_telefono=TEL_INTERNO, rol="assistant", contenido="(catálogo)",
                      wa_message_id="wamid.MEDIA2", estado="enviado"))
        await s.commit()
    enviados.clear()
    await W._aplicar_estado({"clase": "estado", "wa_message_id": "wamid.MEDIA2",
                             "estado": "fallido", "error": '{"code":131053}'})
    check("✅ un 131053 de un teléfono interno (__) no molesta a la dueña", not enviados, str(enviados))
    async with factory() as s:
        await s.execute(delete(Mensaje).where(Mensaje.cliente_telefono == TEL_INTERNO))
        await s.commit()


# ─── 4) META-7: UN PDF NO ES UN PAGO COMPROBADO ─────────────────────

async def caso_documento_no_es_pago() -> None:
    print("\n4) 📎 META-7: un DOCUMENTO se registra, pero el bot NO afirma que recibió el pago")
    await _limpiar()
    factory = get_session_factory()
    async with factory() as s:
        s.add(Cliente(telefono=TEL_DOC, nombre="Rosa", ultimo_entrante_at=now_utc()))
        await s.commit()

    situaciones: list[str] = []
    resultados = {"ok": True, "pago_id": 1, "pedido_id": 1}

    async def _situacion(telefono, situacion, nombre):
        situaciones.append(situacion)
        return [{"texto": "(al cliente)", "wa_message_id": "wamid.X", "estado": "enviado"}]

    async def _descarga(media_id):
        return b"%PDF-1.4 esto es un pdf cualquiera", "application/pdf"

    def _guardar(media_id, contenido, mime):
        return f"/tmp/{media_id}.pdf"

    async def _registrar(session, telefono, **k):
        return resultados

    reales = (T.descargar_media, T._guardar_comprobante, T._responder_situacion, T._bot_activo)
    import app.agent.tools as tools_mod
    real_registrar = tools_mod.registrar_comprobante
    T.descargar_media, T._guardar_comprobante = _descarga, _guardar
    T._responder_situacion, T._bot_activo = _situacion, _si
    tools_mod.registrar_comprobante = _registrar
    enviados.clear()
    try:
        v = await T._procesar_comprobante(
            TEL_DOC, f"wamid.PDF.{RUN}", f"media_pdf_{RUN}", None, "Rosa", "application/pdf"
        )
    finally:
        (T.descargar_media, T._guardar_comprobante, T._responder_situacion, T._bot_activo) = reales
        tools_mod.registrar_comprobante = real_registrar

    check("el carril termina bien: el PAGO se registra igual (el dinero nunca se descarta)",
          v == "ok", repr(v))
    check("🔴 pero el bot NO le dice que recibió su pago: dice que lo está revisando",
          bool(situaciones) and "MONTO no cuadra" in situaciones[-1]
          and "coordinas la entrega" not in situaciones[-1], str(situaciones[-1:]))
    check("🔴 y la dueña se entera de que hay un archivo que MIRAR ('comprobante_sin_leer')",
          "comprobante_sin_leer" in await _motivos(TEL_DOC), str(await _motivos(TEL_DOC)))


# ─── 5) META-5: LA MEDIA PASA POR EL FRENO ANTI-ATROPELLO ────────────

async def caso_media_con_el_chat_tomado() -> None:
    print("\n5) 📸 META-5: con el chat TOMADO por la dueña, la media NO sale")
    await _limpiar()
    factory = get_session_factory()
    async with factory() as s:
        s.add(Cliente(telefono=TEL_MEDIA, nombre="Rosa", bot_pausado=True, pausado_por="dueña"))
        await s.commit()

    async with factory() as s:
        tomado = await tl._la_duena_tomo_el_chat(s, TEL_MEDIA)
        libre = await tl._la_duena_tomo_el_chat(s, TEL)
    check("🔴 el freno ve el chat que la dueña tomó", tomado is True)
    check("y NO frena a un cliente cualquiera", libre is False)

    # Y el bot no puede decirle al cliente que le mandó algo que no le mandó.
    medios: list[str] = []

    async def _imagen(telefono, link, caption=None):
        medios.append(link)
        return {"messages": [{"id": "wamid.FOTO"}]}

    async def _documento(telefono, link, filename, caption=None):
        medios.append(link)
        return {"messages": [{"id": "wamid.PDF"}]}

    reales = (tl.enviar_imagen, tl.enviar_video, mc.enviar_imagen, mc.enviar_documento)
    tl.enviar_imagen = tl.enviar_video = _imagen
    mc.enviar_imagen, mc.enviar_documento = _imagen, _documento
    try:
        from app.models import Producto, ProductoMedia

        async with factory() as s:
            prod = (await s.execute(
                select(Producto)
                .join(ProductoMedia, ProductoMedia.producto_id == Producto.id)
                .limit(1)
            )).scalars().first()
            if prod is None:
                check("hay algún producto con fotos para la prueba", False, "ninguno tiene media")
            else:
                r = await tl.enviar_fotos_producto(s, TEL_MEDIA, prod.nombre)
                check("🔴 no se envía NINGUNA foto con el chat tomado",
                      r.get("enviadas") == 0 and not medios, f"{r} {medios}")
                check("   ...y se le dice al modelo que NO diga que las mandó",
                      "NO le digas al cliente que se las mandaste" in (r.get("nota") or ""),
                      str(r))
            cat = await tl.enviar_catalogo(s, TEL_MEDIA)
            check("🔴 el catálogo PDF tampoco sale con el chat tomado",
                  cat.get("ok") is False and not [m for m in medios if "catalogo" in m], str(cat))
    finally:
        tl.enviar_imagen, tl.enviar_video, mc.enviar_imagen, mc.enviar_documento = reales


# ─── 6 y 7) META-9 y META-11: EL WEBHOOK ────────────────────────────

async def caso_webhook() -> None:
    print("\n6) ⌨️ META-9: 'escribiendo…' SOLO si el bot va a responder de verdad")
    await _limpiar()
    marcados: list[str] = []

    async def _marcar(message_id):
        marcados.append(message_id)

    real_marcar = mc.marcar_leido_y_escribiendo
    mc.marcar_leido_y_escribiendo = _marcar
    real_activo, real_pausado, real_permitido = (
        T._bot_activo, T._cliente_pausado, T._numero_permitido
    )
    try:
        T._bot_activo, T._cliente_pausado = _no, (lambda tel: _no())
        await W._marcar_leido_si_vamos_a_responder(
            {"telefono": TEL, "message_id": "wamid.LEIDO1"}
        )
        check("🔴 con el bot APAGADO no se marca leído ni se muestra 'escribiendo…'",
              not marcados, str(marcados))

        T._bot_activo, T._cliente_pausado = _si, (lambda tel: _si())
        await W._marcar_leido_si_vamos_a_responder(
            {"telefono": TEL, "message_id": "wamid.LEIDO2"}
        )
        check("🔴 con el chat TOMADO por la dueña, tampoco", not marcados, str(marcados))

        T._bot_activo, T._cliente_pausado = _si, (lambda tel: _no())
        await W._marcar_leido_si_vamos_a_responder(
            {"telefono": TEL, "message_id": "wamid.LEIDO3"}
        )
        check("✅ y cuando el bot SÍ va a contestar, se marca como siempre",
              marcados == ["wamid.LEIDO3"], str(marcados))
    finally:
        mc.marcar_leido_y_escribiendo = real_marcar
        T._bot_activo, T._cliente_pausado, T._numero_permitido = (
            real_activo, real_pausado, real_permitido
        )

    print("\n7) 🙋‍♀️ META-11: el bot NO le vende a la DUEÑA")
    await _limpiar()
    guardado = W.settings.dueno_telefono
    guardado_mc = mc.settings.dueno_telefono
    W.settings.dueno_telefono = mc.settings.dueno_telefono = DUENA
    # Mismo motivo que en el caso 2: el resolvedor único mira la TABLA antes que el entorno, y en
    # el taller la tabla trae el número REAL de la dueña. El entorno se sigue fijando (es la
    # tercera capa y no estorba), pero el que manda en la prueba es el memo.
    dn._memo_valor, dn._memo_hasta = DUENA, time.monotonic() + 3600
    await rc._client().delete(mc._MARCA_VENTANA)
    try:
        check("se la reconoce por la cola de 10 dígitos (+58…, 0…, sin prefijo)",
              await W._es_la_duena(f"+{DUENA}") and await W._es_la_duena(DUENA)
              and not await W._es_la_duena(TEL))
        r = await W._procesar_entrante({
            "telefono": DUENA, "message_id": "wamid.DUENA1", "tipo": "text",
            "texto": "ok, ya voy", "nombre": "Maired",
        })
        factory = get_session_factory()
        async with factory() as s:
            ficha = (await s.execute(
                select(Cliente).where(Cliente.telefono == DUENA)
            )).scalar_one_or_none()
        check("🔴 su mensaje NO entra al carril de cliente", r == "duena", repr(r))
        check("🔴 y NO se le crea ficha de Cliente (ni CRM sucio, ni tokens, ni venta)",
              ficha is None, str(ficha))
        check("🔴 pero SÍ se aprovecha: su ventana de 24h queda abierta",
              await rc.get_cache(mc._MARCA_VENTANA) == "abierta")

        W.settings.dueno_telefono = ""
        # 🔴 EL CANDADO DE router.py:195, AHORA EN EL RESOLVEDOR. Vacío = NADIE es la dueña. Este
        # check es EL que prueba que el arreglo no reintrodujo el riesgo de dejar al bot mudo con
        # el mundo entero: si se pone rojo, NO SE DESPLIEGA.
        dn._memo_valor, dn._memo_hasta = "", time.monotonic() + 3600
        check("✅ sin DUENO_TELEFONO configurado, NADIE es la dueña (el bot no se queda mudo)",
              not await W._es_la_duena(TEL) and not await W._es_la_duena(""))
    finally:
        W.settings.dueno_telefono = guardado
        mc.settings.dueno_telefono = guardado_mc
        dn._memo_valor, dn._memo_hasta = "", 0.0


# ─── 8) META-10: EL FRENO DE TASA Y EL 429 ──────────────────────────

async def caso_freno_de_tasa() -> None:
    print("\n8) 🛑 META-10: hay freno por NÚMERO DEL NEGOCIO, y el 429 de Meta se respeta")
    # El contador se pone a mano y NO con un bucle de 15 envíos: un bucle puede cruzar el cambio
    # de segundo, reiniciarse solo y dejar el banco rojo por una carrera en vez de por un bug.
    mc._segundo_actual, mc._envios_del_segundo = int(time.monotonic()), 0
    t0 = time.monotonic()
    await mc._freno_de_tasa()
    sin_freno = time.monotonic() - t0
    check("el ritmo normal no paga peaje (el freno no puede estorbar al cliente)",
          sin_freno < 0.5, f"{sin_freno:.2f}s")

    mc._segundo_actual = int(time.monotonic())
    mc._envios_del_segundo = mc.TOPE_ENVIOS_SEGUNDO
    t0 = time.monotonic()
    await mc._freno_de_tasa()  # el que se pasa del cupo
    con_freno = time.monotonic() - t0
    check("🔴 pasado el cupo del segundo, el envío ESPERA (antes: nada limitaba nada)",
          con_freno >= 0.9, f"{con_freno:.2f}s")
    check("y tras esperar, el cupo arranca limpio (no se queda frenando para siempre)",
          mc._envios_del_segundo == 1, str(mc._envios_del_segundo))

    class _Resp:
        def __init__(self, cabeceras):
            self.headers = cabeceras

    check("un `Retry-After` de Meta se obedece", mc._espera_del_429(_Resp({"retry-after": "2"})) == 2.0)
    check("🔴 y se acota a 2 s: arriba hay un cliente esperando su respuesta",
          mc._espera_del_429(_Resp({"retry-after": "600"})) == 2.0)
    check("un `Retry-After` ausente o basura no revienta",
          mc._espera_del_429(_Resp({})) == 1.0 and mc._espera_del_429(_Resp({"retry-after": "ya"})) == 1.0)

    check("el código de Meta se saca del cuerpo del rechazo",
          mc._codigo_en('{"error":{"message":"x","code":131047}}') == 131047)
    check("y los tres códigos de calidad están vigilados",
          mc.CODIGOS_DE_CALIDAD == {131047, 131049, 130472}, str(mc.CODIGOS_DE_CALIDAD))


# ─── 9) META-13: EL TOPE DE LO QUE NOS BAJAMOS ──────────────────────

async def caso_media_gigante() -> None:
    print("\n9) 💾 META-13: `descargar_media` tiene tope, y lo que no cabe NO se reintenta")
    await _limpiar()
    check("el tope existe y es razonable (25 MB: pasa cualquier captura o PDF de banco)",
          8 * 1024 * 1024 <= mc.MAX_MEDIA_BYTES <= 50 * 1024 * 1024, str(mc.MAX_MEDIA_BYTES))

    async def _gigante(media_id):
        raise mc.MediaDemasiadoGrande("el archivo pesa 100 MB")

    avisos: list[str] = []

    async def _avisar(telefono, **k):
        avisos.append(k.get("motivo", ""))

    media_grande = f"media_grande_{RUN}"
    reales = (T.descargar_media, T._avisar_a_la_duena, T._bot_activo)
    T.descargar_media, T._avisar_a_la_duena, T._bot_activo = _gigante, _avisar, _no
    try:
        v = await T._procesar_comprobante(
            TEL, f"wamid.GRANDE.{RUN}", media_grande, None, "Rosa", "application/pdf",
            ultimo_intento=False,
        )
    finally:
        T.descargar_media, T._avisar_a_la_duena, T._bot_activo = reales

    check("🔴 un archivo gigante NO se reintenta 3 veces: se rinde a la primera",
          v == "rendido", repr(v))
    check("🔴 y no se abandona en silencio: la dueña recibe el aviso",
          "comprobante_sin_procesar" in avisos, str(avisos))

    factory = get_session_factory()
    async with factory() as s:
        burbuja = (await s.execute(
            select(Mensaje.media_id).where(Mensaje.cliente_telefono == TEL)
        )).scalars().all()
    check("🔴 y la burbuja entra al hilo con su media_id (el panel se lo baja de Meta y ella LO VE)",
          media_grande in burbuja, str(burbuja))


# ─── 10) META-1 y META-2: EL ECO ────────────────────────────────────

async def caso_eco() -> None:
    print("\n10) 🪞 META-1/META-2: el cinturón anti-eco llega a tiempo y el candado se marca al final")
    await _limpiar()
    mio, suyo = f"wamid.MIO.{RUN}", f"wamid.ECO.{RUN}"
    await rc._client().delete(f"cache:mio:{mio}", f"cache:eco:{suyo}")

    check("un wamid desconocido no es nuestro", not await mc.es_mensaje_propio(mio))
    await mc.marcar_mensaje_propio(mio)
    check("🔴 en cuanto Meta devuelve el id, la marca YA está (sin esperar a la fila de la BD, "
          "que llega 3-4 s tarde: ese era el TOCTOU)",
          await mc.es_mensaje_propio(mio))

    eco_mio = {"clase": "eco", "telefono": TEL, "message_id": mio, "tipo": "text",
               "texto": "soy el bot", "timestamp": None}
    r = await W._procesar_eco(eco_mio)
    factory = get_session_factory()
    async with factory() as s:
        cli = (await s.execute(select(Cliente).where(Cliente.telefono == TEL))).scalar_one_or_none()
    check("🔴 el eco de un mensaje NUESTRO se descarta por la marca de Redis", r == "eco_propio", repr(r))
    check("   ...y el bot NO se pausa a sí mismo (si no, quedaría MUDO con todos)",
          cli is None or cli.bot_pausado is False, str(cli and cli.bot_pausado))

    # META-2: el candado se marca DESPUÉS de la pausa. Si la pausa revienta, el reintento de Meta
    # tiene que poder volver a entrar — antes se descartaba como "duplicado" y la pausa se perdía.
    eco = {"clase": "eco", "telefono": TEL, "message_id": suyo, "tipo": "text",
           "texto": "yo le contesto", "timestamp": None}

    def _revienta():
        raise RuntimeError("Postgres se cayó al poner la pausa")

    # `_procesar_eco` importa `get_session_factory` DENTRO de la función, así que hay que
    # sustituirlo en su módulo (mismo truco que usa `probar_no_se_evapora`).
    real_factory = dbmod.get_session_factory
    dbmod.get_session_factory = _revienta
    try:
        await W._procesar_eco(eco)
    except Exception:  # el handler del webhook lo atrapa y devuelve 503 (F1)
        pass
    finally:
        dbmod.get_session_factory = real_factory
    check("🔴 si la pausa no se pudo poner, el eco NO queda marcado como procesado",
          not await rc.get_cache(f"cache:eco:{suyo}"))

    r = await W._procesar_eco(eco)  # el reintento de Meta: esta vez SÍ
    async with factory() as s:
        cli = (await s.execute(select(Cliente).where(Cliente.telefono == TEL))).scalar_one()
    check("🔴 y el reintento de Meta SÍ pone la pausa (antes: 'eco_duplicado' y el bot hablando "
          "encima de la dueña delante del cliente)",
          r == "eco" and cli.bot_pausado is True and cli.pausado_por == "dueña",
          f"{r!r} {cli.bot_pausado} {cli.pausado_por}")
    check("   ...y AHORA sí queda marcado", bool(await rc.get_cache(f"cache:eco:{suyo}")))
    check("   ...así que una tercera entrega ya no repite nada",
          await W._procesar_eco(eco) == "eco_duplicado")


# ─── 11) EL PAGO CONFIRMADO QUE NADIE LE CONFIRMA AL CLIENTE ────────

async def caso_pago_que_nadie_confirma() -> None:
    print("\n11) 💰 BUG 1 de Maired: el aviso de pago que se descartaba EN SILENCIO")
    factory = get_session_factory()
    real_activo, real_redactar, real_envio = T._bot_activo, T.redactar_mensaje, T.enviar_texto

    async def _redacta(*a, **k):
        return "listo Rosa, ya me llegó tu pago 💚"

    # (a) LA DUEÑA TIENE EL CHAT TOMADO. Es EL CASO NORMAL: si está confirmando el pago es
    #     porque tomó ese chat para cobrar. Antes el bot se callaba (bien) y nadie avisaba (mal).
    await _limpiar()
    async with factory() as s:
        s.add(Cliente(telefono=TEL, nombre="Rosa", ultimo_entrante_at=now_utc(),
                      bot_pausado=True, pausado_por="dueña"))
        await s.commit()
    T._bot_activo, T.redactar_mensaje = _si, _redacta
    enviados.clear()
    try:
        await T._notificar_cliente_pago(TEL, "su pago quedó confirmado")
    finally:
        T._bot_activo, T.redactar_mensaje = real_activo, real_redactar

    check("✅ con el chat TOMADO el bot NO le habla encima a la dueña (eso ya estaba bien)",
          not [t for t, _ in enviados if t == TEL], str(enviados))
    check("🔴 pero YA NO se va mudo: queda la fila 'pago_en_chat_tomado' en la bandeja",
          "pago_en_chat_tomado" in await _motivos(TEL), str(await _motivos(TEL)))
    check("🔴 ...y a ella le llega el WhatsApp para que se lo diga ella al cliente",
          bool(enviados), str(enviados))

    # ANTI-INUNDACIÓN: el panel puede disparar la tarea dos veces por el MISMO cliente
    # (verificar monto ⇒ parcial ⇒ confirmar). Eso es DOS filas, pero UN solo WhatsApp.
    enviados.clear()
    T._bot_activo, T.redactar_mensaje = _si, _redacta
    try:
        await T._notificar_cliente_pago(TEL, "su pago quedó confirmado")
    finally:
        T._bot_activo, T.redactar_mensaje = real_activo, real_redactar
    check("🔴 el segundo toque del MISMO cliente no repite el WhatsApp (candado de 15 min)",
          not enviados, str(enviados))
    check("   ...pero la fila de la bandeja sí se abre igual (cada toque importa)",
          (await _motivos(TEL)).count("pago_en_chat_tomado") == 2, str(await _motivos(TEL)))

    # (b) META RECHAZA TODOS LOS GLOBOS: al cliente no le llegó NADA, y eso SÍ es una avería.
    await _limpiar()
    async with factory() as s:
        s.add(Cliente(telefono=TEL_DOC, nombre="Ana", ultimo_entrante_at=now_utc()))
        await s.commit()

    async def _rechaza(destino, texto):
        raise RuntimeError("Meta devolvió 400")

    T._bot_activo, T.redactar_mensaje, T.enviar_texto = _si, _redacta, _rechaza
    try:
        await T._notificar_cliente_pago(TEL_DOC, "su pago quedó confirmado")
    finally:
        T._bot_activo, T.redactar_mensaje, T.enviar_texto = (
            real_activo, real_redactar, real_envio
        )
    check("🔴 con Meta rechazando TODO, la dueña se entera ('pago_no_entregado')",
          "pago_no_entregado" in await _motivos(TEL_DOC), str(await _motivos(TEL_DOC)))
    check("   ...y la fila queda aunque el WhatsApp a ELLA tampoco haya podido salir",
          len(await _motivos(TEL_DOC)) == 1, str(await _motivos(TEL_DOC)))

    # (c) EL TURNO A MEDIAS: pasa el globo 1 y falla el 2. La MEDIA VERDAD es lo peligroso.
    await _limpiar()
    async with factory() as s:
        s.add(Cliente(telefono=TEL_MEDIA, nombre="Luz", ultimo_entrante_at=now_utc()))
        await s.commit()

    async def _redacta_dos_globos(*a, **k):
        return "listo Luz, ya me llegó 💚\n\nte coordino la entrega para mañana"

    intentos: list[str] = []

    async def _falla_el_segundo(destino, texto):
        intentos.append(texto)
        if len(intentos) > 1:
            raise RuntimeError("Meta rechazó el segundo globo")
        return {"messages": [{"id": "wamid.MEDIO1"}]}

    T._bot_activo, T.redactar_mensaje, T.enviar_texto = _si, _redacta_dos_globos, _falla_el_segundo
    try:
        await T._notificar_cliente_pago(TEL_MEDIA, "su pago quedó confirmado")
    finally:
        T._bot_activo, T.redactar_mensaje, T.enviar_texto = (
            real_activo, real_redactar, real_envio
        )
    check("🔴 medio aviso de pago ⇒ 'mensaje_a_medias' (el cliente vio MEDIA confirmación)",
          "mensaje_a_medias" in await _motivos(TEL_MEDIA), str(await _motivos(TEL_MEDIA)))

    # ANTI-REGRESIÓN: el camino BUENO no puede abrirle ni una fila. Un detector que grita
    # siempre se acaba ignorando, y esta bandeja es la que le avisa cuando hay dinero en juego.
    await _limpiar()
    async with factory() as s:
        s.add(Cliente(telefono=TEL, nombre="Rosa", ultimo_entrante_at=now_utc()))
        await s.commit()
    T._bot_activo, T.redactar_mensaje = _si, _redacta
    enviados.clear()
    try:
        await T._notificar_cliente_pago(TEL, "su pago quedó confirmado")
    finally:
        T._bot_activo, T.redactar_mensaje = real_activo, real_redactar
    check("✅ cuando el aviso SÍ sale, no se le abre NINGUNA fila (no avisamos de lo que funciona)",
          not await _motivos(TEL) and any(t == TEL for t, _ in enviados),
          f"{await _motivos(TEL)} {enviados}")


async def main() -> None:
    # Nada sale al mundo: los tres módulos que envían quedan con espías.
    T.enviar_texto = _espia
    tl.enviar_texto = _espia
    mc.enviar_texto = _espia
    # La LISTA BLANCA del taller es real (y puede tener números de verdad en
    # `numeros_permitidos_extra`, que dejaría fuera a los teléfonos de este banco). Se sustituye
    # por una que solo deja pasar a los de aquí: además de hacer la prueba determinista,
    # GARANTIZA que esta corrida no le hable ni le abra un aviso a un cliente REAL.
    T.settings.numeros_permitidos = ""
    T._numero_permitido = lambda tel: _fijo(tel in TODOS or (tel or "").startswith("__"))

    await caso_interruptor_del_aviso_de_pago()
    await caso_ventana_de_la_duena()
    await caso_telemetria_de_calidad()
    await caso_media_no_entregada()
    await caso_documento_no_es_pago()
    await caso_media_con_el_chat_tomado()
    await caso_webhook()
    await caso_freno_de_tasa()
    await caso_media_gigante()
    await caso_eco()
    await caso_pago_que_nadie_confirma()


async def _correr() -> None:
    # El `dueno_telefono` REAL del taller no se toca; cada caso pone el suyo y lo devuelve.
    try:
        await main()
    finally:
        print("\n12) LIMPIEZA")
        await _limpiar()
        check("los teléfonos de prueba se borraron (no ensucian el panel de la dueña)", True)

    print()
    if fallos:
        print(f"   🔴 {len(fallos)} FALLO(S) — NO DESPLEGAR:")
        for f in fallos:
            print(f"      - {f}")
        sys.exit(1)
    print("   ✅ RIESGO META BAJO CONTROL: el interruptor cubre el aviso de pago, los avisos a la")
    print("      dueña miran antes de saltar, los rechazos de calidad se ven, un PDF no es un pago,")
    print("      la media respeta el relevo y nada sale sin freno hacia Meta.")


asyncio.run(_correr())
