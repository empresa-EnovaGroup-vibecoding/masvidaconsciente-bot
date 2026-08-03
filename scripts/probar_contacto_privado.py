"""CONTACTOS PRIVADOS — el bot NO le habla a la familia (migración 031).

Se corre DENTRO del contenedor del bot (usa la BD REAL del taller).

Qué se prueba, en orden de gravedad:
  1. EL LECTOR (`_es_contacto_privado`) y su FALLA ABIERTA: con Postgres caído el bot sigue
     atendiendo. Es la decisión discutible del arreglo, y por eso tiene su propio assert: el
     silencio global es la avería que ya costó una semana muda.
  2. EL SEGUNDO CINTURÓN (`_estado_pausa` → 'privado') y que NO rompe lo de antes: el bot que se
     pausa a sí mismo al escalar SIGUE pudiendo despedirse (el bug SIL de julio, que no se puede
     reintroducir).
  3. EL SEXTO PUNTO (`_la_duena_tomo_el_chat`): a la familia tampoco le salen las FOTOS.
  4. 🔴 EL CASO DE ORO, contra SIL-7: un mensaje de un contacto privado muere ANTES de dejar
     huella. Ni sube `no_leidos`, ni mueve `ultimo_entrante_at`, ni escribe en `mensajes`, ni
     marca leído. Ni contador huérfano ni hilo vacío: las dos mitades del desajuste, a la vez.
  5. Y NO CAMBIA A NADIE MÁS: el mismo mensaje sin la marca sigue pasando y sí sube el contador.
  6. EL BARREDOR NO DELATA A LA FAMILIA: un privado que escribió hace 40 min no dispara el
     "lleva media hora esperando" sobre la hermana de la dueña.
  7. EL ENDPOINT devuelve 404 con un teléfono que no existe — y NO crea una ficha fantasma (esa
     fue la corrección de la revisión cruzada: UPDATE + 404, nunca UPSERT).

⚠️ LOS TELÉFONOS DE PRUEBA **NO** EMPIEZAN POR "__", y es obligatorio: tanto `_es_contacto_privado`
como `_la_duena_tomo_el_chat` y el SQL del barredor descartan esos antes de mirar nada, así que con
un "__" el freno ni se ejercitaría. El prefijo 58999000 es imposible como número venezolano real,
para que un residuo se reconozca de un vistazo. Se limpia AL PRINCIPIO Y AL FINAL (regla dura).
Nada de esto manda WhatsApp de verdad: Meta y el encolado se sustituyen por espías.
"""
import asyncio
import sys
from datetime import timedelta

import httpx
from sqlalchemy import delete, select

import app.webhook.router as R
from app.api.security import crear_token
from app.config import get_settings
from app.main import app
from app.models import Cliente, Intervencion, Mensaje, now_utc
from app.services.db import get_session_factory

TEL = "589990001111"        # el contacto privado
TEL_NORMAL = "589990002222"  # el control: un cliente de verdad
TEL_BARREDOR = "589990003333"  # el privado que escribió justo antes de que lo marcaran
TEL_FANTASMA = "589990009999"  # este NO existe en `clientes` y NO puede llegar a existir
TODOS = (TEL, TEL_NORMAL, TEL_BARREDOR, TEL_FANTASMA)
fallos: list[str] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"   {'[OK ]' if ok else '[MAL]'} {nombre}{('  → ' + detalle) if detalle and not ok else ''}")
    if not ok:
        fallos.append(nombre)


def _auth() -> dict:
    """Un JWT de VERDAD, el mismo que emite el login del panel."""
    return {"Authorization": f"Bearer {crear_token(get_settings().admin_email)}"}


async def _fijo(v):
    return v


async def _limpiar() -> None:
    factory = get_session_factory()
    async with factory() as s:
        for tel in TODOS:
            await s.execute(delete(Intervencion).where(Intervencion.cliente_telefono == tel))
            await s.execute(delete(Mensaje).where(Mensaje.cliente_telefono == tel))
            await s.execute(delete(Cliente).where(Cliente.telefono == tel))
        await s.commit()


async def _poner(tel: str, *, privado: bool, pausado: str | None = None, minutos: int | None = None):
    factory = get_session_factory()
    cuando = now_utc() - timedelta(minutes=minutos) if minutos is not None else None
    async with factory() as s:
        s.add(Cliente(
            telefono=tel, nombre="Prueba privado", privado=privado,
            bot_pausado=pausado is not None, pausado_por=pausado,
            ultimo_entrante_at=cuando, ultima_interaccion=cuando or now_utc(),
            no_leidos=0,
        ))
        await s.commit()


async def _ficha(tel: str) -> Cliente | None:
    factory = get_session_factory()
    async with factory() as s:
        return (
            await s.execute(select(Cliente).where(Cliente.telefono == tel))
        ).scalar_one_or_none()


async def main() -> None:
    factory = get_session_factory()
    await _limpiar()  # al PRINCIPIO: una corrida anterior cortada no puede falsear esta
    try:
        print("\n1) EL LECTOR DEL WEBHOOK (y su FALLA ABIERTA)")
        await _poner(TEL, privado=False)
        check("un cliente normal NO es privado", await R._es_contacto_privado(TEL) is False)

        async with factory() as s:
            cli = (await s.execute(select(Cliente).where(Cliente.telefono == TEL))).scalar_one()
            cli.privado = True
            await s.commit()
        check("🔴 marcado como privado → True", await R._es_contacto_privado(TEL) is True)
        check("un teléfono interno ('__…') → False sin tocar la base",
              await R._es_contacto_privado("__loquesea") is False)

        # La base caída: se sustituye la fábrica de sesiones por una que revienta. El lector tiene
        # que decir "no es privado" y dejar al bot atendiendo — un error de lectura es GLOBAL, y
        # dejar mudo al bot con TODOS los clientes reales es peor que una respuesta de más.
        import app.services.db as DB
        original_factory = DB.get_session_factory

        def _revienta():
            raise RuntimeError("Postgres caído (simulado)")

        DB.get_session_factory = _revienta  # type: ignore[assignment]
        try:
            abierto = await R._es_contacto_privado(TEL)
        finally:
            DB.get_session_factory = original_factory  # type: ignore[assignment]
        check("🔴 con la base caída FALLA ABIERTA (el bot sigue atendiendo)", abierto is False)

        print("\n2) EL SEGUNDO CINTURÓN (`_estado_pausa`) — y lo de antes sigue igual")
        from app.workers.tasks import _cliente_pausado, _estado_pausa, _lo_paso_una_persona

        check("privado y SIN pausa → (True, 'privado')",
              await _estado_pausa(TEL) == (True, "privado"), str(await _estado_pausa(TEL)))
        check("⇒ `_cliente_pausado` dice True (el bot no atiende)",
              await _cliente_pausado(TEL) is True)
        check("⇒ `_lo_paso_una_persona` dice True (el bot se CALLA del todo)",
              await _lo_paso_una_persona(TEL) is True)

        # REGRESIÓN: el bot que se pausó SOLO al escalar sigue pudiendo despedirse. Es el bug SIL
        # de julio (el cliente escribía "Hola" y no recibía NADA): no se puede reintroducir.
        await _poner(TEL_NORMAL, privado=False, pausado="bot")
        check("sin marca + pausado por el BOT → (True, 'bot') como siempre",
              await _estado_pausa(TEL_NORMAL) == (True, "bot"), str(await _estado_pausa(TEL_NORMAL)))
        check("🔴 y su 'Dame un momentito y te confirmo' SIGUE saliendo",
              await _lo_paso_una_persona(TEL_NORMAL) is False)

        print("\n3) EL SEXTO PUNTO: a la familia tampoco le salen las FOTOS")
        from app.agent.tools import _la_duena_tomo_el_chat

        async with factory() as s:
            check("privado y sin pausa → la media NO sale",
                  await _la_duena_tomo_el_chat(s, TEL) is True)

        print("\n4) 🔴 EL CASO DE ORO (contra SIL-7): el mensaje muere ANTES de dejar huella")
        leidos: list[str] = []
        encolados: list[dict] = []
        import app.services.meta_client as MC
        original_leido = MC.marcar_leido_y_escribiendo
        original_duena = R._es_la_duena
        original_encolar = R._encolar_mensaje

        async def _espia_leido(message_id):
            leidos.append(message_id)

        async def _espia_encolar(mensaje):
            encolados.append(mensaje)
            return "ok"

        MC.marcar_leido_y_escribiendo = _espia_leido  # type: ignore[assignment]
        R._es_la_duena = lambda tel: _fijo(False)  # type: ignore[assignment]
        R._encolar_mensaje = _espia_encolar  # type: ignore[assignment]
        try:
            entrante = {
                "telefono": TEL, "nombre": "Tía", "tipo": "text",
                "texto": "hola mija como estas", "message_id": "wamid.PRIVADO_PRUEBA",
            }
            salida = await R._procesar_entrante(dict(entrante))
            ficha = await _ficha(TEL)
            async with factory() as s:
                n_msgs = len((await s.execute(
                    select(Mensaje).where(Mensaje.cliente_telefono == TEL)
                )).scalars().all())

            check("el webhook lo corta y devuelve 'privado'", salida == "privado", str(salida))
            check("🔴 NO sube `no_leidos` (ni globito huérfano…)",
                  ficha is not None and ficha.no_leidos == 0, str(ficha and ficha.no_leidos))
            check("🔴 NO se guarda en `mensajes` (…ni hilo vacío)", n_msgs == 0, f"{n_msgs} filas")
            check("NO mueve `ultimo_entrante_at` (no abre ventana de 24h)",
                  ficha is not None and ficha.ultimo_entrante_at is None,
                  str(ficha and ficha.ultimo_entrante_at))
            check("NO marca leído ni manda 'escribiendo…'", leidos == [], str(leidos))
            check("y no llega al buffer: cero IA gastada", encolados == [], str(encolados))

            print("\n5) Y NO CAMBIA A NADIE MÁS")
            async with factory() as s:
                cli = (await s.execute(
                    select(Cliente).where(Cliente.telefono == TEL_NORMAL)
                )).scalar_one()
                cli.bot_pausado, cli.pausado_por = False, None
                await s.commit()
            normal = dict(entrante, telefono=TEL_NORMAL, message_id="wamid.NORMAL_PRUEBA")
            salida_n = await R._procesar_entrante(normal)
            ficha_n = await _ficha(TEL_NORMAL)
            check("sin la marca, el mensaje sigue su camino de siempre",
                  salida_n == "ok", str(salida_n))
            check("y ese sí sube `no_leidos`",
                  ficha_n is not None and ficha_n.no_leidos == 1, str(ficha_n and ficha_n.no_leidos))
        finally:
            MC.marcar_leido_y_escribiendo = original_leido  # type: ignore[assignment]
            R._es_la_duena = original_duena  # type: ignore[assignment]
            R._encolar_mensaje = original_encolar  # type: ignore[assignment]

        print("\n6) EL BARREDOR NO DELATA A LA FAMILIA")
        import app.services.barredor as B
        import app.workers.tasks as T
        espiados: list[tuple] = []
        original_enviar = MC.enviar_texto
        original_permitido = T._numero_permitido

        async def _espia_envio(destino, texto):
            espiados.append((destino, texto))
            return {"espia": True}

        # La lista blanca del taller es real y dejaría fuera al teléfono de prueba: se sustituye
        # por una que solo lo deja pasar a él. Así, si el filtro `privado = false` faltara, el
        # aviso saldría — que es justo lo que este caso tiene que poder ver.
        MC.enviar_texto = _espia_envio  # type: ignore[assignment]
        T._numero_permitido = lambda tel: _fijo(tel == TEL_BARREDOR)  # type: ignore[assignment]
        try:
            await _poner(TEL_BARREDOR, privado=True, minutos=40)
            async with factory() as s:
                await B.vigilar_bot_callado(s)
                avisados = set((await s.execute(
                    select(Intervencion.cliente_telefono).where(
                        Intervencion.cliente_telefono == TEL_BARREDOR,
                        Intervencion.estado == "pendiente",
                    )
                )).scalars().all())
            check("🔴 un contacto privado colgado NO dispara el aviso a la dueña",
                  TEL_BARREDOR not in avisados, str(sorted(avisados)))
            check("y no le sale ningún WhatsApp por él", espiados == [], str(espiados))
        finally:
            MC.enviar_texto = original_enviar  # type: ignore[assignment]
            T._numero_permitido = original_permitido  # type: ignore[assignment]

        print("\n7) EL ENDPOINT: 404 si no existe, y NO crea fichas fantasma")
        transporte = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transporte, base_url="http://test") as c:
            r404 = await c.put(
                f"/api/clientes/{TEL_FANTASMA}/privado", json={"privado": True}, headers=_auth(),
            )
            rok = await c.put(
                f"/api/clientes/{TEL}/privado", json={"privado": False}, headers=_auth(),
            )
        check("🔴 con un teléfono que no existe → 404 (no UPSERT)",
              r404.status_code == 404, f"{r404.status_code} · {r404.text[:90]}")
        check("y NO quedó una ficha fantasma en la lista de Conversaciones",
              await _ficha(TEL_FANTASMA) is None)
        check("sobre un chat que sí existe → 200 y desmarca", rok.status_code == 200,
              f"{rok.status_code} · {rok.text[:90]}")
        ficha_final = await _ficha(TEL)
        check("la marca se quitó de verdad (se puede deshacer)",
              ficha_final is not None and ficha_final.privado is False)
        check("y NO tocó la pausa ni su firma",
              ficha_final is not None and ficha_final.bot_pausado is False
              and ficha_final.pausado_por is None, str(ficha_final and ficha_final.pausado_por))
    finally:
        await _limpiar()  # y al FINAL: el taller queda como estaba

    print()
    if fallos:
        print(f"   🔴 {len(fallos)} FALLO(S): " + " · ".join(fallos))
        sys.exit(1)
    print("   ✅ El bot no le habla a la familia, y nadie más nota el cambio")


if __name__ == "__main__":
    asyncio.run(main())
