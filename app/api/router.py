"""API REST que alimenta el dashboard. Todo protegido con login (JWT),
excepto el propio login."""
from datetime import date, datetime, time, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.security import (
    crear_token,
    usuario_actual,
    verify_password,
)
from app.models import Cliente, Mensaje, Pedido, Producto
from app.services.db import get_session_factory

router = APIRouter(prefix="/api", tags=["dashboard"])


# ─── Esquemas ────────────────────────────────────────────────────────

class LoginIn(BaseModel):
    email: str
    password: str


class ProductoIn(BaseModel):
    nombre: str
    categoria: str | None = None
    descripcion: str | None = None
    precio: float | None = None
    presentacion: str | None = None
    disponible: bool = True


class EstadoIn(BaseModel):
    estado: str


# ─── Login ───────────────────────────────────────────────────────────

@router.post("/login")
async def login(datos: LoginIn):
    from app.models import Usuario

    factory = get_session_factory()
    async with factory() as session:
        usuario = (
            await session.execute(select(Usuario).where(Usuario.email == datos.email))
        ).scalar_one_or_none()
        if usuario is None or not verify_password(datos.password, usuario.password_hash):
            raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")
    return {"access_token": crear_token(datos.email), "token_type": "bearer"}


# ─── Métricas del dashboard ──────────────────────────────────────────

@router.get("/metricas")
async def metricas(_: str = Depends(usuario_actual)):
    hoy_inicio = datetime.combine(date.today(), time.min, tzinfo=timezone.utc)
    factory = get_session_factory()
    async with factory() as session:
        pedidos_hoy = (
            await session.execute(
                select(func.count()).select_from(Pedido).where(Pedido.created_at >= hoy_inicio)
            )
        ).scalar() or 0
        ventas_hoy = (
            await session.execute(
                select(func.coalesce(func.sum(Pedido.total), 0)).where(
                    Pedido.created_at >= hoy_inicio,
                    Pedido.estado != "cancelado",
                )
            )
        ).scalar() or Decimal("0")
        clientes_total = (
            await session.execute(select(func.count()).select_from(Cliente))
        ).scalar() or 0
        pendientes = (
            await session.execute(
                select(func.count()).select_from(Pedido).where(Pedido.estado == "pendiente")
            )
        ).scalar() or 0
    return {
        "pedidos_hoy": pedidos_hoy,
        "ventas_hoy_usd": float(ventas_hoy),
        "clientes_total": clientes_total,
        "pedidos_pendientes": pendientes,
    }


# ─── Pedidos ─────────────────────────────────────────────────────────

@router.get("/pedidos")
async def listar_pedidos(_: str = Depends(usuario_actual)):
    factory = get_session_factory()
    async with factory() as session:
        pedidos = (
            await session.execute(select(Pedido).order_by(Pedido.created_at.desc()).limit(100))
        ).scalars().all()
    return [
        {
            "id": p.id,
            "cliente": p.cliente_telefono,
            "estado": p.estado,
            "items": p.items,
            "total_usd": float(p.total) if p.total else 0,
            "notas": p.notas,
            "fecha": p.created_at.isoformat(),
        }
        for p in pedidos
    ]


@router.patch("/pedidos/{pedido_id}")
async def cambiar_estado(pedido_id: int, datos: EstadoIn, _: str = Depends(usuario_actual)):
    validos = {"pendiente", "confirmado", "preparando", "entregado", "cancelado"}
    if datos.estado not in validos:
        raise HTTPException(status_code=400, detail="Estado inválido")
    factory = get_session_factory()
    async with factory() as session:
        pedido = await session.get(Pedido, pedido_id)
        if pedido is None:
            raise HTTPException(status_code=404, detail="Pedido no encontrado")
        pedido.estado = datos.estado
        await session.commit()
    return {"ok": True}


# ─── Productos (catálogo) ────────────────────────────────────────────

@router.get("/productos")
async def listar_productos(_: str = Depends(usuario_actual)):
    factory = get_session_factory()
    async with factory() as session:
        productos = (
            await session.execute(select(Producto).order_by(Producto.categoria, Producto.nombre))
        ).scalars().all()
    return [
        {
            "id": p.id,
            "nombre": p.nombre,
            "categoria": p.categoria,
            "descripcion": p.descripcion,
            "precio": float(p.precio) if p.precio is not None else None,
            "presentacion": p.presentacion,
            "disponible": p.disponible,
        }
        for p in productos
    ]


@router.post("/productos")
async def crear_producto(datos: ProductoIn, _: str = Depends(usuario_actual)):
    factory = get_session_factory()
    async with factory() as session:
        prod = Producto(
            nombre=datos.nombre,
            categoria=datos.categoria,
            descripcion=datos.descripcion,
            precio=Decimal(str(datos.precio)) if datos.precio is not None else None,
            presentacion=datos.presentacion,
            disponible=datos.disponible,
        )
        session.add(prod)
        await session.commit()
        await session.refresh(prod)
    return {"id": prod.id}


@router.patch("/productos/{producto_id}")
async def editar_producto(producto_id: int, datos: ProductoIn, _: str = Depends(usuario_actual)):
    factory = get_session_factory()
    async with factory() as session:
        prod = await session.get(Producto, producto_id)
        if prod is None:
            raise HTTPException(status_code=404, detail="Producto no encontrado")
        prod.nombre = datos.nombre
        prod.categoria = datos.categoria
        prod.descripcion = datos.descripcion
        prod.precio = Decimal(str(datos.precio)) if datos.precio is not None else None
        prod.presentacion = datos.presentacion
        prod.disponible = datos.disponible
        await session.commit()
    return {"ok": True}


# ─── Conversaciones ──────────────────────────────────────────────────

@router.get("/conversaciones")
async def listar_conversaciones(_: str = Depends(usuario_actual)):
    """Lista de clientes con su último mensaje."""
    factory = get_session_factory()
    async with factory() as session:
        clientes = (
            await session.execute(
                select(Cliente).order_by(Cliente.ultima_interaccion.desc()).limit(100)
            )
        ).scalars().all()
        resultado = []
        for c in clientes:
            ultimo = (
                await session.execute(
                    select(Mensaje)
                    .where(Mensaje.cliente_telefono == c.telefono)
                    .order_by(Mensaje.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            resultado.append(
                {
                    "telefono": c.telefono,
                    "nombre": c.nombre,
                    "ultimo_mensaje": ultimo.contenido if ultimo else None,
                    "ultima_interaccion": c.ultima_interaccion.isoformat(),
                }
            )
    return resultado


@router.get("/conversaciones/{telefono}")
async def detalle_conversacion(telefono: str, _: str = Depends(usuario_actual)):
    factory = get_session_factory()
    async with factory() as session:
        mensajes = (
            await session.execute(
                select(Mensaje)
                .where(Mensaje.cliente_telefono == telefono)
                .order_by(Mensaje.created_at)
            )
        ).scalars().all()
    return [
        {"rol": m.rol, "contenido": m.contenido, "fecha": m.created_at.isoformat()}
        for m in mensajes
    ]
