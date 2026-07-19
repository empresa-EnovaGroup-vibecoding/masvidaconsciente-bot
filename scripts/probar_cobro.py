"""BANCO DE PRUEBAS DEL COBRO — el camino del DINERO, contra el catálogo REAL.

Existe porque el 2026-07-12 se descubrió que el bot **hablaba de un producto y cobraba otro**:
la respuesta se veía perfecta y la mentira estaba en la base de datos. Desde entonces,
**el cobro se verifica en la BD (`SELECT items, total FROM pedidos`), nunca en lo que dice el bot.**

v2 (2026-07-13) — reescrito para el "CÓDIGO DE BARRAS": el pedido ya no se empareja por un
nombre en texto libre, sino por `variante_id`, un número de una lista CERRADA que el propio
código le inyecta al modelo. El modelo NO PUEDE escribir un id que no le dimos.

REGLA: correr esto DESPUÉS de cualquier cambio que toque el catálogo, las herramientas o el
cobro. **Si algo sale MAL, no se despliega.**

    docker exec -w /app -e PYTHONPATH=/app <contenedor-bot> python scripts/probar_cobro.py
"""
import asyncio
import sys
from datetime import UTC, timedelta
from decimal import Decimal

from sqlalchemy import delete, select, text

from app.agent.tools import registrar_pedido
from app.models import Pedido, PrecioDia, Producto, ProductoVariante, hoy_venezuela
from app.services.db import get_session_factory

TELEFONO = "__simulador__"  # el panel ya lo excluye de la lista de clientes
_ok = 0
_mal: list[str] = []


def check(bien: bool, titulo: str, detalle: str = "") -> None:
    global _ok
    if bien:
        _ok += 1
        print(f"  [OK ] {titulo}")
    else:
        _mal.append(titulo)
        print(f"  [MAL] {titulo}" + (f"  → {detalle}" if detalle else ""))


def fecha_entrega_valida() -> str:
    """Una fecha en que SÍ se entrega (mañana; si cae domingo, pasado)."""
    f = hoy_venezuela() + timedelta(days=1)
    if f.weekday() == 6:
        f += timedelta(days=1)
    return f.isoformat()


async def limpiar(session) -> None:
    await session.execute(delete(Pedido).where(Pedido.cliente_telefono == TELEFONO))
    await session.commit()


async def pedir(session, variante_id, cantidad=1, nombre="x"):
    """Registra un pedido y devuelve (respuesta, el pedido TAL COMO QUEDÓ EN LA BD)."""
    await limpiar(session)
    r = await registrar_pedido(
        session,
        TELEFONO,
        items=[{"variante_id": variante_id, "producto": nombre, "cantidad": cantidad}],
        entrega=f"{fecha_entrega_valida()} delivery",
    )
    ped = (
        await session.execute(
            select(Pedido)
            .where(Pedido.cliente_telefono == TELEFONO)
            .order_by(Pedido.created_at.desc())
        )
    ).scalars().first()
    return r, ped


async def variantes_de(session, nombre_like):
    prod = (
        await session.execute(select(Producto).where(Producto.nombre.ilike(f"%{nombre_like}%")))
    ).scalars().first()
    if prod is None:
        return None, []
    vs = (
        await session.execute(
            select(ProductoVariante)
            .where(ProductoVariante.producto_id == prod.id)
            .order_by(ProductoVariante.orden)
        )
    ).scalars().all()
    return prod, vs


# 🔴 ESTE BANCO ESCRIBE EN LA BASE (pone precios del día de mentira para probar el cobro).
#
# El 2026-07-14 se corrió contra PRODUCCIÓN, reventó a mitad… y **dejó vivo un precio FALSO**:
# "Tortas keto 250g = $25", cargado como EL PRECIO DE HOY. El bot se lo habría dicho a un cliente
# real. Se borró a mano — pero no puede volver a depender de que la prueba termine bien.
#
# Ahora la limpieza corre SIEMPRE (`finally`) y con BISTURÍ: se anota qué filas HABÍA antes y solo
# se borran las que creó el banco. (Borrar "todos los precios de hoy" habría sido peor: le habría
# borrado a la dueña los precios REALES que acabara de cargar.)
_PRECIOS_QUE_YA_ESTABAN: set[int] = set()


async def _anotar_precios_previos() -> None:
    factory = get_session_factory()
    async with factory() as s:
        _PRECIOS_QUE_YA_ESTABAN.update(
            (await s.execute(select(PrecioDia.id))).scalars().all()
        )


async def _borrar_precios_de_prueba() -> None:
    factory = get_session_factory()
    try:
        async with factory() as s:
            if _PRECIOS_QUE_YA_ESTABAN:
                stmt = delete(PrecioDia).where(PrecioDia.id.not_in(_PRECIOS_QUE_YA_ESTABAN))
            else:
                stmt = delete(PrecioDia)  # no había ninguno antes ⇒ todo lo de ahora es del banco
            borradas = (await s.execute(stmt)).rowcount
            await s.commit()
        if borradas:
            print(f"   🧹 limpiado: {borradas} precio(s) del día de prueba (los reales, intactos)")
    except Exception as exc:  # noqa: BLE001
        print(f"   ⚠️  NO se pudo limpiar el precio del día de prueba: {exc}")


async def main() -> None:
    await _anotar_precios_previos()
    try:
        await _correr()
    finally:
        # Pase lo que pase (aunque el banco reviente a mitad), no queda un precio de mentira vivo.
        await _borrar_precios_de_prueba()


async def _correr() -> None:
    factory = get_session_factory()
    async with factory() as session:

        # ═══════════════════════════════════════════════════════════════════════
        print("\n1) EL CATÁLOGO ESTÁ SANO (si esto falla, todo lo demás miente)")

        repetidos = (
            await session.execute(text(
                "SELECT count(*) FROM (SELECT nombre FROM productos "
                "GROUP BY nombre HAVING count(*) > 1) x"
            ))
        ).scalar()
        check(repetidos == 0,
              "NINGÚN nombre de producto repetido (era la fuga de $3 de la Kombucha)",
              f"hay {repetidos} nombre(s) repetido(s)")

        sin_tamano = (
            await session.execute(text(
                "SELECT count(*) FROM productos p WHERE NOT EXISTS "
                "(SELECT 1 FROM producto_variantes v WHERE v.producto_id = p.id)"
            ))
        ).scalar()
        check(sin_tamano == 0,
              "TODOS los productos tienen al menos un tamaño (si no, son invendibles)",
              f"{sin_tamano} producto(s) sin tamaño")

        # ═══════════════════════════════════════════════════════════════════════
        print("\n2) 🔴 EL CÓDIGO DE BARRAS: cada tamaño cobra SU precio (verificado en la BD)")

        prod, vs = await variantes_de(session, "kombucha")
        check(prod is not None and len(vs) == 2,
              "la Kombucha es UN producto con DOS tamaños",
              f"{len(vs)} tamaño(s)")

        if len(vs) == 2:
            chica, grande = vs[0], vs[1]
            for v in (chica, grande):
                r, ped = await pedir(session, v.id, 1, "Kombucha")
                esperado = float(v.precio)
                cobrado = float(ped.total) if ped and ped.total is not None else None
                item = (ped.items or [{}])[0] if ped else {}
                check(r.get("ok") and cobrado == esperado,
                      f"Kombucha {v.presentacion} → cobra ${esperado} (en la BD, no en la charla)",
                      f"cobró {cobrado}")
                check(item.get("variante_id") == v.id,
                      f"el pedido guarda el código de barras del tamaño {v.presentacion}",
                      f"guardó {item.get('variante_id')}")
                check(item.get("presentacion") == v.presentacion,
                      f"el recibo dice el tamaño ({v.presentacion}) — sin eso se despacha el que no es",
                      f"dice {item.get('presentacion')!r}")

            # El caso REAL: dos tamaños, dos precios. Antes SIEMPRE cobraba el del primero.
            _, ped_chica = await pedir(session, chica.id, 1)
            _, ped_grande = await pedir(session, grande.id, 1)
            check(float(ped_chica.total) != float(ped_grande.total),
                  "🔴 los dos tamaños NO se confunden (antes los dos cobraban $4)",
                  f"{ped_chica.total} vs {ped_grande.total}")

            _, ped2 = await pedir(session, grande.id, 2)
            check(float(ped2.total) == float(grande.precio) * 2,
                  f"Kombucha {grande.presentacion} x2 = ${float(grande.precio) * 2}",
                  f"cobró {ped2.total}")

        # ═══════════════════════════════════════════════════════════════════════
        print("\n3) LO QUE SE RECHAZA (jamás cobrar a la suerte)")

        r, ped = await pedir(session, 999999, 1)
        check(not r.get("ok") and ped is None,
              "un id que NO existe → RECHAZA (y no crea pedido)")
        check(bool(r.get("opciones_validas")),
              "y le devuelve la LISTA REAL para que corrija (no aproxima)")

        if len(vs) == 2:
            for mala in (0, -1, "dos", None):
                r, ped = await pedir(session, vs[0].id, mala)
                check(not r.get("ok") and ped is None,
                      f"cantidad {mala!r} → RECHAZA (con 0 el pedido salía GRATIS)")

            grande.disponible = False
            await session.commit()
            r, ped = await pedir(session, grande.id, 1)
            check(not r.get("ok") and ped is None,
                  "un TAMAÑO agotado → RECHAZA (no se vende lo que no hay)")
            grande.disponible = True
            await session.commit()

            prod.disponible = False
            await session.commit()
            r, ped = await pedir(session, grande.id, 1)
            check(not r.get("ok") and ped is None,
                  "el PRODUCTO agotado apaga TODOS sus tamaños → RECHAZA")
            prod.disponible = True
            await session.commit()

        # ═══════════════════════════════════════════════════════════════════════
        print("\n4) 🔴 EL PRECIO DEL DÍA, POR TAMAÑO (lo pidió Maired)")

        torta, tvs = await variantes_de(session, "Tortas keto")
        check(torta is not None and len(tvs) >= 2,
              "las Tortas keto tienen sus tamaños separados (antes: '250g / 500g / 1kg' en un texto)",
              f"{len(tvs)} tamaño(s)")

        if len(tvs) >= 2:
            await session.execute(
                delete(PrecioDia).where(PrecioDia.variante_id.in_([v.id for v in tvs]))
            )
            await session.commit()

            r, ped = await pedir(session, tvs[0].id, 1, "Tortas keto")
            check(not r.get("ok") and r.get("necesita_ayuda") and ped is None,
                  "sin precio de HOY → RECHAZA y pide ayuda (jamás inventa ni usa el de ayer)")

            session.add(PrecioDia(producto_id=torta.id, variante_id=tvs[0].id,
                                  precio=Decimal("25.00"), fecha=hoy_venezuela()))
            await session.commit()

            r, ped = await pedir(session, tvs[0].id, 1, "Tortas keto")
            check(r.get("ok") and ped and float(ped.total) == 25.0,
                  f"con el precio de hoy del {tvs[0].presentacion} → cobra $25",
                  f"cobró {ped.total if ped else None}")

            r, ped = await pedir(session, tvs[1].id, 1, "Tortas keto")
            check(not r.get("ok") and ped is None,
                  f"🔴 pero el {tvs[1].presentacion} SIGUE sin precio → RECHAZA "
                  "(el precio de un tamaño NO vale para otro)")

            session.add(PrecioDia(producto_id=torta.id, variante_id=tvs[1].id,
                                  precio=Decimal("45.00"), fecha=hoy_venezuela()))
            await session.commit()
            r, ped = await pedir(session, tvs[1].id, 1, "Tortas keto")
            check(r.get("ok") and ped and float(ped.total) == 45.0,
                  "🔴 DOS precios del día distintos el MISMO día (el índice viejo lo IMPEDÍA)",
                  f"cobró {ped.total if ped else None}")

            await session.execute(
                delete(PrecioDia).where(PrecioDia.variante_id.in_([v.id for v in tvs]))
            )
            session.add(PrecioDia(producto_id=torta.id, variante_id=tvs[0].id,
                                  precio=Decimal("25.00"),
                                  fecha=hoy_venezuela() - timedelta(days=1)))
            await session.commit()
            r, ped = await pedir(session, tvs[0].id, 1, "Tortas keto")
            check(not r.get("ok") and ped is None,
                  "el precio de AYER NO se reutiliza → RECHAZA")

            await session.execute(
                delete(PrecioDia).where(PrecioDia.variante_id.in_([v.id for v in tvs]))
            )
            await session.commit()

        # ═══════════════════════════════════════════════════════════════════════
        print("\n5) 🔴 UNA SOLA FUENTE DE VERDAD DEL PRECIO")

        if len(vs) == 2:
            antes = prod.precio
            prod.precio = Decimal("999.00")   # el campo viejo, el del PRODUCTO
            await session.commit()
            _, ped = await pedir(session, vs[0].id, 1)
            check(ped and float(ped.total) == float(vs[0].precio),
                  "el precio del PRODUCTO ya NO manda: se cobra el del TAMAÑO (si no, ella lo "
                  "subiría en un sitio y el bot cobraría el otro)",
                  f"cobró {ped.total if ped else None}, debía cobrar {vs[0].precio}")
            prod.precio = antes
            await session.commit()

        # ═══════════════════════════════════════════════════════════════════════
        print("\n6) PEDIDO QUE YA ESPERA PAGO: repetir lo mismo NO lo reabre ni recalcula")
        if len(vs) == 2:
            respuesta, pedido = await pedir(session, vs[0].id, 1)
            pedido.estado = "esperando_pago"
            await session.commit()
            total_congelado = pedido.total
            repetido = await registrar_pedido(
                session,
                TELEFONO,
                items=[{"variante_id": vs[0].id, "cantidad": 1}],
            )
            await session.refresh(pedido)
            check(
                repetido.get("ok") and "SIN CAMBIOS" in str(repetido.get("nota")),
                "la repetición se reconoce como el MISMO pedido",
                str(repetido),
            )
            check(
                pedido.estado == "esperando_pago" and pedido.total == total_congelado,
                "mantiene estado y precio congelados (no vuelve a pendiente)",
                f"estado={pedido.estado} total={pedido.total}",
            )
            cambiado = await registrar_pedido(
                session,
                TELEFONO,
                items=[{"variante_id": vs[0].id, "cantidad": 2}],
            )
            await session.refresh(pedido)
            check(
                cambiado.get("ok") and pedido.estado == "pendiente",
                "un cambio REAL de cantidad sí reabre y recalcula",
                f"estado={pedido.estado}",
            )

        print("\n7) EL RELOJ DE VENEZUELA (el precio del día no se pierde a las 8 pm)")
        from datetime import datetime
        esperado = (datetime.now(UTC) - timedelta(hours=4)).date()
        check(hoy_venezuela() == esperado,
              "el precio del día usa el reloj de Venezuela (UTC-4), no el del servidor")

        await limpiar(session)

    print()
    print("=" * 68)
    if _mal:
        print(f"  🔴 {len(_mal)} PRUEBA(S) MAL — NO DESPLEGAR:")
        for t in _mal:
            print(f"     - {t}")
        print("=" * 68)
        sys.exit(1)
    print(f"  ✅ {_ok} PRUEBAS EN VERDE — el cobro está blindado por el CÓDIGO DE BARRAS")
    print("=" * 68)


asyncio.run(main())
