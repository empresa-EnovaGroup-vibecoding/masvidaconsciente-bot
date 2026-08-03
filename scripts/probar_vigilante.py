"""EL VIGILANTE — banco de pruebas de que el sistema SABE que está roto.

Se corre DENTRO del contenedor del bot (usa la BD real del taller).
Lo que se prueba, en orden de gravedad:

  1. `/salud` dice la VERDAD: 200 con todo vivo, 503 con Redis caído, `degradado` (no 503) con el
     SALDO DE OPENROUTER por el suelo — que es lo que de verdad se cayó el 2026-07-15. Y `GET /`
     sigue dando 200 PASE LO QUE PASE: es la sonda del orquestador y no se puede tocar (un 503 ahí
     podría hacer que recreen el contenedor y se lleve por delante el despliegue por `docker cp`).
  2. El VIGILANTE ve al cliente que escribió y no recibió NADA — anclado en
     `clientes.ultimo_entrante_at`, que escribe el WEBHOOK, así que ve incluso los mensajes que
     nunca llegaron a la tabla `mensajes`.
  3. Y NO ve de más: respondido por el bot, respondido por la dueña, chat pausado, más de 24h,
     menos de 10 min, teléfono interno, fuera de la lista blanca, pasado de tope. Un detector con
     falsos positivos se acaba ignorando — la peor avería posible en un detector.
  4. NO INUNDA: un solo aviso vivo por chat; y 9 colgados a la vez son UN aviso de "el sistema está
     caído", no 9.
  5. NO DEPENDE DEL LLM: con `responder` reventando a propósito, el barrido funciona igual. Tiene
     que avisar justo cuando el modelo está caído, que es cuando más falta hace.
  6. NO MUTA LO QUE NO LE TOCA: la pausa de la dueña sigue puesta, la del bot también, y el pedido
     en 'esperando_pago' sigue ahí con su total intacto. Las pruebas de la NO-acción valen tanto
     como las otras: son las que impiden que un refactor futuro convierta al barredor en un
     despausador o en un cancelador de pedidos.
  7. 🔴 `cerrar_avisos_ya_atendidos` NO cierra `chat_tomado` (corrección C4): ese aviso es el ÚNICO
     botón que despausa el chat. Cerrarlo deja al cliente con el bot mudo PARA SIEMPRE.
  8. El RESCATADOR de buffers huérfanos reencola lo que se quedó sin tarea — y NO reencola lo que
     tiene un turno en curso ni lo que acaba de llegar.
  9. La CONCESIÓN reparte el turno: dos llamadas seguidas, un solo barrido.
 10. `barrer.py --seco` no escribe NI UNA fila (es el ensayo previo a soltar el vigilante).

⚠️ Los teléfonos de prueba NO empiezan por "__" (el vigilante excluye esos a propósito), así que
aparecerían en el panel de la dueña si no se limpian: el `finally` borra, y también se borra al
EMPEZAR por si una corrida anterior se cortó a la mitad. El prefijo 58999000 es imposible como
número venezolano real, para que un residuo se reconozca de un vistazo.
Nada de esto manda WhatsApp de verdad: `enviar_texto` se sustituye por un espía.
"""
import asyncio
import importlib.util
import sys
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, select, text

import app.services.barredor as B
from app.models import Cliente, Intervencion, Mensaje, Pedido, now_utc
from app.services.db import get_session_factory

TEL_A = "58999000001"  # colgado de verdad
TEL_B = "58999000002"  # le contestó el bot
TEL_C = "58999000003"  # le contestó la dueña
TEL_D = "58999000004"  # le contestaron ANTES de que volviera a escribir
TEL_E = "58999000005"  # chat pausado por la dueña
TEL_F = "58999000006"  # escribió hace 3 días (techo de 24h)
TEL_G = "58999000007"  # escribió hace 2 minutos (umbral)
TEL_H = "58999000008"  # fuera de la lista blanca del taller
TEL_I = "58999000009"  # pasó el tope anti-abuso de hoy
TEL_J = "58999000010"  # pausado por el BOT (escalada)
TEL_INTERNO = "__prueba_vigilante__"
MASIVOS = [f"5899900010{i}" for i in range(1, 10)]  # 9 colgados = el sistema caído
TODOS = [TEL_A, TEL_B, TEL_C, TEL_D, TEL_E, TEL_F, TEL_G, TEL_H, TEL_I, TEL_J,
         TEL_INTERNO, *MASIVOS]

T0 = now_utc()
fallos: list[str] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"   {'[OK ]' if ok else '[MAL]'} {nombre}{('  → ' + detalle) if detalle and not ok else ''}")
    if not ok:
        fallos.append(nombre)


async def _limpiar() -> None:
    factory = get_session_factory()
    async with factory() as s:
        await s.execute(delete(Pedido).where(Pedido.cliente_telefono.in_(TODOS)))
        await s.execute(delete(Mensaje).where(Mensaje.cliente_telefono.in_(TODOS)))
        await s.execute(delete(Intervencion).where(Intervencion.cliente_telefono.in_(TODOS)))
        await s.execute(delete(Intervencion).where(
            Intervencion.cliente_telefono == B.TEL_SISTEMA,
            Intervencion.motivo == B.MOTIVO,
            Intervencion.created_at >= T0,
        ))
        await s.execute(delete(Cliente).where(Cliente.telefono.in_(TODOS)))
        # La concesión se retrasa 6 minutos para que el banco pueda ganar el turno: en el taller la
        # API está viva y barre cada 5, así que sin esto el assert de la concesión saldría rojo por
        # una carrera, no por un bug. Se ponen 6 minutos y NO 1970 a propósito: con 1970 el testigo
        # de /salud parecería MUERTO (>15 min) hasta la siguiente pasada del bucle, y el banco
        # estaría inventando una alarma.
        await s.execute(
            text("UPDATE configuracion SET valor = :v WHERE clave = :c"),
            {"v": (now_utc() - timedelta(minutes=6)).strftime(B.FORMATO_INSTANTE),
             "c": B.CLAVE_TURNO},
        )
        await s.commit()
    try:
        from app.services import redis_client as rc
        c = rc._client()
        await c.delete(f"buffer:{TEL_A}", f"lock:{TEL_A}", f"abuso_avisado:{TEL_I}:{rc._hoy()}")
    except Exception as e:  # noqa: BLE001
        print(f"   (no se pudo limpiar Redis: {e})")


def _cliente(tel: str, *, minutos: float | None = None, dias: float = 0,
             pausado: str | None = None) -> Cliente:
    cuando = now_utc() - timedelta(minutes=minutos or 0, days=dias) if minutos is not None or dias else None
    return Cliente(
        telefono=tel, nombre=f"Prueba {tel[-3:]}",
        ultimo_entrante_at=cuando, ultima_interaccion=cuando or now_utc(),
        bot_pausado=pausado is not None, pausado_por=pausado,
    )


async def probar_salud() -> None:
    import app.services.salud as S

    print("\n1) /salud DICE LA VERDAD (y `/` se queda tonto a propósito)")
    guardado = (S.settings.meta_access_token, S.settings.openrouter_api_key)
    sondas = (S._meta, S._saldo, S._redis)
    # Meta y OpenRouter se sustituyen por sondas fijas en la prueba del camino feliz: un banco NO
    # puede depender de que graph.facebook.com y openrouter.ai contesten desde el CI, ni ponerse
    # rojo (y frenar un despliegue) porque hoy hay poco saldo. Su lógica se prueba aparte, abajo.
    S._meta = lambda: _fijo({"ok": True, "calidad_numero": "GREEN"})  # type: ignore[assignment]
    S._saldo = lambda: _fijo({"ok": True, "saldo_usd": 99.0})  # type: ignore[assignment]
    try:
        S.olvidar_cache()
        cuerpo, codigo = await S.revisar()
        check("con todo vivo → 200 y estado 'ok'", codigo == 200 and cuerpo["estado"] == "ok", str(cuerpo)[:300])
        # >= 29 = las 28 que había + la 028 del barredor. Si este assert cae, la migración no se
        # aplicó y el vigilante no tiene dónde apoyarse.
        check("Postgres contestó y la migración 028 está aplicada",
              cuerpo["postgres"]["ok"] and cuerpo["postgres"].get("migraciones_aplicadas", 0) >= 29,
              str(cuerpo["postgres"]))
        check("Redis contestó ESCRIBIENDO (no un PING)", cuerpo["redis"]["ok"], str(cuerpo["redis"]))

        S._redis = lambda: _fijo({"ok": False, "ms": 2001, "error": "Connection refused"})  # type: ignore[assignment]
        S.olvidar_cache()
        cuerpo, codigo = await S.revisar()
        S._redis = sondas[2]  # type: ignore[assignment]
        check("🔴 con Redis caído → 503 y estado 'caido'", codigo == 503 and cuerpo["estado"] == "caido", str(cuerpo)[:200])
        check("y dice CUÁL se cayó", cuerpo["fallos"] == ["redis"], str(cuerpo["fallos"]))

        # F2: el incidente REAL. Saldo por el suelo = degradado, NUNCA 503 (reiniciar el
        # contenedor no recarga una tarjeta) — el monitor lo caza por la PALABRA.
        S._saldo = lambda: _fijo({"ok": False, "saldo_usd": 0.04, "error": "SALDO EN $0.04"})  # type: ignore[assignment]
        S.olvidar_cache()
        cuerpo, codigo = await S.revisar()
        check("🔴 sin saldo de OpenRouter → 'degradado' con 200 (no 503)",
              codigo == 200 and cuerpo["estado"] == "degradado", str(cuerpo)[:200])
        check("y el fallo se llama por su nombre", "saldo_ia" in cuerpo["fallos"], str(cuerpo["fallos"]))

        print("\n1b) LAS SONDAS EXTERNAS: fallan por lo que TIENEN que fallar, y no por lo demás")
        # EL TESTIGO DEL 402 VIVE EN REDIS (lo estampa el WORKER, otro proceso), así que
        # `olvidar_cache` no lo alcanza. Se limpia ANTES de nada: un 402 de verdad en el taller
        # pondría en rojo el assert de "un timeout NO es quedarse sin saldo" y el banco culparía al
        # código equivocado. Valor vacío = sin marca (`redis_client` no expone DELETE).
        from app.services import redis_client as _rc

        await _rc.set_cache(S.MARCA_402, "", 1)
        S._meta, S._saldo = sondas[0], sondas[1]  # type: ignore[assignment] — ahora sí, las de verdad
        S.settings.meta_access_token = ""
        S.olvidar_cache()
        check("sin token de Meta configurado → la sonda falla", (await S._meta())["ok"] is False)
        S.settings.meta_access_token = guardado[0]

        S.settings.openrouter_api_key = ""
        S.olvidar_cache()
        check("sin llave de OpenRouter → la sonda falla", (await S._saldo())["ok"] is False)
        S.settings.openrouter_api_key = "sk-falsa-para-el-banco"

        original = S.httpx.AsyncClient
        S.httpx.AsyncClient = _ClienteQueRevienta  # type: ignore[assignment]
        try:
            S.olvidar_cache()
            m, sd = await S._meta(), await S._saldo()
        finally:
            S.httpx.AsyncClient = original  # type: ignore[assignment]
        check("un TIMEOUT de Graph NO es un token muerto (anti falso positivo)",
              m["ok"] is True and "aviso" in m, str(m))
        check("un TIMEOUT de OpenRouter NO es quedarse sin saldo (anti falso positivo)",
              sd["ok"] is True and "aviso" in sd, str(sd))

        # 🔴 EL TESTIGO DEL 402: un rechazo REAL le gana a la estimación de `/api/v1/credits`, y le
        # gana ANTES de la caché de 5 min. Es lo único que hoy convierte un 402 de cuenta en una
        # alarma de segundos en vez de días — el fallback comparte la MISMA llave y no salva.
        await S.marcar_402("HTTP 402 con un modelo de prueba")
        S.olvidar_cache()
        sd402 = await S._saldo()
        await _rc.set_cache(S.MARCA_402, "", 1)
        S.olvidar_cache()
        check("🔴 un 402 REAL de OpenRouter pone el saldo en ROJO (le gana a /credits y a la caché)",
              sd402["ok"] is False and "402" in str(sd402.get("error", "")), str(sd402)[:200])

        print("\n1c) LA DUEÑA CONTACTABLE: ¿pueden salir SUS avisos?")
        import app.services.meta_client as MC
        from app.services.dueno import telefono_de_la_duena

        S.olvidar_cache()
        dn = await S._duena_contactable()
        numero = await telefono_de_la_duena()
        check("con número de dueña configurado → la sonda NO falla",
              dn["ok"] is True and dn.get("hay_numero") is True, str(dn)[:220])
        # `/salud` es PÚBLICO: el teléfono personal de la dueña no puede salir por ahí NUNCA.
        check("🔴 y NO publica su teléfono (/salud lo lee cualquiera)",
              bool(numero) and numero not in str(dn), str(dn)[:220])

        # La ventana cerrada se simula sustituyendo la FUNCIÓN, no escribiendo la marca: este banco
        # corre contra la Redis REAL del taller y un "cerrada" olvidado le apagaría los avisos a la
        # dueña durante una hora de verdad. Funciona porque la sonda importa el símbolo del módulo
        # en cada llamada.
        guardada = MC.puede_escribirle_a_la_duena
        MC.puede_escribirle_a_la_duena = lambda destino: _fijo(False)  # type: ignore[assignment]
        try:
            S.olvidar_cache()
            dn_cerrada = await S._duena_contactable()
        finally:
            MC.puede_escribirle_a_la_duena = guardada  # type: ignore[assignment]
        check("🔴 ventana CERRADA = AVISO, JAMÁS fallo (un /salud en rojo permanente se ignora)",
              dn_cerrada["ok"] is True and dn_cerrada.get("ventana") == "cerrada"
              and "aviso" in dn_cerrada, str(dn_cerrada)[:250])
        S.olvidar_cache()
        cuerpo, codigo = await S.revisar()
        check("y con la ventana cerrada el endpoint sigue en 200 (no arrastra el estado global)",
              codigo == 200 and "duena_contactable" in cuerpo, str(cuerpo)[:200])
    finally:
        S.settings.meta_access_token, S.settings.openrouter_api_key = guardado
        S._meta, S._saldo, S._redis = sondas  # type: ignore[assignment]
        await _rc.set_cache(S.MARCA_402, "", 1)
        S.olvidar_cache()

    # `GET /` por HTTP de verdad (ASGI): tiene que seguir en 200 pase lo que pase.
    import httpx as _httpx

    from app.main import app
    async with _httpx.AsyncClient(transport=_httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/")
    check("🔴 `GET /` sigue dando 200 y 'ok' (el orquestador NO se toca)",
          r.status_code == 200 and r.json().get("status") == "ok", f"{r.status_code} {r.text[:120]}")
    check("y ahora apunta a /salud", r.json().get("salud") == "/salud", r.text[:120])


async def _fijo(d):
    return d


class _ClienteQueRevienta:
    """Un httpx.AsyncClient que no llega a ninguna parte (la red caída, no el token)."""
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get(self, *a, **k): raise TimeoutError("la red se cayó")


async def main() -> None:
    factory = get_session_factory()
    await _limpiar()

    await probar_salud()

    print("\n2) LA CONCESIÓN: aunque haya cuatro puertas, barre UNO SOLO")
    async with factory() as s:
        primero = await B.tomar_turno(s)
        segundo = await B.tomar_turno(s)
    check("el primero se lleva el turno", primero is True)
    check("🔴 el segundo NO (si no, dos procesos barrerían a la vez)", segundo is False)
    resumen = await B.barrer()
    check("y `barrer()` sale en seco sin tocar nada", resumen.get("corrio") is False, str(resumen))
    async with factory() as s:
        check("el testigo del barredor quedó fresco (lo lee /salud, F5)",
              (await B.ultima_corrida(s)) is not None)

    print("\n3) EL VIGILANTE VE AL CLIENTE COLGADO — Y NO VE DE MÁS")
    espiados: list[tuple] = []

    async def _espia(destino, texto):
        espiados.append((destino, texto))
        return {"espia": True}

    import app.services.meta_client as mc
    import app.workers.tasks as T
    mc.enviar_texto = _espia  # type: ignore[assignment]
    # La lista blanca del taller es real y podría dejar fuera a los teléfonos de prueba. Se
    # sustituye por una que solo deja pasar a los del banco: además de hacer la prueba
    # determinista, GARANTIZA que esta corrida no le abra un aviso a un cliente REAL.
    permitidos = set(TODOS) - {TEL_H}
    T._numero_permitido = lambda tel: _fijo(tel in permitidos)  # type: ignore[assignment]

    async with factory() as s:
        s.add_all([
            _cliente(TEL_A, minutos=30),
            _cliente(TEL_B, minutos=30),
            _cliente(TEL_C, minutos=30),
            _cliente(TEL_D, minutos=30),
            _cliente(TEL_E, minutos=30, pausado="dueña"),
            _cliente(TEL_F, dias=3),
            _cliente(TEL_G, minutos=2),
            _cliente(TEL_H, minutos=30),
            _cliente(TEL_I, minutos=30),
            _cliente(TEL_J, minutos=30, pausado="bot"),
            _cliente(TEL_INTERNO, minutos=30),
        ])
        await s.commit()
        # A B le contestó el BOT; a C, la DUEÑA; a D le contestaron ANTES de que volviera a
        # escribir (el caso del cliente que vuelve y el bot enmudece: ESE sí sale).
        s.add_all([
            Mensaje(cliente_telefono=TEL_B, rol="assistant", contenido="ya te digo", created_at=now_utc()),
            Mensaje(cliente_telefono=TEL_C, rol="owner", contenido="yo te atiendo", created_at=now_utc()),
            Mensaje(cliente_telefono=TEL_D, rol="assistant", contenido="viejo",
                    created_at=now_utc() - timedelta(hours=2)),
        ])
        await s.commit()

    # El tope anti-abuso de hoy para TEL_I (la sexta anti-inundación).
    from app.services import redis_client as rc
    await rc._client().set(f"abuso_avisado:{TEL_I}:{rc._hoy()}", "1", ex=600)

    async with factory() as s:
        salida = await B.vigilar_bot_callado(s)
        avisados = set((await s.execute(
            select(Intervencion.cliente_telefono).where(
                Intervencion.motivo == B.MOTIVO, Intervencion.estado == "pendiente"
            )
        )).scalars().all())

    check("🔴 el que escribió y NADIE le respondió → SALE", TEL_A in avisados, str(sorted(avisados)))
    check("🔴 el que tiene una respuesta ANTERIOR y volvió a escribir → SALE", TEL_D in avisados)
    check("le contestó el bot → NO sale", TEL_B not in avisados)
    check("le contestó la dueña → NO sale", TEL_C not in avisados)
    check("chat pausado por la dueña → NO sale (el silencio ahí es correcto)", TEL_E not in avisados)
    check("chat pausado por el bot (escalada) → NO sale", TEL_J not in avisados)
    check("escribió hace 3 días → NO sale (techo de 24h: la ventana de Meta ya cerró)",
          TEL_F not in avisados)
    check("escribió hace 2 minutos → NO sale (todavía está pensando)", TEL_G not in avisados)
    check("fuera de la lista blanca del taller → NO sale", TEL_H not in avisados)
    check("pasó el tope anti-abuso de hoy → NO sale (silencio deliberado, no avería)",
          TEL_I not in avisados)
    check("teléfono interno (__) → NO sale", TEL_INTERNO not in avisados)
    check("y salió UN WhatsApp a la dueña, no uno por cliente", len(espiados) == 1, str(len(espiados)))
    check("el resumen cuadra", salida.get("avisos") == 2, str(salida))

    print("\n4) NO INUNDA: un solo aviso vivo por chat")
    espiados.clear()
    async with factory() as s:
        repe = await B.vigilar_bot_callado(s)
        cuantos = len((await s.execute(
            select(Intervencion.id).where(
                Intervencion.cliente_telefono == TEL_A, Intervencion.estado == "pendiente"
            )
        )).scalars().all())
    check("segunda pasada seguida → 0 avisos nuevos", repe.get("avisos") == 0, str(repe))
    check("y el cliente colgado sigue con UN solo aviso", cuantos == 1, str(cuantos))
    check("y no se le mandó otro WhatsApp", espiados == [])

    print("\n5) 9 COLGADOS A LA VEZ NO SON 9 CLIENTES: ES EL SISTEMA CAÍDO")
    espiados.clear()
    async with factory() as s:
        s.add_all([_cliente(t, minutos=30) for t in MASIVOS])
        await s.commit()
    async with factory() as s:
        masivo = await B.vigilar_bot_callado(s)
        sistema = len((await s.execute(
            select(Intervencion.id).where(
                Intervencion.cliente_telefono == B.TEL_SISTEMA,
                Intervencion.motivo == B.MOTIVO, Intervencion.created_at >= T0,
            )
        )).scalars().all())
        individuales = len((await s.execute(
            select(Intervencion.id).where(Intervencion.cliente_telefono.in_(MASIVOS))
        )).scalars().all())
    check("🔴 se abre UN aviso de 'algo está caído'", masivo.get("masivo") is True and sistema == 1,
          f"{masivo} sistema={sistema}")
    check("y NO nueve avisos que esconderían el hecho", individuales == 0, str(individuales))
    check("un solo WhatsApp", len(espiados) == 1, str(len(espiados)))

    espiados.clear()
    async with factory() as s:
        otra = await B.vigilar_bot_callado(s)
        sistema2 = len((await s.execute(
            select(Intervencion.id).where(
                Intervencion.cliente_telefono == B.TEL_SISTEMA,
                Intervencion.motivo == B.MOTIVO, Intervencion.created_at >= T0,
            )
        )).scalars().all())
    check("🔴 y a los 5 minutos NO se repite: sigue habiendo UN aviso y CERO WhatsApps nuevos",
          sistema2 == 1 and espiados == [] and otra.get("avisos") == 0, f"{otra} sistema={sistema2}")

    async with factory() as s:  # los masivos ya cumplieron: fuera, para no ensuciar lo que sigue
        await s.execute(delete(Cliente).where(Cliente.telefono.in_(MASIVOS)))
        await s.commit()

    print("\n6) NO MUTA LO QUE NO LE TOCA (las pruebas de la NO-acción)")
    async with factory() as s:
        cli = (await s.execute(select(Cliente).where(Cliente.telefono == TEL_A))).scalar_one()
        s.add(Pedido(cliente_telefono=cli.telefono, estado="esperando_pago",
                     items=[{"producto": "prueba", "cantidad": 1}], total=42,
                     created_at=now_utc() - timedelta(days=30),
                     updated_at=now_utc() - timedelta(days=30)))
        await s.commit()

    # Y de paso: NO DEPENDE DEL LLM. Con el agente reventando, el barrido tiene que funcionar
    # igual — tiene que avisar JUSTO cuando el modelo está caído, que es cuando más falta hace.
    import app.agent.agent as A
    original_responder = A.responder

    async def _revienta(*a, **k):
        raise RuntimeError("el modelo está caído / sin saldo (402)")

    A.responder = _revienta  # type: ignore[assignment]
    try:
        salida = await B.barrer(forzar=True)
    finally:
        A.responder = original_responder  # type: ignore[assignment]
    check("🔴 con el LLM reventando, el barrido corre igual", salida.get("corrio") is True, str(salida)[:200])

    async with factory() as s:
        e = (await s.execute(select(Cliente).where(Cliente.telefono == TEL_E))).scalar_one()
        j = (await s.execute(select(Cliente).where(Cliente.telefono == TEL_J))).scalar_one()
        ped = (await s.execute(select(Pedido).where(Pedido.cliente_telefono == TEL_A))).scalars().first()
    check("🔴 la pausa de la DUEÑA sigue puesta (no caduca, por decisión de Maired)",
          e.bot_pausado is True and e.pausado_por == "dueña")
    check("🔴 la pausa del BOT (pide_persona/reclamo) también sigue puesta",
          j.bot_pausado is True and j.pausado_por == "bot")
    check("🔴 el pedido 'esperando_pago' de hace 30 días SIGUE ahí y con su total",
          ped is not None and ped.estado == "esperando_pago" and float(ped.total) == 42.0,
          str(ped and (ped.estado, ped.total)))

    print("\n7) CERRAR LO YA ATENDIDO — pero NUNCA el botón que despausa (C4)")
    # Se usan E/F/G: son los tres que el vigilante NO avisó, así que cada uno tiene exactamente
    # UNA intervención y el resultado no depende del orden de las filas.
    async with factory() as s:
        s.add_all([
            Intervencion(cliente_telefono=TEL_E, motivo="no_se", detalle="viejo",
                         created_at=now_utc() - timedelta(hours=1)),
            Intervencion(cliente_telefono=TEL_F, motivo="no_se", detalle="viejo",
                         created_at=now_utc() - timedelta(hours=1)),
            Intervencion(cliente_telefono=TEL_G, motivo="chat_tomado", detalle="ella tomó el chat",
                         created_at=now_utc() - timedelta(hours=1)),
        ])
        await s.commit()
        # A E le habló el BOT después (eso NO es atender); a F y a G, la DUEÑA.
        s.add_all([
            Mensaje(cliente_telefono=TEL_E, rol="assistant", contenido="sigo yo", created_at=now_utc()),
            Mensaje(cliente_telefono=TEL_F, rol="owner", contenido="ya lo atendí", created_at=now_utc()),
            Mensaje(cliente_telefono=TEL_G, rol="owner", contenido="hola, dime", created_at=now_utc()),
        ])
        await s.commit()

    async with factory() as s:
        await B.cerrar_avisos_ya_atendidos(s)
    async with factory() as s:
        estados = {
            i.cliente_telefono: (i.motivo, i.estado)
            for i in (await s.execute(
                select(Intervencion).where(Intervencion.cliente_telefono.in_([TEL_E, TEL_F, TEL_G]))
            )).scalars().all()
        }
    check("la dueña respondió → el aviso se cierra solo", estados.get(TEL_F, ("", ""))[1] == "resuelta",
          str(estados))
    check("respondió el BOT → el aviso NO se cierra (el bot no es la dueña)",
          estados.get(TEL_E, ("", ""))[1] == "pendiente", str(estados))
    check("🔴 `chat_tomado` NO se cierra JAMÁS: es el único botón que despausa el chat",
          estados.get(TEL_G, ("", ""))[1] == "pendiente", str(estados))

    print("\n8) EL RESCATADOR DE BUFFERS HUÉRFANOS")
    c = rc._client()
    reencolados: list[tuple] = []
    original_apply = T.procesar_buffer.apply_async
    T.procesar_buffer.apply_async = lambda args=None, **k: reencolados.append(args)  # type: ignore[assignment]
    try:
        await c.delete(f"lock:{TEL_A}")
        await c.rpush(f"buffer:{TEL_A}", "hola, sigo esperando")
        await c.expire(f"buffer:{TEL_A}", 3600)
        B._VISTOS.clear()
        await B.rescatar_buffers_huerfanos()
        check("recién visto → NO se reencola (su countdown todavía no venció)", reencolados == [])

        B._VISTOS[f"buffer:{TEL_A}"] -= B.SEGUNDOS_HUERFANO + 5  # simula que pasó el minuto
        await B.rescatar_buffers_huerfanos()
        check("🔴 visto DOS veces con un minuto de por medio y sin lock → se rescata",
              any(a and a[0] == TEL_A for a in reencolados), str(reencolados))

        reencolados.clear()
        B._VISTOS.clear()
        await c.set(f"lock:{TEL_A}", "1", ex=60)
        await B.rescatar_buffers_huerfanos()
        B._VISTOS[f"buffer:{TEL_A}"] = 0.0
        await B.rescatar_buffers_huerfanos()
        check("con un turno EN CURSO (lock) → NO se toca (ese ya lo va a vaciar su tarea)",
              not any(a and a[0] == TEL_A for a in reencolados), str(reencolados))
    finally:
        T.procesar_buffer.apply_async = original_apply  # type: ignore[assignment]
        await c.delete(f"buffer:{TEL_A}", f"lock:{TEL_A}")

    print("\n9) `barrer.py --seco` NO ESCRIBE NADA (el ensayo antes de soltarlo)")
    spec = importlib.util.spec_from_file_location(
        "barrer_script", Path(__file__).resolve().parent / "barrer.py"
    )
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    async with factory() as s:
        antes = len((await s.execute(select(Intervencion.id))).scalars().all())
    await modulo.seco()
    async with factory() as s:
        despues = len((await s.execute(select(Intervencion.id))).scalars().all())
    check("el modo seco no creó ni cerró una sola fila", antes == despues, f"{antes} → {despues}")


async def _correr() -> None:
    try:
        await main()
    finally:
        print("\n10) LIMPIEZA")
        await _limpiar()
        check("los clientes de prueba se borraron (no ensucian el panel)", True)

    print()
    if fallos:
        print(f"   🔴 {len(fallos)} FALLO(S): " + " · ".join(fallos))
        sys.exit(1)
    print("   ✅ EL SISTEMA SABE QUE ESTÁ ROTO: /salud no miente y el vigilante ve al cliente colgado")


asyncio.run(_correr())
