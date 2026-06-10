"""API REST que alimenta el dashboard. Todo protegido con login (JWT),
excepto el propio login."""
import os
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.security import (
    crear_token,
    usuario_actual,
    verify_password,
)
from app.models import Cliente, Configuracion, Mensaje, Pago, Pedido, Producto, now_utc
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


class RechazoIn(BaseModel):
    motivo: str | None = None


# Claves de configuracion editables desde el panel. El bot YA las lee
# (info_negocio, generar_datos_pago y _avisar_duena leen estas mismas claves).
CLAVES_CONFIG = [
    "negocio_nombre",
    "negocio_ubicacion",
    "negocio_pago",
    "negocio_instagram",
    "pago_movil_banco",
    "pago_movil_cedula",
    "pago_movil_telefono",
    "pago_movil_titular",
    "dueno_telefono",
]


class ConfiguracionIn(BaseModel):
    valores: dict[str, str | None]


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


# ─── Reporte de ventas (hoy / semana / mes) ──────────────────────────

@router.get("/reporte")
async def reporte_ventas(_: str = Depends(usuario_actual)):
    """Ventas COBRADAS (pagos confirmados) + pedidos, por periodo.

    'ventas_usd' suma solo pagos en estado 'confirmado' = dinero realmente
    cobrado y verificado por la duena (no pedidos sin pagar)."""
    ahora = now_utc()
    hoy_inicio = datetime.combine(ahora.date(), time.min, tzinfo=timezone.utc)
    factory = get_session_factory()
    async with factory() as session:
        async def resumen(desde: datetime) -> dict:
            ventas = (
                await session.execute(
                    select(func.coalesce(func.sum(Pago.monto_usd), 0)).where(
                        Pago.estado == "confirmado", Pago.created_at >= desde
                    )
                )
            ).scalar() or Decimal("0")
            num_ventas = (
                await session.execute(
                    select(func.count()).select_from(Pago).where(
                        Pago.estado == "confirmado", Pago.created_at >= desde
                    )
                )
            ).scalar() or 0
            pedidos = (
                await session.execute(
                    select(func.count()).select_from(Pedido).where(Pedido.created_at >= desde)
                )
            ).scalar() or 0
            return {
                "ventas_usd": float(ventas),
                "num_ventas": num_ventas,
                "pedidos": pedidos,
            }

        return {
            "hoy": await resumen(hoy_inicio),
            "semana": await resumen(ahora - timedelta(days=7)),
            "mes": await resumen(ahora - timedelta(days=30)),
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
    # Estados que la duena puede fijar manualmente desde el dashboard.
    # 'esperando_pago' lo pone el bot al generar el cobro, y 'pagado' solo se fija
    # al confirmar un pago (POST /api/pagos/{id}/confirmar) — nunca por esta via.
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


# ─── Configuración del negocio (datos que el bot usa) ────────────────

@router.get("/configuracion")
async def obtener_configuracion(_: str = Depends(usuario_actual)):
    """Devuelve los datos editables del negocio (nombre, ubicacion, Pago Movil,
    WhatsApp de avisos...). Son las claves que el bot lee para atender y cobrar."""
    factory = get_session_factory()
    async with factory() as session:
        filas = (await session.execute(select(Configuracion))).scalars().all()
        actual = {f.clave: f.valor for f in filas}
    return {clave: actual.get(clave) for clave in CLAVES_CONFIG}


@router.put("/configuracion")
async def guardar_configuracion(datos: ConfiguracionIn, _: str = Depends(usuario_actual)):
    """Guarda (upsert) los datos del negocio. Ignora claves desconocidas por
    seguridad: solo se aceptan las de CLAVES_CONFIG."""
    factory = get_session_factory()
    async with factory() as session:
        for clave, valor in datos.valores.items():
            if clave not in CLAVES_CONFIG:
                continue
            fila = await session.get(Configuracion, clave)
            if fila is None:
                session.add(Configuracion(clave=clave, valor=valor, updated_at=now_utc()))
            else:
                fila.valor = valor
                fila.updated_at = now_utc()
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


# ─── Pagos (cobro) ───────────────────────────────────────────────────

@router.get("/pagos")
async def listar_pagos(estado: str | None = None, _: str = Depends(usuario_actual)):
    factory = get_session_factory()
    async with factory() as session:
        stmt = select(Pago).order_by(Pago.created_at.desc()).limit(100)
        if estado:
            stmt = (
                select(Pago)
                .where(Pago.estado == estado)
                .order_by(Pago.created_at.desc())
                .limit(100)
            )
        pagos = (await session.execute(stmt)).scalars().all()
        pedidos: dict[int, Pedido] = {}
        for p in pagos:
            if p.pedido_id not in pedidos:
                pedidos[p.pedido_id] = await session.get(Pedido, p.pedido_id)
    salida = []
    for p in pagos:
        ped = pedidos.get(p.pedido_id)
        salida.append({
            "id": p.id,
            "pedido_id": p.pedido_id,
            "cliente": ped.cliente_telefono if ped else None,
            "items": ped.items if ped else None,
            "estado": p.estado,
            "metodo": p.metodo,
            "monto_usd": float(p.monto_usd) if p.monto_usd is not None else None,
            "monto_bs": float(p.monto_bs) if p.monto_bs is not None else None,
            "tasa_usada": float(p.tasa_usada) if p.tasa_usada is not None else None,
            "referencia": p.referencia,
            "tiene_comprobante": bool(p.comprobante_media_id or p.comprobante_url),
            "confirmado_por": p.confirmado_por,
            "fecha": p.created_at.isoformat(),
        })
    return salida


@router.post("/pagos/{pago_id}/confirmar")
async def confirmar_pago(pago_id: int, usuario: str = Depends(usuario_actual)):
    factory = get_session_factory()
    async with factory() as session:
        pago = await session.get(Pago, pago_id)
        if pago is None:
            raise HTTPException(status_code=404, detail="Pago no encontrado")
        if pago.estado != "reportado":
            raise HTTPException(status_code=409, detail=f"El pago ya está {pago.estado}")
        pago.estado = "confirmado"
        pago.confirmado_por = usuario
        pago.updated_at = now_utc()
        pedido = await session.get(Pedido, pago.pedido_id)
        if pedido is not None:
            pedido.estado = "pagado"
        await session.commit()
        telefono = pedido.cliente_telefono if pedido else None

    if telefono:
        from app.workers.tasks import notificar_cliente_pago

        notificar_cliente_pago.apply_async((
            telefono,
            "la duena acaba de CONFIRMAR el pago del cliente; cierra la venta con calidez, "
            "agradecele su compra y dile que coordinan la entrega",
        ))
    return {"ok": True, "pago_id": pago_id, "estado": "confirmado"}


@router.post("/pagos/{pago_id}/rechazar")
async def rechazar_pago(
    pago_id: int, datos: RechazoIn | None = None, usuario: str = Depends(usuario_actual)
):
    factory = get_session_factory()
    async with factory() as session:
        pago = await session.get(Pago, pago_id)
        if pago is None:
            raise HTTPException(status_code=404, detail="Pago no encontrado")
        if pago.estado != "reportado":
            raise HTTPException(status_code=409, detail=f"El pago ya está {pago.estado}")
        pago.estado = "rechazado"
        pago.confirmado_por = usuario
        pago.motivo_rechazo = datos.motivo if datos else None
        pago.updated_at = now_utc()
        pedido = await session.get(Pedido, pago.pedido_id)
        if pedido is not None:
            pedido.estado = "esperando_pago"
        await session.commit()
        telefono = pedido.cliente_telefono if pedido else None

    if telefono:
        from app.workers.tasks import notificar_cliente_pago

        notificar_cliente_pago.apply_async((
            telefono,
            "no se pudo verificar el pago del cliente; pidele con suavidad y sin alarmar que "
            "reenvie el comprobante o la referencia correcta",
        ))
    return {"ok": True, "pago_id": pago_id, "estado": "rechazado"}


@router.get("/pagos/{pago_id}/comprobante")
async def ver_comprobante(pago_id: int, _: str = Depends(usuario_actual)):
    """Sirve la imagen/PDF del comprobante. PROTEGIDO: solo con sesion iniciada
    (trae datos bancarios). El dashboard lo descarga como blob con su token."""
    factory = get_session_factory()
    async with factory() as session:
        pago = await session.get(Pago, pago_id)
    if pago is None or not pago.comprobante_url:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado")
    if not os.path.exists(pago.comprobante_url):
        raise HTTPException(status_code=404, detail="Archivo de comprobante no disponible")
    return FileResponse(pago.comprobante_url)
