"""EL DINERO VISTO DESDE EL PANEL — los endpoints que la dueña toca con el ratón.

🔴 POR QUÉ EXISTE (auditoría forense del 2026-08-02). Había 16 bancos en verde y **ninguno
llamaba a un endpoint HTTP del dinero**. `probar_delivery` ejercita la *tool* `registrar_pedido`;
`probar_cobro` ejercita el cobro del bot. Pero el panel escribe en las MISMAS filas por otra
puerta, y esa puerta no la vigilaba nadie. Lo que se coló por ahí:

  · **`PUT /pedidos/{id}/items` borraba el flete del total.** Recalculaba `total` solo con los
    productos y lo asignaba, mientras `costo_envio` seguía con su valor en la fila. La dueña
    corregía una cantidad y **regalaba el envío**; encima `generar_datos_pago` calcula el 20% de
    divisas restando el envío, así que restaba un flete que ya no estaba sumado:
    ($20−$3)×0,80+$3 = **$16,60** cuando lo correcto eran **$19,00**.
  · **No invalidaba la cotización.** Los montos cacheados (`cobro:` en Redis) eran los del pedido
    ANTERIOR, y `registrar_comprobante` los daba por buenos porque el `pedido_id` no cambia: la
    corrección de la dueña se ignoraba en silencio justo en el carril del dinero.
  · **No revalidaba la fecha.** Meterle un producto de 2 días de anticipación a un pedido para
    mañana pasaba sin freno: el panel dejaba vendida una fecha que el bot habría rechazado.

⚠️ SE HABLA POR HTTP DE VERDAD, con JWT real y ASGITransport. Es la lección que `probar_roles` ya
se dio a sí mismo: llamar a la función del endpoint a pelo **no evalúa los `Depends`**, así que ni
el guardia ni la validación del cuerpo llegan a correr. Un test que no pasa por la puerta no
prueba que la puerta cierre.

Lo que se prueba:
  1. 🩹 EL FLETE SOBREVIVE a corregir los items desde el panel.
  2. 🧾 EL DESGLOSE viaja al panel (productos y envío por separado, para poder auditar el total).
  3. 🗑️ LA COTIZACIÓN VIEJA SE TIRA (si no, el próximo comprobante se valida contra el monto viejo).
  4. 📅 UNA FECHA QUE YA NO SIRVE BLOQUEA la corrección (mismo calendario que usa el bot).

Se corre DENTRO del contenedor del bot. Limpia todo lo que crea.
"""
import asyncio
import json
import sys
from datetime import timedelta
from decimal import Decimal

import httpx
from sqlalchemy import delete, select

from app.agent.tools import generar_datos_pago, registrar_pedido
from app.api.security import crear_token
from app.config import get_settings
from app.main import app
from app.models import (
    Cliente,
    Intervencion,
    Pago,
    Pedido,
    Producto,
    ProductoVariante,
    ZonaEntrega,
    now_utc,
)
from app.services import redis_client as rc
from app.services.db import get_session_factory

TEL = "__prueba_cobro_panel__"
ZONA = "__zona_panel_prueba"
fallos: list[str] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"   {'[OK ]' if ok else '[MAL]'} {nombre}{('  → ' + detalle) if detalle and not ok else ''}")
    if not ok:
        fallos.append(nombre)


def _auth() -> dict:
    """Un JWT de VERDAD, el mismo que emite el login del panel."""
    return {"Authorization": f"Bearer {crear_token(get_settings().admin_email)}"}


async def _limpiar(s, zonas: bool = False) -> None:
    peds = (await s.execute(select(Pedido).where(Pedido.cliente_telefono == TEL))).scalars().all()
    for p in peds:
        await s.execute(delete(Pago).where(Pago.pedido_id == p.id))
    for p in peds:
        await s.delete(p)
    # 🔴 LAS INTERVENCIONES TAMBIÉN. Este banco dispara avisos ('ventana_cerrada' al notificar un
    # pago, entre otros) y NO los borraba: el 2026-08-02 dejó 16 avisos de prueba en la bandeja
    # REAL de la dueña, mezclados con los suyos. Un banco que ensucia el panel se acaba
    # desactivando — y con él se va la red que vigila el cobro.
    #
    # ⚠️ La limpieza del FINAL no siempre alcanza, y no es un descuido: confirmar un pago ENCOLA
    # `notificar_cliente_pago` en Celery, y esa tarea corre en el worker DESPUÉS de que el banco
    # terminó — el aviso nace cuando ya no hay nadie para borrarlo. Por eso lo que de verdad
    # mantiene limpia la bandeja es esta misma llamada al PRINCIPIO: cada corrida se lleva lo que
    # dejó la anterior. El techo pasa de "se acumulan para siempre" a "como mucho 2, hasta la
    # próxima corrida".
    await s.execute(delete(Intervencion).where(Intervencion.cliente_telefono == TEL))
    await s.execute(delete(Cliente).where(Cliente.telefono == TEL))
    if zonas:
        await s.execute(delete(ZonaEntrega).where(ZonaEntrega.nombre == ZONA))
    await s.commit()
    await rc.borrar_cobro(TEL)


async def main() -> None:
    factory = get_session_factory()
    transporte = httpx.ASGITransport(app=app)

    async with factory() as s:
        await _limpiar(s, zonas=True)
        try:
            prod, var = (await s.execute(
                select(Producto, ProductoVariante)
                .join(ProductoVariante, ProductoVariante.producto_id == Producto.id)
                .where(ProductoVariante.precio.is_not(None),
                       ProductoVariante.disponible.is_(True),
                       Producto.disponible.is_(True))
                .order_by(ProductoVariante.precio.desc())
            )).first()
            precio = Decimal(str(var.precio))
            zona = ZonaEntrega(nombre=ZONA, costo=Decimal("3"), orden=99)
            s.add_all([zona, Cliente(telefono=TEL, nombre="Prueba Panel")])
            await s.commit()
            await s.refresh(zona)

            manana = (now_utc() + timedelta(days=30)).date().isoformat()
            print(f"\n   (producto real: {prod.nombre} {var.presentacion} = ${precio} · zona $3)")

            # El bot toma el pedido: 1 unidad + envío.
            r = await registrar_pedido(
                s, TEL, [{"variante_id": var.id, "cantidad": 1}],
                entrega_fecha=manana, zona_id=zona.id,
            )
            check("el bot registró el pedido con su zona", r.get("ok") is True,
                  str(r.get("nota"))[:80])
            pedido_id = r["pedido_id"]

            # Y lo cotiza: así queda una cotización viva en Redis (`cobro:`).
            cobro = await generar_datos_pago(s, TEL, pedido_id)
            check("y lo cotizó (queda una cotización viva en Redis)", cobro.get("ok") is True,
                  str(cobro.get("nota"))[:80])
            check("la cotización está en la caché antes de tocar nada",
                  await rc.get_cache(f"cobro:{TEL}") is not None)

            async with httpx.AsyncClient(transport=transporte, base_url="http://test") as c:

                print("\n1) 🩹 EL FLETE SOBREVIVE A CORREGIR LOS ITEMS")
                # La dueña corrige: eran 2, no 1. Manda SOLO los productos, como hace el panel.
                resp = await c.put(
                    f"/api/pedidos/{pedido_id}/items",
                    json={"items": [{"producto": prod.nombre, "variante_id": var.id,
                                     "cantidad": 2}]},
                    headers=_auth(),
                )
                check("la corrección se guarda (HTTP 200)", resp.status_code == 200,
                      f"{resp.status_code} · {resp.text[:90]}")
                esperado = float(precio * 2 + Decimal("3"))
                cuerpo = resp.json() if resp.status_code == 200 else {}
                check(f"🩹 el total DEVUELTO lleva el envío: ${esperado:g}",
                      abs(float(cuerpo.get("total_usd", 0)) - esperado) < 0.01,
                      f"dio {cuerpo.get('total_usd')} — sin flete daría {float(precio * 2):g}")

                ped = await s.get(Pedido, pedido_id)
                await s.refresh(ped)
                check("y la FILA queda coherente (total = productos + costo_envio)",
                      abs(float(ped.total) - esperado) < 0.01 and float(ped.costo_envio) == 3.0,
                      f"total={ped.total} envio={ped.costo_envio}")

                print("\n2) 🧾 EL DESGLOSE VIAJA AL PANEL")
                # Sin esto la dueña ve "Total $23" con ítems que suman $20 y ninguna línea que
                # explique la diferencia: la fuga del flete sería INAUDITABLE desde el panel.
                check("devuelve el subtotal de productos",
                      abs(float(cuerpo.get("subtotal_productos", -1)) - float(precio * 2)) < 0.01,
                      str(cuerpo.get("subtotal_productos")))
                check("devuelve el costo del envío por separado",
                      abs(float(cuerpo.get("costo_envio", -1)) - 3.0) < 0.01,
                      str(cuerpo.get("costo_envio")))

                print("\n3) 🗑️ LA COTIZACIÓN VIEJA SE TIRA")
                check("🗑️ `cobro:` ya no existe tras cambiar el pedido",
                      await rc.get_cache(f"cobro:{TEL}") is None,
                      "sigue viva: el próximo comprobante se validaría contra el monto VIEJO")

                print("\n4) 📅 UNA FECHA QUE YA NO SIRVE BLOQUEA LA CORRECCIÓN")
                # Se le pone al pedido una fecha imposible (ayer) y se intenta corregir: el
                # endpoint tiene que rebotar con el MISMO calendario que usa el bot, en vez de
                # dejar vendida una fecha que el bot habría rechazado.
                ped.entrega_fecha = (now_utc() - timedelta(days=1)).date()
                await s.commit()
                resp2 = await c.put(
                    f"/api/pedidos/{pedido_id}/items",
                    json={"items": [{"producto": prod.nombre, "variante_id": var.id,
                                     "cantidad": 1}]},
                    headers=_auth(),
                )
                check("📅 se rechaza con 400 y un motivo entendible", resp2.status_code == 400,
                      f"{resp2.status_code} · {resp2.text[:90]}")
                detalle = ""
                if resp2.status_code == 400:
                    try:
                        detalle = json.loads(resp2.text).get("detail", "")
                    except (ValueError, AttributeError):
                        detalle = resp2.text
                check("y el mensaje dice la primera fecha posible",
                      "primera fecha posible" in detalle.lower(), detalle[:90])

                ped3 = await s.get(Pedido, pedido_id)
                await s.refresh(ped3)
                check("el pedido NO se tocó al rechazar (sigue en 2 unidades)",
                      abs(float(ped3.total) - esperado) < 0.01, f"total={ped3.total}")

                print("\n5) 🔁 CAMBIAR EL PRODUCTO DE UN ÍTEM **HACE ALGO**")
                # El <select> del panel solo reescribe `producto` y deja el `variante_id` viejo.
                # Antes ganaba siempre el id: la dueña elegía otro producto, salía "Guardado" sin
                # error, y al recargar reaparecía el de antes. Corregir un producto mal tomado por
                # el bot era IMPOSIBLE desde el panel, y sin ningún aviso.
                ped3.entrega_fecha = (now_utc() + timedelta(days=30)).date()  # fecha usable otra vez
                await s.commit()
                otro = (await s.execute(
                    select(Producto, ProductoVariante)
                    .join(ProductoVariante, ProductoVariante.producto_id == Producto.id)
                    .where(ProductoVariante.precio.is_not(None),
                           ProductoVariante.disponible.is_(True),
                           Producto.disponible.is_(True),
                           Producto.id != prod.id)
                    .order_by(ProductoVariante.precio.desc())
                )).first()
                if otro is None:
                    check("(no hay un segundo producto con precio para probarlo)", False)
                else:
                    p2, v2 = otro
                    n_tam = len((await s.execute(
                        select(ProductoVariante).where(ProductoVariante.producto_id == p2.id)
                    )).scalars().all())
                    # Se manda el nombre NUEVO con el variante_id VIEJO: lo que hace el panel.
                    resp3 = await c.put(
                        f"/api/pedidos/{pedido_id}/items",
                        json={"items": [{"producto": p2.nombre, "variante_id": var.id,
                                         "cantidad": 1}]},
                        headers=_auth(),
                    )
                    if n_tam == 1:
                        # Un solo tamaño ⇒ se puede resolver sin ambigüedad: DEBE cambiar.
                        guardado = (resp3.json().get("items") or [{}])[0].get("producto") \
                            if resp3.status_code == 200 else None
                        check(f"🔁 se guarda «{p2.nombre}», no el producto viejo",
                              guardado == p2.nombre,
                              f"HTTP {resp3.status_code} · quedó «{guardado}» "
                              f"(el viejo era «{prod.nombre}»)")
                    else:
                        # Varios tamaños ⇒ NO se adivina, pero tampoco se ignora en silencio.
                        check(f"🔁 «{p2.nombre}» tiene {n_tam} tamaños ⇒ se RECHAZA, no se ignora",
                              resp3.status_code == 400, f"HTTP {resp3.status_code}")
                        check("y el motivo pide elegir el tamaño",
                              "tamaño" in resp3.text.lower(), resp3.text[:90])
                print("\n6) 💳 LA MÁQUINA DE ESTADOS DE LOS PAGOS (auditoría 2026-08-02)")
                # Un pedido de laboratorio con dos pagos, para reproducir las secuencias reales.
                lab = Pedido(cliente_telefono=TEL, items=[], total=Decimal("50"),
                             estado="esperando_pago")
                s.add(lab); await s.commit(); await s.refresh(lab)
                pa = Pago(pedido_id=lab.id, metodo="pago_movil", monto_usd=Decimal("50"),
                          monto_bs=Decimal("36000"), estado="parcial")
                pb = Pago(pedido_id=lab.id, metodo="pago_movil", monto_usd=Decimal("50"),
                          monto_bs=Decimal("36000"), estado="reportado")
                s.add_all([pa, pb]); await s.commit()
                await s.refresh(pa); await s.refresh(pb)

                # DIN-4: pagó parcial, completó con un 2º comprobante, la dueña confirma el bueno…
                await c.post(f"/api/pagos/{pb.id}/confirmar", headers=_auth())
                # …y luego reabre y confirma el parcial, que el panel le sigue ofreciendo.
                await c.post(f"/api/pagos/{pa.id}/reabrir", headers=_auth())
                dup = await c.post(f"/api/pagos/{pa.id}/confirmar", headers=_auth())
                n_conf = len((await s.execute(select(Pago).where(
                    Pago.pedido_id == lab.id, Pago.estado == "confirmado"))).scalars().all())
                check("💰 no se pueden confirmar DOS pagos del mismo pedido (la venta se contaba doble)",
                      dup.status_code == 409 and n_conf == 1,
                      f"HTTP {dup.status_code} · confirmados={n_conf}")

                # EST-2: dos comprobantes simultáneos ⇒ dos filas 'reportado'. Lo frena la BD
                # (índice parcial de la migración 026), que es el único sitio donde comprobar y
                # escribir son un mismo acto.
                bloqueado = False
                try:
                    s.add(Pago(pedido_id=lab.id, metodo="pago_movil", monto_usd=Decimal("50"),
                               estado="reportado", comprobante_media_id="__panel_dup_a"))
                    await s.commit()
                    s.add(Pago(pedido_id=lab.id, metodo="pago_movil", monto_usd=Decimal("50"),
                               estado="reportado", comprobante_media_id="__panel_dup_b"))
                    await s.commit()
                except Exception:
                    await s.rollback()
                    bloqueado = True
                check("🔒 la BD impide dos pagos 'reportado' en el mismo pedido (carrera de Meta)",
                      bloqueado, "sin el índice ux_pago_reportado_por_pedido entran los dos")

                # DIN-7a: un pedido CANCELADO no resucita porque alguien confirme un pago viejo.
                canc = Pedido(cliente_telefono=TEL, items=[], total=Decimal("30"),
                              estado="cancelado")
                s.add(canc); await s.commit(); await s.refresh(canc)
                pcan = Pago(pedido_id=canc.id, metodo="pago_movil", monto_usd=Decimal("30"),
                            monto_bs=Decimal("21000"), estado="reportado")
                s.add(pcan); await s.commit(); await s.refresh(pcan)
                rc_ = await c.post(f"/api/pagos/{pcan.id}/confirmar", headers=_auth())
                await s.refresh(canc)
                check("🚫 un pedido CANCELADO no salta a 'pagado' al confirmar un pago",
                      rc_.status_code == 409 and canc.estado == "cancelado",
                      f"HTTP {rc_.status_code} · quedó en '{canc.estado}'")

                # DIN-11: el carril de parcial/sobrepago existía SOLO en bolívares.
                div = Pedido(cliente_telefono=TEL, items=[], total=Decimal("18.40"),
                             estado="esperando_pago")
                s.add(div); await s.commit(); await s.refresh(div)
                pdiv = Pago(pedido_id=div.id, metodo="divisas", monto_usd=Decimal("18.40"),
                            monto_bs=None, estado="reportado")
                s.add(pdiv); await s.commit(); await s.refresh(pdiv)
                rz = await c.post(f"/api/pagos/{pdiv.id}/verificar-monto", headers=_auth(),
                                  json={"monto_recibido": 10})
                await s.refresh(pdiv)
                check("💵 'Monto distinto' también funciona con pagos en DIVISAS (Zelle/Binance)",
                      rz.status_code == 200 and pdiv.estado == "parcial",
                      f"HTTP {rz.status_code} · estado='{pdiv.estado}' (antes: 400 seco)")
                check("y devuelve la moneda para que el panel no pida Bs por un pago en $",
                      (rz.json() if rz.status_code == 200 else {}).get("moneda") == "$",
                      str(rz.text[:70]))
        finally:
            await _limpiar(s, zonas=True)

    print()
    if fallos:
        print(f"   🔴 {len(fallos)} FALLO(S): " + " · ".join(fallos[:5]))
        sys.exit(1)
    print("   ✅ EL PANEL NO PUEDE ROMPER EL COBRO: el flete sobrevive, el desglose se ve, "
          "la cotización vieja muere y el calendario manda")


asyncio.run(main())
