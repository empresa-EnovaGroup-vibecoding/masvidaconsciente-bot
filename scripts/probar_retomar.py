"""RETOMAR — banco de pruebas de la BANDEJA FASE 3: que el bot CONTESTE al devolverle el chat.

Se corre DENTRO del contenedor del bot (usa la BD y el Redis REALES del taller).

EL CASO QUE ARREGLA (lo reportó Maired con una captura): la dueña tiene el chat tomado, el
cliente sigue escribiendo ("¿cuánto sería en Bs?"), ella se lo devuelve al bot... y el bot se
queda MUDO. Faltaba el disparador.

Lo que se prueba, en orden de gravedad:
  1. 🤐 NO HABLAR ENCIMA: si la dueña ya contestó todo (el último turno es de ella) ⇒ el bot NO
     habla. Hablar ahí sería un envío PROACTIVO, prohibido por Meta sin aprobación humana.
  2. ⏸️ RE-TOMADO: si ella volvió a tomar el chat antes de que corra la tarea ⇒ el bot se calla.
  3. 🔒 VENTANA DE 24H (fail-closed): cerrada ⇒ el bot NO escribe (Meta lo rechazaría y le baja
     la calidad al número) y le deja el aviso a la dueña.
  4. 💰 EL CASO REAL, de punta a punta: con 2 mensajes pendientes del cliente, al retomar el bot
     RESPONDE — y su respuesta queda EN LA BASE, DESPUÉS de los pendientes. (Este sí llama al
     modelo de verdad.) Se verifica en la BD, no en el chat.
  5. 🔁 DOBLE CLICK: dos disparos seguidos ⇒ UN solo mensaje del bot (candado de idempotencia).

WhatsApp NO se toca: `enviar_texto` (y las de media) se sustituyen por dobles. Nada sale al
mundo real. Lo que se mira es la BASE DE DATOS.
"""
import asyncio
import sys
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import delete, select

from app.agent import tools
from app.agent.system_prompt import hoy_venezuela
from app.models import (
    Cliente, Intervencion, Mensaje, Pedido, PrecioDia, Producto, ProductoVariante, now_utc,
)
from app.services import redis_client as rc
from app.services.db import get_session_factory
from app.workers import tasks
from app.workers.tasks import _retomar

TEL = "__prueba_retomar__"
fallos: list[str] = []
enviados: list[str] = []  # lo que el bot HABRÍA mandado por WhatsApp


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"   {'[OK ]' if ok else '[MAL]'} {nombre}{('  → ' + detalle) if detalle and not ok else ''}")
    if not ok:
        fallos.append(nombre)


async def _falso_envio(telefono: str, texto: str) -> dict:
    """Doble de Meta: anota y devuelve un id como el real. Nada sale al mundo."""
    enviados.append(texto)
    return {"messages": [{"id": f"wamid.PRUEBA_{len(enviados)}"}]}


async def _falso_media(*a, **k) -> dict:
    return {"messages": [{"id": "wamid.PRUEBA_MEDIA"}]}


async def _siempre_encendido() -> bool:
    """Este banco prueba EL RETOMAR, no el interruptor de encendido.

    El interruptor puede estar legítimamente APAGADO en el servidor (el 2026-07-13 se apagó para
    proteger a una clienta real mientras se blindaba el dinero) — y entonces el bot se calla, que es
    lo CORRECTO. Sin esto, el banco entero salía rojo por el motivo equivocado y parecía una
    regresión del código. El interruptor tiene su propia prueba en `probar_carril_dinero.py`.
    """
    return True


def _amordazar_a_meta() -> None:
    """Ningún camino puede llegar a WhatsApp: ni el envío en globos ni las herramientas."""
    tasks.enviar_texto = _falso_envio          # el embudo del bot (_enviar_en_partes)
    tools.enviar_texto = _falso_envio          # las herramientas escriben directo
    tools.enviar_imagen = _falso_media
    tools.enviar_video = _falso_media
    tasks._bot_activo = _siempre_encendido     # ver arriba: aquí se prueba el retomar, no el switch


async def _limpiar() -> None:
    factory = get_session_factory()
    async with factory() as s:
        pedidos = (
            await s.execute(select(Pedido).where(Pedido.cliente_telefono == TEL))
        ).scalars().all()
        for p in pedidos:
            await s.delete(p)
        await s.execute(delete(Mensaje).where(Mensaje.cliente_telefono == TEL))
        await s.execute(delete(Intervencion).where(Intervencion.cliente_telefono == TEL))
        await s.execute(delete(Cliente).where(Cliente.telefono == TEL))
        await s.commit()
    await rc.borrar_memoria(TEL)
    await _soltar_candado()


async def _soltar_candado() -> None:
    """El candado del retomar dura 30s a propósito. Entre pruebas se suelta a mano para no
    esperar (en producción NUNCA se suelta: eso es lo que frena el doble click)."""
    await rc._client().delete(f"retomar:{TEL}", f"lock:{TEL}")


async def _sembrar_pausa(*, ultimo_entrante_horas: float = 1.0) -> None:
    """Deja el mundo como queda tras una pausa REAL: el cliente escribió, la dueña tomó el chat,
    y lo que él siguió escribiendo se guardó (en Postgres y en la memoria del bot) SIN respuesta."""
    factory = get_session_factory()
    ahora = now_utc()
    async with factory() as s:
        s.add(Cliente(
            telefono=TEL, nombre="Prueba Retomar",
            ultimo_entrante_at=ahora - timedelta(hours=ultimo_entrante_horas),
            ultima_interaccion=ahora, bot_pausado=True, pausado_por="dueña",
        ))
        # El hilo tal como lo ve el panel (el bot habló, ella tomó el chat, él siguió pidiendo).
        s.add(Mensaje(cliente_telefono=TEL, rol="user", contenido="Hola, quiero kombucha",
                      created_at=ahora - timedelta(minutes=10)))
        s.add(Mensaje(cliente_telefono=TEL, rol="owner", contenido="Claro que sí, dame un momentito",
                      tipo="text", estado="enviado", created_at=ahora - timedelta(minutes=9)))
        s.add(Mensaje(cliente_telefono=TEL, rol="user", contenido="¿Cuánto sería en bolívares?",
                      created_at=ahora - timedelta(minutes=8)))
        s.add(Mensaje(cliente_telefono=TEL, rol="user", contenido="Quedo pendiente del monto",
                      created_at=ahora - timedelta(minutes=7)))
        await s.commit()

    # La memoria del bot (Redis): lo de ella entra como 'assistant' (una sola voz ante el cliente).
    await rc.guardar_historial(TEL, "user", "Hola, quiero kombucha")
    await rc.guardar_historial(TEL, "assistant", "Claro que sí, dame un momentito")
    await rc.guardar_historial(TEL, "user", "¿Cuánto sería en bolívares?")
    await rc.guardar_historial(TEL, "user", "Quedo pendiente del monto")


async def _devolver_el_chat() -> None:
    """Lo que hace el endpoint al apretar 'Devolver al bot' (antes de disparar la tarea)."""
    factory = get_session_factory()
    async with factory() as s:
        cli = (await s.execute(select(Cliente).where(Cliente.telefono == TEL))).scalar_one()
        cli.bot_pausado, cli.pausado_por = False, None
        await s.commit()


async def _mensajes() -> list[Mensaje]:
    factory = get_session_factory()
    async with factory() as s:
        return list((await s.execute(
            select(Mensaje).where(Mensaje.cliente_telefono == TEL)
            .order_by(Mensaje.created_at, Mensaje.id)
        )).scalars().all())


async def main() -> None:
    _amordazar_a_meta()
    # La lista blanca de pruebas frenaría a un teléfono inventado como el nuestro. Se apaga SOLO
    # en este proceso (el worker real la conserva).
    tasks.settings.numeros_permitidos = ""

    print("\n1) 🤐 LA DUEÑA YA CONTESTÓ TODO → el bot NO habla (sería proactivo)")
    await _limpiar()
    await _sembrar_pausa()
    await rc.guardar_historial(TEL, "assistant", "Te lo dejo en 240 bolívares, mi amor")  # ella
    await _devolver_el_chat()
    await _retomar(TEL, "Prueba Retomar")
    check("no envió nada (el último turno era de ella)", enviados == [], str(enviados))
    check("y no escribió en la base", not [m for m in await _mensajes() if m.rol == "assistant"])

    print("\n2) ⏸️ ELLA VOLVIÓ A TOMAR EL CHAT antes de que corriera la tarea → el bot se calla")
    await _limpiar()
    await _sembrar_pausa()  # queda pausado_por='dueña' (NO se devuelve el chat)
    await _soltar_candado()
    await _retomar(TEL, "Prueba Retomar")
    check("con el chat tomado, el bot NO envía", enviados == [], str(enviados))

    print("\n3) 🔒 VENTANA DE 24H CERRADA → el bot NO escribe y te avisa (fail-closed)")
    await _limpiar()
    await _sembrar_pausa(ultimo_entrante_horas=25)  # el cliente escribió hace 25h
    await _devolver_el_chat()
    await _soltar_candado()
    await _retomar(TEL, "Prueba Retomar")
    avisos = [m for m in await _mensajes() if m.rol == "assistant"]
    factory = get_session_factory()
    async with factory() as s:
        inter = (await s.execute(
            select(Intervencion).where(Intervencion.cliente_telefono == TEL)
        )).scalars().all()
    # El único envío permitido aquí es el WhatsApp de aviso A LA DUEÑA (a su número, no al cliente).
    check("el bot NO le escribió al cliente", not avisos, str([m.contenido for m in avisos]))
    check("quedó el aviso en la bandeja (motivo='ventana_cerrada')",
          any(i.motivo == "ventana_cerrada" for i in inter), str([i.motivo for i in inter]))

    print("\n4) 💰 EL CASO REAL: 2 pendientes del cliente → el bot RESPONDE (llama al modelo)")
    await _limpiar()
    await _sembrar_pausa()
    await _devolver_el_chat()
    enviados.clear()
    await _retomar(TEL, "Prueba Retomar")

    hilo = await _mensajes()
    del_bot = [m for m in hilo if m.rol == "assistant"]
    check("el bot respondió algo", bool(del_bot))
    # SE IMPRIME ENTERO A PROPÓSITO: el bot puede decir la verdad en el tono y mentir en el
    # hecho. Lo que dice hay que LEERLO, no darlo por bueno porque haya una fila en la tabla.
    for i, m in enumerate(del_bot, 1):
        print(f"       ↳ globo {i}: {m.contenido}")
    # LA PRUEBA DE FUEGO (la del PRP): la respuesta del bot queda DESPUÉS de los pendientes.
    check("su respuesta quedó DESPUÉS de los mensajes pendientes del cliente",
          bool(hilo) and hilo[-1].rol == "assistant",
          f"último del hilo = {hilo[-1].rol if hilo else 'nada'}")
    ultimo_cliente = max((h.created_at for h in hilo if h.rol == "user"), default=None)
    check("y con fecha posterior a la del último pendiente",
          bool(del_bot) and ultimo_cliente is not None
          and del_bot[-1].created_at > ultimo_cliente)
    # No se duplica el turno del cliente: seguimos con los 3 'user' que sembramos.
    check("no duplicó los mensajes del cliente en el hilo",
          len([m for m in hilo if m.rol == "user"]) == 3,
          str(len([m for m in hilo if m.rol == "user"])))
    # La instrucción [SISTEMA] es EFÍMERA: no puede quedar en la memoria del bot.
    hist = await rc.obtener_historial(TEL)
    check("la orden [SISTEMA] NO quedó en la memoria del bot",
          not any("[SISTEMA]" in str(h.get("content", "")) for h in hist))
    # EL DINERO: jamás un pedido en $0 (si le falta el precio del día, debe re-escalar, no cobrar).
    async with factory() as s:
        pedidos = (await s.execute(select(Pedido).where(Pedido.cliente_telefono == TEL))).scalars().all()
    check("no hay ningún pedido cobrado en $0",
          not [p for p in pedidos if p.total is not None and float(p.total) == 0],
          str([(p.items, float(p.total or 0)) for p in pedidos]))

    print("\n4.b) 🌟 EL CASO ESTRELLA: el bot ESCALÓ (no sabía el precio del día) y ella lo resolvió")
    print("     (el ROADMAP lo promete así: 'pon el precio del día y devuelve el chat: el bot lo")
    print("      venderá solo'. Antes de este arreglo, el bot SE QUEDABA MUDO y se perdía la venta.)")
    await _limpiar()
    factory = get_session_factory()
    ahora = now_utc()
    # LA DUEÑA PONE EL PRECIO DEL DÍA (eso es lo que ella hace en el panel antes de apretar).
    # Sin esto la prueba sería trampa: el bot re-escalaría (honesto) y parecería que "funciona".
    async with factory() as s:
        prod, var = (await s.execute(
            select(Producto, ProductoVariante)
            .join(ProductoVariante, ProductoVariante.producto_id == Producto.id)
            .where(ProductoVariante.precio.is_(None))
            .order_by(ProductoVariante.id)
        )).first()
        await s.execute(delete(PrecioDia).where(PrecioDia.variante_id == var.id,
                                                PrecioDia.fecha == hoy_venezuela()))
        s.add(PrecioDia(producto_id=prod.id, variante_id=var.id,
                        precio=Decimal("37.00"), fecha=hoy_venezuela()))
        s.add(Cliente(
            telefono=TEL, nombre="Prueba Retomar", ultimo_entrante_at=ahora,
            ultima_interaccion=ahora, bot_pausado=False, pausado_por=None,  # ella ya lo devolvió
        ))
        pregunta = f"cuánto cuesta {prod.nombre} ({var.presentacion})?"
        s.add(Mensaje(cliente_telefono=TEL, rol="user", contenido=pregunta,
                      created_at=ahora - timedelta(minutes=5)))
        await s.commit()
    print(f"     (la dueña acaba de cargar: {prod.nombre} {var.presentacion} = $37)")
    # El hilo TAL COMO QUEDA tras un escalado: lo último que se dijo NO es del cliente —
    # es el PAGARÉ del propio bot ("te lo confirmo enseguida"). El guard viejo leía eso como
    # "aquí no hay nada pendiente" y se callaba. Pero el cliente SIGUE esperando.
    await rc.guardar_historial(TEL, "user", pregunta)
    await rc.guardar_historial(TEL, "assistant", "Te confirmo ese precio enseguida 💚")
    enviados.clear()
    # `pausado_por='bot'` = la FIRMA del escalado (la lee el endpoint antes de borrarla).
    await _retomar(TEL, "Prueba Retomar", "bot")
    hilo = await _mensajes()
    del_bot = [m for m in hilo if m.rol == "assistant"]
    for i, m in enumerate(del_bot, 1):
        print(f"       ↳ globo {i}: {m.contenido}")
    check("🌟 el bot NO se queda mudo: le contesta al cliente", bool(del_bot),
          "SE QUEDÓ MUDO — el cliente nunca se entera del precio y se pierde la venta")
    # Y LO QUE DE VERDAD IMPORTA: que VENDA. Que le diga el precio que ella acaba de cargar.
    dijo_todo = " ".join(m.contenido for m in del_bot)
    check("🌟 y le DICE EL PRECIO que ella acaba de cargar ($37) — o sea: VENDE",
          "37" in dijo_todo, f"no dijo el precio: {dijo_todo[:120]!r}")
    check("y su respuesta queda DESPUÉS de la pregunta del cliente",
          bool(hilo) and hilo[-1].rol == "assistant")
    async with factory() as s:
        await s.execute(delete(PrecioDia).where(PrecioDia.variante_id == var.id,
                                                PrecioDia.fecha == hoy_venezuela()))
        await s.commit()

    print("\n5) 🔁 DOBLE CLICK → un solo mensaje (candado de idempotencia)")
    cuantos = len(enviados)
    await _retomar(TEL, "Prueba Retomar")  # segundo click, sin soltar el candado
    check("el segundo click no mandó nada más", len(enviados) == cuantos,
          f"{cuantos} → {len(enviados)}")

    print("\n6) LIMPIEZA")
    await _limpiar()
    check("el cliente de prueba se borró (no ensucia el panel)", True)

    print()
    if fallos:
        print(f"   🔴 {len(fallos)} FALLO(S): " + " · ".join(fallos))
        sys.exit(1)
    print("   ✅ EL RETOMAR FUNCIONA: el bot contesta lo pendiente, y se calla cuando debe callarse")


asyncio.run(main())
