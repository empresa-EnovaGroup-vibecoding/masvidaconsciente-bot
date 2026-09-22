"""Lecturas autoritativas. Ningún texto del modelo se convierte aquí en dato del negocio."""
import hashlib
import json

from sqlalchemy import select, text

from app.agent.contratos_atencion import Contexto, Hecho
from app.models import (
    Cliente,
    Configuracion,
    Conocimiento,
    Intervencion,
    MetodoPago,
    Pedido,
    Producto,
    ProductoVariante,
    ZonaEntrega,
    hoy_venezuela,
)
from app.services.db import get_session_factory


def revision(valor) -> str:
    return hashlib.sha256(json.dumps(valor, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def hecho(fuente, entidad, campo, valor) -> Hecho:
    return Hecho(fuente, entidad, campo, valor, revision(valor))


async def bloquear_cliente(session, telefono):
    # También serializa la creación de clientes y avisos: FOR UPDATE solo no bloquea filas ausentes.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:telefono, 39))"),
        {"telefono": telefono},
    )
    return (await session.execute(
        select(Cliente).where(Cliente.telefono == telefono).with_for_update()
    )).scalar_one_or_none()


async def cargar_contexto(telefono: str) -> Contexto:
    from app.agent.tools import _precio_efectivo

    factory = get_session_factory()
    async with factory() as session:
        cliente = (await session.execute(
            select(Cliente).where(Cliente.telefono == telefono)
        )).scalar_one_or_none()
        config = {r.clave: r.valor for r in (await session.execute(select(Configuracion))).scalars()}
        ctx = Contexto(
            pausado=bool(cliente and (cliente.bot_pausado or cliente.privado)),
            activo=str(config.get("bot_activo", "1")).strip().lower() not in {"0", "false", "off", "no"},
            borrador=dict(cliente.borrador_confirmado or {}) if cliente else {},
            hoy=hoy_venezuela(),
            negocio={k: config[k] for k in ("negocio_ubicacion", "negocio_instagram") if config.get(k)},
        )
        for p in (await session.execute(select(Producto))).scalars():
            ctx.productos[p.id] = {
                "id": p.id, "nombre": p.nombre, "categoria": p.categoria,
                "descripcion": p.descripcion, "duracion": p.duracion,
                "se_congela": p.se_congela, "apto_diabeticos": p.apto_diabeticos,
                "disponibilidad": p.disponible, "variantes": {},
            }
        for v in (await session.execute(select(ProductoVariante))).scalars():
            if v.producto_id in ctx.productos:
                precio = await _precio_efectivo(session, v)
                ctx.productos[v.producto_id]["variantes"][v.id] = {
                    "id": v.id, "presentacion": v.presentacion, "precio": precio,
                    "sabores": v.sabores, "disponibilidad": v.disponible,
                }
        for z in (await session.execute(select(ZonaEntrega).where(ZonaEntrega.disponible.is_(True)))).scalars():
            ctx.zonas[z.id] = {
                "id": z.id, "nombre": z.nombre, "referencias": z.referencias,
                "costo": z.costo, "es_retiro": z.es_retiro,
            }
        for k in (await session.execute(select(Conocimiento).where(Conocimiento.activo.is_(True)))).scalars():
            ctx.conocimiento[k.id] = {
                "id": k.id, "titulo": k.titulo, "contenido": k.contenido,
                "tema": k.tema_confirmado, "producto_id": k.producto_id,
                "confirmado": k.confirmado,
            }
        for m in (await session.execute(select(MetodoPago).where(MetodoPago.activo.is_(True)))).scalars():
            ctx.metodos[m.titulo or m.tipo] = {"id": m.id, "tipo": m.tipo}
        pedido = (await session.execute(
            select(Pedido).where(Pedido.cliente_telefono == telefono, Pedido.estado != "cancelado")
            .order_by(Pedido.created_at.desc()).limit(1)
        )).scalar_one_or_none()
        if pedido:
            ctx.pedido = {
                "id": pedido.id, "estado": pedido.estado, "items": pedido.items,
                "fecha": pedido.entrega_fecha.isoformat() if pedido.entrega_fecha else None,
                "zona_id": pedido.zona_id, "referencia": pedido.entrega_referencia,
                "metodo": pedido.metodo_elegido, "total": pedido.total,
            }
        return ctx


async def guardar_borrador(telefono, borrador):
    factory = get_session_factory()
    async with factory() as session:
        cliente = await bloquear_cliente(session, telefono)
        if cliente and (cliente.bot_pausado or cliente.privado):
            return False
        if cliente is None:
            cliente = Cliente(telefono=telefono)
            session.add(cliente)
        cliente.borrador_confirmado = borrador
        await session.commit()
    return True


def valor_actual(ctx: Contexto, h: Hecho):
    if h.fuente == "producto":
        return ctx.productos.get(h.entidad, {}).get(h.campo)
    if h.fuente == "variante":
        for p in ctx.productos.values():
            if h.entidad in p["variantes"]:
                return p["variantes"][h.entidad].get(h.campo)
    if h.fuente == "conocimiento":
        k = ctx.conocimiento.get(h.entidad, {})
        return k.get(h.campo) if k.get("confirmado") else None
    if h.fuente == "negocio":
        return ctx.negocio.get(h.campo)
    if h.fuente == "pedido" and ctx.pedido and ctx.pedido["id"] == h.entidad:
        return ctx.pedido.get(h.campo)
    if h.fuente == "metodos":
        return list(ctx.metodos)
    return None


async def tomar_acuse(telefono):
    """Reserva un único acuse por intervención. Un reintento no vuelve a prometer atención."""
    factory = get_session_factory()
    async with factory() as session:
        c = await bloquear_cliente(session, telefono)
        if not c or c.privado or not c.bot_pausado or c.pausado_por != "bot":
            return False
        i = (await session.execute(select(Intervencion).where(
            Intervencion.cliente_telefono == telefono, Intervencion.estado == "pendiente",
        ).order_by(Intervencion.id.desc()).limit(1).with_for_update())).scalar_one_or_none()
        if i is None or i.acuse_intentado:
            return False
        i.acuse_intentado = True
        await session.commit()
        return True


async def fuentes_vigentes(telefono, hechos):
    if not hechos:
        return True
    ctx = await cargar_contexto(telefono)
    return all(revision(valor_actual(ctx, h)) == h.revision for h in hechos)
