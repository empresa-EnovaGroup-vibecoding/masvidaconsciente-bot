"""Contratos cerrados: el modelo propone intenciones, nunca hechos ni texto saliente."""
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Tema = Literal[
    "precio", "duracion", "se_congela", "apto_diabeticos", "descripcion",
    "disponibilidad", "sabores", "foto", "fecha", "ubicacion", "metodos_pago",
    "catalogo", "productos", "estado_pedido", "ingredientes", "alergenos",
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
    borrador: dict = field(default_factory=dict)
    pausado: bool = False
    activo: bool = True
    hoy: date = field(default_factory=date.today)
    humano_sin_acuerdo: bool = False


@dataclass
class DecisionTurno:
    tipo: Literal["responder", "pedir_dato", "relevo", "silencio"]
    texto: str = ""
    hechos: list[Hecho] = field(default_factory=list)
    motivo: str = "no_se"
    pendiente: str = ""


class MensajeConfirmado(str):
    """Texto armado por código, con las fuentes que se revisan antes de enviarlo."""

    def __new__(cls, texto, *, hechos=(), relevo=False):
        obj = super().__new__(cls, texto)
        obj.hechos = tuple(hechos)
        obj.relevo = relevo
        return obj
