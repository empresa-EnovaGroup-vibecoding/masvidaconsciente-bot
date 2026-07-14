"""EL DELIVERY — el "código de barras" del envío.

🔴 POR QUÉ EXISTE (caso REAL, 2026-07-13 21:26, con una CLIENTA de verdad):
Quería un producto de $20 con delivery. El bot escribió:

    "El total en bolívares es de $23 USD a la tasa BCV del día."

Sumó $20 + $3 **de cabeza**, llamó bolívares a unos dólares, y NO había ningún pedido en la base.
El prompt se lo prohibía DOS VECES ("No sumes el envío al total", "no calcules delivery") y lo hizo
igual. **La causa de fondo: el sistema NO SABÍA COBRAR DELIVERY.** Y lo que no existe, se inventa.

Lo que se prueba:
  1. 🧮 EL CÓDIGO SUMA, no el bot: producto $20 + envío $3 = $23 (y queda en la BD).
  2. 🧊 EL ENVÍO SE CONGELA en el pedido: si mañana sube a $4, el pedido de ayer NO cambia.
  3. 🧾 EL RECIBO enseña la línea del envío (si no, el cliente no puede cantar una zona mal puesta).
  4. 🔒 SIN ZONA NO SE COBRA: `generar_datos_pago` lo rechaza y le da la lista de zonas al bot.
  5. 🚫 UNA ZONA INVENTADA se rechaza (lista CERRADA: el bot no puede escribir un id que no le dimos).
  6. 💵 EL 20% DE DIVISAS **NO** TOCA EL FLETE (si no, la dueña paga el delivery de su bolsillo).
  7. 🏠 EL RETIRO sale sin costo y no suma nada.

Se corre DENTRO del contenedor del bot. Limpia todo lo que crea.
"""
import asyncio
import sys
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import delete, select

from app.agent.tools import generar_datos_pago, registrar_pedido
from app.models import Cliente, Pago, Pedido, Producto, ProductoVariante, ZonaEntrega, now_utc
from app.services.db import get_session_factory

TEL = "__prueba_delivery__"
fallos: list[str] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"   {'[OK ]' if ok else '[MAL]'} {nombre}{('  → ' + detalle) if detalle and not ok else ''}")
    if not ok:
        fallos.append(nombre)


async def _limpiar(s, zonas: bool = False) -> None:
    """Borra el cliente y sus pedidos. Las zonas SOLO al final (`zonas=True`): borrarlas a mitad
    del banco dejaba la lista cerrada VACÍA y los tests siguientes fallaban por el motivo
    equivocado — parecía un bug del código y era de la prueba."""
    peds = (await s.execute(select(Pedido).where(Pedido.cliente_telefono == TEL))).scalars().all()
    for p in peds:
        await s.execute(delete(Pago).where(Pago.pedido_id == p.id))
    for p in peds:
        await s.delete(p)
    await s.execute(delete(Cliente).where(Cliente.telefono == TEL))
    if zonas:
        await s.execute(delete(ZonaEntrega).where(ZonaEntrega.nombre.like("__zona_prueba%")))
    await s.commit()


async def main() -> None:
    factory = get_session_factory()
    async with factory() as s:
        await _limpiar(s, zonas=True)   # arranque limpio

        # Un producto REAL del catálogo, con su precio real.
        prod, var = (await s.execute(
            select(Producto, ProductoVariante)
            .join(ProductoVariante, ProductoVariante.producto_id == Producto.id)
            .where(ProductoVariante.precio.is_not(None), ProductoVariante.disponible.is_(True),
                   Producto.disponible.is_(True))
            .order_by(ProductoVariante.precio.desc())
        )).first()
        precio = Decimal(str(var.precio))
        s.add(Cliente(telefono=TEL, nombre="Prueba Delivery"))

        # Las zonas de prueba (se borran al final).
        retiro = ZonaEntrega(nombre="__zona_prueba_retiro", costo=Decimal("0"), es_retiro=True, orden=0)
        cerca = ZonaEntrega(nombre="__zona_prueba_cerca", costo=Decimal("3"), orden=1)
        lejos = ZonaEntrega(nombre="__zona_prueba_lejos", costo=Decimal("5"), orden=2)
        s.add_all([retiro, cerca, lejos])
        await s.commit()
        await s.refresh(retiro); await s.refresh(cerca); await s.refresh(lejos)

        manana = (now_utc() + timedelta(days=30)).date().isoformat()
        items = [{"variante_id": var.id, "cantidad": 1}]

        print(f"\n   (producto real: {prod.nombre} {var.presentacion} = ${precio})")

        print("\n1) 🧮 EL CÓDIGO SUMA EL ENVÍO (el bot NO)")
        r = await registrar_pedido(s, TEL, items, entrega_fecha=manana, zona_id=cerca.id)
        check("el pedido se registró", r.get("ok") is True, str(r.get("nota"))[:90])
        esperado = float(precio + Decimal("3"))
        check(f"total = producto ${precio} + envío $3 = ${esperado:g}",
              r.get("ok") and abs(r["total_usd"] - esperado) < 0.01, str(r.get("total_usd")))

        print("\n2) 🧾 EL RECIBO ENSEÑA LA LÍNEA DEL ENVÍO")
        print("   " + "\n   ".join((r.get("resumen") or "").splitlines()))
        check("el recibo dice el envío y su zona",
              "Envío a __zona_prueba_cerca" in (r.get("resumen") or ""))
        check("y el total del recibo es el de la suma del código",
              f"Total: ${esperado:g}" in (r.get("resumen") or "").replace(".00", ""))

        print("\n3) 🧊 EL ENVÍO QUEDA CONGELADO EN EL PEDIDO")
        ped = await s.get(Pedido, r["pedido_id"])
        check("la BD guarda la zona y su costo", ped.zona_nombre == "__zona_prueba_cerca"
              and float(ped.costo_envio) == 3.0, f"{ped.zona_nombre} / {ped.costo_envio}")
        cerca.costo = Decimal("4")          # la dueña sube el envío HOY
        await s.commit()
        ped2 = await s.get(Pedido, r["pedido_id"])
        check("🧊 sube el envío a $4 y el pedido de ANTES no cambia de precio",
              float(ped2.costo_envio) == 3.0 and float(ped2.total) == esperado,
              f"envio={ped2.costo_envio} total={ped2.total}")
        cerca.costo = Decimal("3")
        await s.commit()

        print("\n4) 💵 EL 20% DE DIVISAS **NO** TOCA EL FLETE")
        cobro = await generar_datos_pago(s, TEL, ped.id)
        check("el cobro se generó", cobro.get("ok") is True, str(cobro.get("nota"))[:90])
        # productos × 0,80 + envío COMPLETO
        div_ok = float((precio * Decimal("0.80")).quantize(Decimal("0.01")) + Decimal("3"))
        div_mal = float(((precio + Decimal("3")) * Decimal("0.80")).quantize(Decimal("0.01")))
        check(f"en divisas: producto×0,80 + envío = ${div_ok:g} (NO ${div_mal:g})",
              cobro.get("ok") and abs(cobro["monto_usd_divisas"] - div_ok) < 0.01,
              f"dio {cobro.get('monto_usd_divisas')} — si diera {div_mal:g}, la dueña PAGA el flete")

        print("\n5) 🔒 SIN ZONA NO SE COBRA")
        await _limpiar(s)
        s.add(Cliente(telefono=TEL, nombre="Prueba Delivery"))
        await s.commit()
        r2 = await registrar_pedido(s, TEL, items, entrega_fecha=manana)  # SIN zona
        check("el pedido se registra igual (no rompemos el flujo)", r2.get("ok") is True)
        cobro2 = await generar_datos_pago(s, TEL, r2["pedido_id"])
        check("🔒 pero NO se puede cobrar sin saber cómo lo recibe", cobro2.get("ok") is False,
              str(cobro2)[:80])
        check("y se le da al bot la lista CERRADA de zonas", bool(cobro2.get("zonas")))

        print("\n6) 🚫 UNA ZONA INVENTADA SE RECHAZA (lista CERRADA)")
        r3 = await registrar_pedido(s, TEL, items, entrega_fecha=manana, zona_id=999999)
        check("un id_zona que no existe ⇒ rechazado", r3.get("ok") is False, str(r3)[:70])
        check("y se le devuelven las zonas de verdad", bool(r3.get("zonas")))

        print("\n7) 🏠 EL RETIRO NO SUMA NADA")
        await _limpiar(s)
        s.add(Cliente(telefono=TEL, nombre="Prueba Delivery"))
        await s.commit()
        r4 = await registrar_pedido(s, TEL, items, entrega_fecha=manana, zona_id=retiro.id)
        check("el retiro se registra", r4.get("ok") is True)
        check(f"y el total sigue siendo ${precio} (sin envío)",
              r4.get("ok") and abs(r4["total_usd"] - float(precio)) < 0.01, str(r4.get("total_usd")))
        check("el recibo dice que es retiro y sin costo",
              "sin costo" in (r4.get("resumen") or ""))

        await _limpiar(s, zonas=True)

    print()
    if fallos:
        print(f"   🔴 {len(fallos)} FALLO(S): " + " · ".join(fallos[:5]))
        sys.exit(1)
    print("   ✅ EL DELIVERY LO COBRA EL CÓDIGO: el bot elige la zona, jamás la suma ni la inventa")


asyncio.run(main())
