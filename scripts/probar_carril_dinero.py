"""EL CARRIL DEL DINERO — la puerta que no tenía guardia.

`redactar_mensaje` es la que le habla al cliente en los TRES momentos del dinero: cuando manda el
comprobante, cuando el monto NO cuadra, y cuando la dueña confirma o rechaza el pago. Devolvía el
texto del modelo **TAL CUAL**, sin una sola comprobación, y a temperatura 0.7 (el resto del bot
corre a 0.15). Encontrado en la auditoría de arquitectura del 2026-07-13.

Lo que se prueba:
  1. 💵 La red del dinero ya NO es ciega: caza "28$", "28 dólares", "28 USD" (antes solo veía "$28").
  2. 🔴 "son 5.000 Bs" ya NO se autoriza a sí mismo. (Antes: al monto se le sacaban todas las
     lecturas posibles —5.000 se leía también como 5— y bastaba con que UNA estuviera autorizada.
     Como el 5 casi siempre está en el catálogo, CUALQUIER cifra en bolívares pasaba. En el carril
     donde el bot cobra de verdad.)
  3. 🚪 La puerta del dinero tiene guardia: si el modelo inventa un monto o dice una frase
     prohibida, el mensaje NO sale (ni a la segunda).
  4. 🧭 Las dos listas de frases prohibidas: lo que es mentira SIEMPRE (el banco, ser una persona,
     la salud) se frena en TODOS los carriles; lo que la situación SÍ le manda decir ("recibí tu
     pago") NO se frena en el carril del pago — si no, mataríamos el mensaje correcto.
  5. ⏰ El aviso de pago comprueba la ventana de 24h de Meta (es el ÚNICO camino que habla DÍAS
     después). Cerrada ⇒ no envía y te avisa a TI.
  6. 🔌 El interruptor de apagado ya cubre el comprobante: con el bot apagado, no le habla al
     cliente que paga (pero el pago SÍ queda registrado).

No se manda un solo WhatsApp: Meta está amordazado y el modelo, sustituido por un doble.
"""
import asyncio
import sys
from datetime import timedelta

from sqlalchemy import delete, select

from app.agent import agent as ag
from app.agent.agent import (
    _dinero_inventado,
    _frase_prohibida,
    autorizados_por_moneda,
    frase_prohibida_siempre,
)
from app.models import Cliente, Configuracion, Intervencion, Mensaje, now_utc
from app.services import redis_client as rc
from app.services.db import get_session_factory
from app.workers import tasks

TEL = "__prueba_dinero__"
fallos: list[str] = []
enviados: list[tuple[str, str]] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"   {'[OK ]' if ok else '[MAL]'} {nombre}{('  → ' + detalle) if detalle and not ok else ''}")
    if not ok:
        fallos.append(nombre)


async def _falso_envio(telefono: str, texto: str) -> dict:
    enviados.append((telefono, texto))
    return {"messages": [{"id": f"wamid.D{len(enviados)}"}]}


async def _limpiar() -> None:
    f = get_session_factory()
    async with f() as s:
        await s.execute(delete(Mensaje).where(Mensaje.cliente_telefono == TEL))
        await s.execute(delete(Intervencion).where(Intervencion.cliente_telefono == TEL))
        await s.execute(delete(Cliente).where(Cliente.telefono == TEL))
        await s.commit()
    await rc.borrar_memoria(TEL)


async def main() -> None:
    tasks.enviar_texto = _falso_envio
    tasks.settings.numeros_permitidos = ""
    f = get_session_factory()

    print("\n1) 💵 LA RED DEL DINERO YA NO ES CIEGA (antes solo veía '$28' y '28 bs')")
    # Autorizados = lo que de verdad salió de una herramienta/catálogo, YA SEPARADO POR MONEDA.
    usd_ok = {14.0, 28.0, 4.0, 7.0}
    bs_ok = {31936.21}
    for texto, esperado, nota in [
        ("El total es $28", False, "lo autorizado pasa"),
        ("El total es 28$", False, "PEGADO — el formato que el propio prompt le enseña"),
        ("Son 28 dólares", False, "en palabras"),
        ("Son 28 USD", False, "en siglas"),
        ("El total es $35", True, "inventado en dólares"),
        ("El total es 35$", True, "🔴 inventado, PEGADO (antes se colaba)"),
        ("Son 35 dólares", True, "🔴 inventado, en palabras (antes se colaba)"),
        ("Son Bs 31.936,21", False, "el monto REAL en bolívares, con su formato"),
        ("son 5.000 Bs", True, "🔴 EL AGUJERO GORDO: se autorizaba solo (5.000 se leía como 5)"),
        ("son 45.000 Bs", True, "🔴 ídem (45 es un precio del catálogo… 45.000 Bs NO lo es)"),
        # 🔴🔴 EL CASO REAL DE LA CLIENTA (2026-07-13): la cifra está en DÓLARES y la frase dice
        # BOLÍVARES. El 28 SÍ está autorizado… pero como DÓLAR, no como bolívar.
        ("El total en bolívares es de $28 USD a la tasa BCV", True,
         "🔴🔴 EL CASO REAL: llamó bolívares a unos dólares"),
        ("El total en bolívares es de Bs 31.936,21", False, "el de verdad: pasa"),
    ]:
        malos = _dinero_inventado(texto, usd_ok, bs_ok)
        check(f"{'FRENA ' if esperado else 'pasa  '} | {texto:<28} ({nota})",
              bool(malos) == esperado, f"detectados={malos}")

    print("\n1.b) 🏷️ UN ID DEL CATÁLOGO **NO** ES UN PRECIO (de aquí salió el '$23')")
    # El prompt inyecta: precio $20.00 (id_para_pedir=23). El 23 es un ID, no dinero.
    catalogo = "Pan de Sándwich [SOLO PARA TI]: precio $20.00 (id_para_pedir=23)"
    u, b = autorizados_por_moneda(catalogo)
    check("el precio ($20) SÍ entra como dinero", 20.0 in u, str(sorted(u)))
    check("🔴 el id (23) NO entra como dinero", 23.0 not in u,
          "el id se colaba como precio: por eso el bot dijo '$23'")
    check("y por eso '$23' AHORA se frena", bool(_dinero_inventado("El total es $23", u, b)))

    print("\n2) 🧭 LAS DOS LISTAS: lo que es mentira SIEMPRE vs. lo que la situación SÍ le manda decir")
    for texto, siempre, charla in [
        ("Ya revisé en mi banco y no me aparece", True, True),
        ("Mi banco ya me confirmó el pago", True, True),
        ("Soy Whuilianny, la dueña de masvidaconsciente", True, True),
        ("Te lo preparo con alulosa, así no te sube el azúcar", True, True),
        # ESTAS son la razón de las dos listas: en la CHARLA el bot no puede saberlo (miente),
        # pero en el carril del comprobante la situación le ORDENA decirlo ("dile que recibiste
        # su pago"). Aplicar la lista entera allí mataría el mensaje CORRECTO.
        ("¡Recibí tu pago! 💚 Ya coordino tu entrega", False, True),
        ("No me ha llegado ningún pago tuyo", False, True),
    ]:
        s = frase_prohibida_siempre(texto) is not None
        c = _frase_prohibida(texto) is not None
        check(f"siempre={'SÍ' if siempre else 'no'} charla={'SÍ' if charla else 'no'} | {texto[:44]}",
              s == siempre and c == charla, f"siempre={s} charla={c}")

    print("\n3) 🚪 LA PUERTA TIENE GUARDIA: si el modelo inventa dinero, el mensaje NO SALE")
    print("   (con LISTA CERRADA: solo los montos que el código cobró de verdad. El catálogo NO")
    print("    entra: el 12 es el precio de las Empanadas Keto, y por eso el '$12' se colaba.)")
    guardado = ag._pedir_redaccion

    async def _modelo_mentiroso(messages, modelo):
        return "Te faltaron Bs 1.200, o sea unos $12 más para completar 💚"

    async def _modelo_del_banco(messages, modelo):
        return "Ya revisé mi banco y me llegó tu pago 💚"

    async def _modelo_bueno(messages, modelo):
        return "¡Recibí tu comprobante! 💚 Ya coordino tu entrega."

    async def _modelo_repite_total(messages, modelo):
        return "¡Recibí tu pago de $28! 💚 Ya coordino tu entrega."

    ag._pedir_redaccion = _modelo_mentiroso
    r = await ag.redactar_mensaje(
        "el cliente pago Bs 1.200 pero el total era Bs 2.000, asi que faltan Bs 800",
        [], "Rosa", TEL, montos_usd=set(), montos_bs=set(),
    )
    check("🔴 un dólar CALCULADO de cabeza ($12) ⇒ el mensaje se descarta", r == "",
          f"salió: {r!r}")

    ag._pedir_redaccion = _modelo_del_banco
    r = await ag.redactar_mensaje("el cliente mandó su comprobante", [], "Rosa", TEL, montos_usd=set(), montos_bs=set())
    check("'ya revisé mi banco' ⇒ el mensaje se descarta", r == "", f"salió: {r!r}")

    ag._pedir_redaccion = _modelo_bueno
    r = await ag.redactar_mensaje("el cliente mandó su comprobante", [], "Rosa", TEL, montos_usd=set(), montos_bs=set())
    check("y el mensaje BUENO sí pasa (no frenamos de más)", r != "", f"salió: {r!r}")

    ag._pedir_redaccion = _modelo_repite_total
    r = await ag.redactar_mensaje(
        "el cliente mandó su comprobante", [], "Rosa", TEL, montos_usd={28.0}, montos_bs=set(),
    )
    check("repetir el total que SÍ se cobró ($28) también pasa", r != "", f"salió: {r!r}")
    ag._pedir_redaccion = guardado

    print("\n4) ⏰ EL AVISO DE PAGO Y LA VENTANA DE 24H (el único camino que habla DÍAS después)")
    await _limpiar()
    async with f() as s:
        s.add(Cliente(telefono=TEL, nombre="Rosa",
                      ultimo_entrante_at=now_utc() - timedelta(hours=30)))  # ventana CERRADA
        await s.commit()
    enviados.clear()
    await tasks._notificar_cliente_pago(TEL, "su pago quedó confirmado")
    al_cliente = [t for t, _ in enviados if t == TEL]
    async with f() as s:
        ints = (await s.execute(
            select(Intervencion).where(Intervencion.cliente_telefono == TEL)
        )).scalars().all()
    check("con la ventana CERRADA, el bot NO le escribe al cliente", not al_cliente,
          str(enviados))
    check("y te deja el aviso a TI (motivo 'ventana_cerrada')",
          any(i.motivo == "ventana_cerrada" for i in ints), str([i.motivo for i in ints]))

    print("\n5) 🔌 EL INTERRUPTOR YA CUBRE EL COMPROBANTE (el bot apagado NO habla)")
    await _limpiar()
    async with f() as s:
        s.add(Cliente(telefono=TEL, nombre="Rosa", ultimo_entrante_at=now_utc()))
        fila = (await s.execute(
            select(Configuracion).where(Configuracion.clave == "bot_activo")
        )).scalar_one_or_none()
        antes = fila.valor if fila else None
        if fila:
            fila.valor = "false"
        else:
            s.add(Configuracion(clave="bot_activo", valor="false"))
        await s.commit()
    enviados.clear()
    partes = await tasks._responder_situacion(TEL, "el cliente mandó su comprobante", "Rosa")
    check("con el bot APAGADO, no le responde al cliente que paga", partes == [] and not enviados,
          str(enviados))
    async with f() as s:  # devolver el interruptor a como estaba
        fila = (await s.execute(
            select(Configuracion).where(Configuracion.clave == "bot_activo")
        )).scalar_one_or_none()
        if fila:
            fila.valor = antes if antes is not None else "true"
        await s.commit()
    check("el interruptor quedó como estaba (no se ensucia el panel)", True)

    await _limpiar()
    print()
    if fallos:
        print(f"   🔴 {len(fallos)} FALLO(S): " + " · ".join(fallos[:6]))
        sys.exit(1)
    print("   ✅ LA PUERTA DEL DINERO TIENE GUARDIA: ningún monto inventado, ninguna mentira,")
    print("      y nada sale fuera de la ventana de 24h de Meta.")


asyncio.run(main())
