"""Contratos cerrados: el modelo propone intenciones, nunca hechos ni texto saliente."""
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Tema = Literal[
    "precio", "duracion", "se_congela", "apto_diabeticos", "descripcion",
    "disponibilidad", "sabores", "foto", "fecha", "ubicacion", "metodos_pago",
    "catalogo", "productos", "estado_pedido", "entrega", "ingredientes", "alergenos",
    "conservacion", "envio_nacional", "politica", "desconocido",
]
TEMAS_CONFIRMABLES = (
    "ingredientes", "alergenos", "conservacion", "envio_nacional", "politica",
)


class Cerrado(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Consulta(Cerrado):
    tema: Tema
    producto_id: int | None = None
    variante_id: int | None = None
    conocimiento_id: int | None = None
    evidencia: str = Field(default="", max_length=300)


class Seleccion(Cerrado):
    producto_id: int
    variante_id: int | None = None
    cantidad: int | None = Field(default=None, ge=1, le=100)
    evidencia_producto: str = Field(default="", max_length=300)
    evidencia_variante: str = Field(default="", max_length=100)
    evidencia_cantidad: str = Field(default="", max_length=100)
    opciones: str = Field(default="", max_length=200)


class SolicitudTurno(Cerrado):
    intencion: Literal[
        "consultar", "elegir", "registrar", "cobrar", "entrega", "comprobante",
        "saludo", "agradecimiento", "despedida", "identidad", "humano", "reclamo",
        "excepcion", "desconocido",
    ]
    consultas: list[Consulta] = Field(default_factory=list, max_length=8)
    seleccion: list[Seleccion] = Field(default_factory=list, max_length=20)
    zona_id: int | None = None
    evidencia_zona: str = Field(default="", max_length=200)
    fecha_texto: str = Field(default="", max_length=100)
    referencia: str = Field(default="", max_length=300)
    franja: str = Field(default="", max_length=100)
    metodo: str = Field(default="", max_length=100)
    nombre: str = Field(default="", max_length=100)
    detalle: str = Field(default="", max_length=500)
    evidencia_accion: str = Field(default="", max_length=300)
    tono: Literal["neutro", "calido", "serio"] = "neutro"


@dataclass(frozen=True)
class Hecho:
    fuente: str
    entidad: int | str
    campo: str
    valor: object
    revision: str


@dataclass
class Contexto:
    productos: dict[int, dict] = field(default_factory=dict)
    zonas: dict[int, dict] = field(default_factory=dict)
    conocimiento: dict[int, dict] = field(default_factory=dict)
    metodos: dict[str, dict] = field(default_factory=dict)
    negocio: dict[str, str] = field(default_factory=dict)
    pedido: dict | None = None
    # 🗂️ PR4: TODOS los pedidos no cancelados (hasta 3, el más nuevo primero, cada uno con `origen`,
    # `franja`, `pago_pendiente`); `pedido` sigue siendo el primero (compatibilidad con los tests de E0).
    pedidos: list[dict] = field(default_factory=list)
    # Propuestas del expediente que nadie confirmó todavía en la Bandeja.
    propuestas_pendientes: int = 0
    borrador: dict = field(default_factory=dict)
    pausado: bool = False
    activo: bool = True
    hoy: date = field(default_factory=date.today)
    # Una persona del negocio dijo algo de la venta que aún NO es dato confirmado (hay propuestas
    # pendientes): registrar, cobrar o afirmar el estado sería adivinar. Se calcula en cargar_contexto.
    humano_sin_acuerdo: bool = False


@dataclass
class DecisionTurno:
    tipo: Literal["responder", "pedir_dato", "relevo", "silencio"]
    texto: str = ""
    hechos: list[Hecho] = field(default_factory=list)
    motivo: str = "no_se"
    pendiente: str = ""
    producto: str = ""


class MensajeConfirmado(str):
    """Texto armado por código, con las fuentes que se revisan antes de enviarlo."""

    def __new__(cls, texto, *, hechos=(), relevo=False):
        obj = super().__new__(cls, texto)
        obj.hechos = tuple(hechos)
        obj.relevo = relevo
        return obj


# ══ EL EXPEDIENTE DE LA VENTA (PR3, SESIONES (37)): lo que la DUEÑA dijo a mano ══
#
# El modelo lee SOLO los mensajes de Whuilianny (texto o 🎤) y PROPONE eventos tipados copiando
# literales; el CÓDIGO los resuelve contra el catálogo (`app/agent/expediente.py`). Aquí no viaja
# ni un id ni un precio: los pone el programa.

TipoEventoDuena = Literal[
    "pedido_tomado", "pago_confirmado", "entrega_acordada", "entregado", "precio_especial",
    "cancelado", "respuesta_general", "nada",
]


def _numero_como_texto(v):
    """Un modelo barato escribe `"cantidad_literal": 2` en vez de `"2"`. En modo estricto eso
    tumbaría el contrato entero por una comilla; aquí el número pasa a texto ANTES de validar.
    Nada más se coacciona (los booleanos y las listas siguen siendo un error)."""
    if isinstance(v, bool):
        return v
    if isinstance(v, int | float):
        return str(v)
    return v


class ItemDuena(Cerrado):
    nombre_literal: str = Field(default="", max_length=120)
    cantidad_literal: str = Field(default="", max_length=40)

    @field_validator("nombre_literal", "cantidad_literal", mode="before")
    @classmethod
    def _texto(cls, v):
        return _numero_como_texto(v)


class EventoDuena(Cerrado):
    tipo: TipoEventoDuena
    items: list[ItemDuena] = Field(default_factory=list, max_length=10)
    total_literal: str = Field(default="", max_length=40)
    monto_literal: str = Field(default="", max_length=40)
    metodo: str = Field(default="", max_length=60)
    fecha_texto: str = Field(default="", max_length=60)
    momento_texto: str = Field(default="", max_length=60)
    lugar_texto: str = Field(default="", max_length=200)
    tema: str = Field(default="", max_length=40)
    contenido: str = Field(default="", max_length=400)
    # Copia LITERAL del trozo de la dueña que sostiene el evento. Si no consta, se descarta.
    evidencia: str = Field(default="", max_length=300)

    @field_validator(
        "total_literal", "monto_literal", "metodo", "fecha_texto", "momento_texto",
        "lugar_texto", "tema", "contenido", "evidencia", mode="before",
    )
    @classmethod
    def _texto(cls, v):
        return _numero_como_texto(v)


class ExtraccionDuena(Cerrado):
    eventos: list[EventoDuena] = Field(default_factory=list, max_length=6)


class ItemPropuesto(BaseModel):
    """Un ítem YA RESUELTO por el código contra el catálogo (ids reales, precio de hoy)."""

    model_config = ConfigDict(extra="forbid")
    producto_id: int
    variante_id: int
    nombre: str
    presentacion: str = ""
    cantidad: int = Field(default=1, ge=1, le=100)
    precio_unitario: float | None = None


class PropuestaExpediente(BaseModel):
    """Lo que viaja en `intervenciones.propuesta` (JSONB) y lo que `aplicar_propuesta` ejecuta.

    No es estricta a propósito (viene de JSON: enteros y flotantes ya normalizados), pero SÍ cerrada:
    una clave que no esté aquí no se aplica. `resultado` se llena al aplicar o descartar.
    """

    model_config = ConfigDict(extra="forbid")
    tipo: TipoEventoDuena
    telefono: str
    items: list[ItemPropuesto] = Field(default_factory=list)
    total: float | None = None
    monto: float | None = None
    moneda: str = ""
    metodo: str = ""
    fecha: str | None = None
    franja: str = ""
    lugar: str = ""
    tema: str = ""
    contenido: str = ""
    evidencia: str = ""
    evidencia_mensaje_id: int | None = None
    confianza: float = 0.0
    pedido_id: int | None = None
    resumen: str = ""
    resultado: str = ""
    # 💰 PR6b: cuando el bot le pregunta a la dueña por WhatsApp si un PAGO entró (solo `pago_confirmado`),
    # deja aquí el id del mensaje que le mandó (para casar su respuesta citada) y cuándo se lo preguntó.
    pregunta_wamid: str | None = None
    preguntada_at: str | None = None
