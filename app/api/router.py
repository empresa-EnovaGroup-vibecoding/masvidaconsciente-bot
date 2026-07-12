"""API REST que alimenta el dashboard. Todo protegido con login (JWT),
excepto el propio login."""
import os
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, StringConstraints
from sqlalchemy import delete, func, select

from app.config import get_settings
from app.api.security import (
    crear_token,
    usuario_actual,
    verify_password,
)
from app.models import (
    CatalogoPdf,
    Cliente,
    Configuracion,
    Conocimiento,
    Intervencion,
    Mensaje,
    MetodoPago,
    Pago,
    Pedido,
    PrecioDia,
    Producto,
    ProductoMedia,
    hoy_venezuela,
    inicio_dia_venezuela,
    now_utc,
)
from app.services.db import get_session_factory
from app.services.redis_client import borrar_memoria

router = APIRouter(prefix="/api", tags=["dashboard"])

# El SIMULADOR del panel ("Mi Bot" -> probar) crea pedidos y pagos REALES en la base, con este
# telefono. Antes solo la lista de CLIENTES lo excluia: los pedidos de prueba se colaban en el
# panel y SUMABAN en el reporte de ventas. Ahora se excluyen de todo lo que la duena mira.
SIMULADOR = "__simulador__"


def _pedidos_simulador():
    """Subconsulta con los ids de los pedidos que nacieron del simulador."""
    return select(Pedido.id).where(Pedido.cliente_telefono.like(SIMULADOR + "%"))


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
    duracion: str | None = None
    se_congela: str | None = None
    apto_diabeticos: str | None = None
    info: str | None = None
    disponible: bool = True


class EstadoIn(BaseModel):
    estado: str


class RechazoIn(BaseModel):
    motivo: str | None = None


class MontoIn(BaseModel):
    monto_recibido: float


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
    # Modelo de IA conversacional, lo elige la PROVEEDORA (no la clienta). El bot
    # lo lee con leer_modelo_ia(). La voz (transcripción) va aparte y fija.
    "modelo_ia",
]


class ConfiguracionIn(BaseModel):
    valores: dict[str, str | None]


class TasaIn(BaseModel):
    margen_pct: float | None = None
    manual_valor: float | None = None
    manual_activa: bool | None = None


class PersonalidadIn(BaseModel):
    personalidad: str


class ProbarIn(BaseModel):
    mensaje: str
    historial: list[dict] | None = None
    # Telefono de prueba propio (ej. "__simulador__ana"), para correr varias conversaciones a la
    # vez sin que se mezclen sus pedidos. DEBE empezar por "__simulador__": jamas puede escribirle
    # ni leerle el historial a un cliente REAL.
    telefono: str | None = None


class NotasIn(BaseModel):
    notas: str | None = None


class ClienteEditIn(BaseModel):
    nombre: str | None = None
    notas: str | None = None


class ItemEditIn(BaseModel):
    producto: str
    cantidad: int


class PedidoItemsIn(BaseModel):
    items: list[ItemEditIn]


class ConocimientoIn(BaseModel):
    categoria: str | None = None
    titulo: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    contenido: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class BotEstadoIn(BaseModel):
    activo: bool


class PausaIn(BaseModel):
    pausado: bool


class MensajesIn(BaseModel):
    valores: dict[str, str]


class MetodoPagoIn(BaseModel):
    tipo: str = "pago_movil"  # pago_movil | transferencia | zelle | binance | efectivo | otro
    titulo: str
    titular: str | None = None
    banco: str | None = None
    telefono: str | None = None
    cedula: str | None = None
    cuenta: str | None = None
    correo: str | None = None
    wallet: str | None = None
    instrucciones: str | None = None
    activo: bool = True
    orden: int = 0


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
    hoy_inicio = inicio_dia_venezuela()  # el "hoy" de la dueña, no el del servidor (UTC)
    factory = get_session_factory()
    async with factory() as session:
        pedidos_hoy = (
            await session.execute(
                select(func.count()).select_from(Pedido).where(
                    Pedido.created_at >= hoy_inicio,
                    Pedido.cliente_telefono.not_like(SIMULADOR + "%"),
                )
            )
        ).scalar() or 0
        ventas_hoy = (
            await session.execute(
                select(func.coalesce(func.sum(Pedido.total), 0)).where(
                    Pedido.created_at >= hoy_inicio,
                    Pedido.estado != "cancelado",
                    Pedido.cliente_telefono.not_like(SIMULADOR + "%"),
                )
            )
        ).scalar() or Decimal("0")
        clientes_total = (
            await session.execute(
                select(func.count()).select_from(Cliente).where(
                    Cliente.telefono.not_like(SIMULADOR + "%")
                )
            )
        ).scalar() or 0
        pendientes = (
            await session.execute(
                select(func.count()).select_from(Pedido).where(
                    Pedido.estado == "pendiente",
                    Pedido.cliente_telefono.not_like(SIMULADOR + "%"),
                )
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
    hoy_inicio = inicio_dia_venezuela()  # el "hoy" de la dueña, no el del servidor (UTC)
    factory = get_session_factory()
    async with factory() as session:
        async def resumen(desde: datetime) -> dict:
            ventas = (
                await session.execute(
                    select(func.coalesce(func.sum(Pago.monto_usd), 0)).where(
                        Pago.estado == "confirmado",
                        Pago.created_at >= desde,
                        Pago.pedido_id.not_in(_pedidos_simulador()),
                    )
                )
            ).scalar() or Decimal("0")
            num_ventas = (
                await session.execute(
                    select(func.count()).select_from(Pago).where(
                        Pago.estado == "confirmado",
                        Pago.created_at >= desde,
                        Pago.pedido_id.not_in(_pedidos_simulador()),
                    )
                )
            ).scalar() or 0
            pedidos = (
                await session.execute(
                    select(func.count()).select_from(Pedido).where(
                        Pedido.created_at >= desde,
                        Pedido.cliente_telefono.not_like(SIMULADOR + "%"),
                    )
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
            await session.execute(
                select(Pedido)
                .where(Pedido.cliente_telefono.not_like(SIMULADOR + "%"))
                .order_by(Pedido.created_at.desc())
                .limit(100)
            )
        ).scalars().all()
        # Nombre del cliente por telefono (un solo lookup para todos los pedidos).
        telefonos = {p.cliente_telefono for p in pedidos}
        nombres: dict[str, str | None] = {}
        if telefonos:
            filas = (
                await session.execute(
                    select(Cliente.telefono, Cliente.nombre).where(
                        Cliente.telefono.in_(telefonos)
                    )
                )
            ).all()
            nombres = {t: n for t, n in filas}
        # Pago "bloqueante" por pedido (confirmado/parcial/reportado): así el panel sabe,
        # SIN intentar y fallar, si un pedido se puede editar/eliminar.
        pedido_ids = [p.id for p in pedidos]
        bloqueo: dict[int, str] = {}
        if pedido_ids:
            prioridad = {"reportado": 1, "parcial": 2, "confirmado": 3}
            filas_pg = (
                await session.execute(
                    select(Pago.pedido_id, Pago.estado).where(Pago.pedido_id.in_(pedido_ids))
                )
            ).all()
            for pid, est in filas_pg:
                if est in prioridad and prioridad[est] > prioridad.get(bloqueo.get(pid), 0):
                    bloqueo[pid] = est
    return [
        {
            "id": p.id,
            "cliente": p.cliente_telefono,
            "nombre": nombres.get(p.cliente_telefono),
            "estado": p.estado,
            "items": p.items,
            "total_usd": float(p.total) if p.total else 0,
            "notas": p.notas,
            "fecha": p.created_at.isoformat(),
            "pago_bloqueante": bloqueo.get(p.id),  # confirmado|parcial|reportado|None
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


@router.delete("/pedidos/{pedido_id}")
async def borrar_pedido(pedido_id: int, _: str = Depends(usuario_actual)):
    """Elimina un pedido y sus pagos. La dueña manda: el panel ya le avisó la consecuencia
    (si había plata, esa venta sale de sus reportes) y ella confirmó. Los items van en JSONB
    dentro del propio pedido, así que no quedan huérfanos; los pagos se borran primero para
    no violar la FK pagos.pedido_id -> pedidos.id."""
    factory = get_session_factory()
    async with factory() as session:
        pedido = await session.get(Pedido, pedido_id)
        if pedido is None:
            raise HTTPException(status_code=404, detail="Pedido no encontrado")
        pagos = (
            await session.execute(select(Pago).where(Pago.pedido_id == pedido_id))
        ).scalars().all()
        for pg in pagos:
            await session.delete(pg)
        await session.delete(pedido)
        await session.commit()
    return {"ok": True}


@router.put("/pedidos/{pedido_id}/items")
async def editar_items_pedido(
    pedido_id: int, datos: PedidoItemsIn, _: str = Depends(usuario_actual)
):
    """Corrige los items/cantidades de un pedido (si el bot los tomó mal). Recalcula el
    total desde los PRECIOS DEL CATÁLOGO (nunca inventados; mismo emparejamiento que usa el
    bot). La dueña manda: si el pedido ya tenía un pago, el panel le avisó que el monto puede
    no cuadrar con lo cobrado y ella confirmó."""
    # local: evita efectos de import al cargar
    from app.agent.tools import _buscar_producto, _precio_efectivo

    if not datos.items:
        raise HTTPException(status_code=400, detail="El pedido debe tener al menos un producto.")
    factory = get_session_factory()
    async with factory() as session:
        pedido = await session.get(Pedido, pedido_id)
        if pedido is None:
            raise HTTPException(status_code=404, detail="Pedido no encontrado")
        items_pedido = []
        total = Decimal("0")
        for it in datos.items:
            cantidad = max(1, int(it.cantidad))
            prod = await _buscar_producto(session, it.producto, solo_disponibles=False)
            if prod is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"No encontré el producto «{it.producto}» en el catálogo.",
                )
            # El precio EFECTIVO (fijo, o el precio del día si su precio cambia), igual que
            # el bot. Antes hacía `(prod.precio or 0) * cantidad`: al editar un pedido con un
            # producto de PRECIO DEL DÍA, el total se recalculaba en $0 y el pedido quedaba
            # GRATIS. Sin precio de hoy NO se recalcula: se avisa, nunca se cobra $0.
            precio = await _precio_efectivo(session, prod)
            if precio is None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"«{prod.nombre}» todavía no tiene precio de hoy. Ponle el precio del día "
                        f"en «El bot te necesita» y vuelve a guardar el pedido."
                    ),
                )
            total += precio * cantidad
            items_pedido.append(
                {
                    "producto": prod.nombre,
                    "cantidad": cantidad,
                    "precio_unitario": float(precio),
                    "presentacion": prod.presentacion,
                }
            )
        pedido.items = items_pedido
        pedido.total = total
        await session.commit()
    return {"ok": True, "total_usd": float(total), "items": items_pedido}


# ─── Productos (catálogo) ────────────────────────────────────────────

@router.get("/productos")
async def listar_productos(_: str = Depends(usuario_actual)):
    from app.services import r2

    factory = get_session_factory()
    async with factory() as session:
        productos = (
            await session.execute(select(Producto).order_by(Producto.categoria, Producto.nombre))
        ).scalars().all()
        # Primera FOTO de cada producto, para la miniatura en la tarjeta del catálogo.
        # UNA sola consulta para todos -> escala a cientos de productos sin N+1.
        ids = [p.id for p in productos]
        primera_img: dict[int, str] = {}
        if ids:
            filas = (
                await session.execute(
                    select(ProductoMedia)
                    .where(ProductoMedia.producto_id.in_(ids), ProductoMedia.tipo == "imagen")
                    .order_by(ProductoMedia.producto_id, ProductoMedia.orden, ProductoMedia.id)
                )
            ).scalars().all()
            for m in filas:
                primera_img.setdefault(m.producto_id, m.clave)  # la primera por orden
    return [
        {
            "id": p.id,
            "nombre": p.nombre,
            "categoria": p.categoria,
            "descripcion": p.descripcion,
            "precio": float(p.precio) if p.precio is not None else None,
            "presentacion": p.presentacion,
            "duracion": p.duracion,
            "se_congela": p.se_congela,
            "apto_diabeticos": p.apto_diabeticos,
            "info": p.info,
            "disponible": p.disponible,
            "imagen": r2.url_publica(primera_img[p.id]) if p.id in primera_img else None,
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
            duracion=datos.duracion,
            se_congela=datos.se_congela,
            apto_diabeticos=datos.apto_diabeticos,
            info=datos.info,
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
        prod.duracion = datos.duracion
        prod.se_congela = datos.se_congela
        prod.apto_diabeticos = datos.apto_diabeticos
        prod.info = datos.info
        prod.disponible = datos.disponible
        await session.commit()
    return {"ok": True}


@router.delete("/productos/{producto_id}")
async def borrar_producto(producto_id: int, _: str = Depends(usuario_actual)):
    """Elimina un producto del catálogo. Los pedidos anteriores NO se afectan:
    guardan sus items como copia (JSONB), no dependen del catálogo."""
    factory = get_session_factory()
    async with factory() as session:
        prod = await session.get(Producto, producto_id)
        if prod is None:
            raise HTTPException(status_code=404, detail="Producto no encontrado")
        await session.delete(prod)
        await session.commit()
    return {"ok": True}


# ─── Media de productos (fotos/videos en R2) ─────────────────────────────

_MAX_IMAGEN = 5 * 1024 * 1024   # 5 MB (límite de imagen de WhatsApp)
_MAX_VIDEO = 16 * 1024 * 1024   # 16 MB (límite de video de WhatsApp)


@router.get("/productos/{producto_id}/media")
async def listar_media(producto_id: int, _: str = Depends(usuario_actual)):
    from app.services import r2

    factory = get_session_factory()
    async with factory() as session:
        filas = (
            await session.execute(
                select(ProductoMedia)
                .where(ProductoMedia.producto_id == producto_id)
                .order_by(ProductoMedia.orden, ProductoMedia.id)
            )
        ).scalars().all()
    return [{"id": m.id, "tipo": m.tipo, "url": r2.url_publica(m.clave)} for m in filas]


@router.post("/productos/{producto_id}/media")
async def subir_media(
    producto_id: int, archivo: UploadFile = File(...), _: str = Depends(usuario_actual)
):
    """Sube una foto o video del producto a R2. En la BD se guarda solo la ruta (clave)."""
    from app.services import r2

    if not r2.configurado():
        raise HTTPException(
            status_code=503, detail="El almacenamiento de fotos (R2) no está configurado"
        )
    ct = (archivo.content_type or "").lower()
    if ct.startswith("image/"):
        tipo, limite = "imagen", _MAX_IMAGEN
    elif ct.startswith("video/"):
        tipo, limite = "video", _MAX_VIDEO
    else:
        raise HTTPException(status_code=400, detail="Solo se aceptan imágenes o videos")
    ext = (ct.split("/")[-1].split(";")[0] or "bin").strip()
    contenido = await archivo.read()
    if len(contenido) > limite:
        mb = limite // (1024 * 1024)
        cosa = "videos" if tipo == "video" else "imágenes"
        raise HTTPException(
            status_code=413, detail=f"El archivo es muy grande (máximo {mb} MB para {cosa})"
        )
    factory = get_session_factory()
    async with factory() as session:
        prod = await session.get(Producto, producto_id)
        if prod is None:
            raise HTTPException(status_code=404, detail="Producto no encontrado")
        n = (
            await session.execute(
                select(func.count())
                .select_from(ProductoMedia)
                .where(ProductoMedia.producto_id == producto_id)
            )
        ).scalar() or 0
        clave = f"productos/{producto_id}/{uuid4().hex}.{ext}"
        subido = await r2.subir(
            clave, contenido, archivo.content_type or "application/octet-stream"
        )
        if not subido:
            raise HTTPException(status_code=502, detail="No se pudo subir el archivo a R2")
        m = ProductoMedia(producto_id=producto_id, tipo=tipo, clave=clave, orden=int(n))
        session.add(m)
        await session.commit()
        await session.refresh(m)
    return {"id": m.id, "tipo": tipo, "url": r2.url_publica(clave)}


@router.delete("/media/{media_id}")
async def borrar_media(media_id: int, _: str = Depends(usuario_actual)):
    from app.services import r2

    factory = get_session_factory()
    async with factory() as session:
        m = await session.get(ProductoMedia, media_id)
        if m is None:
            raise HTTPException(status_code=404, detail="Media no encontrada")
        await r2.borrar(m.clave)  # si falla en R2, igual quitamos el registro de la BD
        await session.delete(m)
        await session.commit()
    return {"ok": True}


# ─── Catálogo en PDF (guardado en la BASE DE DATOS, sobrevive redeploys) ──

@router.get("/catalogo-pdf")
async def estado_catalogo_pdf(_: str = Depends(usuario_actual)):
    """Indica si hay un catálogo PDF cargado."""
    factory = get_session_factory()
    async with factory() as session:
        fila = await session.get(CatalogoPdf, 1)
    return {"tiene": fila is not None and bool(fila.contenido)}


@router.post("/catalogo-pdf")
async def subir_catalogo_pdf(archivo: UploadFile = File(...), _: str = Depends(usuario_actual)):
    """Sube el catálogo en PDF y lo guarda EN LA BASE DE DATOS (persistente). Máx 100 MB."""
    contenido = await archivo.read()
    if len(contenido) > 100 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="El PDF es muy grande (máximo 100 MB)")
    ct = (archivo.content_type or "").lower()
    if "pdf" not in ct and not (archivo.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF")
    # Valida que el CONTENIDO sea realmente un PDF (no solo el nombre/tipo declarado).
    if not contenido.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="El archivo no parece un PDF válido")
    factory = get_session_factory()
    async with factory() as session:
        fila = await session.get(CatalogoPdf, 1)
        if fila is None:
            session.add(CatalogoPdf(id=1, contenido=contenido, actualizado=now_utc()))
        else:
            fila.contenido = contenido
            fila.actualizado = now_utc()
        await session.commit()
    return {"ok": True}


@router.delete("/catalogo-pdf")
async def borrar_catalogo_pdf(_: str = Depends(usuario_actual)):
    factory = get_session_factory()
    async with factory() as session:
        fila = await session.get(CatalogoPdf, 1)
        if fila is not None:
            await session.delete(fila)
            await session.commit()
    return {"ok": True}


@router.get("/catalogo/archivo")
async def servir_catalogo_pdf():
    """PÚBLICO (sin login): sirve el catálogo PDF (desde la BD) para que Meta lo
    descargue al enviarlo y para que el cliente lo abra. Es un folleto público."""
    factory = get_session_factory()
    async with factory() as session:
        fila = await session.get(CatalogoPdf, 1)
    if fila is None or not fila.contenido:
        raise HTTPException(status_code=404, detail="No hay catálogo cargado")
    return Response(
        content=bytes(fila.contenido),
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="Catalogo.pdf"'},
    )


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


# ─── Tasa BCV (margen + candado manual) ──────────────────────────────

@router.get("/tasa")
async def estado_de_tasa(_: str = Depends(usuario_actual)):
    """Tasa base (BCV), margen, candado manual y tasa efectiva que se cobra hoy."""
    from app.services.tasa import estado_tasa

    return await estado_tasa()


@router.put("/tasa")
async def guardar_tasa(datos: TasaIn, _: str = Depends(usuario_actual)):
    """Guarda el margen (%), la tasa manual y si el candado manual esta activo.
    El cambio se aplica al instante en el proximo cobro."""
    cambios: dict[str, str] = {}
    if datos.margen_pct is not None:
        cambios["tasa_margen_pct"] = str(datos.margen_pct)
    if datos.manual_valor is not None:
        cambios["tasa_manual"] = str(datos.manual_valor)
    if datos.manual_activa is not None:
        cambios["tasa_manual_activa"] = "1" if datos.manual_activa else "0"
    factory = get_session_factory()
    async with factory() as session:
        # BLINDAJE DEL COBRO: no dejar el candado manual activo sin una tasa
        # manual válida (>0). Si quedara activo con valor None/<=0, el bot se
        # quedaría SIN tasa efectiva y el cobro se rompe.
        if datos.manual_activa is True:
            efectivo = datos.manual_valor
            if efectivo is None:
                fila_actual = await session.get(Configuracion, "tasa_manual")
                try:
                    efectivo = (
                        float(fila_actual.valor)
                        if fila_actual is not None and fila_actual.valor not in (None, "")
                        else None
                    )
                except (TypeError, ValueError):
                    efectivo = None
            if efectivo is None or efectivo <= 0:
                raise HTTPException(
                    status_code=400,
                    detail="Para activar el candado manual necesitas un valor de "
                    "tasa válido (mayor que 0).",
                )
        for clave, valor in cambios.items():
            fila = await session.get(Configuracion, clave)
            if fila is None:
                session.add(Configuracion(clave=clave, valor=valor, updated_at=now_utc()))
            else:
                fila.valor = valor
                fila.updated_at = now_utc()
        await session.commit()
    return {"ok": True}


# ─── Bot: personalidad editable + simulador ──────────────────────────

@router.get("/personalidad")
async def obtener_personalidad(_: str = Depends(usuario_actual)):
    """La personalidad activa del bot + la original (para 'restaurar')."""
    from app.agent.system_prompt import leer_personalidad, personalidad_default

    return {"personalidad": await leer_personalidad(), "default": personalidad_default()}


@router.put("/personalidad")
async def guardar_personalidad(datos: PersonalidadIn, _: str = Depends(usuario_actual)):
    """Guarda la personalidad editable (clave 'personalidad'). Vacío = vuelve al
    default. Las reglas críticas del cobro NO se tocan: se anexan siempre aparte."""
    valor = datos.personalidad.strip()
    factory = get_session_factory()
    async with factory() as session:
        fila = await session.get(Configuracion, "personalidad")
        if fila is None:
            session.add(Configuracion(clave="personalidad", valor=valor, updated_at=now_utc()))
        else:
            fila.valor = valor
            fila.updated_at = now_utc()
        await session.commit()
    return {"ok": True}


@router.post("/probar")
async def probar_bot(datos: ProbarIn, _: str = Depends(usuario_actual)):
    """Simulador: corre el agente con un mensaje de prueba y devuelve su respuesta,
    SIN enviar nada por WhatsApp. Usa un teléfono de prueba ('__simulador__…')."""
    from app.agent.agent import responder

    telefono = (datos.telefono or SIMULADOR).strip()
    if not telefono.startswith(SIMULADOR):
        raise HTTPException(
            status_code=400,
            detail="El simulador solo puede usar teléfonos de prueba (empiezan por __simulador__).",
        )

    respuesta = await responder(
        telefono=telefono,
        mensaje_usuario=datos.mensaje,
        historial=datos.historial or [],
        nombre_cliente="Prueba",
    )
    return {"respuesta": respuesta}


# ─── Interruptor del bot (encender / apagar) ─────────────────────────

@router.get("/bot-estado")
async def obtener_bot_estado(_: str = Depends(usuario_actual)):
    factory = get_session_factory()
    async with factory() as session:
        fila = await session.get(Configuracion, "bot_activo")
    activo = (
        True
        if fila is None or fila.valor is None
        else fila.valor.strip().lower() not in ("0", "false", "no", "off")
    )
    return {"activo": activo}


@router.put("/bot-estado")
async def guardar_bot_estado(datos: BotEstadoIn, _: str = Depends(usuario_actual)):
    factory = get_session_factory()
    async with factory() as session:
        fila = await session.get(Configuracion, "bot_activo")
        valor = "1" if datos.activo else "0"
        if fila is None:
            session.add(Configuracion(clave="bot_activo", valor=valor, updated_at=now_utc()))
        else:
            fila.valor = valor
            fila.updated_at = now_utc()
        await session.commit()
    return {"ok": True, "activo": datos.activo}


# ─── Mensajes automáticos (guías editables; el bot los redacta) ──────

@router.get("/mensajes")
async def obtener_mensajes(_: str = Depends(usuario_actual)):
    from app.services.mensajes import CLAVES_MENSAJES, MENSAJES_DEFAULT

    factory = get_session_factory()
    async with factory() as session:
        filas = (
            await session.execute(
                select(Configuracion).where(Configuracion.clave.in_(CLAVES_MENSAJES))
            )
        ).scalars().all()
        actual = {f.clave: f.valor for f in filas}
    return {clave: (actual.get(clave) or MENSAJES_DEFAULT[clave]) for clave in CLAVES_MENSAJES}


@router.put("/mensajes")
async def guardar_mensajes(datos: MensajesIn, _: str = Depends(usuario_actual)):
    from app.services.mensajes import CLAVES_MENSAJES

    factory = get_session_factory()
    async with factory() as session:
        for clave, valor in datos.valores.items():
            if clave not in CLAVES_MENSAJES:
                continue
            limpio = (valor or "").strip()
            fila = await session.get(Configuracion, clave)
            if fila is None:
                session.add(Configuracion(clave=clave, valor=limpio, updated_at=now_utc()))
            else:
                fila.valor = limpio
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
            if ultimo is None:
                continue  # sin mensajes (p.ej. chat borrado) -> no aparece en Conversaciones
            resultado.append(
                {
                    "telefono": c.telefono,
                    "nombre": c.nombre,
                    "ultimo_mensaje": ultimo.contenido if ultimo else None,
                    "ultima_interaccion": c.ultima_interaccion.isoformat(),
                    "bot_pausado": c.bot_pausado,
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


@router.delete("/conversaciones/{telefono}")
async def borrar_conversacion(telefono: str, _: str = Depends(usuario_actual)):
    """Borra el historial de mensajes de un cliente + su memoria en Redis.
    NO toca al cliente, ni sus pedidos, ni sus pagos: el registro de cobro queda
    intacto. Solo limpia el chat del panel y la memoria del bot."""
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(delete(Mensaje).where(Mensaje.cliente_telefono == telefono))
        await session.commit()
    await borrar_memoria(telefono)
    return {"ok": True}


# ─── Clientes (CRM) ──────────────────────────────────────────────────

@router.get("/clientes")
async def listar_clientes(_: str = Depends(usuario_actual)):
    """Lista de clientes con sus totales: nº de pedidos, total gastado (pagos
    confirmados) y última compra. Excluye el cliente de prueba del simulador."""
    factory = get_session_factory()
    async with factory() as session:
        clientes = (
            await session.execute(
                select(Cliente)
                .where(Cliente.telefono.not_like(SIMULADOR + "%"))
                .order_by(Cliente.ultima_interaccion.desc())
                .limit(500)
            )
        ).scalars().all()
        ped_rows = (
            await session.execute(
                select(Pedido.cliente_telefono, func.count(), func.max(Pedido.created_at))
                .group_by(Pedido.cliente_telefono)
            )
        ).all()
        ped_stats = {r[0]: (r[1], r[2]) for r in ped_rows}
        gasto_rows = (
            await session.execute(
                select(Pedido.cliente_telefono, func.coalesce(func.sum(Pago.monto_usd), 0))
                .join(Pago, Pago.pedido_id == Pedido.id)
                .where(Pago.estado == "confirmado")
                .group_by(Pedido.cliente_telefono)
            )
        ).all()
        gasto = {r[0]: float(r[1]) for r in gasto_rows}
    salida = []
    for c in clientes:
        num, ultima = ped_stats.get(c.telefono, (0, None))
        salida.append({
            "telefono": c.telefono,
            "nombre": c.nombre,
            "num_pedidos": num,
            "total_gastado_usd": gasto.get(c.telefono, 0.0),
            "ultima_compra": ultima.isoformat() if ultima else None,
            "ultima_interaccion": c.ultima_interaccion.isoformat(),
        })
    return salida


@router.get("/clientes/{telefono}")
async def detalle_cliente(telefono: str, _: str = Depends(usuario_actual)):
    """Ficha del cliente: datos, notas, total gastado e historial de pedidos."""
    factory = get_session_factory()
    async with factory() as session:
        cliente = (
            await session.execute(select(Cliente).where(Cliente.telefono == telefono))
        ).scalar_one_or_none()
        if cliente is None:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")
        pedidos = (
            await session.execute(
                select(Pedido)
                .where(Pedido.cliente_telefono == telefono)
                .order_by(Pedido.created_at.desc())
                .limit(50)
            )
        ).scalars().all()
        total = (
            await session.execute(
                select(func.coalesce(func.sum(Pago.monto_usd), 0))
                .join(Pedido, Pago.pedido_id == Pedido.id)
                .where(Pedido.cliente_telefono == telefono, Pago.estado == "confirmado")
            )
        ).scalar() or 0
        # ¿Se puede borrar el cliente? No, si tiene algún pago vivo (confirmado/parcial/
        # reportado): el panel deshabilita el botón en vez de dejar fallar el borrado.
        pedido_ids = [p.id for p in pedidos]
        puede_borrar = True
        if pedido_ids:
            hay_pago = (
                await session.execute(
                    select(Pago.id)
                    .where(
                        Pago.pedido_id.in_(pedido_ids),
                        Pago.estado.in_(["confirmado", "parcial", "reportado"]),
                    )
                    .limit(1)
                )
            ).first()
            puede_borrar = hay_pago is None
    return {
        "telefono": cliente.telefono,
        "nombre": cliente.nombre,
        "puede_borrar": puede_borrar,
        "notas": cliente.notas,
        "primera_interaccion": cliente.primera_interaccion.isoformat(),
        "ultima_interaccion": cliente.ultima_interaccion.isoformat(),
        "total_gastado_usd": float(total),
        "num_pedidos": len(pedidos),
        "pedidos": [
            {
                "id": p.id,
                "estado": p.estado,
                "items": p.items,
                "total_usd": float(p.total) if p.total else 0,
                "fecha": p.created_at.isoformat(),
            }
            for p in pedidos
        ],
    }


@router.put("/clientes/{telefono}/notas")
async def guardar_notas_cliente(telefono: str, datos: NotasIn, _: str = Depends(usuario_actual)):
    """Guarda las notas internas del cliente (privadas; el cliente nunca las ve)."""
    factory = get_session_factory()
    async with factory() as session:
        cliente = (
            await session.execute(select(Cliente).where(Cliente.telefono == telefono))
        ).scalar_one_or_none()
        if cliente is None:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")
        cliente.notas = datos.notas
        await session.commit()
    return {"ok": True}


@router.put("/clientes/{telefono}/pausa")
async def pausar_bot_cliente(telefono: str, datos: PausaIn, _: str = Depends(usuario_actual)):
    """Pausa/reactiva el bot SOLO para este cliente (la dueña atiende ese chat)."""
    factory = get_session_factory()
    async with factory() as session:
        cliente = (
            await session.execute(select(Cliente).where(Cliente.telefono == telefono))
        ).scalar_one_or_none()
        if cliente is None:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")
        cliente.bot_pausado = datos.pausado
        await session.commit()
    return {"ok": True, "pausado": datos.pausado}


@router.put("/clientes/{telefono}")
async def editar_cliente(telefono: str, datos: ClienteEditIn, _: str = Depends(usuario_actual)):
    """Corrige el nombre y/o las notas (ficha) del cliente, si se tomaron mal."""
    factory = get_session_factory()
    async with factory() as session:
        cliente = (
            await session.execute(select(Cliente).where(Cliente.telefono == telefono))
        ).scalar_one_or_none()
        if cliente is None:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")
        if datos.nombre is not None:
            cliente.nombre = datos.nombre.strip() or None
        if datos.notas is not None:
            cliente.notas = datos.notas
        await session.commit()
    return {"ok": True}


@router.delete("/clientes/{telefono}")
async def borrar_cliente(telefono: str, _: str = Depends(usuario_actual)):
    """Resetea al cliente por completo: su ficha, TODOS sus pedidos, pagos, mensajes y la
    memoria del bot (así vuelve a tratarlo como nuevo). La dueña manda: el panel ya le avisó
    la consecuencia (si había plata, ese historial de cobro sale de sus reportes) y ella
    confirmó."""
    factory = get_session_factory()
    async with factory() as session:
        cliente = (
            await session.execute(select(Cliente).where(Cliente.telefono == telefono))
        ).scalar_one_or_none()
        if cliente is None:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")
        pedidos = (
            await session.execute(select(Pedido).where(Pedido.cliente_telefono == telefono))
        ).scalars().all()
        pedido_ids = [p.id for p in pedidos]
        pagos = []
        if pedido_ids:
            pagos = (
                await session.execute(select(Pago).where(Pago.pedido_id.in_(pedido_ids)))
            ).scalars().all()
        # Se limpia todo en orden (pagos → pedidos → mensajes → cliente) para respetar las FKs.
        for pg in pagos:
            await session.delete(pg)
        for p in pedidos:
            await session.delete(p)
        await session.execute(delete(Mensaje).where(Mensaje.cliente_telefono == telefono))
        await session.delete(cliente)
        await session.commit()
    await borrar_memoria(telefono)
    return {"ok": True}


# ─── "EL BOT TE NECESITA": bandeja de intervenciones + precio del día ─

class PrecioDiaIn(BaseModel):
    producto_id: int
    precio: float
    nota: str | None = None


_MOTIVO_TEXTO = {
    "precio_del_dia": "Te piden un precio del día",
    "no_se": "El bot no sabe algo",
    "pide_persona": "El cliente pide hablar con una persona",
    "reclamo": "El cliente está reclamando",
}


@router.get("/intervenciones")
async def listar_intervenciones(estado: str = "pendiente", _: str = Depends(usuario_actual)):
    """La bandeja 'EL BOT TE NECESITA': los chats donde el bot se calló y te espera.
    El bot llama a `pedir_ayuda` cuando NO le toca resolver algo (un precio que cambia,
    algo que no sabe, un cliente que pide una persona, un reclamo) — así jamás inventa."""
    factory = get_session_factory()
    async with factory() as session:
        stmt = select(Intervencion).order_by(Intervencion.created_at.desc()).limit(100)
        if estado in ("pendiente", "resuelta"):
            stmt = stmt.where(Intervencion.estado == estado)
        filas = (await session.execute(stmt)).scalars().all()
        telefonos = {i.cliente_telefono for i in filas}
        nombres: dict[str, str | None] = {}
        if telefonos:
            nombres = dict(
                (
                    await session.execute(
                        select(Cliente.telefono, Cliente.nombre).where(
                            Cliente.telefono.in_(telefonos)
                        )
                    )
                ).all()
            )
    return [
        {
            "id": i.id,
            "cliente": i.cliente_telefono,
            "nombre": nombres.get(i.cliente_telefono),
            "motivo": i.motivo,
            "motivo_texto": _MOTIVO_TEXTO.get(i.motivo, i.motivo),
            "detalle": i.detalle,
            "mensaje_cliente": i.mensaje_cliente,
            "estado": i.estado,
            "fecha": i.created_at.isoformat(),
        }
        for i in filas
    ]


@router.post("/intervenciones/{intervencion_id}/resolver")
async def resolver_intervencion(
    intervencion_id: int, reactivar: bool = True, _: str = Depends(usuario_actual)
):
    """La dueña ya atendió ese chat: cierra el aviso y (por defecto) REACTIVA el bot,
    para que vuelva a atender a ese cliente."""
    factory = get_session_factory()
    async with factory() as session:
        inter = await session.get(Intervencion, intervencion_id)
        if inter is None:
            raise HTTPException(status_code=404, detail="Aviso no encontrado")
        inter.estado = "resuelta"
        inter.resuelta_at = now_utc()
        if reactivar:
            cliente = (
                await session.execute(
                    select(Cliente).where(Cliente.telefono == inter.cliente_telefono)
                )
            ).scalar_one_or_none()
            if cliente is not None:
                cliente.bot_pausado = False
        await session.commit()
    return {"ok": True, "bot_reactivado": reactivar}


@router.get("/precio-dia")
async def ver_precios_dia(_: str = Depends(usuario_actual)):
    """Los productos de PRECIO VARIABLE (precio vacío a propósito: su costo cambia de un
    día a otro) y el precio que la dueña les dio HOY (si ya lo dio)."""
    factory = get_session_factory()
    async with factory() as session:
        prods = (
            await session.execute(
                select(Producto).where(Producto.precio.is_(None)).order_by(Producto.nombre)
            )
        ).scalars().all()
        hoy = dict(
            (
                await session.execute(
                    select(PrecioDia.producto_id, PrecioDia.precio).where(
                        PrecioDia.fecha == hoy_venezuela()
                    )
                )
            ).all()
        )
    return [
        {
            "producto_id": p.id,
            "nombre": p.nombre,
            "presentacion": p.presentacion,
            "precio_hoy": float(hoy[p.id]) if p.id in hoy else None,
        }
        for p in prods
    ]


@router.put("/precio-dia")
async def poner_precio_dia(datos: PrecioDiaIn, _: str = Depends(usuario_actual)):
    """La dueña dice cuánto está HOY ese producto. Vale SOLO por hoy: mañana el bot se lo
    vuelve a preguntar (un precio viejo jamás se reutiliza). Si lo corrige, se sobreescribe."""
    if datos.precio <= 0:
        raise HTTPException(status_code=400, detail="El precio debe ser mayor que 0.")
    factory = get_session_factory()
    async with factory() as session:
        prod = await session.get(Producto, datos.producto_id)
        if prod is None:
            raise HTTPException(status_code=404, detail="Producto no encontrado")
        fila = (
            await session.execute(
                select(PrecioDia).where(
                    PrecioDia.producto_id == datos.producto_id, PrecioDia.fecha == hoy_venezuela()
                )
            )
        ).scalar_one_or_none()
        if fila is None:
            session.add(
                PrecioDia(
                    producto_id=datos.producto_id,
                    precio=Decimal(str(datos.precio)),
                    nota=datos.nota,
                    fecha=hoy_venezuela(),
                )
            )
        else:
            fila.precio = Decimal(str(datos.precio))
            fila.nota = datos.nota
        await session.commit()
    return {"ok": True, "producto": prod.nombre, "precio_hoy": datos.precio}


# ─── Conocimiento del negocio (FAQ + info que usa el bot) ────────────

@router.get("/conocimiento")
async def listar_conocimiento(_: str = Depends(usuario_actual)):
    factory = get_session_factory()
    async with factory() as session:
        filas = (
            await session.execute(
                select(Conocimiento).order_by(Conocimiento.categoria, Conocimiento.titulo)
            )
        ).scalars().all()
    return [
        {"id": c.id, "categoria": c.categoria, "titulo": c.titulo, "contenido": c.contenido}
        for c in filas
    ]


@router.post("/conocimiento")
async def crear_conocimiento(datos: ConocimientoIn, _: str = Depends(usuario_actual)):
    from app.services.embeddings import obtener_embedding

    factory = get_session_factory()
    async with factory() as session:
        c = Conocimiento(
            categoria=(datos.categoria or "").strip() or None,
            titulo=datos.titulo,
            contenido=datos.contenido,
            embedding=await obtener_embedding(f"{datos.titulo}. {datos.contenido}"),
        )
        session.add(c)
        await session.commit()
        await session.refresh(c)
    return {"id": c.id}


@router.patch("/conocimiento/{cid}")
async def editar_conocimiento(cid: int, datos: ConocimientoIn, _: str = Depends(usuario_actual)):
    from app.services.embeddings import obtener_embedding

    factory = get_session_factory()
    async with factory() as session:
        c = await session.get(Conocimiento, cid)
        if c is None:
            raise HTTPException(status_code=404, detail="Entrada no encontrada")
        c.categoria = (datos.categoria or "").strip() or None
        c.titulo = datos.titulo
        c.contenido = datos.contenido
        # El contenido cambió → recalcula el embedding (búsqueda semántica al día).
        c.embedding = await obtener_embedding(f"{datos.titulo}. {datos.contenido}")
        c.updated_at = now_utc()
        await session.commit()
    return {"ok": True}


@router.delete("/conocimiento/{cid}")
async def borrar_conocimiento(cid: int, _: str = Depends(usuario_actual)):
    factory = get_session_factory()
    async with factory() as session:
        c = await session.get(Conocimiento, cid)
        if c is None:
            raise HTTPException(status_code=404, detail="Entrada no encontrada")
        await session.delete(c)
        await session.commit()
    return {"ok": True}


# ─── Métodos de pago (cuentas de la dueña que el bot ofrece y valida) ─

@router.get("/metodos-pago")
async def listar_metodos_pago(_: str = Depends(usuario_actual)):
    """Lista TODAS las cuentas/métodos de pago (activos e inactivos)."""
    factory = get_session_factory()
    async with factory() as session:
        metodos = (
            await session.execute(
                select(MetodoPago).order_by(MetodoPago.orden, MetodoPago.id)
            )
        ).scalars().all()
    return [
        {
            "id": m.id,
            "tipo": m.tipo,
            "titulo": m.titulo,
            "titular": m.titular,
            "banco": m.banco,
            "telefono": m.telefono,
            "cedula": m.cedula,
            "cuenta": m.cuenta,
            "correo": m.correo,
            "wallet": m.wallet,
            "instrucciones": m.instrucciones,
            "activo": m.activo,
            "orden": m.orden,
        }
        for m in metodos
    ]


@router.post("/metodos-pago")
async def crear_metodo_pago(datos: MetodoPagoIn, _: str = Depends(usuario_actual)):
    factory = get_session_factory()
    async with factory() as session:
        metodo = MetodoPago(
            tipo=datos.tipo,
            titulo=datos.titulo,
            titular=datos.titular,
            banco=datos.banco,
            telefono=datos.telefono,
            cedula=datos.cedula,
            cuenta=datos.cuenta,
            correo=datos.correo,
            wallet=datos.wallet,
            instrucciones=datos.instrucciones,
            activo=datos.activo,
            orden=datos.orden,
        )
        session.add(metodo)
        await session.commit()
        await session.refresh(metodo)
    return {"ok": True, "id": metodo.id}


@router.put("/metodos-pago/{metodo_id}")
async def editar_metodo_pago(
    metodo_id: int, datos: MetodoPagoIn, _: str = Depends(usuario_actual)
):
    factory = get_session_factory()
    async with factory() as session:
        metodo = await session.get(MetodoPago, metodo_id)
        if metodo is None:
            raise HTTPException(status_code=404, detail="Método de pago no encontrado")
        metodo.tipo = datos.tipo
        metodo.titulo = datos.titulo
        metodo.titular = datos.titular
        metodo.banco = datos.banco
        metodo.telefono = datos.telefono
        metodo.cedula = datos.cedula
        metodo.cuenta = datos.cuenta
        metodo.correo = datos.correo
        metodo.wallet = datos.wallet
        metodo.instrucciones = datos.instrucciones
        metodo.activo = datos.activo
        metodo.orden = datos.orden
        metodo.updated_at = now_utc()
        await session.commit()
    return {"ok": True}


@router.delete("/metodos-pago/{metodo_id}")
async def borrar_metodo_pago(metodo_id: int, _: str = Depends(usuario_actual)):
    factory = get_session_factory()
    async with factory() as session:
        metodo = await session.get(MetodoPago, metodo_id)
        if metodo is None:
            raise HTTPException(status_code=404, detail="Método de pago no encontrado")
        await session.delete(metodo)
        await session.commit()
    return {"ok": True}


# ─── Pagos (cobro) ───────────────────────────────────────────────────

@router.get("/pagos")
async def listar_pagos(estado: str | None = None, _: str = Depends(usuario_actual)):
    factory = get_session_factory()
    async with factory() as session:
        stmt = (
            select(Pago)
            .where(Pago.pedido_id.not_in(_pedidos_simulador()))
            .order_by(Pago.created_at.desc())
            .limit(100)
        )
        if estado:
            stmt = (
                select(Pago)
                .where(Pago.estado == estado, Pago.pedido_id.not_in(_pedidos_simulador()))
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
            "monto_recibido": float(p.monto_recibido) if p.monto_recibido is not None else None,
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
        from app.services.mensajes import leer_guia
        from app.workers.tasks import notificar_cliente_pago

        notificar_cliente_pago.apply_async((telefono, await leer_guia("msg_guia_confirmado")))
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
        from app.services.mensajes import leer_guia
        from app.workers.tasks import notificar_cliente_pago

        notificar_cliente_pago.apply_async((telefono, await leer_guia("msg_guia_rechazado")))
    return {"ok": True, "pago_id": pago_id, "estado": "rechazado"}


@router.post("/pagos/{pago_id}/verificar-monto")
async def verificar_monto(pago_id: int, datos: MontoIn, usuario: str = Depends(usuario_actual)):
    """Para cuando el monto pagado NO calza con el cobrado (la tasa cambió, etc.).
    Registra cuánto se recibió (Bs) y decide:
      - recibido >= total -> confirmado (si pagó de más, avisa el saldo a favor)
      - recibido <  total -> parcial (el pedido sigue esperando el resto)
    """
    factory = get_session_factory()
    async with factory() as session:
        pago = await session.get(Pago, pago_id)
        if pago is None:
            raise HTTPException(status_code=404, detail="Pago no encontrado")
        if pago.estado not in ("reportado", "parcial"):
            raise HTTPException(status_code=409, detail=f"El pago ya está {pago.estado}")
        if pago.monto_bs is None:
            raise HTTPException(
                status_code=400,
                detail="Este pago no tiene monto en Bs para comparar; usa Confirmar o Rechazar",
            )
        recibido = Decimal(str(datos.monto_recibido))
        total_bs = pago.monto_bs
        pago.monto_recibido = recibido
        pago.confirmado_por = usuario
        pago.updated_at = now_utc()
        pedido = await session.get(Pedido, pago.pedido_id)
        if recibido >= total_bs:
            pago.estado = "confirmado"
            if pedido is not None:
                pedido.estado = "pagado"
        else:
            pago.estado = "parcial"
            if pedido is not None:
                pedido.estado = "esperando_pago"
        await session.commit()
        telefono = pedido.cliente_telefono if pedido else None
        estado_final = pago.estado

    if telefono:
        from app.workers.tasks import notificar_cliente_pago

        if estado_final == "confirmado":
            situacion = (
                "el pago del cliente quedo CONFIRMADO; cierra la venta con calidez y agradece la compra"
            )
            if recibido > total_bs:
                situacion += (
                    f". Ademas pago Bs {(recibido - total_bs):.2f} de mas: dile con cariño que "
                    f"le queda ese saldo a favor para su proxima compra"
                )
        else:
            situacion = (
                f"el cliente pago Bs {recibido:.2f} pero el total era Bs {total_bs:.2f}, asi que "
                f"faltan Bs {(total_bs - recibido):.2f}. Pidele con suavidad y sin reclamar que "
                f"complete ese monto restante para poder despachar su pedido"
            )
        notificar_cliente_pago.apply_async((telefono, situacion))
    return {"ok": True, "pago_id": pago_id, "estado": estado_final}


@router.post("/pagos/{pago_id}/reabrir")
async def reabrir_pago(pago_id: int, _: str = Depends(usuario_actual)):
    """Devuelve un pago rechazado o parcial a la bandeja "Por verificar"
    (estado 'reportado'). Correccion interna de la dueña: NO se notifica al cliente."""
    factory = get_session_factory()
    async with factory() as session:
        pago = await session.get(Pago, pago_id)
        if pago is None:
            raise HTTPException(status_code=404, detail="Pago no encontrado")
        if pago.estado not in ("rechazado", "parcial"):
            raise HTTPException(
                status_code=409,
                detail="Solo se puede reabrir un pago rechazado o parcial.",
            )
        pago.estado = "reportado"
        pago.motivo_rechazo = None
        pago.updated_at = now_utc()
        await session.commit()
    return {"ok": True}


@router.post("/pagos/{pago_id}/anular")
async def anular_pago(pago_id: int, usuario: str = Depends(usuario_actual)):
    """REVERSA SEGURA de un pago confirmado por error. Pasa el pago a 'rechazado'
    y revierte el pedido a 'esperando_pago', de modo que /reporte (que suma pagos
    confirmados) deje de contarlo. NO toca montos. Correccion interna de la dueña:
    NO se notifica al cliente. El registro se conserva (no se borra fisicamente)."""
    factory = get_session_factory()
    async with factory() as session:
        pago = await session.get(Pago, pago_id)
        if pago is None:
            raise HTTPException(status_code=404, detail="Pago no encontrado")
        if pago.estado != "confirmado":
            raise HTTPException(
                status_code=409,
                detail="Solo se puede anular un pago confirmado.",
            )
        pago.estado = "rechazado"
        pago.confirmado_por = usuario
        pago.motivo_rechazo = "anulado por la dueña"
        pago.updated_at = now_utc()
        pedido = await session.get(Pedido, pago.pedido_id)
        if pedido is not None and pedido.estado == "pagado":
            pedido.estado = "esperando_pago"
        await session.commit()
    return {"ok": True}


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
