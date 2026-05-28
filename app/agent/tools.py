"""Las 5 herramientas del agente.

El número de teléfono del cliente se inyecta server-side (desde el contexto del
webhook) — el LLM nunca lo ve ni lo puede falsificar.
"""
from decimal import Decimal

from sqlalchemy import select

from app.models import Cliente, Configuracion, Pedido, Producto
from app.services.db import get_session_factory

# ─── Schemas que ve el LLM (formato OpenAI / OpenRouter) ──────────────

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "ver_catalogo",
            "description": "Muestra los productos disponibles. Úsala cuando el cliente pregunte qué hay o pida ver el menú. Puedes filtrar por categoría.",
            "parameters": {
                "type": "object",
                "properties": {
                    "categoria": {
                        "type": "string",
                        "enum": ["panaderia", "dulceria", "congelados", "artesanal", "harinas"],
                        "description": "Categoría a mostrar. Omitir para ver todo.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "info_producto",
            "description": "Da el detalle de un producto: ingredientes, precio y presentación. Úsala cuando pregunten por un producto específico.",
            "parameters": {
                "type": "object",
                "properties": {
                    "nombre": {"type": "string", "description": "Nombre del producto a consultar"}
                },
                "required": ["nombre"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "registrar_pedido",
            "description": "Registra el pedido del cliente una vez que confirmó qué quiere. El total se calcula con los precios reales del catálogo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "producto": {"type": "string"},
                                "cantidad": {"type": "integer"},
                            },
                            "required": ["producto", "cantidad"],
                        },
                    },
                    "notas": {"type": "string", "description": "Notas del pedido (opcional)"},
                },
                "required": ["items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "info_negocio",
            "description": "Da información del negocio: ubicación, método de pago y redes. Úsala para dudas de ubicación, cómo pagar, etc.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ver_pedidos_cliente",
            "description": "Muestra los pedidos previos de este cliente. Úsala si pregunta por su pedido o quiere repetir uno.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


# ─── Implementaciones ────────────────────────────────────────────────

async def ver_catalogo(session, telefono, categoria=None):
    stmt = select(Producto).where(Producto.disponible.is_(True))
    if categoria:
        stmt = stmt.where(Producto.categoria == categoria.lower())
    productos = (await session.execute(stmt)).scalars().all()
    if not productos:
        return {"productos": [], "nota": "no hay productos en esa categoría"}
    return {
        "productos": [
            {
                "nombre": p.nombre,
                "categoria": p.categoria,
                "precio_usd": float(p.precio) if p.precio is not None else "a consultar",
                "presentacion": p.presentacion,
            }
            for p in productos
        ]
    }


async def info_producto(session, telefono, nombre):
    prod = (
        await session.execute(
            select(Producto).where(Producto.nombre.ilike(f"%{nombre}%"))
        )
    ).scalars().first()
    if prod is None:
        return {"encontrado": False, "nota": f"no tengo un producto llamado '{nombre}'"}
    return {
        "encontrado": True,
        "nombre": prod.nombre,
        "categoria": prod.categoria,
        "descripcion": prod.descripcion,
        "precio_usd": float(prod.precio) if prod.precio is not None else "a consultar",
        "presentacion": prod.presentacion,
        "disponible": prod.disponible,
    }


async def registrar_pedido(session, telefono, items, notas=None):
    cliente = (
        await session.execute(select(Cliente).where(Cliente.telefono == telefono))
    ).scalar_one_or_none()
    if cliente is None:
        session.add(Cliente(telefono=telefono))

    items_pedido = []
    total = Decimal("0")
    for it in items:
        prod = (
            await session.execute(
                select(Producto).where(
                    Producto.nombre.ilike(f"%{it['producto']}%"),
                    Producto.disponible.is_(True),
                )
            )
        ).scalars().first()
        if prod is None:
            return {"ok": False, "nota": f"no encontré el producto '{it['producto']}'"}
        cantidad = int(it.get("cantidad", 1))
        subtotal = (prod.precio or Decimal("0")) * cantidad
        total += subtotal
        items_pedido.append(
            {
                "producto": prod.nombre,
                "cantidad": cantidad,
                "precio_unitario": float(prod.precio) if prod.precio is not None else None,
                "presentacion": prod.presentacion,
            }
        )

    pedido = Pedido(cliente_telefono=telefono, items=items_pedido, total=total, notas=notas)
    session.add(pedido)
    await session.commit()
    await session.refresh(pedido)
    return {
        "ok": True,
        "pedido_id": pedido.id,
        "items": items_pedido,
        "total_usd": float(total),
        "nota": "pedido registrado; coordina el Pago Móvil con el cliente",
    }


async def info_negocio(session, telefono):
    filas = (await session.execute(select(Configuracion))).scalars().all()
    config = {f.clave: f.valor for f in filas}
    return {
        "nombre": config.get("negocio_nombre", "masvidaconsciente"),
        "ubicacion": config.get("negocio_ubicacion", "Cabudare, Venezuela"),
        "pago": config.get("negocio_pago", "Pago Móvil"),
        "instagram": config.get("negocio_instagram", "@masvidaconsciente"),
    }


async def ver_pedidos_cliente(session, telefono):
    pedidos = (
        await session.execute(
            select(Pedido).where(Pedido.cliente_telefono == telefono).order_by(Pedido.created_at.desc()).limit(5)
        )
    ).scalars().all()
    return {
        "pedidos": [
            {"id": p.id, "estado": p.estado, "items": p.items, "total_usd": float(p.total) if p.total else None}
            for p in pedidos
        ]
    }


_DISPATCH = {
    "ver_catalogo": ver_catalogo,
    "info_producto": info_producto,
    "registrar_pedido": registrar_pedido,
    "info_negocio": info_negocio,
    "ver_pedidos_cliente": ver_pedidos_cliente,
}


async def ejecutar_tool(nombre: str, args: dict, telefono: str, session_factory=None):
    fn = _DISPATCH.get(nombre)
    if fn is None:
        return {"error": f"herramienta desconocida: {nombre}"}
    if session_factory is None:
        session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            return await fn(session, telefono, **args)
        except Exception as e:  # noqa: BLE001 — devolver el error al LLM para que se recupere
            return {"error": str(e)}
