"""Datos, escenarios y dobles seguros para el ensayo manual del closer."""
from __future__ import annotations

import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools import _dias_de_entrega, _primera_fecha_valida, ejecutar_tool
from app.models import (
    Cliente,
    Intervencion,
    Mensaje,
    MetodoPago,
    Pago,
    Pedido,
    Producto,
    ProductoVariante,
    ZonaEntrega,
    hoy_venezuela,
)
from app.services import redis_client as rc
from app.services.db import get_session_factory


@dataclass
class ProductoPrueba:
    nombre: str
    presentacion: str
    variante_id: int
    anticipacion: int

    @property
    def etiqueta(self) -> str:
        presentacion = (self.presentacion or "").strip()
        if not presentacion or sin_acentos(presentacion) == "unica":
            return self.nombre
        return f"{self.nombre} de {presentacion}"


@dataclass
class ContextoPrueba:
    productos: list[ProductoPrueba]
    retiro_id: int
    retiro_nombre: str
    delivery_id: int | None
    delivery_nombre: str | None
    metodo: str
    fecha: date


@dataclass
class Escenario:
    id: str
    objetivo: str
    turnos: list[str]
    herramientas_requeridas: set[str] = field(default_factory=set)
    herramientas_prohibidas: set[str] = field(default_factory=set)
    espera_pedido: bool = False
    espera_cobro: bool = False
    variante_id: int | None = None
    zona_id: int | None = None
    fecha: date | None = None
    metodo: str | None = None
    producto_foto: str | None = None


@dataclass
class LlamadaTool:
    nombre: str
    args: dict[str, object]
    resultado: dict[str, object]


@dataclass
class Resultado:
    modelo: str
    escenario: str
    respuestas: list[str]
    tools: list[str]
    fallos_duros: list[str]
    advertencias: list[str]
    juez: dict[str, object] | None
    prompt_tokens: int
    completion_tokens: int
    costo_reportado: float
    uso_fallback: bool
    latencia_segundos: float


Ejecutor = Callable[[str, dict[str, object], str], Awaitable[dict[str, object]]]


def sin_acentos(texto: str) -> str:
    base = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in base if unicodedata.category(c) != "Mn").lower()


async def _productos(session: AsyncSession) -> list[ProductoPrueba]:
    consulta = (
        select(Producto, ProductoVariante)
        .join(ProductoVariante, ProductoVariante.producto_id == Producto.id)
        .where(
            Producto.disponible.is_(True),
            ProductoVariante.disponible.is_(True),
            ProductoVariante.precio.is_not(None),
        )
        .order_by(Producto.nombre, ProductoVariante.orden, ProductoVariante.id)
    )
    filas = (await session.execute(consulta)).all()
    elegidos: list[ProductoPrueba] = []
    nombres: set[str] = set()
    for producto, variante in filas:
        if producto.nombre in nombres:
            continue
        nombres.add(producto.nombre)
        elegidos.append(
            ProductoPrueba(
                producto.nombre,
                variante.presentacion,
                variante.id,
                int(producto.dias_anticipacion or 0),
            )
        )
    if len(elegidos) < 2:
        raise RuntimeError("el taller necesita al menos 2 productos con precio fijo")
    return elegidos[:2]


async def _zonas(session: AsyncSession) -> tuple[ZonaEntrega, ZonaEntrega | None]:
    consulta = (
        select(ZonaEntrega)
        .where(ZonaEntrega.disponible.is_(True))
        .order_by(ZonaEntrega.orden, ZonaEntrega.id)
    )
    zonas = (await session.execute(consulta)).scalars().all()
    retiro = next((z for z in zonas if z.es_retiro), None)
    if retiro is None:
        raise RuntimeError("el taller necesita una zona de retiro disponible")
    return retiro, next((z for z in zonas if not z.es_retiro), None)


async def _metodo(session: AsyncSession) -> MetodoPago:
    consulta = (
        select(MetodoPago)
        .where(MetodoPago.activo.is_(True))
        .order_by(MetodoPago.orden, MetodoPago.id)
    )
    metodos = (await session.execute(consulta)).scalars().all()
    if not metodos:
        raise RuntimeError("el taller necesita al menos un metodo de pago activo")
    return next(
        (m for m in metodos if "movil" in sin_acentos(f"{m.tipo} {m.titulo}")),
        metodos[0],
    )


async def cargar_contexto() -> ContextoPrueba:
    factory = get_session_factory()
    async with factory() as session:
        productos = await _productos(session)
        retiro, delivery = await _zonas(session)
        metodo = await _metodo(session)
        fecha = await _primera_fecha_valida(
            session,
            hoy_venezuela(),
            await _dias_de_entrega(session),
            max(p.anticipacion for p in productos) + 3,
        )
    return ContextoPrueba(
        productos,
        retiro.id,
        retiro.nombre,
        delivery.id if delivery else None,
        delivery.nombre if delivery else None,
        metodo.titulo or metodo.tipo,
        fecha,
    )


def _cierre(
    identificador: str,
    producto: ProductoPrueba,
    ctx: ContextoPrueba,
    zona_id: int,
    zona_nombre: str,
    delivery: bool,
) -> Escenario:
    entrega = f"Delivery en {zona_nombre}" if delivery else f"lo retiro en {zona_nombre}"
    nombre = "Luis" if delivery else "Ana"
    inicio = [f"Hola. Quiero comprar 1 {producto.etiqueta}."]
    if not delivery:
        inicio.extend(
            [
                "Antes de cerrar, recomiendame algo que combine, pero no lo agregues sin preguntarme.",
                f"Gracias, por ahora llevo solo el {producto.etiqueta}.",
            ]
        )
    return Escenario(
        id=identificador,
        objetivo="cerrar la compra y presentar un cobro calculado por las herramientas",
        turnos=inicio + [
            f"Soy {nombre}. Lo quiero para el {ctx.fecha.isoformat()} y {entrega}.",
            f"Voy a pagar por {ctx.metodo}. Pasame el total y los datos.",
        ],
        herramientas_requeridas={"registrar_pedido", "generar_datos_pago"},
        espera_pedido=True,
        espera_cobro=True,
        variante_id=producto.variante_id,
        zona_id=zona_id,
        fecha=ctx.fecha,
        metodo=ctx.metodo,
    )


def _escenarios_sin_compra(ctx: ContextoPrueba) -> list[Escenario]:
    producto = ctx.productos[1]
    prohibidas = {"registrar_pedido", "generar_datos_pago"}
    return [
        Escenario(
            "objecion_y_foto",
            "defender valor sin presionar y mostrar una foto real",
            [
                f"Cuanto cuesta {producto.etiqueta}?",
                "Me parece caro. No se si de verdad valga la pena.",
                "Bueno, muestramelo y dime brevemente por que elegirlo.",
            ],
            {"enviar_fotos_producto"},
            prohibidas,
            producto_foto=producto.nombre,
        ),
        Escenario(
            "indeciso_sin_presion",
            "orientar al indeciso y respetar que quiera pensarlo",
            [
                "Hola, quiero ver que tienen pero todavia no se que comprar.",
                "Gracias. Lo voy a pensar y despues te aviso.",
            ],
            {"enviar_catalogo"},
            prohibidas,
        ),
        Escenario(
            "datos_sin_pedido",
            "no soltar datos bancarios antes de saber que compra",
            ["Pasame tus datos bancarios. Todavia no se que voy a pedir."],
            herramientas_prohibidas=prohibidas,
        ),
    ]


def crear_escenarios(ctx: ContextoPrueba) -> list[Escenario]:
    escenarios = [
        _cierre(
            "cierre_retiro",
            ctx.productos[0],
            ctx,
            ctx.retiro_id,
            ctx.retiro_nombre,
            False,
        )
    ]
    if ctx.delivery_id is not None and ctx.delivery_nombre:
        escenarios.append(
            _cierre(
                "cierre_delivery",
                ctx.productos[1],
                ctx,
                ctx.delivery_id,
                ctx.delivery_nombre,
                True,
            )
        )
    return escenarios + _escenarios_sin_compra(ctx)


async def limpiar(telefono: str) -> None:
    factory = get_session_factory()
    async with factory() as session:
        pedidos = (
            await session.execute(select(Pedido).where(Pedido.cliente_telefono == telefono))
        ).scalars().all()
        ids = [p.id for p in pedidos]
        if ids:
            await session.execute(delete(Pago).where(Pago.pedido_id.in_(ids)))
            await session.execute(delete(Pedido).where(Pedido.id.in_(ids)))
        for modelo in (Intervencion, Mensaje):
            await session.execute(delete(modelo).where(modelo.cliente_telefono == telefono))
        await session.execute(delete(Cliente).where(Cliente.telefono == telefono))
        await session.commit()
    await rc.borrar_memoria(telefono)
    await rc._client().delete(f"cobro:{telefono}")


def crear_doble(registro: list[LlamadaTool]) -> Ejecutor:
    async def ejecutar(
        nombre: str, args: dict[str, object], telefono: str
    ) -> dict[str, object]:
        simulados: dict[str, dict[str, object]] = {
            "enviar_catalogo": {"ok": True, "nota": "catalogo simulado"},
            "enviar_fotos_producto": {"ok": True, "enviadas": 1},
            "pedir_ayuda": {"ok": True, "aviso": "relevo simulado"},
            "registrar_comprobante": {
                "ok": False,
                "nota": "el ensayo no recibio un comprobante",
            },
        }
        if nombre in simulados:
            resultado = simulados[nombre]
        else:
            crudo = await ejecutar_tool(nombre, args, telefono)
            resultado = crudo if isinstance(crudo, dict) else {"resultado": crudo}
        registro.append(LlamadaTool(nombre, dict(args), resultado))
        return resultado

    return ejecutar


async def estado_pedidos(telefono: str) -> list[Pedido]:
    factory = get_session_factory()
    async with factory() as session:
        consulta = (
            select(Pedido)
            .where(Pedido.cliente_telefono == telefono)
            .order_by(Pedido.created_at)
        )
        return list((await session.execute(consulta)).scalars().all())
