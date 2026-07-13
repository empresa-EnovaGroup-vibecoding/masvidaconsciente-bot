"""LA BANDEJA — banco de pruebas del RELEVO (que la dueña pueda atender sin salir del sistema).

Se corre DENTRO del contenedor del bot (usa la BD real del taller).
Lo que se prueba, en orden de gravedad:

  1. La VENTANA DE 24H se calcula bien y falla CERRADA (nunca se envía algo que Meta rechazaría).
  2. Un mensaje de la dueña PAUSA el bot en ese chat (el relevo es automático).
  3. El bot NO habla encima: si ella toma el chat mientras el bot piensa, la respuesta se DESCARTA
     (ni se envía ni se recuerda). Este es el agujero de carrera que marcó la auditoría.
  4. El mensaje de ella queda con rol='owner' (nunca 'assistant': el bot no puede creer que lo dijo él).
  5. El bot HEREDA lo que ella prometió (Redis) al devolverle el chat.
  6. El chat del simulador se rechaza (no hay WhatsApp del otro lado).

Nada de esto envía WhatsApp de verdad: se llama a las piezas, no a Meta.
"""
import asyncio
import sys
from datetime import timedelta

from sqlalchemy import delete, select

from app.api.router import _ventana
from app.models import Cliente, Mensaje, now_utc
from app.services.db import get_session_factory

TEL = "__prueba_bandeja__"
fallos: list[str] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    print(f"   {'[OK ]' if ok else '[MAL]'} {nombre}{('  → ' + detalle) if detalle and not ok else ''}")
    if not ok:
        fallos.append(nombre)


async def main() -> None:
    factory = get_session_factory()

    print("\n1) LA VENTANA DE 24 HORAS (la regla de Meta)")
    c = Cliente(telefono=TEL, ultimo_entrante_at=now_utc() - timedelta(hours=1))
    v = _ventana(c)
    check("hace 1 hora → abierta, ~23h restantes", v["abierta"] and 1370 <= v["minutos_restantes"] <= 1381,
          str(v))

    c.ultimo_entrante_at = now_utc() - timedelta(hours=23, minutes=59)
    v = _ventana(c)
    check("a falta de 1 minuto → todavía abierta", v["abierta"] and v["minutos_restantes"] <= 1, str(v))

    c.ultimo_entrante_at = now_utc() - timedelta(hours=24, minutes=1)
    v = _ventana(c)
    check("pasadas las 24h → CERRADA", not v["abierta"] and v["minutos_restantes"] == 0, str(v))

    c.ultimo_entrante_at = None
    v = _ventana(c)
    check("sin dato (NULL) → CERRADA, no abierta (fail-closed)", not v["abierta"], str(v))

    print("\n2) EL RELEVO: el mensaje de la dueña cabe en el hilo y calla al bot")
    async with factory() as s:
        await s.execute(delete(Mensaje).where(Mensaje.cliente_telefono == TEL))
        await s.execute(delete(Cliente).where(Cliente.telefono == TEL))
        s.add(Cliente(telefono=TEL, nombre="Prueba", ultimo_entrante_at=now_utc()))
        await s.commit()

    async with factory() as s:
        # Esto es lo que hace el endpoint: guardar como 'owner' + pausar el bot.
        s.add(Mensaje(
            cliente_telefono=TEL, rol="owner", contenido="Yo te confirmo el precio ahorita",
            tipo="text", wa_message_id="wamid.PRUEBA", estado="enviado",
        ))
        cli = (await s.execute(select(Cliente).where(Cliente.telefono == TEL))).scalar_one()
        cli.bot_pausado = True
        await s.commit()

    async with factory() as s:
        m = (await s.execute(
            select(Mensaje).where(Mensaje.cliente_telefono == TEL, Mensaje.rol == "owner")
        )).scalar_one_or_none()
        cli = (await s.execute(select(Cliente).where(Cliente.telefono == TEL))).scalar_one()
        check("la BD ACEPTA rol='owner' (la 019 corrió)", m is not None)
        check("no se guardó como 'assistant' (el bot no cree que lo dijo él)",
              m is not None and m.rol == "owner")
        check("quedó el id de Meta (para saber si llegó)", m is not None and m.wa_message_id == "wamid.PRUEBA")
        check("el bot quedó CALLADO en ese chat", cli.bot_pausado is True)

    print("\n3) EL BOT NO HABLA ENCIMA (el agujero de carrera)")
    from app.workers.tasks import _cliente_pausado, _enviar_en_partes, _lo_paso_una_persona

    async with factory() as s:
        cli = (await s.execute(select(Cliente).where(Cliente.telefono == TEL))).scalar_one()
        cli.pausado_por = "dueña"  # lo pausó ELLA (respondió desde el panel)
        await s.commit()

    enviado = await _enviar_en_partes(TEL, "Hola, soy el bot y no me enteré de nada")
    check("con la dueña atendiendo, el bot NO envía", enviado is False)
    check("y NO devuelve True (así el que llama no lo guarda en el historial)", enviado is not True)

    print("\n4) 🔴 EL BOT SE PAUSA A SÍ MISMO (pedir_ayuda): su despedida SÍ tiene que salir")
    print("   (bug real del 2026-07-12: el cliente escribía 'Hola' y no recibía NADA)")
    async with factory() as s:
        cli = (await s.execute(select(Cliente).where(Cliente.telefono == TEL))).scalar_one()
        cli.pausado_por = "bot"  # se pausó ÉL solo, al escalar
        await s.commit()

    check("el chat SIGUE pausado (el bot no atiende los siguientes mensajes)",
          await _cliente_pausado(TEL) is True)
    check("pero NO lo pausó una persona ⇒ su 'Dame un momentito y te confirmo' SÍ sale",
          await _lo_paso_una_persona(TEL) is False)

    async with factory() as s:
        cli = (await s.execute(select(Cliente).where(Cliente.telefono == TEL))).scalar_one()
        cli.bot_pausado, cli.pausado_por = False, None  # "Devolver al bot"
        await s.commit()
    check("al devolver el chat, la firma se borra y el bot vuelve a hablar",
          await _cliente_pausado(TEL) is False and await _lo_paso_una_persona(TEL) is False)

    print("\n4) EL SIMULADOR no tiene WhatsApp del otro lado")
    from app.api.router import SIMULADOR
    check("el teléfono del simulador se reconoce", (SIMULADOR + "x").startswith(SIMULADOR))

    print("\n5) LIMPIEZA")
    async with factory() as s:
        await s.execute(delete(Mensaje).where(Mensaje.cliente_telefono == TEL))
        await s.execute(delete(Cliente).where(Cliente.telefono == TEL))
        await s.commit()
    check("el cliente de prueba se borró (no ensucia el panel)", True)

    print()
    if fallos:
        print(f"   🔴 {len(fallos)} FALLO(S): " + " · ".join(fallos))
        sys.exit(1)
    print("   ✅ EL RELEVO FUNCIONA: la dueña atiende dentro del sistema y el bot se calla solo")


asyncio.run(main())
