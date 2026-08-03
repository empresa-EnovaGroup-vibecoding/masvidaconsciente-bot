"""EL MENSAJE DEL CLIENTE NO SE EVAPORA — banco de la auditoría del 2026-08-02 (tanda 3).

Se corre DENTRO del contenedor del bot (usa la BD y el Redis REALES del taller).

LOS CUATRO CAMINOS POR LOS QUE UN MENSAJE DESAPARECÍA SIN DEJAR RASTRO:

  1. 🔒 EL LOCK TOMADO (SIL-1). t=0 llega "hola" → tarea a t+15; t=15 esa tarea toma el lock y se
     pone a pensar; t=20 llega "sí, dale, lo quiero" → tarea a t=35; a t=35 el lock SIGUE tomado
     ⇒ la tarea se iba MUDA, sin log y sin dejar otra programada, y el "sí, lo quiero" se pudría
     en el buffer hasta expirar (1 h). Ahora "ocupado" NO es "listo": se reencola.

  2. 🎙️ LA NOTA DE VOZ (SIL-1b). Ese mismo `return` en el carril de voz/eventos era PEOR: ahí el
     texto no está en Redis, es una variable local — la transcripción, ya descargada y ya PAGADA
     a Gemini. Y el reintento de Meta no la salva: el webhook ya quemó el message_id. Ahora se
     DERRAMA al buffer de texto, que sí tiene red.

  3. 💸 EL 402 DE OPENROUTER (SIL-10). El buffer se vaciaba ANTES de pensar (LRANGE+DELETE
     atómico) y el historial se guardaba DESPUÉS. Si `responder()` reventaba —402 por saldo, que
     pasó de verdad el 2026-07-15— el mensaje del cliente no quedaba en NINGÚN sitio: ni en
     Redis, ni en `mensajes`. Y el webhook ya había sumado 1 a `no_leidos`: en el panel se veía
     el globito de NO LEÍDO sobre una conversación sin nada nuevo dentro. Ahora se anota ANTES,
     y si el turno se cae igual, se rescata y se avisa.

  4. 🎈 EL TURNO A MEDIAS (SIL-8). El bot responde en varios globos. Si Meta rechazaba el 2 de 4,
     el 3 y el 4 se descartaban SIN dejar fila (nadie podía saber que faltaban), y el bot GUARDABA
     la respuesta ENTERA en su memoria: creía haber dado la cuenta y la cédula, y por eso no las
     repetía nunca. Media verdad en el momento del cobro.

  5. 📋 EL PANEL MUDO (SIL-15). Las dos escrituras del panel se tragaban el fallo con un log. Un
     pestañeo de la base borraba el intercambio COMPLETO del hilo y la dueña seguía atendiendo a
     ciegas, sin ninguna señal de que le faltaban mensajes.

WhatsApp NO se toca (`enviar_texto` sustituido por un doble) y el MODELO tampoco (`responder`
sustituido). Nada sale al mundo real; lo que se mira es Redis y la BASE DE DATOS.
"""
import asyncio
import sys

from celery.exceptions import MaxRetriesExceededError
from sqlalchemy import delete, select

import app.services.db as dbmod
from app.models import Cliente, Intervencion, Mensaje
from app.services import redis_client as rc
from app.services.db import get_session_factory
from app.workers import tasks

TEL = "__prueba_evapora__"
DUENA = "__duena_evapora__"  # solo para `_hueco_en_el_panel`, que lee la VARIABLE DE ENTORNO
fallos: list[str] = []
enviados: list[tuple[str, str]] = []  # (destino, texto) — lo que HABRÍA salido por WhatsApp


def a_la_duena() -> list[tuple[str, str]]:
    """Lo que salió hacia CUALQUIERA que no sea el cliente de prueba = el aviso a la dueña.

    No se compara contra una constante a propósito: `_avisar_a_la_duena` saca el destino de la
    tabla `configuracion` (que en el taller SÍ tiene un `dueno_telefono` cargado) y cae a la
    variable de entorno solo si no está. Comparar contra un número fijo dejaría el banco rojo en
    el servidor por el motivo equivocado.
    """
    return [(d, t) for d, t in enviados if d != TEL]


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"   {'[OK ]' if ok else '[MAL]'} {nombre}{('  → ' + detalle) if detalle and not ok else ''}")
    if not ok:
        fallos.append(nombre)


async def _falso_envio(telefono: str, texto: str) -> dict:
    """Doble de Meta: anota y devuelve un id como el real. Nada sale al mundo."""
    enviados.append((telefono, texto))
    return {"messages": [{"id": f"wamid.EVAP{len(enviados)}"}]}


async def _siempre_encendido() -> bool:
    """Aquí se prueba que el mensaje no se pierda, NO el interruptor de encendido.

    El interruptor puede estar legítimamente APAGADO en el servidor (pasó el 2026-07-13, para
    proteger a una clienta real): sin esto el banco entero saldría rojo por el motivo equivocado.
    El interruptor tiene su propia prueba en `probar_carril_dinero.py`.
    """
    return True


async def _limpiar() -> None:
    f = get_session_factory()
    async with f() as s:
        await s.execute(delete(Mensaje).where(Mensaje.cliente_telefono == TEL))
        await s.execute(delete(Intervencion).where(Intervencion.cliente_telefono == TEL))
        await s.execute(delete(Cliente).where(Cliente.telefono == TEL))
        await s.commit()
    await rc.borrar_memoria(TEL)
    await _soltar_candados()
    enviados.clear()


async def _soltar_candados() -> None:
    """Los candados antiinundación duran 15 min: sin esto, el segundo caso del banco no vería
    ningún aviso y parecería una regresión (mismo patrón que `probar_retomar._soltar_candado`)."""
    await rc._client().delete(
        f"aviso:sin_respuesta:{TEL}", "aviso:panel_incompleto", f"lock:{TEL}"
    )


async def _filas_user() -> list[str]:
    f = get_session_factory()
    async with f() as s:
        return list((await s.execute(
            select(Mensaje.contenido)
            .where(Mensaje.cliente_telefono == TEL, Mensaje.rol == "user")
        )).scalars().all())


async def _motivos() -> list[str]:
    f = get_session_factory()
    async with f() as s:
        return list((await s.execute(
            select(Intervencion.motivo).where(Intervencion.cliente_telefono == TEL)
        )).scalars().all())


# ─── 1) EL LOCK TOMADO: "ocupado" NO es "listo" ──────────────────────

async def caso_lock_tomado() -> None:
    print("\n1) 🔒 EL LOCK TOMADO: el 'sí, dale, lo quiero' YA NO se pudre en el buffer (SIL-1)")
    await _limpiar()
    await rc.agregar_a_buffer(TEL, "si, dale, lo quiero")
    tomado = await rc.adquirir_lock(TEL)  # como si otro turno estuviera pensando
    check("(preparación) el lock queda tomado por 'otro turno'", tomado)

    veredicto = await tasks._procesar(TEL, None)
    check("con el lock tomado, `_procesar` devuelve 'ocupado' (antes: return mudo)",
          veredicto == "ocupado", f"devolvió {veredicto!r}")
    quedan = await rc._client().llen(f"buffer:{TEL}")
    check("🔴 y NO se vació el buffer: el mensaje del cliente sigue ahí",
          quedan == 1, f"quedan {quedan} mensajes")
    await rc.liberar_lock(TEL)


def caso_reencolado() -> None:
    """El wrapper de Celery, en seco: `_run` y `retry` sustituidos por dobles.

    Va FUERA del `asyncio.run` a propósito: `tasks._run` hace `run_until_complete`, y llamarlo
    desde dentro de un loop vivo revienta con 'this event loop is already running'.
    """
    print("\n1.b) 🔁 Y EL WRAPPER REENCOLA (8 × 20 s = 160 s > los 120 s que dura el lock)")
    real_run, real_retry = tasks._run, tasks.procesar_buffer.retry
    veredicto = {"valor": "ocupado"}
    corridas: list[str] = []
    reintentos: list[dict] = []

    def _run_falso(coro):
        corridas.append(coro.__qualname__)
        coro.close()  # aquí se prueba el WRAPPER, no el turno
        return veredicto["valor"]

    tasks._run = _run_falso
    tasks.procesar_buffer.retry = lambda **k: reintentos.append(k)
    try:
        tasks.procesar_buffer(TEL, None)
        check("veredicto 'ocupado' ⇒ la tarea se REENCOLA (antes se iba muda)",
              len(reintentos) == 1, str(reintentos))
        check("y espera 20 s, no 10 (la mitad de mensajes en vuelo: el carril del DINERO no "
              "puede hacer cola detrás de los reintentos)",
              bool(reintentos) and reintentos[0].get("countdown") == 20, str(reintentos))

        veredicto["valor"] = "ok"
        reintentos.clear()
        tasks.procesar_buffer(TEL, None)
        check("un turno normal NO reencola nada", not reintentos, str(reintentos))

        # Y cuando se agotan los 8 intentos, alguien tiene que ENTERARSE.
        def _retry_agotado(**k):
            raise MaxRetriesExceededError()

        veredicto["valor"] = "ocupado"
        corridas.clear()
        tasks.procesar_buffer.retry = _retry_agotado
        tasks.procesar_buffer(TEL, None)
        check("🔴 agotados los reintentos, se AVISA a la dueña (no se abandona en silencio)",
              any("_avisar_turno_perdido" in c for c in corridas), str(corridas))
    finally:
        tasks._run, tasks.procesar_buffer.retry = real_run, real_retry


# ─── 2) LA NOTA DE VOZ SE DERRAMA AL BUFFER ──────────────────────────

async def caso_voz_derramada() -> None:
    print("\n2) 🎙️ LA NOTA DE VOZ NO SE EVAPORA: se derrama al carril que sí tiene red (SIL-1b)")
    await _limpiar()
    programadas: list[tuple] = []
    real_apply = tasks.procesar_buffer.apply_async
    tasks.procesar_buffer.apply_async = lambda args=None, **k: programadas.append((args, k))
    try:
        await rc.adquirir_lock(TEL)  # el turno está ocupado
        veredicto = await tasks._responder_y_enviar(TEL, "quiero dos kombuchas", None)
        check("con el turno ocupado devuelve 'ocupado' (antes: return, y la voz al limbo)",
              veredicto == "ocupado", f"devolvió {veredicto!r}")
        enbuffer = await rc._client().lrange(f"buffer:{TEL}", 0, -1)
        check("🔴 la transcripción PAGADA queda en el buffer de texto",
              enbuffer == ["quiero dos kombuchas"], str(enbuffer))
        check("y se programa el turno de texto que la va a contestar (countdown=15)",
              len(programadas) == 1 and programadas[0][1].get("countdown") == 15,
              str(programadas))

        # El caso completo, con el audio de punta a punta.
        await rc._client().delete(f"buffer:{TEL}")
        programadas.clear()
        real_descarga, real_transcribe = tasks.descargar_media, tasks.transcribir_audio

        async def _falsa_descarga(media_id):
            return b"...audio...", "audio/ogg"

        async def _falsa_transcripcion(contenido, mime):
            return "hola, quiero encargar dos tortas"

        tasks.descargar_media, tasks.transcribir_audio = _falsa_descarga, _falsa_transcripcion
        try:
            await tasks._procesar_audio(TEL, "media_evap_1", None, "audio/ogg")
        finally:
            tasks.descargar_media, tasks.transcribir_audio = real_descarga, real_transcribe
        enbuffer = await rc._client().lrange(f"buffer:{TEL}", 0, -1)
        check("🔴 de punta a punta: la nota de voz transcrita aparece en el buffer",
              enbuffer == ["hola, quiero encargar dos tortas"], str(enbuffer))
    finally:
        tasks.procesar_buffer.apply_async = real_apply
        await rc.liberar_lock(TEL)


# ─── 3) EL 402: el mensaje sobrevive a que la IA se caiga ─────────────

async def caso_402() -> None:
    print("\n3) 💸 EL 402 DE OPENROUTER: el mensaje del cliente SOBREVIVE (SIL-10)")
    await _limpiar()
    real_responder = tasks.responder

    async def _ia_caida(*a, **k):
        raise RuntimeError("402 Payment Required: sin saldo en OpenRouter")

    tasks.responder = _ia_caida
    try:
        await rc.agregar_a_buffer(TEL, "quiero 2 cajas de empanadas")
        veredicto = await tasks._procesar(TEL, "Rosa")
        check("el turno termina con veredicto 'error' (no con un traceback suelto)",
              veredicto == "error", f"devolvió {veredicto!r}")

        hist = await rc.obtener_historial(TEL)
        check("🔴 (a) lo que escribió el cliente está en el historial del bot",
              any(h.get("role") == "user" and "empanadas" in h.get("content", "") for h in hist),
              str(hist))
        filas = await _filas_user()
        check("🔴 (b) y en la tabla `mensajes`, para que la dueña lo VEA en el panel",
              any("empanadas" in f for f in filas), str(filas))
        check("🔴 (c) y hay aviso en la bandeja (motivo 'sin_respuesta')",
              "sin_respuesta" in await _motivos(), str(await _motivos()))
        check("🔴 (d) y le llegó el WhatsApp a la dueña",
              bool(a_la_duena()), str(enviados))
        check("una sola fila `user`: la bandera `guardado` no duplica",
              len(filas) == 1, str(filas))

        # El candado: una caída de una hora NO puede ser 200 WhatsApps.
        enviados.clear()
        await rc.agregar_a_buffer(TEL, "sigo esperando")
        await tasks._procesar(TEL, "Rosa")
        check("🔒 el segundo turno perdido NO manda otro WhatsApp (candado de 15 min)",
              not bool(a_la_duena()), str(enviados))
        filas = await _filas_user()
        check("pero el segundo mensaje SÍ queda escrito igual (el rescate no lleva candado)",
              any("sigo esperando" in f for f in filas), str(filas))
    finally:
        tasks.responder = real_responder

    # ANTI-REGRESIÓN: el camino feliz no puede duplicar la fila del cliente.
    await _limpiar()

    async def _ia_buena(*a, **k):
        return "listo, te lo anoto\n\nte confirmo el total en un momentito"

    tasks.responder = _ia_buena
    try:
        await rc.agregar_a_buffer(TEL, "quiero una torta")
        veredicto = await tasks._procesar(TEL, "Rosa")
    finally:
        tasks.responder = real_responder
    filas = await _filas_user()
    check("✅ camino feliz: veredicto 'ok' y EXACTAMENTE una fila `user`",
          veredicto == "ok" and filas == ["quiero una torta"], f"{veredicto!r} {filas}")
    check("✅ y sin ningún aviso a la dueña (no avisamos de lo que funcionó)",
          not await _motivos() and not a_la_duena(),
          f"{await _motivos()} {enviados}")


# ─── 4) EL TURNO A MEDIAS ────────────────────────────────────────────

async def caso_turno_a_medias() -> None:
    print("\n4) 🎈 EL TURNO A MEDIAS: ni un globo se evapora, y el bot no recuerda de más (SIL-8)")
    await _limpiar()
    real_envio = tasks.enviar_texto
    intentos: list[str] = []

    async def _falla_en_el_segundo(telefono, texto):
        intentos.append(texto)
        if len(intentos) >= 2:
            raise RuntimeError("Meta dijo que no")
        return {"messages": [{"id": "wamid.EVAP_A_MEDIAS"}]}

    tasks.enviar_texto = _falla_en_el_segundo
    try:
        partes = await tasks._enviar_en_partes(
            TEL, "globo uno\n\nla cuenta y la cedula\n\nglobo tres\n\nglobo cuatro"
        )
    finally:
        tasks.enviar_texto = real_envio
    check("🔴 los 4 globos dejan fila (antes: `break` seco, el 3 y el 4 no existían)",
          len(partes) == 4, str([p["estado"] for p in partes]))
    check("y los 3 que no salieron van en 'fallido' (lo único que el panel pinta en ROJO)",
          [p["estado"] for p in partes] == ["enviado", "fallido", "fallido", "fallido"],
          str([p["estado"] for p in partes]))
    check("con el motivo escrito: 'no se intentó'",
          "no se intentó" in (partes[2].get("error") or ""), str(partes[2]))
    check("solo se intentó enviar 2 veces (no se insiste con Meta)", len(intentos) == 2,
          str(intentos))

    memoria = tasks._lo_que_llego(partes, "TEXTO ENTERO")
    check("🔴 el bot recuerda SOLO lo que llegó ('globo uno')", memoria == "globo uno", repr(memoria))
    check("🔴 y NO recuerda haber dado la cuenta y la cédula", "cedula" not in memoria, repr(memoria))

    respuesta = "globo uno\n\nglobo dos"
    todos = [{"texto": "globo uno", "estado": "enviado"}, {"texto": "globo dos", "estado": "enviado"}]
    check("✅ en el camino feliz devuelve la respuesta TAL CUAL (radio de explosión cero)",
          tasks._lo_que_llego(todos, respuesta) is respuesta,
          repr(tasks._lo_que_llego(todos, respuesta)))

    await tasks._avisar_turno_a_medias(TEL, "Rosa", partes)
    check("🔴 y la dueña se entera: aviso 'mensaje_a_medias' en la bandeja",
          "mensaje_a_medias" in await _motivos(), str(await _motivos()))
    check("con el WhatsApp correspondiente", bool(a_la_duena()), str(enviados))

    await _limpiar()
    ninguno = [{"texto": "globo uno", "estado": "fallido"}, {"texto": "dos", "estado": "fallido"}]
    await tasks._avisar_turno_a_medias(TEL, "Rosa", ninguno)
    check("caída TOTAL de Meta ⇒ NO avisa (lo peligroso es la MEDIA verdad, no el silencio total)",
          not await _motivos(), str(await _motivos()))


# ─── 5) EL PANEL NO PIERDE MENSAJES EN SILENCIO ──────────────────────

async def caso_panel_mudo() -> None:
    print("\n5) 📋 EL PANEL NO PIERDE MENSAJES EN SILENCIO (SIL-15)")
    await _limpiar()
    real_factory = dbmod.get_session_factory
    caprichoso = {"fallos": 1}

    def _factory_capricho():
        if caprichoso["fallos"] > 0:
            caprichoso["fallos"] -= 1
            raise RuntimeError("la conexión del pool estaba muerta (el clásico)")
        return real_factory()

    partes = [{"texto": "hola, aquí está tu total", "wa_message_id": "wamid.X", "estado": "enviado"}]
    dbmod.get_session_factory = _factory_capricho
    try:
        ok = await tasks._guardar_en_panel(TEL, "Rosa", "cuanto sale?", partes)
        check("(a) falla UNA vez y el reintento la salva: devuelve True",
              ok is True, f"devolvió {ok!r}")
        filas = await _filas_user()
        check("y el intercambio quedó escrito de verdad", filas == ["cuanto sale?"], str(filas))
        check("sin molestar a la dueña por un pestañeo de la base",
              not bool(a_la_duena()), str(enviados))

        # (b) la base NO vuelve: el hueco tiene que ser RUIDOSO.
        await _limpiar()
        caprichoso["fallos"] = 99
        ok = await tasks._guardar_en_panel(TEL, "Rosa", "sigo sin poder pagar", partes)
        check("(b) si no vuelve, devuelve False (R3: la bandera `guardado` no puede MENTIR)",
              ok is False, f"devolvió {ok!r}")
        check("🔴 y le avisa a la dueña de que el panel está INCOMPLETO",
              any("panel" in t.lower() for _, t in a_la_duena()), str(enviados))

        # (c) el candado: una base caída no son 200 WhatsApps.
        enviados.clear()
        ok = await tasks._guardar_entrante(TEL, "Rosa", "otro mensaje mas")
        check("(c) el segundo hueco NO manda otro WhatsApp (candado de 15 min)",
              not a_la_duena(), f"{ok!r} {enviados}")
        check("y `_guardar_entrante` también dice la verdad (False)", ok is False, f"devolvió {ok!r}")
    finally:
        dbmod.get_session_factory = real_factory


async def main() -> None:
    tasks.enviar_texto = _falso_envio
    tasks._bot_activo = _siempre_encendido
    tasks.settings.numeros_permitidos = ""
    dueno_antes = tasks.settings.dueno_telefono
    tasks.settings.dueno_telefono = DUENA
    try:
        await caso_lock_tomado()
        await caso_voz_derramada()
        await caso_402()
        await caso_turno_a_medias()
        await caso_panel_mudo()
        await _limpiar()
    finally:
        tasks.settings.dueno_telefono = dueno_antes


asyncio.run(main())
caso_reencolado()  # fuera del loop: el wrapper de Celery llama a `_run`, que abre el suyo

print()
if fallos:
    print(f"   🔴 {len(fallos)} FALLO(S): " + " · ".join(fallos[:6]))
    sys.exit(1)
print("   ✅ EL MENSAJE DEL CLIENTE NO SE EVAPORA: ni con el lock tomado, ni con la nota de voz,")
print("      ni con la IA caída, ni con medio turno rechazado, ni con la base pestañeando.")
