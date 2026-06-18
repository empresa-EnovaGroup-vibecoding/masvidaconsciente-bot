from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    LargeBinary,
    Numeric,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Producto(Base):
    __tablename__ = "productos"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(Text)
    categoria: Mapped[str | None] = mapped_column(Text, nullable=True)
    descripcion: Mapped[str | None] = mapped_column(Text, nullable=True)
    precio: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    presentacion: Mapped[str | None] = mapped_column(Text, nullable=True)
    disponible: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Cliente(Base):
    __tablename__ = "clientes"

    id: Mapped[int] = mapped_column(primary_key=True)
    telefono: Mapped[str] = mapped_column(Text, unique=True)
    nombre: Mapped[str | None] = mapped_column(Text, nullable=True)
    notas: Mapped[str | None] = mapped_column(Text, nullable=True)
    bot_pausado: Mapped[bool] = mapped_column(Boolean, default=False)
    primera_interaccion: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    ultima_interaccion: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Pedido(Base):
    __tablename__ = "pedidos"
    __table_args__ = (
        CheckConstraint(
            "estado IN ('pendiente','confirmado','preparando','entregado',"
            "'cancelado','esperando_pago','pagado')",
            name="ck_pedido_estado",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_telefono: Mapped[str] = mapped_column(ForeignKey("clientes.telefono"))
    estado: Mapped[str] = mapped_column(Text, default="pendiente")
    items: Mapped[list] = mapped_column(JSONB)
    total: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    notas: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Mensaje(Base):
    __tablename__ = "mensajes"
    __table_args__ = (
        CheckConstraint("rol IN ('user','assistant')", name="ck_mensaje_rol"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)
    cliente_telefono: Mapped[str] = mapped_column(Text)
    rol: Mapped[str] = mapped_column(Text)
    contenido: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Usuario(Base):
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    nombre: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Configuracion(Base):
    __tablename__ = "configuracion"

    clave: Mapped[str] = mapped_column(Text, primary_key=True)
    valor: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Conocimiento(Base):
    __tablename__ = "conocimiento"

    id: Mapped[int] = mapped_column(primary_key=True)
    categoria: Mapped[str | None] = mapped_column(Text, nullable=True)
    titulo: Mapped[str] = mapped_column(Text)
    contenido: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class CatalogoPdf(Base):
    """El catálogo en PDF guardado EN LA BASE DE DATOS (sobrevive redeploys, a
    diferencia del disco). Una sola fila (id=1)."""
    __tablename__ = "catalogo_pdf"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    contenido: Mapped[bytes] = mapped_column(LargeBinary)
    actualizado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Pago(Base):
    __tablename__ = "pagos"
    __table_args__ = (
        CheckConstraint(
            "estado IN ('reportado','confirmado','rechazado','parcial')",
            name="ck_pago_estado",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    pedido_id: Mapped[int] = mapped_column(ForeignKey("pedidos.id"))
    metodo: Mapped[str] = mapped_column(Text, default="pago_movil")
    monto_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    monto_bs: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    monto_recibido: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    tasa_usada: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    referencia: Mapped[str | None] = mapped_column(Text, nullable=True)
    comprobante_media_id: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)
    comprobante_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    estado: Mapped[str] = mapped_column(Text, default="reportado")
    confirmado_por: Mapped[str | None] = mapped_column(Text, nullable=True)
    motivo_rechazo: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
