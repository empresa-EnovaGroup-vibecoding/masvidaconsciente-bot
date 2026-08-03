"""API REST que alimenta el dashboard. Todo protegido con login (JWT),
excepto el propio login."""
import json
import logging
import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, StringConstraints
from sqlalchemy import delete, func, select

from app.api.security import (
    crear_token,
    leer_rol,
    proveedora_actual,
    usuario_actual,
    verify_password,
)
from app.config import get_settings
from app.models import (
    CatalogoPdf,
    Cliente,
    Configuracion,
    Conocimiento,
    Feriado,
    Intervencion,
    Mensaje,
    MetodoPago,
    Pago,
    Pedido,
    PrecioDia,
    Producto,
    ProductoMedia,
    ProductoVariante,
    Usuario,
    ZonaEntrega,
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
logger = logging.getLogger(__name__)
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
    # Días de anticipación que necesita ESTE producto (0 = mismo día si hay stock).
    dias_anticipacion: int = 0
    disponible: bool = True


class FeriadoIn(BaseModel):
    fecha: str  # AAAA-MM-DD
    motivo: str | None = None


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
    # Días en que el negocio SÍ entrega (separados por coma). Es un CANDADO: el código valida
    # la fecha de entrega contra esto y el bot no puede prometer un día cerrado.
    "dias_entrega",
    # Horario de ATENCIÓN (el bot responde igual fuera de hora, pero lo sabe y ajusta lo que promete).
    "hora_apertura",
    "hora_cierre",
    # Hasta qué hora se aceptan pedidos para el MISMO día. Pasada esa hora, el código deja de
    # permitir "hoy" y el bot ofrece el próximo día de entrega. Es un CANDADO.
    "hora_corte",
    # Modelo de IA conversacional, lo elige la PROVEEDORA (no la clienta). El bot
    # lo lee con leer_modelo_ia(). La voz (transcripción) va aparte y fija.
    "modelo_ia",
    # SINÓNIMOS DEL BUSCADOR: lo que el cliente DICE no siempre es lo que está ESCRITO en el
    # catálogo. Pide "bebidas" y en la base pone "Kombucha", "Kéfir", "Yogurt Kéfirado" —
    # ninguna contiene esa palabra, así que el buscador devolvía CERO y el bot decía "de eso no
    # tengo" sobre tres productos que SÍ vende. Formato: una línea por término,
    # "termino: palabra1, palabra2". Vacío = se usa el default de tools.py (_SINONIMOS_DEFAULT).
    "sinonimos_busqueda",
    # LOS DOS AGENTES (fase 5, migración 025). Palancas de la PROVEEDORA.
    # `agente_modo`: 'uno' (el agente único de siempre) | 'dos' (Operador + Voz).
    # `modelo_operador` / `modelo_voz`: ausentes ⇒ caen a `modelo_ia` (compatibilidad).
    "agente_modo",
    "modelo_operador",
    "modelo_voz",
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
    # El CÓDIGO DE BARRAS del tamaño: es lo que se cobra. El panel lo reenvía tal cual; si no
    # viene (pedido viejo), se busca por nombre — y solo vale si el producto tiene UN tamaño:
    # con varios NO se adivina (adivinar era exactamente la fuga de la Kombucha).
    variante_id: int | None = None
    cantidad: int  # PAQUETES completos, nunca unidades sueltas
    # Lo que el cliente eligió dentro del paquete (relleno, masa, sabor). No toca el precio,
    # pero la dueña lo necesita para cocinar: si el panel no lo reenvía, se PERDÍA al editar.
    opciones: str | None = None


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
            # ══ LA ENTREGA Y EL FLETE, QUE ESTABAN EN LA BD Y NO SALÍAN POR NINGÚN ENDPOINT ══
            # `entrega` se guarda desde la migración 016 y el envío desde la 023, pero este dict
            # nunca los devolvió. Consecuencias: el bloque del calendario del panel NUNCA llegó a
            # pintarse (el campo era opcional en TypeScript, así que compilaba sin una queja), y un
            # pedido de $23 mostraba ítems por $20 sin una sola línea que explicara los otros $3.
            # El flete es DINERO: si no se ve, su fuga no se puede auditar desde el panel.
            # (Auditoría 2026-08-02, API-4.)
            "entrega": p.entrega,
            "entrega_fecha": p.entrega_fecha.isoformat() if p.entrega_fecha else None,
            "zona_nombre": p.zona_nombre,
            "costo_envio": float(p.costo_envio or 0),
            # Lo que el panel necesita para cuadrar a ojo: ítems + envío = total.
            "subtotal_productos": float(p.total or 0) - float(p.costo_envio or 0),
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
    from app.agent.tools import _buscar_producto, _precio_efectivo, _validar_entrega
    from app.services.redis_client import borrar_cobro

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
            # El TAMAÑO es lo que se cobra. Si el ítem trae su `variante_id` (los pedidos nuevos
            # lo traen), se usa ESE. Si es un pedido viejo sin id, se busca por nombre y solo
            # vale si el producto tiene UN tamaño: con varios NO se adivina (era la fuga).
            variante = None
            if getattr(it, "variante_id", None):
                variante = await session.get(ProductoVariante, int(it.variante_id))
                # 🔴 SI LA DUEÑA CAMBIÓ EL PRODUCTO, MANDA EL NOMBRE — NO EL ID VIEJO.
                # El <select> del panel solo reescribe `producto` y deja intacto el `variante_id`
                # del ítem anterior. Como aquí ganaba SIEMPRE el id, elegir otro producto en el
                # desplegable **no hacía absolutamente nada**: salía "Guardado", sin error, y al
                # recargar reaparecía el producto de antes. No había forma de corregir un producto
                # mal tomado por el bot. (Auditoría 2026-08-02, API-1.)
                #
                # Solo se descarta el id cuando el nombre resuelve a un producto que EXISTE y es
                # OTRO: así un ítem viejo cuyo producto fue renombrado (el nombre ya no resuelve a
                # nada) sigue cobrándose por su id, como hasta ahora.
                if variante is not None and (it.producto or "").strip():
                    prod_del_id = await session.get(Producto, variante.producto_id)
                    otro = await _buscar_producto(session, it.producto, solo_disponibles=False)
                    if otro is not None and prod_del_id is not None and otro.id != prod_del_id.id:
                        variante = None  # cae al camino por nombre (que exige tamaño único)
            if variante is None:
                prod = await _buscar_producto(session, it.producto, solo_disponibles=False)
                if prod is None:
                    raise HTTPException(
                        status_code=400,
                        detail=f"No encontré el producto «{it.producto}» en el catálogo.",
                    )
                vs = (
                    await session.execute(
                        select(ProductoVariante)
                        .where(ProductoVariante.producto_id == prod.id)
                        .order_by(ProductoVariante.orden, ProductoVariante.id)
                    )
                ).scalars().all()
                if len(vs) != 1:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"«{prod.nombre}» tiene varios tamaños y cada uno cuesta distinto. "
                            "Elige el tamaño en el pedido antes de guardar."
                        ),
                    )
                variante = vs[0]
            prod = await session.get(Producto, variante.producto_id)
            # El precio EFECTIVO del TAMAÑO (fijo, o el del día). Antes hacía
            # `(prod.precio or 0) * cantidad`: al editar un pedido con un producto de PRECIO DEL
            # DÍA el total se recalculaba en $0 y el pedido quedaba GRATIS. Sin precio de hoy NO
            # se recalcula: se avisa, nunca se cobra $0.
            precio = await _precio_efectivo(session, variante)
            if precio is None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"«{prod.nombre}» ({variante.presentacion}) todavía no tiene precio de "
                        "hoy. Ponlo en «El bot te necesita» y vuelve a guardar el pedido."
                    ),
                )
            total += precio * cantidad
            items_pedido.append(
                {
                    "producto": prod.nombre,
                    "variante_id": variante.id,
                    "cantidad": cantidad,
                    "precio_unitario": float(precio),
                    "presentacion": variante.presentacion,
                    "opciones": (it.opciones or "").strip() or None,
                }
            )
        # ══ EL FLETE NO SE PIERDE AL CORREGIR LOS ITEMS ══
        # `total` de arriba son SOLO los productos. El envío ya está congelado en la fila desde
        # `registrar_pedido` y esta pantalla no lo edita, así que hay que volver a sumarlo. Sin
        # esta línea, corregir una cantidad borraba el envío del total mientras `costo_envio`
        # seguía con su valor: la dueña regalaba el flete en cada corrección, y
        # `generar_datos_pago` calculaba el 20% de divisas restando un envío que ya no estaba
        # sumado (($20−$3)×0,80+$3 = $16,60 en vez de $19,00).
        envio = Decimal(str(pedido.costo_envio or 0))
        total_final = total + envio

        # Cambiar los productos puede volver IMPOSIBLE la fecha ya acordada (meterle una torta
        # de 2 días de anticipación a un pedido para mañana). Se revalida con el MISMO calendario
        # que usa el bot: el panel no puede dejar vendida una fecha que el bot rechazaría.
        if pedido.entrega_fecha is not None:
            problema = await _validar_entrega(session, pedido.entrega_fecha, items_pedido)
            if problema is not None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Con esos productos ya no se puede entregar el "
                        f"{pedido.entrega_fecha.isoformat()}: {problema['motivo']}. La primera "
                        f"fecha posible es {problema['primera_fecha_valida'].isoformat()}. "
                        "Cámbiala en el pedido y vuelve a guardar."
                    ),
                )

        telefono_cliente = pedido.cliente_telefono
        pedido.items = items_pedido
        pedido.total = total_final
        await session.commit()

    # La COTIZACIÓN vieja ya no vale. Si ese cliente tenía un cobro en curso, los montos
    # cacheados (`cobro:` en Redis) son los del pedido ANTERIOR, y `registrar_comprobante` los
    # da por buenos porque el `pedido_id` sigue siendo el mismo: la corrección de la dueña se
    # ignoraba en silencio justo en el carril del dinero. Se tira para que el bot re-cotice.
    await borrar_cobro(telefono_cliente)
    return {
        "ok": True,
        "total_usd": float(total_final),
        "subtotal_productos": float(total),
        "costo_envio": float(envio),
        "items": items_pedido,
    }


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

        # LOS TAMAÑOS. El precio vive AQUÍ desde la migración 022 (la Kombucha de 350ml cuesta
        # $4 y la de 700ml $7). El campo `precio` del producto se sigue devolviendo para no
        # romper nada, pero YA NO manda: el bot cobra el del tamaño.
        tamanos: dict[int, list] = {}
        if ids:
            for v in (
                await session.execute(
                    select(ProductoVariante)
                    .where(ProductoVariante.producto_id.in_(ids))
                    .order_by(ProductoVariante.orden, ProductoVariante.id)
                )
            ).scalars().all():
                tamanos.setdefault(v.producto_id, []).append(v)
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
            "dias_anticipacion": p.dias_anticipacion or 0,
            "disponible": p.disponible,
            "imagen": r2.url_publica(primera_img[p.id]) if p.id in primera_img else None,
            "variantes": [
                {
                    "id": v.id,
                    "presentacion": v.presentacion,
                    "precio": float(v.precio) if v.precio is not None else None,
                    "sabores": v.sabores,
                    "disponible": v.disponible,
                    "orden": v.orden,
                }
                for v in (tamanos.get(p.id) or [])
            ],
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
            dias_anticipacion=max(0, int(datos.dias_anticipacion or 0)),
            disponible=datos.disponible,
        )
        # NOMBRE ÚNICO: dos productos con el mismo nombre fue exactamente la causa de la fuga
        # de la Kombucha (el bot cobraba siempre el primero). Si quiere otro precio, es un
        # TAMAÑO del mismo producto, no un producto nuevo.
        repe = (
            await session.execute(select(Producto.id).where(Producto.nombre.ilike(datos.nombre)))
        ).scalar_one_or_none()
        if repe is not None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Ya tienes un producto llamado '{datos.nombre}'. Si es el mismo producto en "
                    "otro tamaño (u otro precio), agrégalo como TAMAÑO dentro de ese producto, "
                    "no como un producto nuevo."
                ),
            )
        session.add(prod)
        await session.flush()
        # Y NACE CON SU PRIMER TAMAÑO, en la misma transacción: un producto sin tamaño no tiene
        # precio ni id_para_pedir ⇒ el bot NO PODRÍA VENDERLO, y sin un solo error en el log.
        session.add(ProductoVariante(
            producto_id=prod.id,
            presentacion=(datos.presentacion or "").strip() or "única",
            precio=Decimal(str(datos.precio)) if datos.precio is not None else None,
            disponible=datos.disponible,
            orden=0,
        ))
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

        repe = (
            await session.execute(
                select(Producto.id).where(
                    Producto.nombre.ilike(datos.nombre), Producto.id != producto_id
                )
            )
        ).scalar_one_or_none()
        if repe is not None:
            raise HTTPException(
                status_code=400,
                detail=f"Ya tienes otro producto llamado '{datos.nombre}'. Los nombres no se repiten.",
            )

        variantes = (
            await session.execute(
                select(ProductoVariante)
                .where(ProductoVariante.producto_id == producto_id)
                .order_by(ProductoVariante.orden, ProductoVariante.id)
            )
        ).scalars().all()

        # 🔴 UNA SOLA FUENTE DE VERDAD DEL PRECIO — cerrada la fuga B4.
        # El precio del tamaño se edita SOLO en la sección Tamaños (editar_variante). Este endpoint
        # (editar PRODUCTO) ya NO toca el precio del tamaño: si algo manda `precio` (un frontend
        # viejo cacheado, o la API), se RECHAZA en voz alta, jamás lo pisa en silencio —que era la
        # fuga: guardar el producto cobraba el precio VIEJO del campo legado. El precio inicial de
        # un producto NUEVO lo pone `crear_producto` (siembra el primer tamaño).
        nuevo_precio = Decimal(str(datos.precio)) if datos.precio is not None else None
        if datos.precio is not None and variantes:
            donde = "sus tamaños" if len(variantes) > 1 else "su tamaño"
            raise HTTPException(
                status_code=400,
                detail=f"El precio de '{prod.nombre}' vive en {donde}. Edítalo abajo, en Tamaños.",
            )
        if len(variantes) == 1:
            variantes[0].presentacion = (datos.presentacion or "").strip() or "única"
            variantes[0].disponible = datos.disponible

        prod.nombre = datos.nombre
        prod.categoria = datos.categoria
        prod.descripcion = datos.descripcion
        if datos.precio is not None:
            prod.precio = nuevo_precio  # espejo legado (el BOT ya no lo lee); solo si se mandó
        prod.presentacion = datos.presentacion
        prod.duracion = datos.duracion
        prod.se_congela = datos.se_congela
        prod.apto_diabeticos = datos.apto_diabeticos
        prod.info = datos.info
        prod.dias_anticipacion = max(0, int(datos.dias_anticipacion or 0))
        prod.disponible = datos.disponible
        await session.commit()
    return {"ok": True}


class DisponibleIn(BaseModel):
    disponible: bool


@router.patch("/productos/{producto_id}/disponible")
async def marcar_agotado(producto_id: int, datos: DisponibleIn, _: str = Depends(usuario_actual)):
    """El botón "Agotado" del catálogo. Endpoint PROPIO a propósito.

    Antes el panel reconstruía el producto ENTERO a mano y lo mandaba por PATCH: con un producto
    de VARIOS tamaños eso incluía un `precio` que el PATCH ahora rechaza (el precio vive en el
    tamaño) ⇒ marcar agotada la Kombucha habría FALLADO. Aquí solo se toca lo que se pidió.

    El agotado del PRODUCTO manda sobre todos sus tamaños (así lo lee el bot). Al re-activarlo,
    los tamaños que ella dejó agotados a mano SIGUEN agotados: no se resucita nada.
    """
    factory = get_session_factory()
    async with factory() as session:
        prod = await session.get(Producto, producto_id)
        if prod is None:
            raise HTTPException(status_code=404, detail="Producto no encontrado")
        prod.disponible = datos.disponible
        await session.commit()
    return {"ok": True, "disponible": datos.disponible}


# ─── LOS TAMAÑOS (lo que se COBRA) ───────────────────────────────────

class VarianteIn(BaseModel):
    presentacion: Annotated[
        str, StringConstraints(min_length=1, max_length=60, strip_whitespace=True)
    ]
    precio: float | None = None     # None = PRECIO DEL DÍA (lo pone la dueña cada día)
    sabores: str | None = None
    disponible: bool = True
    orden: int = 0


@router.post("/productos/{producto_id}/variantes")
async def crear_variante(producto_id: int, datos: VarianteIn, _: str = Depends(usuario_actual)):
    """Agrega un TAMAÑO a un producto (la Kombucha de 700ml, la torta de 1kg…)."""
    factory = get_session_factory()
    async with factory() as session:
        prod = await session.get(Producto, producto_id)
        if prod is None:
            raise HTTPException(status_code=404, detail="Producto no encontrado")
        repe = (
            await session.execute(
                select(ProductoVariante.id).where(
                    ProductoVariante.producto_id == producto_id,
                    ProductoVariante.presentacion.ilike(datos.presentacion),
                )
            )
        ).scalar_one_or_none()
        if repe is not None:
            raise HTTPException(
                status_code=400, detail=f"Ese producto ya tiene el tamaño '{datos.presentacion}'."
            )
        v = ProductoVariante(
            producto_id=producto_id,
            presentacion=datos.presentacion,
            precio=Decimal(str(datos.precio)) if datos.precio is not None else None,
            sabores=datos.sabores,
            disponible=datos.disponible,
            orden=datos.orden,
        )
        session.add(v)
        await session.commit()
        await session.refresh(v)
    return {"id": v.id}


@router.patch("/variantes/{variante_id}")
async def editar_variante(variante_id: int, datos: VarianteIn, _: str = Depends(usuario_actual)):
    factory = get_session_factory()
    async with factory() as session:
        v = await session.get(ProductoVariante, variante_id)
        if v is None:
            raise HTTPException(status_code=404, detail="Ese tamaño no existe")
        v.presentacion = datos.presentacion
        v.precio = Decimal(str(datos.precio)) if datos.precio is not None else None
        v.sabores = datos.sabores
        v.disponible = datos.disponible
        v.orden = datos.orden
        v.updated_at = now_utc()
        await session.commit()
    return {"ok": True}


@router.delete("/variantes/{variante_id}")
async def borrar_variante(variante_id: int, _: str = Depends(usuario_actual)):
    """Quita un tamaño. NO se puede quitar el ÚLTIMO: sin tamaño, el producto queda sin precio
    ni id con el que el bot pueda registrarlo ⇒ INVENDIBLE, y sin un solo error."""
    factory = get_session_factory()
    async with factory() as session:
        v = await session.get(ProductoVariante, variante_id)
        if v is None:
            raise HTTPException(status_code=404, detail="Ese tamaño no existe")
        cuantos = (
            await session.execute(
                select(func.count())
                .select_from(ProductoVariante)
                .where(ProductoVariante.producto_id == v.producto_id)
            )
        ).scalar_one()
        if cuantos <= 1:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No puedes quitar el único tamaño: el producto quedaría sin precio y el bot "
                    "no podría venderlo. Si ya no lo vendes, márcalo como agotado o bórralo."
                ),
            )
        await session.delete(v)
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
# Los límites de WhatsApp (video 16 MB · imagen 5 MB) viven en app/services/media_convert.py,
# que es quien deja CUALQUIER archivo subido en el formato que WhatsApp exige.


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
    """Sube una foto o video del producto a R2. En la BD se guarda solo la ruta (clave).

    🔴 EL FORMATO SE ARREGLA AQUÍ, EN LA PUERTA (2026-07-14): la dueña sube LO QUE SEA
    (el .mov del iPhone, un HEIC, un WebP) y el sistema lo convierte a lo que WhatsApp
    exige (video MP4/H.264 ≤16MB · imagen JPEG/PNG ≤5MB). Antes el .mov de la Torta keto
    se guardaba tal cual y el envío por WhatsApp iba a fallar SIEMPRE. Lo que queda en R2
    ya es enviable; si no se puede convertir, se RECHAZA con un mensaje claro."""
    from app.services import media_convert, r2

    if not r2.configurado():
        raise HTTPException(
            status_code=503, detail="El almacenamiento de fotos (R2) no está configurado"
        )
    ct = (archivo.content_type or "").lower()
    if ct.startswith("image/"):
        tipo = "imagen"
    elif ct.startswith("video/"):
        tipo = "video"
    else:
        raise HTTPException(status_code=400, detail="Solo se aceptan imágenes o videos")
    contenido = await archivo.read()
    # Tope de ENTRADA generoso (el video crudo del teléfono pesa mucho más que el
    # convertido): la conversión de abajo lo deja en el límite real de WhatsApp.
    if len(contenido) > 300 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="El archivo es muy grande (máximo 300 MB)")
    try:
        if tipo == "video":
            contenido, ct, ext = await media_convert.normalizar_video(contenido)
        else:
            contenido, ct, ext = await media_convert.normalizar_imagen(contenido, ct)
    except media_convert.MediaInvalida as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001 — mejor rechazar que guardar algo inenviable
        raise HTTPException(
            status_code=500, detail="No se pudo procesar el archivo; intenta de nuevo"
        ) from e
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

# Claves que SOLO puede ver y tocar la PROVEEDORA (Enova), nunca la clienta.
# `modelo_ia` ya estaba documentado así en CLAUDE.md §5: *"palanca de PROVEEDOR, no de la
# clienta; cuando la clienta tenga su propio rol/login se le esconde"*. Ese rol ya existe
# (migración 024), así que aquí se esconde de verdad — hasta hoy la whitelist era plana y la
# dueña podía cambiarle el modelo al bot desde la pantalla de Configuración.
CLAVES_PROVEEDORA = {"modelo_ia", "agente_modo", "modelo_operador", "modelo_voz"}


@router.get("/configuracion")
async def obtener_configuracion(email: str = Depends(usuario_actual)):
    """Devuelve los datos editables del negocio (nombre, ubicacion, Pago Movil,
    WhatsApp de avisos...). Son las claves que el bot lee para atender y cobrar.

    Las claves de PROVEEDORA se omiten si quien pregunta es la dueña.
    """
    es_proveedora = await leer_rol(email) == "proveedora"
    factory = get_session_factory()
    async with factory() as session:
        filas = (await session.execute(select(Configuracion))).scalars().all()
        actual = {f.clave: f.valor for f in filas}
    return {
        clave: actual.get(clave)
        for clave in CLAVES_CONFIG
        if es_proveedora or clave not in CLAVES_PROVEEDORA
    }


@router.put("/configuracion")
async def guardar_configuracion(datos: ConfiguracionIn, email: str = Depends(usuario_actual)):
    """Guarda (upsert) los datos del negocio. Ignora claves desconocidas por
    seguridad: solo se aceptan las de CLAVES_CONFIG.

    Y las de PROVEEDORA se rechazan (403) si quien las manda es la dueña. Se RECHAZA en vez de
    ignorarlas en silencio: si el panel manda una clave que no le toca, es un bug del panel y hay
    que verlo, no taparlo.
    """
    es_proveedora = await leer_rol(email) == "proveedora"
    if not es_proveedora:
        prohibidas = CLAVES_PROVEEDORA & set(datos.valores)
        if prohibidas:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Estas opciones solo las toca la proveedora (Enova): "
                    f"{', '.join(sorted(prohibidas))}."
                ),
            )
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


# ─── Las HERRAMIENTAS del agente (fase 4) — solo la proveedora ────────
#
# ⚠️ `tools_activas` NO está en CLAVES_CONFIG, y es a propósito: así el `PUT /configuracion`
# genérico —que descarta en silencio lo que no conoce— NO puede escribir esta clave NUNCA.
# Endpoint propio = validación propia. Es la misma razón por la que `mensajes.py` tiene el suyo.

class HerramientasIn(BaseModel):
    activas: list[str]


@router.get("/herramientas")
async def obtener_herramientas(_: str = Depends(proveedora_actual)):
    """Qué sabe hacer el bot, y qué se le puede apagar."""
    from app.services.tools_config import BLINDADAS, TOOLS, leer_tools_activas

    activas = await leer_tools_activas()
    return {
        "herramientas": [
            {
                "nombre": n,
                "etiqueta": m["etiqueta"],
                "descripcion": m["descripcion"],
                "pierde": m["pierde"],
                "activa": n in activas,
                "blindada": n in BLINDADAS,
                "motivo_blindaje": m.get("motivo_blindaje"),
            }
            for n, m in TOOLS.items()
        ]
    }


@router.put("/herramientas")
async def guardar_herramientas(datos: HerramientasIn, _: str = Depends(proveedora_actual)):
    """Enciende y apaga capacidades del bot. Se manda la lista COMPLETA de activas (idempotente).

    Se RECHAZA (400) el intento de apagar una BLINDADA en vez de ignorarlo en silencio: si el
    panel manda algo que no puede, es un bug del panel y hay que verlo, no taparlo. Mismo criterio
    que `PUT /tasa`, el otro sitio donde una validación protege el cobro.
    """
    from app.services.tools_config import BLINDADAS, CLAVE, TOOLS, serializar

    pedidas = set(datos.activas)
    if raras := pedidas - set(TOOLS):
        raise HTTPException(
            status_code=400, detail=f"Herramienta desconocida: {', '.join(sorted(raras))}"
        )
    if faltan := BLINDADAS - pedidas:
        raise HTTPException(
            status_code=400,
            detail=(
                "Estas no se pueden apagar (el cobro y las redes de seguridad dependen de ellas): "
                + ", ".join(sorted(faltan))
                + "."
            ),
        )
    factory = get_session_factory()
    async with factory() as session:
        fila = await session.get(Configuracion, CLAVE)
        valor = serializar(pedidas)
        if fila is None:
            session.add(Configuracion(clave=CLAVE, valor=valor, updated_at=now_utc()))
        else:
            fila.valor = valor
            fila.updated_at = now_utc()
        await session.commit()
    return {"ok": True, "activas": sorted(pedidas)}


# ─── Quién soy, y quiénes hay (roles — migración 024) ─────────────────

@router.get("/yo")
async def quien_soy(email: str = Depends(usuario_actual)):
    """El usuario de la sesión y su ROL. El panel lo usa para esconder lo que no le toca.

    Ojo: esconder en el frontend es COSMÉTICO. La puerta de verdad es `proveedora_actual` en el
    backend — quien quiera saltarse el panel y llamar la API a mano se come un 403 igual.
    """
    return {"email": email, "rol": await leer_rol(email)}


class UsuarioIn(BaseModel):
    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=120)]
    password: Annotated[str, StringConstraints(min_length=8, max_length=72)]
    nombre: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=80)] = None
    rol: str = "duena"


class RolIn(BaseModel):
    rol: str


@router.get("/usuarios")
async def listar_usuarios(_: str = Depends(proveedora_actual)):
    """Los usuarios del panel. Solo la proveedora."""
    factory = get_session_factory()
    async with factory() as session:
        filas = (
            await session.execute(select(Usuario).order_by(Usuario.id))
        ).scalars().all()
    settings = get_settings()
    return [
        {
            "id": u.id,
            "email": u.email,
            "nombre": u.nombre,
            "rol": u.rol,
            # La cuenta ADMIN_EMAIL no se puede degradar ni borrar: `_crear_admin` le fuerza
            # rol='proveedora' en CADA arranque. Es la red anti-bloqueo. El panel la pinta en gris.
            "protegido": u.email == settings.admin_email,
        }
        for u in filas
    ]


@router.post("/usuarios")
async def crear_usuario(datos: UsuarioIn, _: str = Depends(proveedora_actual)):
    """Crea un usuario del panel (p. ej. la cuenta de la DUEÑA). Solo la proveedora.

    Aquí es donde la separación de roles se vuelve útil: hasta hoy había UNA sola cuenta que
    compartían Enova y la clienta, así que el rol no podía servir de nada.
    """
    from app.api.security import hash_password

    if datos.rol not in ("proveedora", "duena"):
        raise HTTPException(status_code=400, detail="El rol debe ser 'proveedora' o 'duena'.")
    email = datos.email.lower()
    factory = get_session_factory()
    async with factory() as session:
        ya = (
            await session.execute(select(Usuario).where(Usuario.email == email))
        ).scalars().first()
        if ya is not None:
            raise HTTPException(status_code=400, detail="Ya existe un usuario con ese correo.")
        u = Usuario(
            email=email,
            password_hash=hash_password(datos.password),
            nombre=datos.nombre or None,
            rol=datos.rol,
        )
        session.add(u)
        await session.commit()
        await session.refresh(u)
    return {"id": u.id, "email": u.email, "nombre": u.nombre, "rol": u.rol}


@router.patch("/usuarios/{usuario_id}/rol")
async def cambiar_rol(usuario_id: int, datos: RolIn, _: str = Depends(proveedora_actual)):
    """Cambia el rol de un usuario. Solo la proveedora.

    🔒 DOS CANDADOS ANTI-BLOQUEO:
      · La cuenta ADMIN_EMAIL no se toca (además, `_crear_admin` la restauraría al arrancar).
      · No se puede dejar el sistema con CERO proveedoras: si esta es la última, se rechaza.
        Sin esto, un clic podría dejar el sistema sin nadie que pueda tocar el modelo de IA ni
        (desde la fase 4) las herramientas del agente.
    """
    if datos.rol not in ("proveedora", "duena"):
        raise HTTPException(status_code=400, detail="El rol debe ser 'proveedora' o 'duena'.")
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as session:
        u = await session.get(Usuario, usuario_id)
        if u is None:
            raise HTTPException(status_code=404, detail="Ese usuario no existe.")
        if u.email == settings.admin_email:
            raise HTTPException(
                status_code=400,
                detail="La cuenta principal siempre es la proveedora: no se puede degradar.",
            )
        if u.rol == "proveedora" and datos.rol != "proveedora":
            cuantas = (
                await session.execute(
                    select(func.count()).select_from(Usuario).where(Usuario.rol == "proveedora")
                )
            ).scalar() or 0
            if cuantas <= 1:
                raise HTTPException(
                    status_code=400,
                    detail="Es la única proveedora: si la degradas, nadie podría volver a entrar.",
                )
        u.rol = datos.rol
        await session.commit()
    return {"ok": True, "id": usuario_id, "rol": datos.rol}


@router.delete("/usuarios/{usuario_id}")
async def borrar_usuario(usuario_id: int, _: str = Depends(proveedora_actual)):
    """Borra un usuario del panel. Solo la proveedora. Mismos candados anti-bloqueo."""
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as session:
        u = await session.get(Usuario, usuario_id)
        if u is None:
            raise HTTPException(status_code=404, detail="Ese usuario no existe.")
        if u.email == settings.admin_email:
            raise HTTPException(
                status_code=400, detail="La cuenta principal no se puede borrar."
            )
        if u.rol == "proveedora":
            cuantas = (
                await session.execute(
                    select(func.count()).select_from(Usuario).where(Usuario.rol == "proveedora")
                )
            ).scalar() or 0
            if cuantas <= 1:
                raise HTTPException(
                    status_code=400,
                    detail="Es la única proveedora: si la borras, nadie podría volver a entrar.",
                )
        await session.delete(u)
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


# ─── Modelos de OpenRouter (para el selector del panel) ──────────────

@router.get("/modelos-openrouter")
async def modelos_openrouter(_: str = Depends(proveedora_actual)):
    """Todos los modelos de OpenRouter, para el selector del panel. El panel los agrupa por
    PROVEEDOR con el prefijo del id ('anthropic/…', 'google/…', 'x-ai/…'). Cacheado 1 h para no
    pegarle a OpenRouter en cada carga. Solo la proveedora (Enova) lo ve."""
    from app.services.redis_client import get_cache, set_cache

    cache = await get_cache("cache:openrouter_modelos")
    if cache:
        return json.loads(cache)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://openrouter.ai/api/v1/models")
            resp.raise_for_status()
            data = resp.json().get("data", [])
    except Exception:  # noqa: BLE001 — si OpenRouter no responde, el panel cae al modo "pegar ID"
        logger.warning("No se pudo traer la lista de modelos de OpenRouter")
        return {"modelos": []}
    modelos = sorted(
        ({"id": m["id"], "name": m.get("name") or m["id"]} for m in data if m.get("id")),
        key=lambda m: m["id"],
    )
    salida = {"modelos": modelos}
    try:
        await set_cache("cache:openrouter_modelos", json.dumps(salida), 3600)
    except Exception:  # noqa: BLE001
        pass
    return salida


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
            resultado.append(
                {
                    "telefono": c.telefono,
                    "nombre": c.nombre,
                    # Un chat que la dueña ABRIÓ desde su celular todavía no tiene mensajes
                    # nuestros: antes se descartaba (`continue`) y el chat NO APARECÍA en el
                    # panel — ella no podía seguirlo desde aquí.
                    "ultimo_mensaje": ultimo.contenido if ultimo else None,
                    "ultima_interaccion": c.ultima_interaccion.isoformat(),
                    "bot_pausado": c.bot_pausado,
                    "pausado_por": c.pausado_por,  # 'dueña' = lo tomaste tú
                    "no_leidos": c.no_leidos,
                }
            )
    return resultado


@router.get("/conversaciones-resumen")
async def resumen_conversaciones(_: str = Depends(usuario_actual)):
    """Cuántos chats tiene TOMADOS la dueña (el bot está callado ahí). El panel lo muestra
    arriba: sin este aviso, un "ya te escribo" desde el celular deja el bot mudo en ese chat
    PARA SIEMPRE y nadie se entera (la pausa no caduca, por decisión de Maired)."""
    factory = get_session_factory()
    async with factory() as session:
        n = (
            await session.execute(
                select(func.count())
                .select_from(Cliente)
                .where(Cliente.bot_pausado.is_(True), Cliente.pausado_por == "dueña")
            )
        ).scalar_one()
        sin_leer = (
            await session.execute(
                select(func.count()).select_from(Cliente).where(Cliente.no_leidos > 0)
            )
        ).scalar_one()
    return {"chats_tomados": int(n), "chats_sin_leer": int(sin_leer)}


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
        {
            "id": m.id,
            "rol": m.rol,  # user (cliente) | assistant (bot) | owner (la dueña)
            "contenido": m.contenido,
            "fecha": m.created_at.isoformat(),
            "tipo": m.tipo,
            "media_id": m.media_id,
            # ¿hay un archivo que se pueda VER? El panel lo descarga con su token desde
            # /api/mensajes/{id}/media — nunca una URL pública: un comprobante trae datos
            # bancarios.
            #
            # Cuenta `media_id` además de `media_url`: las fotos que la dueña manda desde SU
            # celular llegan por el eco de Meta con `media_id` pero SIN archivo descargado, así
            # que con la condición vieja (`bool(m.media_url)`) su burbuja salía VACÍA. El
            # endpoint ahora sabe bajarlas de Meta al vuelo.
            "tiene_media": bool(m.media_url or m.media_id),
            "estado": m.estado,  # enviado|entregado|leido|fallido (None = no lo enviamos nosotros)
            "error": m.error,
        }
        for m in mensajes
    ]


@router.get("/mensajes/{mensaje_id}/media")
async def ver_media_mensaje(mensaje_id: int, _: str = Depends(usuario_actual)):
    """Sirve el archivo de UN mensaje del hilo, venga de donde venga.

    PROTEGIDO con login: un comprobante trae datos bancarios. Y se pide por el **id numérico
    del mensaje**, nunca por el nombre del archivo: si la ruta viniera en la URL, un
    `../../etc/passwd` dejaría leer cualquier archivo del servidor.

    🔴 TRES ORÍGENES, Y HASTA HOY SOLO SERVÍA UNO:

    1. **Disco local** — el comprobante que sube el cliente. Es lo único que funcionaba.
    2. **URL http(s)** — las fotos de producto que manda el bot (viven en Cloudflare R2) y el
       catálogo PDF. `os.path.exists("https://…")` da **False**, así que esto devolvía 404 y el
       panel pintaba *"No se pudo cargar el archivo"*. Por eso arreglar la persistencia (que el
       bot GUARDE la fila) no bastaba: sin esto, el dato existiría y **seguiría sin verse**.
    3. **Solo `media_id`, sin archivo** — las fotos que la dueña manda desde SU celular. El eco
       de Meta trae el `media_id` pero nadie descargaba el archivo, así que su burbuja salía
       vacía. Ahora se baja de Meta al vuelo (los media_id de Meta duran ~30 días).

    Se hace PROXY, no redirect: así el archivo sigue viajando con el token del panel, el mismo
    origen y sin depender de que el bucket de R2 mande cabeceras CORS.
    """
    factory = get_session_factory()
    async with factory() as session:
        m = await session.get(Mensaje, mensaje_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Ese mensaje no existe")

    # 2) Remoto (R2 / el propio bot): se proxea en streaming — un video puede pesar.
    if m.media_url and m.media_url.startswith(("http://", "https://")):
        cliente = httpx.AsyncClient(timeout=30, follow_redirects=True)
        try:
            req = cliente.build_request("GET", m.media_url)
            resp = await cliente.send(req, stream=True)
            if resp.status_code >= 400:
                await resp.aclose()
                await cliente.aclose()
                raise HTTPException(status_code=404, detail="Archivo no disponible")
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001
            await cliente.aclose()
            logger.exception("No se pudo traer el archivo remoto del mensaje %s", mensaje_id)
            raise HTTPException(status_code=404, detail="Archivo no disponible") from None

        async def _stream():
            try:
                async for trozo in resp.aiter_bytes():
                    yield trozo
            finally:
                await resp.aclose()
                await cliente.aclose()

        return StreamingResponse(
            _stream(),
            media_type=resp.headers.get("content-type") or m.media_mime or "application/octet-stream",
        )

    # 1) Disco local (el comprobante del cliente).
    if m.media_url and os.path.exists(m.media_url):
        return FileResponse(m.media_url, media_type=m.media_mime or None)

    # 3) Sin archivo pero CON media_id: se baja de Meta al vuelo (la foto de la dueña).
    if m.media_id:
        try:
            from app.services.meta_client import descargar_media

            contenido, mime = await descargar_media(m.media_id)
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo bajar de Meta el media %s", m.media_id)
            raise HTTPException(status_code=404, detail="Archivo no disponible") from None
        return Response(content=contenido, media_type=mime or m.media_mime or None)

    raise HTTPException(status_code=404, detail="Ese mensaje no tiene archivo")


@router.post("/conversaciones/{telefono}/leido")
async def marcar_leido(telefono: str, _: str = Depends(usuario_actual)):
    """Abrir el chat en el panel lo marca como leído (pone el contador a cero)."""
    factory = get_session_factory()
    async with factory() as session:
        cliente = (
            await session.execute(select(Cliente).where(Cliente.telefono == telefono))
        ).scalar_one_or_none()
        if cliente is None:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")
        cliente.no_leidos = 0
        await session.commit()
    return {"ok": True}


# ─── LA BANDEJA: responder desde el panel ────────────────────────────
# La regla de Meta: solo se puede escribir texto libre dentro de las 24h desde el ÚLTIMO
# mensaje DEL CLIENTE. Fuera de eso hay que usar una plantilla aprobada (eso es la Fase 5).
VENTANA_HORAS = 24


def _ventana(cliente: Cliente) -> dict:
    """Cuánto le queda a la dueña para poder responder. NULL ⇒ CERRADA (fail-closed)."""
    if cliente.ultimo_entrante_at is None:
        return {"abierta": False, "minutos_restantes": 0, "cierra": None}
    cierra = cliente.ultimo_entrante_at + timedelta(hours=VENTANA_HORAS)
    restante = (cierra - now_utc()).total_seconds() / 60
    return {
        "abierta": restante > 0,
        "minutos_restantes": max(0, int(restante)),
        "cierra": cierra.isoformat(),
    }


@router.get("/conversaciones/{telefono}/estado")
async def estado_conversacion(telefono: str, _: str = Depends(usuario_actual)):
    """Si la dueña PUEDE responder ahora mismo, y quién está atendiendo el chat.
    Endpoint aparte para no cambiarle la forma a `GET /conversaciones/{telefono}`."""
    factory = get_session_factory()
    async with factory() as session:
        cliente = (
            await session.execute(select(Cliente).where(Cliente.telefono == telefono))
        ).scalar_one_or_none()
        if cliente is None:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")
        return {
            "telefono": cliente.telefono,
            "nombre": cliente.nombre,
            "bot_pausado": cliente.bot_pausado,  # True = el bot NO responde en ese chat
            # 'dueña' = lo tomaste TÚ · 'bot' = el bot se pausó solo al escalarte algo
            "pausado_por": cliente.pausado_por,
            "no_leidos": cliente.no_leidos,
            "ventana": _ventana(cliente),
            "es_simulador": telefono.startswith(SIMULADOR),
        }


class MensajeDueña(BaseModel):
    texto: Annotated[str, StringConstraints(min_length=1, max_length=4000, strip_whitespace=True)]


@router.post("/conversaciones/{telefono}/mensajes")
async def responder_como_dueña(
    telefono: str, datos: MensajeDueña, _: str = Depends(usuario_actual)
):
    """LA DUEÑA RESPONDE DESDE EL PANEL. Hace cinco cosas, en este orden:

    1. Comprueba la VENTANA DE 24H. Cerrada ⇒ 409 y **no se intenta enviar**. Un envío fuera de
       ventana lo rechaza Meta y le baja la calidad al número; siendo Enova Tech Provider, eso
       arriesga la cuenta de TODOS los clientes. Se avisa antes, no se falla en silencio.
    2. Envía por WhatsApp.
    3. Lo guarda con rol='owner' + el id que devolvió Meta (para casar después el estado).
    4. PAUSA EL BOT en ese chat. El relevo es automático al primer mensaje humano: si ella
       habla, el bot se calla. Sin depender de que se acuerde de apretar un botón.
    5. Se lo mete al bot en la memoria (Redis) como 'assistant', para que al devolverle el chat
       NO se contradiga ni repita lo que ella ya dijo. En Postgres queda como 'owner' (la
       verdad de quién habló); ante el cliente hay UNA sola voz.

    El tope de gasto anti-abuso NO aplica aquí: eso frena al BOT, no a la humana.
    """
    from httpx import HTTPError

    from app.services.meta_client import enviar_texto
    from app.services.redis_client import guardar_historial

    texto = datos.texto
    factory = get_session_factory()
    async with factory() as session:
        cliente = (
            await session.execute(select(Cliente).where(Cliente.telefono == telefono))
        ).scalar_one_or_none()
        if cliente is None:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")

        if telefono.startswith(SIMULADOR):
            raise HTTPException(
                status_code=400,
                detail="Este es el chat de prueba del simulador: no hay un WhatsApp real "
                       "del otro lado.",
            )

        # 1) LA VENTANA (antes de tocar nada).
        ventana = _ventana(cliente)
        if not ventana["abierta"]:
            raise HTTPException(
                status_code=409,
                detail="Pasaron más de 24 horas desde el último mensaje del cliente. "
                       "WhatsApp no deja escribirle texto libre hasta que él vuelva a "
                       "escribir. (Las plantillas para reabrir la conversación son el "
                       "siguiente paso.)",
            )

        # 2) Enviar. Si Meta lo rechaza, queda ESCRITO en el hilo, en rojo: nada en silencio.
        try:
            resp = await enviar_texto(telefono, texto)
            wa_id = (resp.get("messages") or [{}])[0].get("id")
        except HTTPError as exc:
            session.add(Mensaje(
                cliente_telefono=telefono, rol="owner", contenido=texto,
                tipo="text", estado="fallido", error=str(exc)[:400],
            ))
            await session.commit()
            raise HTTPException(
                status_code=502, detail=f"WhatsApp no aceptó el mensaje: {exc}"
            ) from exc

        # 3) Guardar quién habló de verdad.
        session.add(Mensaje(
            cliente_telefono=telefono, rol="owner", contenido=texto,
            tipo="text", wa_message_id=wa_id, estado="enviado",
        ))

        # 4) El bot se calla: ella tomó el chat. Firmado 'dueña' → el bot NO manda nada más
        #    (a diferencia de cuando se pausa él solo para escalar: eso va firmado 'bot').
        cliente.bot_pausado = True
        cliente.pausado_por = "dueña"
        cliente.no_leidos = 0
        cliente.ultima_interaccion = now_utc()

        # Si había un aviso de "el bot te necesita" abierto, ya lo atendió.
        for aviso in (
            await session.execute(
                select(Intervencion).where(
                    Intervencion.cliente_telefono == telefono,
                    Intervencion.estado == "pendiente",
                )
            )
        ).scalars().all():
            aviso.estado = "resuelta"
            aviso.resuelta_at = now_utc()

        await session.commit()

    # 5) El bot hereda lo que ella prometió (si falla Redis, el mensaje YA se envió: no se
    #    revierte nada, solo se avisa en el log).
    try:
        await guardar_historial(telefono, "assistant", texto)
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo meter en la memoria del bot el mensaje de la dueña")

    return {"ok": True, "wa_message_id": wa_id, "bot_pausado": True}


@router.delete("/conversaciones/{telefono}")
async def borrar_conversacion(telefono: str, _: str = Depends(usuario_actual)):
    """Borra TODA la conversación de un cliente: sus mensajes, los avisos de la bandeja
    ('te necesita') y la caché del bot en Redis (historial, buffer, cobro en curso…).
    NO toca al cliente, ni sus pedidos, ni sus pagos: el registro del cobro queda intacto
    en Postgres. Solo limpia el chat del panel y el estado transitorio del bot."""
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(delete(Mensaje).where(Mensaje.cliente_telefono == telefono))
        await session.execute(
            delete(Intervencion).where(Intervencion.cliente_telefono == telefono)
        )
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


def _disparar_retomar(telefono: str, nombre: str | None, pausado_por: str | None = None) -> None:
    """LA DUEÑA DEVOLVIÓ EL CHAT: que el bot CONTESTE lo que quedó pendiente.

    `pausado_por` (la FIRMA del freno, leída ANTES de borrarla) decide si el cliente sigue
    esperando: si lo pausó el BOT, es que escaló algo y su "te lo confirmo enseguida" era un
    PAGARÉ, no una respuesta — y hay que pagarlo. Sin esta firma, el bot se quedaba MUDO
    justo en el caso estrella (el precio del día). Ver `_retomar` en tasks.py.

    Hasta hoy, devolver el chat solo apagaba la bandera de pausa. Pero el bot únicamente habla
    cuando ENTRA un mensaje nuevo por el webhook, y los mensajes que el cliente escribió durante
    la pausa YA entraron ⇒ nadie disparaba nada ⇒ el bot se quedaba MUDO y la venta se moría.

    El botón no cambia: el que decide si hay algo que contestar es el sistema (la tarea mira si el
    último turno es del cliente). Y ese click ES la aprobación humana que exige Meta.

    Se llama SIEMPRE DESPUÉS del commit: la tarea vuelve a leer al cliente de la BD y tiene que
    verlo ya despausado, o se callaría creyendo que la dueña sigue atendiendo.
    """
    if telefono.startswith(SIMULADOR):
        return  # el chat de prueba no tiene un WhatsApp del otro lado
    try:
        from app.workers.tasks import retomar_chat

        retomar_chat.apply_async((telefono, nombre, pausado_por))
    except Exception:  # noqa: BLE001 — el chat YA quedó devuelto; esto es solo el disparador
        logger.exception("No se pudo encolar el retomar del chat de %s", telefono)


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
        # La FIRMA de quién había apretado el freno, ANTES de borrarla: decide si el cliente
        # todavía está esperando una respuesta que el bot le prometió. Ver `_retomar`.
        firma_previa = cliente.pausado_por
        cliente.bot_pausado = datos.pausado
        # Este botón lo aprieta UNA PERSONA: queda firmado como 'dueña' para que el bot se
        # calle del todo. Al devolver el chat, la firma se borra. Ver migración 020.
        cliente.pausado_por = "dueña" if datos.pausado else None
        nombre = cliente.nombre
        await session.commit()
    if not datos.pausado:
        _disparar_retomar(telefono, nombre, firma_previa)
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
    # El precio del día es POR TAMAÑO (la torta de 250g y la de 1kg cuestan distinto).
    # `producto_id` se mantiene por compatibilidad, pero manda `variante_id`.
    producto_id: int | None = None
    variante_id: int | None = None
    precio: float
    nota: str | None = None


_MOTIVO_TEXTO = {
    "precio_del_dia": "Te piden un precio del día",
    "no_se": "El bot no sabe algo",
    "pide_persona": "El cliente pide hablar con una persona",
    "reclamo": "El cliente está reclamando",
    # 🔴 EL VIGILANTE. Un cliente escribió y NADIE le contestó — ni el bot ni tú. Este aviso no lo
    # crea el agente: lo crea un barredor que compara `clientes.ultimo_entrante_at` (que escribe el
    # WEBHOOK, antes de que el worker pueda fallar) contra la tabla `mensajes`. Por eso caza incluso
    # los mensajes que nunca llegaron a guardarse — que son justo los de la semana muda del 10-17
    # de julio, cuando el bot llevaba días sin responder y la bandeja estaba vacía.
    "bot_callado": "Un cliente escribió y el bot NO le respondió",
    # Redes nuevas del carril del comprobante y del turno (auditoría 2026-08-02). Sin estas
    # entradas la bandeja mostraría el motivo crudo: no rompe nada, pero se lee feo.
    "sin_respuesta": "Un cliente quedó sin respuesta",
    "mensaje_a_medias": "Al cliente le llegó solo parte del mensaje",
    "comprobante_ilegible": "No se pudo leer un comprobante: míralo tú",
    "comprobante_sin_procesar": "Entró un comprobante y no se pudo procesar",
    "comprobante_sin_archivo": "Se registró un pago pero el archivo no se pudo guardar",
    # Le devolviste el chat al bot, pero pasaron +24h desde el último mensaje del cliente:
    # WhatsApp no deja escribirle. El bot NO le escribió (lado seguro) y te lo avisa a ti.
    "ventana_cerrada": "Pasaron 24h: el bot no puede escribirle",
    # La red del dinero tumbó lo que el bot iba a decir (un monto inventado, una frase prohibida).
    # Al cliente solo le llegó un acuse: la conversación la termina una persona.
    "bot_frenado": "Frené un mensaje del bot: entra tú",
    # 🔴 LA DUEÑA RESPONDIÓ DESDE SU CELULAR (el eco). El chat quedó en SUS manos y el bot se
    # calló ahí. Este aviso no es un problema que atender: ES EL BOTÓN que le devuelve el chat al
    # bot cuando termine (resolver con reactivar=True). Por eso el barredor no lo cierra nunca
    # (`cerrar_avisos_ya_atendidos`) y `pedir_ayuda` no le pisa el motivo. Ver SIL-9.
    "chat_tomado": "Le respondiste tú: devuélvele el chat al bot",
    # Un cliente pasó el tope de mensajes del día: el bot dejó de contestarle para no dispararse
    # el gasto de IA, pero sus mensajes SE SIGUEN GUARDANDO en el hilo. La sigue una persona.
    "tope_diario": "Un cliente pasó el tope del día: contéstale tú",
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
    para que vuelva a atender a ese cliente — y le CONTESTE lo que quedó pendiente."""
    factory = get_session_factory()
    devolver: tuple[str, str | None, str | None] | None = None
    async with factory() as session:
        inter = await session.get(Intervencion, intervencion_id)
        if inter is None:
            raise HTTPException(status_code=404, detail="Aviso no encontrado")
        inter.estado = "resuelta"
        inter.resuelta_at = now_utc()
        # 🔴 HAY AVISOS QUE NO SON UN CHAT PAUSADO, y "reactivar" ahí hace daño de verdad.
        #
        # `tope_diario` (SIL-7) es el caso: ese aviso NO pausó nada — el bot nunca se calló, solo
        # dejó de gastar IA con ese cliente por hoy (`_excede_tope`, webhook/router.py). Así que
        # este bloque no estaría DESHACIENDO su pausa: estaría borrando LA QUE HUBIERA, sea de
        # quien sea. Un clic en "ya lo atendí" sobre el aviso del tope le devolvería al bot un
        # chat que ELLA tomó a propósito (pausado_por='dueña'), y el bot volvería a hablar encima
        # de una conversación suya. Encima dispara `_retomar`, que no consulta el tope por ningún
        # lado: si el historial de Redis termina en un turno del cliente (pasa cuando los globos
        # del turno anterior fallaron, `_algo_llego`), el bot le contesta igual y el tope queda
        # anulado con un clic.
        #
        # `tasa_congelada` (cuando llegue) es un aviso del SISTEMA, no del chat de nadie.
        reactivar = reactivar and inter.motivo not in ("tope_diario", "tasa_congelada")
        if reactivar:
            cliente = (
                await session.execute(
                    select(Cliente).where(Cliente.telefono == inter.cliente_telefono)
                )
            ).scalar_one_or_none()
            if cliente is not None:
                # Este es EL CASO ESTRELLA: casi siempre `pausado_por` == 'bot' (el bot escaló
                # porque no sabía el precio del día). Esa firma es lo que le dice al bot que el
                # cliente SIGUE esperando, aunque el último mensaje del chat sea suyo.
                devolver = (cliente.telefono, cliente.nombre, cliente.pausado_por)
                cliente.bot_pausado = False
                cliente.pausado_por = None
        await session.commit()
    if devolver:
        _disparar_retomar(*devolver)
    return {"ok": True, "bot_reactivado": reactivar}


@router.get("/precio-dia")
async def ver_precios_dia(_: str = Depends(usuario_actual)):
    """Los TAMAÑOS de precio VARIABLE (precio vacío a propósito: su costo cambia de un día a
    otro) y el precio que la dueña les dio HOY.

    Va POR TAMAÑO, no por producto: la torta de 250g y la de 1kg tienen precios distintos, y
    hasta hoy el índice de la tabla IMPEDÍA guardar los dos el mismo día."""
    factory = get_session_factory()
    async with factory() as session:
        filas = (
            await session.execute(
                select(Producto, ProductoVariante)
                .join(ProductoVariante, ProductoVariante.producto_id == Producto.id)
                .where(ProductoVariante.precio.is_(None))
                .order_by(Producto.nombre, ProductoVariante.orden)
            )
        ).all()
        hoy = dict(
            (
                await session.execute(
                    select(PrecioDia.variante_id, PrecioDia.precio).where(
                        PrecioDia.fecha == hoy_venezuela(),
                        PrecioDia.variante_id.is_not(None),
                    )
                )
            ).all()
        )
    return [
        {
            "producto_id": p.id,
            "variante_id": v.id,
            "nombre": p.nombre,
            "presentacion": None if v.presentacion == "única" else v.presentacion,
            "precio_hoy": float(hoy[v.id]) if v.id in hoy else None,
        }
        for p, v in filas
    ]


@router.put("/precio-dia")
async def poner_precio_dia(datos: PrecioDiaIn, _: str = Depends(usuario_actual)):
    """La dueña dice cuánto está HOY ese TAMAÑO. Vale SOLO por hoy: mañana el bot se lo vuelve
    a preguntar (un precio viejo jamás se reutiliza). Si lo corrige, se sobreescribe.

    Va por TAMAÑO: la torta de 250g y la de 1kg tienen precios distintos, y hasta hoy el índice
    de la tabla IMPEDÍA guardar los dos el mismo día."""
    if datos.precio <= 0:
        raise HTTPException(status_code=400, detail="El precio debe ser mayor que 0.")
    factory = get_session_factory()
    async with factory() as session:
        variante = None
        if datos.variante_id:
            variante = await session.get(ProductoVariante, datos.variante_id)
        elif datos.producto_id:
            # Compatibilidad: si solo mandan el producto, vale SOLO si tiene un tamaño.
            vs = (
                await session.execute(
                    select(ProductoVariante).where(
                        ProductoVariante.producto_id == datos.producto_id
                    )
                )
            ).scalars().all()
            if len(vs) == 1:
                variante = vs[0]
        if variante is None:
            raise HTTPException(
                status_code=400,
                detail="Dime de qué TAMAÑO es este precio (cada tamaño tiene el suyo).",
            )
        fila = (
            await session.execute(
                select(PrecioDia).where(
                    PrecioDia.variante_id == variante.id, PrecioDia.fecha == hoy_venezuela()
                )
            )
        ).scalar_one_or_none()
        if fila is None:
            session.add(
                PrecioDia(
                    producto_id=variante.producto_id,
                    variante_id=variante.id,
                    precio=Decimal(str(datos.precio)),
                    nota=datos.nota,
                    fecha=hoy_venezuela(),
                )
            )
        else:
            fila.precio = Decimal(str(datos.precio))
            fila.nota = datos.nota
        await session.commit()
    # OJO: NO devolver `prod.nombre` — `prod` no existe aquí y la sesión ya cerró. Devolverlo
    # lanzaba NameError → 500: el precio SÍ se guardaba (el commit ya pasó) pero el panel decía
    # "no se pudo guardar", y la dueña reintentaba con otro número. (Fuga B3.)
    return {"ok": True, "precio_hoy": datos.precio}


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

# ─── ENTREGAS: las ZONAS y su costo (el "código de barras" del envío) ─
#
# 🔴 Existe por un caso REAL (2026-07-13): el bot le dijo a una clienta "el total en bolívares es
# de $23 USD" porque sumó $20 del producto + $3 del delivery DE CABEZA. El prompt se lo prohibía
# dos veces. La causa de fondo: **el sistema no sabía cobrar delivery** — y lo que no existe, el
# modelo lo inventa. Aquí la dueña carga sus zonas y el CÓDIGO cobra el envío.

class ZonaIn(BaseModel):
    nombre: Annotated[str, StringConstraints(min_length=1, max_length=80, strip_whitespace=True)]
    costo: float = 0
    # Los barrios/urbanizaciones que caen en esta zona. Es lo que impide que el bot ADIVINE.
    referencias: str | None = None
    es_retiro: bool = False
    disponible: bool = True
    orden: int = 0


def _zona_json(z: ZonaEntrega) -> dict:
    return {
        "id": z.id,
        "nombre": z.nombre,
        "costo": float(z.costo or 0),
        "referencias": z.referencias,
        "es_retiro": z.es_retiro,
        "disponible": z.disponible,
        "orden": z.orden,
    }


@router.get("/zonas")
async def listar_zonas(_: str = Depends(usuario_actual)):
    """Las zonas de entrega con su costo (lo que el bot cobra por llevarlo)."""
    factory = get_session_factory()
    async with factory() as session:
        filas = (
            await session.execute(
                select(ZonaEntrega).order_by(ZonaEntrega.orden, ZonaEntrega.id)
            )
        ).scalars().all()
    return [_zona_json(z) for z in filas]


@router.post("/zonas")
async def crear_zona(datos: ZonaIn, _: str = Depends(usuario_actual)):
    """Crea una zona. NO se permiten dos zonas con el mismo nombre: el bot elegiría una al azar
    y cobraría el envío equivocado (la enfermedad de la Kombucha, otra vez)."""
    factory = get_session_factory()
    async with factory() as session:
        repetida = (
            await session.execute(
                select(ZonaEntrega).where(
                    func.lower(ZonaEntrega.nombre) == datos.nombre.strip().lower()
                )
            )
        ).scalar_one_or_none()
        if repetida is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Ya tienes una zona que se llama «{repetida.nombre}». Ponle otro nombre "
                       "o edita la que ya existe.",
            )
        if datos.costo < 0:
            raise HTTPException(status_code=400, detail="El costo del envío no puede ser negativo.")
        zona = ZonaEntrega(
            nombre=datos.nombre.strip(),
            costo=Decimal(str(datos.costo)),
            referencias=(datos.referencias or "").strip() or None,
            es_retiro=datos.es_retiro,
            disponible=datos.disponible,
            orden=datos.orden,
        )
        session.add(zona)
        await session.commit()
        await session.refresh(zona)
        return _zona_json(zona)


@router.put("/zonas/{zona_id}")
async def editar_zona(zona_id: int, datos: ZonaIn, _: str = Depends(usuario_actual)):
    """Edita una zona. Ojo: cambiar el costo NO cambia los pedidos YA hechos — cada pedido se
    guarda con el costo CONGELADO del día en que se hizo."""
    factory = get_session_factory()
    async with factory() as session:
        zona = await session.get(ZonaEntrega, zona_id)
        if zona is None:
            raise HTTPException(status_code=404, detail="Zona no encontrada")
        repetida = (
            await session.execute(
                select(ZonaEntrega).where(
                    func.lower(ZonaEntrega.nombre) == datos.nombre.strip().lower(),
                    ZonaEntrega.id != zona_id,
                )
            )
        ).scalar_one_or_none()
        if repetida is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Ya tienes otra zona que se llama «{repetida.nombre}».",
            )
        if datos.costo < 0:
            raise HTTPException(status_code=400, detail="El costo del envío no puede ser negativo.")
        zona.nombre = datos.nombre.strip()
        zona.costo = Decimal(str(datos.costo))
        zona.referencias = (datos.referencias or "").strip() or None
        zona.es_retiro = datos.es_retiro
        zona.disponible = datos.disponible
        zona.orden = datos.orden
        zona.updated_at = now_utc()
        await session.commit()
        await session.refresh(zona)
        return _zona_json(zona)


@router.delete("/zonas/{zona_id}")
async def borrar_zona(zona_id: int, _: str = Depends(usuario_actual)):
    """Borra una zona. Los pedidos que ya la usaron NO se tocan: conservan su zona y su costo
    congelados (por eso `pedidos.zona_id` es ON DELETE SET NULL)."""
    factory = get_session_factory()
    async with factory() as session:
        zona = await session.get(ZonaEntrega, zona_id)
        if zona is None:
            raise HTTPException(status_code=404, detail="Zona no encontrada")
        await session.delete(zona)
        await session.commit()
    return {"ok": True}


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
        # 🔴 EL PANEL NO PUEDE OFRECER LO QUE EL BACKEND YA RECHAZA. `confirmar` y
        # `verificar-monto` devuelven 409 si el pedido está CANCELADO (`_pedido_admite_cobro`) o si
        # ese pedido ya tiene OTRO pago confirmado (`_no_hay_otro_pago_confirmado`). Sin estos dos
        # campos la bandeja sigue pintando «Confirmar pago» sobre pagos que solo pueden acabar en
        # error. (Auditoría 2026-08-02, DIN-4/DIN-7.)
        #
        # Se guarda una LISTA por pedido, no un solo id: con dos confirmados sobre el mismo pedido
        # —dato histórico anterior al guard— un dict se quedaba con el último, que podía ser el
        # propio pago que se está pintando, y entonces el aviso desaparecía justo donde hace falta.
        confirmados: dict[int, list[int]] = {}
        if pagos:
            filas_conf = (
                await session.execute(
                    select(Pago.pedido_id, Pago.id).where(
                        Pago.pedido_id.in_({p.pedido_id for p in pagos}),
                        Pago.estado == "confirmado",
                    )
                )
            ).all()
            for ped_id, pago_id in filas_conf:
                confirmados.setdefault(ped_id, []).append(pago_id)
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
            # Lo que el panel necesita para NO ofrecer un botón que va a devolver 409:
            "pedido_estado": ped.estado if ped else None,
            "otro_pago_confirmado": next(
                (x for x in confirmados.get(p.pedido_id, []) if x != p.id), None
            ),
            "fecha": p.created_at.isoformat(),
        })
    return salida


async def _no_hay_otro_pago_confirmado(session, pago: Pago) -> None:
    """🔴 UN PEDIDO SE COBRA UNA SOLA VEZ.

    Sin esto la venta se contaba DOS veces en `/reporte` y en la ficha del cliente, y la
    secuencia para llegar ahí era de lo más normal: el cliente paga de menos (pago1 queda
    'parcial'), completa con un segundo comprobante (pago2, por el total), la dueña confirma
    pago2 ⇒ el pedido queda 'pagado'… y pago1 sigue en la bandeja ofreciendo **Reabrir** y, ya
    en 'reportado', **Confirmar**. Dos pagos confirmados sobre el mismo pedido, cada uno por el
    total. Nada lo impedía. (Auditoría 2026-08-02, DIN-4.)
    """
    otro = (
        await session.execute(
            select(Pago.id)
            .where(
                Pago.pedido_id == pago.pedido_id,
                Pago.estado == "confirmado",
                Pago.id != pago.id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if otro is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"El pedido #{pago.pedido_id} ya tiene el pago #{otro} confirmado. Si aquel "
                "estuvo mal, anúlalo primero (botón Anular) y vuelve a intentarlo."
            ),
        )


def _pedido_admite_cobro(pedido: Pedido | None) -> None:
    """Un pedido CANCELADO no resucita porque alguien confirme un pago viejo.

    `confirmar` escribía `pedido.estado = "pagado"` desde CUALQUIER estado. El caso real: el
    cliente se arrepiente, la dueña cancela el pedido, pero el comprobante sigue en "Por
    verificar"; días después lo confirma por limpiar la bandeja ⇒ el pedido salta de 'cancelado'
    a 'pagado', **vuelve a sumar en las métricas** y al cliente le llega un WhatsApp diciéndole
    que su pago quedó confirmado. (Auditoría 2026-08-02, DIN-7.)
    """
    if pedido is not None and pedido.estado == "cancelado":
        raise HTTPException(
            status_code=409,
            detail=(
                f"El pedido #{pedido.id} está CANCELADO: no se le puede confirmar un pago. "
                "Si la venta se retomó, cambia primero el estado del pedido."
            ),
        )


@router.post("/pagos/{pago_id}/confirmar")
async def confirmar_pago(pago_id: int, usuario: str = Depends(usuario_actual)):
    factory = get_session_factory()
    async with factory() as session:
        pago = await session.get(Pago, pago_id)
        if pago is None:
            raise HTTPException(status_code=404, detail="Pago no encontrado")
        if pago.estado != "reportado":
            raise HTTPException(status_code=409, detail=f"El pago ya está {pago.estado}")
        pedido = await session.get(Pedido, pago.pedido_id)
        await _no_hay_otro_pago_confirmado(session, pago)
        _pedido_admite_cobro(pedido)
        pago.estado = "confirmado"
        pago.confirmado_por = usuario
        pago.updated_at = now_utc()
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
        # Un pedido ya ENTREGADO (o cancelado) NO vuelve a "esperando_pago" porque se rechace un
        # comprobante: el estado del pedido dice dónde está la MERCANCÍA, y esa ya salió. Antes se
        # pisaba desde cualquier estado, así que rechazar un pago residual hacía desaparecer el
        # pedido de la lista de entregados. (Auditoría 2026-08-02, DIN-7.)
        if pedido is not None and pedido.estado not in ("entregado", "cancelado"):
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
        pedido = await session.get(Pedido, pago.pedido_id)
        await _no_hay_otro_pago_confirmado(session, pago)
        _pedido_admite_cobro(pedido)

        # 💵 LA MONEDA LA PONE EL PAGO, NO LA PANTALLA. Bolívares si se cobró en bolívares; dólares
        # si fue Zelle / Binance / efectivo (esos guardan `monto_bs = None`). Antes aquí había un
        # 400 seco para TODO pago en divisas: el carril de parcial/sobrepago estaba construido solo
        # sobre bolívares, así que a un Zelle de $18,40 pagado con $10 la dueña solo podía darle
        # Confirmar (regalando la diferencia) o Rechazar (castigando un pago real), aunque el panel
        # le ofreciera el botón igual. (Auditoría 2026-08-02, DIN-11.)
        en_bs = pago.monto_bs is not None
        total = pago.monto_bs if en_bs else pago.monto_usd
        if total is None:
            raise HTTPException(
                status_code=400,
                detail="Este pago no tiene monto registrado para comparar; usa Confirmar o Rechazar",
            )
        moneda = "Bs" if en_bs else "$"
        recibido = Decimal(str(datos.monto_recibido))
        pago.monto_recibido = recibido
        pago.confirmado_por = usuario
        pago.updated_at = now_utc()
        if recibido >= total:
            pago.estado = "confirmado"
            if pedido is not None:
                pedido.estado = "pagado"
        else:
            pago.estado = "parcial"
            # Mismo criterio que en `rechazar`: si ya se entregó, el pedido no vuelve atrás.
            if pedido is not None and pedido.estado != "entregado":
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
            if recibido > total:
                situacion += (
                    f". Ademas pago {moneda} {(recibido - total):.2f} de mas: dile con cariño que "
                    f"le queda ese saldo a favor para su proxima compra"
                )
        else:
            situacion = (
                f"el cliente pago {moneda} {recibido:.2f} pero el total era {moneda} {total:.2f}, "
                f"asi que faltan {moneda} {(total - recibido):.2f}. Pidele con suavidad y sin "
                f"reclamar que complete ese monto restante para poder despachar su pedido"
            )
        notificar_cliente_pago.apply_async((telefono, situacion))
    # `moneda` viaja al panel para que muestre la etiqueta correcta al pedir el monto recibido:
    # hoy dice "Bs" siempre, y en un pago en divisas eso induce a escribir la cifra equivocada.
    return {"ok": True, "pago_id": pago_id, "estado": estado_final, "moneda": moneda}


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


# ─── Calendario: días cerrados (feriados, vacaciones, un viaje) ──────

@router.get("/feriados")
async def listar_feriados(_: str = Depends(usuario_actual)):
    """Los días sueltos en que el negocio NO entrega. El bot no puede prometerlos."""
    factory = get_session_factory()
    async with factory() as session:
        filas = (
            await session.execute(
                select(Feriado).where(Feriado.fecha >= hoy_venezuela()).order_by(Feriado.fecha)
            )
        ).scalars().all()
    return [{"fecha": f.fecha.isoformat(), "motivo": f.motivo} for f in filas]


@router.post("/feriados")
async def crear_feriado(datos: FeriadoIn, _: str = Depends(usuario_actual)):
    try:
        fecha = date.fromisoformat(datos.fecha.strip()[:10])
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Fecha inválida (usa AAAA-MM-DD)."
        ) from None
    factory = get_session_factory()
    async with factory() as session:
        existe = await session.get(Feriado, fecha)
        if existe is not None:
            existe.motivo = (datos.motivo or "").strip() or None
        else:
            session.add(Feriado(fecha=fecha, motivo=(datos.motivo or "").strip() or None))
        await session.commit()
    return {"ok": True, "fecha": fecha.isoformat()}


@router.delete("/feriados/{fecha}")
async def borrar_feriado(fecha: str, _: str = Depends(usuario_actual)):
    try:
        f = date.fromisoformat(fecha.strip()[:10])
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Fecha inválida (usa AAAA-MM-DD)."
        ) from None
    factory = get_session_factory()
    async with factory() as session:
        fila = await session.get(Feriado, f)
        if fila is not None:
            await session.delete(fila)
            await session.commit()
    return {"ok": True}
