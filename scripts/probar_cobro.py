"""BANCO DE PRUEBAS DEL COBRO — la red que faltaba.

Prueba el camino del DINERO contra el catálogo REAL de la base de datos.
Este banco existe porque el 2026-07-12 se descubrió que `_buscar_producto` cobraba
el producto EQUIVOCADO ("Empanadas" devolvía "Empanadas Keto": $12 en vez de $14) y
el bug llevaba MESES vivo sin que nadie lo viera: el bot HABLABA del producto correcto
y COBRABA otro. La respuesta se veía perfecta; la mentira estaba en la base de datos.

REGLA: correr esto DESPUÉS de cualquier cambio que toque el catálogo, las herramientas
o el cobro. Si algo sale MAL, no se despliega.

Cómo correrlo (dentro del contenedor del bot):
    docker exec -w /app -e PYTHONPATH=/app <contenedor-bot> python scripts/probar_cobro.py

Sale con código 1 si alguna prueba falla (para poder engancharlo a un despliegue).
"""
import asyncio
import sys
from decimal import Decimal

from sqlalchemy import delete, select

from app.agent.tools import _buscar_producto, _precio_efectivo, registrar_pedido
from app.models import Pedido, Producto
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
        print(f"  [MAL] {titulo}   -> {detalle}")


# ─────────────────────────────────────────────────────────────────────────────
# 0) EL CATÁLOGO ES SANO: no puede haber DOS productos con el mismo nombre.
#    Si los hay, es IMPOSIBLE cobrar bien: el bot no tiene forma de distinguirlos
#    y ni siquiera puede preguntar ("¿Kombucha o Kombucha?"). Se arregla en el
#    panel, renombrando (ej. "Kombucha 350ml" y "Kombucha 700ml").
# ─────────────────────────────────────────────────────────────────────────────
async def probar_catalogo_sano(session) -> None:
    print("\n0) El catálogo es sano (sin nombres repetidos)")
    prods = (await session.execute(select(Producto).order_by(Producto.id))).scalars().all()
    check(bool(prods), "hay productos en el catálogo", "el catálogo está VACÍO")
    vistos: dict[str, int] = {}
    for p in prods:
        clave = " ".join((p.nombre or "").lower().split())
        if clave in vistos:
            check(
                False,
                f"'{p.nombre}' no está repetido",
                f"los ids {vistos[clave]} y {p.id} se llaman IGUAL (${p.precio}) "
                f"-> RENÓMBRALOS EN EL PANEL o el bot no puede cobrar bien",
            )
        else:
            vistos[clave] = p.id
    if len(vistos) == len(prods):
        check(True, f"los {len(prods)} productos tienen nombres únicos")


# ─────────────────────────────────────────────────────────────────────────────
# 1) INVARIANTE UNIVERSAL: pedir un producto por su NOMBRE EXACTO devuelve ESE
#    producto. Recorre TODO el catálogo, así que se adapta solo cuando la dueña
#    agrega productos. Esta sola prueba habría cazado el bug de las Empanadas.
# ─────────────────────────────────────────────────────────────────────────────
async def probar_nombre_exacto(session) -> None:
    print("\n1) Cada producto del catálogo, pedido por su NOMBRE EXACTO")
    prods = (await session.execute(select(Producto).order_by(Producto.id))).scalars().all()
    nombres = [" ".join((p.nombre or "").lower().split()) for p in prods]
    for p in prods:
        clave = " ".join((p.nombre or "").lower().split())
        if nombres.count(clave) > 1:
            continue  # nombre repetido: ya lo reportó la prueba 0, no se puede exigir aquí
        hallado = await _buscar_producto(session, p.nombre, solo_disponibles=False)
        bien = hallado is not None and hallado.id == p.id
        check(
            bien,
            f"'{p.nombre}' -> {p.nombre}",
            f"devolvió '{hallado.nombre}' (${hallado.precio})" if hallado else "no encontró nada",
        )


# ─────────────────────────────────────────────────────────────────────────────
# 2) Casos REALES que fallaron (o que casi hacen cobrar mal)
# ─────────────────────────────────────────────────────────────────────────────
async def probar_casos_reales(session) -> None:
    print("\n2) Los casos que de verdad pasaron con clientes")
    casos = [
        # (lo que pide el bot, nombre esperado o None, por qué importa)
        ("empanada", "Empanadas", "singular: NUNCA puede caer en las Keto"),
        ("empanadas de platano", "Empanadas", "por ingrediente: las Keto son de almendra"),
        ("galetas", "Galletas New York", "con error de tipeo"),
        ("pan", None, "AMBIGUO (varios panes): debe PREGUNTAR, no adivinar el más caro"),
        ("Torta de unicornio", None, "no existe: debe RECHAZAR, no aproximar"),
    ]
    for pedido, esperado, por_que in casos:
        p = await _buscar_producto(session, pedido, solo_disponibles=True)
        got = p.nombre if p else None
        check(got == esperado, f"'{pedido}' -> {esperado or 'pregunta/rechaza'}  ({por_que})", f"devolvió '{got}'")


# ─────────────────────────────────────────────────────────────────────────────
# 3) EL DINERO de punta a punta: registrar un pedido y verificar EN LA BASE DE
#    DATOS que el producto y el total son los correctos. Los precios se LEEN del
#    catálogo (nunca se escriben a mano) para que la prueba siga valiendo cuando
#    la dueña cambie un precio.
# ─────────────────────────────────────────────────────────────────────────────
async def probar_dinero(session) -> None:
    print("\n3) El DINERO: se registra un pedido y se verifica en la base de datos")
    prod = (
        await session.execute(
            select(Producto).where(Producto.disponible.is_(True)).order_by(Producto.id).limit(1)
        )
    ).scalar_one_or_none()
    if prod is None or prod.precio is None:
        check(False, "hay un producto con precio para probar el cobro", "no hay ninguno")
        return

    cantidad = 2
    esperado = Decimal(str(prod.precio)) * cantidad
    res = await registrar_pedido(session, TELEFONO, [{"producto": prod.nombre, "cantidad": cantidad}])
    check(res.get("ok") is not False,
          f"registrar_pedido acepta '{prod.nombre}' x{cantidad}", str(res)[:120])

    guardado = (
        await session.execute(
            select(Pedido).where(Pedido.cliente_telefono == TELEFONO).order_by(Pedido.id.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if guardado is None:
        check(False, "el pedido quedó guardado en la base de datos", "no se guardó nada")
        return

    item = (guardado.items or [{}])[0]
    check(item.get("producto") == prod.nombre,
          f"grabó el producto CORRECTO ('{prod.nombre}')", f"grabó '{item.get('producto')}'")
    check(Decimal(str(guardado.total)) == esperado,
          f"el total es {cantidad} x ${prod.precio} = ${esperado}", f"grabó ${guardado.total}")


# ─────────────────────────────────────────────────────────────────────────────
# 4) Producto inventado: el código RECHAZA y le devuelve la lista real al agente
# ─────────────────────────────────────────────────────────────────────────────
async def probar_rechazo(session) -> None:
    print("\n4) Si el bot pide un producto que NO existe")
    res = await registrar_pedido(session, TELEFONO, [{"producto": "Pizza de unicornio", "cantidad": 1}])
    check(res.get("ok") is False, "lo RECHAZA (no aproxima ni inventa)", str(res)[:120])
    check(bool(res.get("productos_validos")),
          "le devuelve la lista REAL de productos para que corrija", "no devolvió productos_validos")


# ─────────────────────────────────────────────────────────────────────────────
# 5) PRECIO DEL DÍA: los productos cuyo precio CAMBIA (Tortas keto, torta baja en
#    carbohidratos, Premezclas) están sin precio A PROPÓSITO. Si la dueña no dio el
#    de HOY, el bot NO puede cobrarlos: antes se colaban como $0 (pedido gratis).
# ─────────────────────────────────────────────────────────────────────────────
async def probar_precio_del_dia(session) -> None:
    print("\n5) Productos de PRECIO DEL DÍA (su precio cambia; lo da la dueña)")
    variables = (
        await session.execute(
            select(Producto).where(Producto.precio.is_(None)).order_by(Producto.id)
        )
    ).scalars().all()
    if not variables:
        check(True, "no hay productos de precio variable (nada que probar)")
        return
    for p in variables:
        efectivo = await _precio_efectivo(session, p)
        if efectivo is not None:
            check(True, f"'{p.nombre}': la dueña ya dio el precio de HOY (${efectivo})")
            continue
        res = await registrar_pedido(session, TELEFONO, [{"producto": p.nombre, "cantidad": 1}])
        check(
            res.get("ok") is False,
            f"'{p.nombre}' sin precio de hoy -> NO se puede cobrar (antes salía en $0)",
            str(res)[:110],
        )


# ─────────────────────────────────────────────────────────────────────────────
# 6) LA CANTIDAD ES DINERO (precio × CANTIDAD). Con cantidad 0 el ítem entraba
#    en $0 y el pedido podía cerrarse GRATIS. El schema pide entero >= 1, pero
#    el modelo puede mandar cualquier cosa: el código tiene que rechazarla.
# ─────────────────────────────────────────────────────────────────────────────
async def probar_cantidad(session) -> None:
    print("\n6) La CANTIDAD también es dinero (nada de pedidos en $0)")
    prod = (
        await session.execute(
            select(Producto).where(Producto.precio.is_not(None)).order_by(Producto.id)
        )
    ).scalars().first()
    if prod is None:
        check(False, "hay un producto con precio para probar", "no hay ninguno")
        return
    for mala in (0, -3, "dos", None):
        res = await registrar_pedido(session, TELEFONO, [{"producto": prod.nombre, "cantidad": mala}])
        check(res.get("ok") is False, f"cantidad {mala!r} -> la RECHAZA", str(res)[:110])
    res = await registrar_pedido(session, TELEFONO, [{"producto": prod.nombre, "cantidad": 2}])
    check(res.get("ok") is not False, "cantidad 2 -> sigue funcionando normal", str(res)[:110])


# ─────────────────────────────────────────────────────────────────────────────
# 7) EL PAGO CUADRA CON EL PEDIDO. El monto del cobro se guardaba en una caché
#    por TELÉFONO: si el cliente cambiaba de pedido (Kombucha de $4 -> la de $7),
#    el comprobante se grababa con el monto VIEJO. Un pago de $4 sobre $7.
# ─────────────────────────────────────────────────────────────────────────────
async def probar_pago_cuadra(session) -> None:
    print("\n7) El PAGO registrado cuadra con el pedido (no con uno viejo)")
    from app.agent.tools import generar_datos_pago, registrar_comprobante
    from app.models import Pago

    prods = (
        await session.execute(
            select(Producto).where(Producto.precio.is_not(None)).order_by(Producto.precio)
        )
    ).scalars().all()
    if len(prods) < 2:
        check(True, "no hay dos productos de precios distintos (nada que probar)")
        return
    barato, caro = prods[0], prods[-1]
    if barato.precio == caro.precio:
        check(True, "todos valen igual (nada que probar)")
        return

    # 1º pedido (barato) y su cobro -> deja el monto viejo en la caché.
    r1 = await registrar_pedido(session, TELEFONO, [{"producto": barato.nombre, "cantidad": 1}])
    await generar_datos_pago(session, TELEFONO, r1.get("pedido_id"))
    # 2º pedido (caro): el cliente se cambió. Se cobra ESTE.
    r2 = await registrar_pedido(session, TELEFONO, [{"producto": caro.nombre, "cantidad": 1}])
    await generar_datos_pago(session, TELEFONO, r2.get("pedido_id"))
    await registrar_comprobante(session, TELEFONO, referencia="PRUEBA-CUADRE")

    pedido2 = await session.get(Pedido, r2.get("pedido_id"))
    pago = (
        await session.execute(select(Pago).where(Pago.pedido_id == pedido2.id))
    ).scalars().first()
    check(pago is not None, "el comprobante quedó registrado", "no se creó el pago")
    if pago is not None:
        check(
            Decimal(str(pago.monto_usd)) == Decimal(str(pedido2.total)),
            f"el pago dice ${pago.monto_usd} y el pedido ${pedido2.total} -> CUADRAN",
            f"el pago quedó en ${pago.monto_usd} sobre una venta de ${pedido2.total} "
            f"(se coló el monto del pedido anterior, ${barato.precio})",
        )


# ─────────────────────────────────────────────────────────────────────────────
# 8) UN PEDIDO YA PAGADO NO SE RE-COBRA (antes 'resucitaba' a esperando_pago y
#    se le podía pegar un segundo comprobante encima).
# ─────────────────────────────────────────────────────────────────────────────
async def probar_no_recobro(session) -> None:
    print("\n8) Un pedido ya PAGADO no se cobra otra vez")
    from app.agent.tools import generar_datos_pago

    prod = (
        await session.execute(
            select(Producto).where(Producto.precio.is_not(None)).order_by(Producto.id)
        )
    ).scalars().first()
    r = await registrar_pedido(session, TELEFONO, [{"producto": prod.nombre, "cantidad": 1}])
    pedido = await session.get(Pedido, r.get("pedido_id"))
    pedido.estado = "pagado"
    await session.commit()

    res = await generar_datos_pago(session, TELEFONO, pedido.id)
    check(res.get("ok") is False, "pidiéndolo por su id -> lo RECHAZA", str(res)[:110])
    await session.refresh(pedido)
    check(pedido.estado == "pagado", "el pedido SIGUE 'pagado' (no resucitó)", f"quedó en '{pedido.estado}'")

    # Sin decir el id ("dame los datos otra vez"): puede agarrar OTRO pedido abierto
    # (eso está bien), pero JAMÁS el que ya está pagado.
    res2 = await generar_datos_pago(session, TELEFONO, None)
    check(
        res2.get("pedido_id") != pedido.id,
        "sin decir el id -> NUNCA agarra el pedido pagado",
        f"agarró el pedido pagado ({pedido.id}) y lo revivió",
    )
    await session.refresh(pedido)
    check(pedido.estado == "pagado", "y el pagado sigue intacto", f"quedó en '{pedido.estado}'")


# ─────────────────────────────────────────────────────────────────────────────
# 9) EL PRECIO DEL DÍA VIVE EN EL DÍA DE VENEZUELA (no en el del servidor, que
#    corre en UTC). A las 8 pm de Cabudare el reloj UTC ya es mañana: el precio
#    cargado en la mañana desaparecía. Y el de AYER no se reutiliza JAMÁS.
#    Se prueba dentro de una transacción que se DESHACE (no toca datos reales).
# ─────────────────────────────────────────────────────────────────────────────
async def probar_dia_venezuela(factory) -> None:
    print("\n9) El precio del día usa el día de VENEZUELA (y el de ayer nunca se reutiliza)")
    from datetime import datetime, timedelta, timezone

    from app.models import PrecioDia, hoy_venezuela

    esperado = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    check(hoy_venezuela() == esperado, f"hoy en Venezuela = {esperado} (el servidor va en UTC)")

    async with factory() as s:
        prod = (
            await s.execute(select(Producto).where(Producto.precio.is_(None)).order_by(Producto.id))
        ).scalars().first()
        if prod is None:
            check(True, "no hay productos de precio del día (nada que probar)")
            return
        ya = (
            await s.execute(
                select(PrecioDia).where(
                    PrecioDia.producto_id == prod.id, PrecioDia.fecha == hoy_venezuela()
                )
            )
        ).scalars().first()
        if ya is not None:
            check(True, f"'{prod.nombre}' ya tiene precio de hoy (${ya.precio}); no se toca")
            return
        # Ensayo con red: se escribe y se DESHACE (nunca se confirma).
        s.add(PrecioDia(producto_id=prod.id, precio=Decimal("42.00"), fecha=hoy_venezuela()))
        await s.flush()
        p_hoy = await _precio_efectivo(s, prod)
        check(p_hoy == Decimal("42.00"), f"con precio de HOY -> lo usa (${p_hoy})", f"dio {p_hoy}")
        await s.rollback()

    async with factory() as s:
        prod = (
            await s.execute(select(Producto).where(Producto.precio.is_(None)).order_by(Producto.id))
        ).scalars().first()
        s.add(
            PrecioDia(
                producto_id=prod.id,
                precio=Decimal("99.00"),
                fecha=hoy_venezuela() - timedelta(days=1),  # el de AYER
            )
        )
        await s.flush()
        p_ayer = await _precio_efectivo(s, prod)
        check(p_ayer is None, "con el precio de AYER -> NO lo usa (no cobra)", f"usó ${p_ayer}")
        await s.rollback()


async def limpiar(session) -> None:
    """Borra TODO lo que dejó la prueba: el panel y los reportes quedan limpios."""
    from app.models import Cliente, Mensaje, Pago

    ids = (
        await session.execute(select(Pedido.id).where(Pedido.cliente_telefono == TELEFONO))
    ).scalars().all()
    if ids:
        await session.execute(delete(Pago).where(Pago.pedido_id.in_(ids)))
    await session.execute(delete(Pedido).where(Pedido.cliente_telefono == TELEFONO))
    await session.execute(delete(Mensaje).where(Mensaje.cliente_telefono == TELEFONO))
    await session.execute(delete(Cliente).where(Cliente.telefono == TELEFONO))
    await session.commit()


async def main() -> int:
    print("=" * 68)
    print("  BANCO DE PRUEBAS DEL COBRO — másvida")
    print("=" * 68)
    factory = get_session_factory()
    async with factory() as session:
        try:
            await limpiar(session)  # arranca de cero
            await probar_catalogo_sano(session)
            await probar_nombre_exacto(session)
            await probar_casos_reales(session)
            await probar_dinero(session)
            await probar_rechazo(session)
            await probar_precio_del_dia(session)
            await probar_cantidad(session)
            await probar_pago_cuadra(session)
            await probar_no_recobro(session)
        finally:
            await limpiar(session)  # no deja basura en el panel

    await probar_dia_venezuela(factory)  # usa sus propias transacciones (con ROLLBACK)

    print("\n" + "=" * 68)
    if _mal:
        print(f"  🔴 {len(_mal)} PRUEBA(S) MAL — NO DESPLEGAR:")
        for t in _mal:
            print(f"     - {t}")
        print("=" * 68)
        return 1
    print(f"  ✅ TODO BIEN — {_ok} pruebas pasaron. El cobro está sano.")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
