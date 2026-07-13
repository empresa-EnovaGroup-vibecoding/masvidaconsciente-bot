from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# Venezuela = UTC-4 (sin horario de verano). El servidor corre en UTC: a las 8 de la
# noche de Cabudare el reloj UTC YA es mañana. Usar `date.today()` para el PRECIO DEL DÍA
# hacía que el precio que la dueña cargó en la mañana DESAPARECIERA a las 20:00 VET, y que
# el que cargara esa noche se grabara con fecha de MAÑANA y se cobrara todo el día siguiente
# sin volver a preguntarle: exactamente "reutilizar el precio de ayer", que es lo que el
# cobro tiene PROHIBIDO. Todo el carril del precio del día usa ESTA función, nunca date.today().
def hoy_venezuela() -> date:
    """El día de HOY según el reloj de Venezuela (no el del servidor)."""
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date()


def inicio_dia_venezuela() -> datetime:
    """Las 00:00 de HOY en Venezuela, expresado en UTC (para comparar con `created_at`).

    Sin esto, el "hoy" del panel arranca a las 8 de la noche de Venezuela: las ventas de
    la noche se le mostraban a la dueña como si fueran de mañana."""
    return datetime.combine(hoy_venezuela(), time.min, tzinfo=timezone.utc) + timedelta(hours=4)


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
    # Ficha del producto para el bot (info ESPECÍFICA de ESTE producto, así no mezcla
    # con la de otros). Las 3 casillas clave + un texto libre.
    duracion: Mapped[str | None] = mapped_column(Text, nullable=True)
    se_congela: Mapped[str | None] = mapped_column(Text, nullable=True)
    apto_diabeticos: Mapped[str | None] = mapped_column(Text, nullable=True)
    info: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Cuántos días de ANTICIPACIÓN necesita ESTE producto (0 = puede ser el mismo día si hay
    # stock; las tortas y lo horneado, 2). Lo decide la dueña, producto por producto: los
    # congelados ya están hechos, pero una torta hay que hornearla.
    dias_anticipacion: Mapped[int] = mapped_column(Integer, default=0)
    disponible: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ProductoVariante(Base):
    """Un TAMAÑO de un producto: lo que se COBRA.

    La línea que separa PRODUCTO de TAMAÑO es EL DINERO: solo es un tamaño distinto lo que
    cambia el precio (Kombucha 350ml $4 · 700ml $7). Lo que el cliente escoge sin mover el
    precio (relleno, masa, sabor) es una OPCIÓN y vive dentro del ítem del pedido.

    Antes el precio vivía pegado al producto, y por eso la dueña tuvo que crear DOS productos
    llamados "Kombucha": el buscador devolvía siempre el primero y **siempre cobraba $4**.
    Fuga real de $3 por venta. Ver migración 022 y PRP-producto-variantes.md.
    """

    __tablename__ = "producto_variantes"

    id: Mapped[int] = mapped_column(primary_key=True)
    producto_id: Mapped[int] = mapped_column(ForeignKey("productos.id", ondelete="CASCADE"))
    presentacion: Mapped[str] = mapped_column(Text, default="única")
    # NULL = PRECIO DEL DÍA: lo pone la dueña cada día (tortas, premezclas). No es un olvido:
    # en Venezuela cambia de un día para otro.
    precio: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    # Los sabores son DEL TAMAÑO: la kombucha de 700ml tiene cúrcuma y flor de jamaica; la de
    # 350ml, no. Sin esto, "quiero la de flor de jamaica" no encontraría nada.
    sabores: Mapped[str | None] = mapped_column(Text, nullable=True)
    disponible: Mapped[bool] = mapped_column(Boolean, default=True)
    orden: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ProductoMedia(Base):
    """Una foto o video de un producto, guardado en R2. En la BD va solo la 'clave'
    (ruta del archivo en R2); la URL pública se arma con R2_PUBLIC_URL al mostrar/enviar."""
    __tablename__ = "producto_media"

    id: Mapped[int] = mapped_column(primary_key=True)
    producto_id: Mapped[int] = mapped_column(ForeignKey("productos.id", ondelete="CASCADE"))
    # De qué TAMAÑO es esta foto. NULL = del producto, sin tamaño (neutra): el bot la muestra
    # sin afirmar de cuál es. ON DELETE SET NULL, JAMÁS CASCADE: borrar_media es el único sitio
    # que borra el archivo en R2; si la fila desapareciera, el archivo quedaría huérfano allá,
    # ocupando espacio para siempre.
    variante_id: Mapped[int | None] = mapped_column(
        ForeignKey("producto_variantes.id", ondelete="SET NULL"), nullable=True
    )
    tipo: Mapped[str] = mapped_column(Text, default="imagen")  # 'imagen' | 'video'
    clave: Mapped[str] = mapped_column(Text)  # ruta del objeto en R2
    orden: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Cliente(Base):
    __tablename__ = "clientes"

    id: Mapped[int] = mapped_column(primary_key=True)
    telefono: Mapped[str] = mapped_column(Text, unique=True)
    nombre: Mapped[str | None] = mapped_column(Text, nullable=True)
    notas: Mapped[str | None] = mapped_column(Text, nullable=True)
    bot_pausado: Mapped[bool] = mapped_column(Boolean, default=False)
    # QUIÉN apretó el freno: 'dueña' (una persona tomó el chat) | 'bot' (se pausó él solo al
    # escalar con pedir_ayuda) | None (no está pausado). Son casos OPUESTOS: si fue la dueña,
    # el bot se CALLA; si fue él mismo, su último mensaje ("dame un momentito, te confirmo")
    # SÍ tiene que salir — si no, el cliente se queda con silencio total. Ver migración 020.
    pausado_por: Mapped[str | None] = mapped_column(Text, nullable=True)
    primera_interaccion: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    ultima_interaccion: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    # EL RELOJ DE LAS 24 HORAS DE META: la hora del último mensaje que escribió EL CLIENTE.
    # Meta solo deja responder con texto libre dentro de esa ventana. NULL ⇒ ventana CERRADA
    # (fail-closed a propósito: enviar fuera de ventana quema la calidad del número y, siendo
    # Tech Provider, eso arriesga la cuenta de TODOS los clientes). Ver migración 019.
    ultimo_entrante_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    no_leidos: Mapped[int] = mapped_column(Integer, default=0)


class Intervencion(Base):
    """'El bot te necesita': el bot se topó con algo que NO le toca resolver
    (un precio que cambia, algo que no sabe, un cliente que pide una persona, un
    reclamo). En vez de inventar, PAUSA ese chat, avisa a la dueña y la espera."""

    __tablename__ = "intervenciones"

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_telefono: Mapped[str] = mapped_column(Text)
    motivo: Mapped[str] = mapped_column(Text)  # precio_del_dia|no_se|pide_persona|reclamo
    detalle: Mapped[str | None] = mapped_column(Text, nullable=True)
    mensaje_cliente: Mapped[str | None] = mapped_column(Text, nullable=True)
    estado: Mapped[str] = mapped_column(Text, default="pendiente")  # pendiente|resuelta
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    resuelta_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PrecioDia(Base):
    """El precio de HOY de un producto cuyo precio CAMBIA (Tortas keto, Premezclas…).
    Lo pone la dueña cuando el bot se lo pregunta. Vale SOLO para su fecha: mañana el
    bot vuelve a preguntárselo. Un precio viejo JAMÁS se reutiliza (regla del cobro)."""

    __tablename__ = "precio_dia"

    id: Mapped[int] = mapped_column(primary_key=True)
    producto_id: Mapped[int] = mapped_column(ForeignKey("productos.id", ondelete="CASCADE"))
    # El precio del día es POR TAMAÑO (lo pidió Maired). El índice viejo (producto_id, fecha)
    # lo IMPEDÍA: al cargar el precio de la torta de 500g rechazaba el de la de 1kg del mismo
    # día. Ver migración 022.
    variante_id: Mapped[int | None] = mapped_column(
        ForeignKey("producto_variantes.id", ondelete="CASCADE"), nullable=True
    )
    precio: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    nota: Mapped[str | None] = mapped_column(Text, nullable=True)  # ej. "1kg"
    fecha: Mapped[datetime] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Feriado(Base):
    """Un día suelto en que el negocio NO entrega (viaje, 24 de diciembre, vacaciones).
    Los pone la dueña desde el panel. El código NO deja que el bot prometa esas fechas."""

    __tablename__ = "feriados"

    fecha: Mapped[date] = mapped_column(Date, primary_key=True)
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


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
    # PARA CUÁNDO y CÓMO se entrega, con las palabras del cliente ("sábado en la tarde,
    # delivery en Cabudare"). Antes NO se guardaba: el cliente decía "para el domingo" y a la
    # dueña le llegaba un pedido de $42 sin saber para cuándo era. Ver migración 016.
    entrega: Mapped[str | None] = mapped_column(Text, nullable=True)
    # La FECHA real acordada (no un texto que haya que adivinar). El código la valida contra
    # el calendario del negocio: día de entrega, feriados y anticipación de los productos.
    entrega_fecha: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Mensaje(Base):
    __tablename__ = "mensajes"
    __table_args__ = (
        # 'owner' = lo escribió una PERSONA (la dueña), desde el panel o desde su celular.
        # Sin este rol su mensaje NO CABE en el hilo, y guardarlo como 'assistant' haría que el
        # bot creyera que lo dijo él (y arrastrara promesas que no hizo). Ver migración 019.
        CheckConstraint("rol IN ('user','assistant','owner')", name="ck_mensaje_rol"),
        # El eco de la dueña (lo que escribe desde su celular) puede traer fotos, notas de
        # voz, stickers, ubicaciones y hasta reacciones. Si el tipo no cabe, el INSERT revienta
        # y la excepción se lleva por delante la PAUSA. Ver migración 021.
        CheckConstraint(
            "tipo IN ('text','image','audio','document','sticker','video',"
            "'location','contacts','reaction','otro')",
            name="ck_mensaje_tipo",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # El id del mensaje ENTRANTE (idempotencia de los reintentos de Meta). NO confundir con
    # `wa_message_id`, que es el id del mensaje que NOSOTROS enviamos.
    message_id: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)
    cliente_telefono: Mapped[str] = mapped_column(Text)
    rol: Mapped[str] = mapped_column(Text)  # user | assistant | owner (la dueña)
    contenido: Mapped[str] = mapped_column(Text)
    # QUÉ era: para que el comprobante se VEA en el chat (hoy no entra al hilo y la dueña
    # tendría que responder a ciegas, sin ver la foto del pago).
    tipo: Mapped[str] = mapped_column(Text, default="text")
    media_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Dónde está el archivo DE VERDAD y con qué tipo servirlo. Sin esto, el endpoint del panel
    # tendría que adivinar la extensión, y la imagen que la visión RECHAZA (la que la dueña más
    # necesita ver) no se puede resolver por el Pago: esa nunca crea Pago. Ver migración 021.
    media_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_mime: Mapped[str | None] = mapped_column(Text, nullable=True)
    # CÓMO llegó: nada de fallos en silencio. Meta devuelve un id al enviar y luego manda el
    # estado contra ESE id; sin guardarlo no hay con qué casarlo.
    wa_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    estado: Mapped[str | None] = mapped_column(Text, nullable=True)  # enviado|entregado|leido|fallido
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    # Embedding (vector de significado) para la búsqueda semántica. Lista de floats en
    # JSONB. Nullable: si no se pudo calcular, la entrada igual sirve por búsqueda léxica.
    embedding: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class CatalogoPdf(Base):
    """El catálogo en PDF guardado EN LA BASE DE DATOS (sobrevive redeploys, a
    diferencia del disco). Una sola fila (id=1)."""
    __tablename__ = "catalogo_pdf"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    contenido: Mapped[bytes] = mapped_column(LargeBinary)
    actualizado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class MetodoPago(Base):
    """Una cuenta/método de pago de la dueña (Pago Móvil, Banesco, Binance, Zelle,
    Efectivo…). Varias filas; editables desde el panel. El bot las OFRECE al cliente
    y valida que un comprobante vaya a UNA de estas cuentas."""
    __tablename__ = "metodos_pago"

    id: Mapped[int] = mapped_column(primary_key=True)
    tipo: Mapped[str] = mapped_column(Text, default="pago_movil")
    titulo: Mapped[str] = mapped_column(Text)
    titular: Mapped[str | None] = mapped_column(Text, nullable=True)
    banco: Mapped[str | None] = mapped_column(Text, nullable=True)
    telefono: Mapped[str | None] = mapped_column(Text, nullable=True)
    cedula: Mapped[str | None] = mapped_column(Text, nullable=True)
    cuenta: Mapped[str | None] = mapped_column(Text, nullable=True)
    correo: Mapped[str | None] = mapped_column(Text, nullable=True)
    wallet: Mapped[str | None] = mapped_column(Text, nullable=True)
    instrucciones: Mapped[str | None] = mapped_column(Text, nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    orden: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


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
