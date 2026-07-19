"""EL REGISTRO DE HERRAMIENTAS: qué sabe hacer el bot, y qué se le puede apagar.

La proveedora enciende y apaga capacidades del agente desde el panel, sin desplegar.

🔴 LA COSTURA QUE HACE QUE ESTO SEA SEGURO. `agent.py` NUNCA usa `TOOL_SCHEMAS` para ejecutar:
ejecuta por `ejecutar_tool` → `_DISPATCH`. `TOOL_SCHEMAS` solo sirve para **decirle al LLM qué
existe**. Por eso se filtra SOLO lo que el modelo VE, y `_DISPATCH` se deja INTACTO:

  · Las 7 redes de seguridad siguen pudiendo llamar a `pedir_ayuda` y a `enviar_catalogo` aunque
    el modelo ya no las vea.
  · El worker de visión sigue pudiendo llamar a `registrar_comprobante` directo (se salta
    `ejecutar_tool` por completo).

Si se filtrara el dispatch, apagar una tool mataría redes de seguridad. El gate correcto es
"qué VE el modelo", no "qué puede ejecutar el código".

FAIL-OPEN en tres capas (clave ausente, vacía o con basura ⇒ las 12 activas). Mismo criterio que
`dias_entrega`: un fallo de configuración jamás puede dejar al bot mudo.
"""
import logging

from sqlalchemy import select

from app.models import Configuracion
from app.services.db import get_session_factory

logger = logging.getLogger(__name__)

CLAVE = "tools_activas"  # en `configuracion`, valor CSV (aquí NO hay JSON en ningún sitio)

# Las 12, en el orden en que se pintan en el panel. `pierde` = qué DEJA de hacer el bot si la
# apagas (se le enseña a la proveedora antes de confirmar).
TOOLS: dict[str, dict] = {
    "ver_catalogo": {
        "etiqueta": "Buscar en el catálogo",
        "descripcion": "Encuentra productos por nombre, ingrediente, categoría o sinónimo.",
        "pierde": "El bot no podría saber qué productos existen.",
        "motivo_blindaje": (
            "Es el SUELO ANTIINVENCIÓN. Sin esto el bot no tiene ninguna fuente de verdad sobre "
            "los productos — y su regla nº1 es no inventar."
        ),
    },
    "info_producto": {
        "etiqueta": "Ficha de un producto",
        "descripcion": "Ingredientes, duración, si se congela, si es apto para diabéticos.",
        "pierde": "El bot no sabría de qué está hecho nada.",
        "motivo_blindaje": (
            "Es el SUELO ANTIINVENCIÓN. Sin la ficha, el bot no puede afirmar NINGÚN dato de un "
            "producto sin inventárselo."
        ),
    },
    "enviar_fotos_producto": {
        "etiqueta": "Enviar fotos y videos",
        "descripcion": "Le manda al cliente las fotos o videos del producto por WhatsApp.",
        "pierde": "El bot dejará de mandar fotos. Se las tendrás que mandar tú a mano.",
    },
    "buscar_info": {
        "etiqueta": "Base de conocimiento",
        "descripcion": "Busca en los temas que cargaste (envíos, políticas, alergias…).",
        "pierde": "Ante cualquier duda general, el bot te llamará a ti en vez de responder.",
    },
    "info_negocio": {
        "etiqueta": "Datos del negocio",
        "descripcion": "Ubicación, horarios, método de pago, Instagram.",
        "pierde": "El bot no podrá responder dónde estás ni a qué hora abres.",
    },
    "ver_pedidos_cliente": {
        "etiqueta": "Historial del cliente",
        "descripcion": "Los últimos pedidos de ese cliente.",
        "pierde": "El bot no podrá recordar qué compró antes.",
    },
    "recordar_cliente": {
        "etiqueta": "Memoria del cliente",
        "descripcion": "Guarda su nombre y sus datos (diabético, vegano, alérgico…).",
        "pierde": "El bot le volverá a preguntar el nombre cada vez.",
    },
    "enviar_catalogo": {
        "etiqueta": "Enviar el catálogo PDF",
        "descripcion": "Le manda el PDF del catálogo por WhatsApp.",
        "pierde": "—",
        "motivo_blindaje": (
            "El código la llama SOLO, cuando el bot dice que envió el catálogo y no lo hizo "
            "(la red anti-mentira). Apagarla dejaría esa red sin brazo."
        ),
    },
    "registrar_pedido": {
        "etiqueta": "Registrar el pedido",
        "descripcion": "Crea el pedido y calcula el total (en código, nunca el modelo).",
        "pierde": "—",
        "motivo_blindaje": "Sin esto NO hay pedido: no se puede cobrar.",
    },
    "generar_datos_pago": {
        "etiqueta": "Cobrar",
        "descripcion": "Calcula el monto en bolívares y da los datos bancarios.",
        "pierde": "—",
        "motivo_blindaje": (
            "Es la ÚNICA fuente de los datos bancarios. Apagarla haría que el bot los dijera "
            "de memoria — y ahí es donde el dinero se va a otra cuenta."
        ),
    },
    "registrar_comprobante": {
        "etiqueta": "Recibir el comprobante",
        "descripcion": "Registra el pago que reporta el cliente y te avisa.",
        "pierde": "—",
        "motivo_blindaje": "Sin esto, un pago reportado se pierde y nadie se entera.",
    },
    "pedir_ayuda": {
        "etiqueta": "Llamarte a ti",
        "descripcion": "Pausa el bot y te avisa por WhatsApp cuando no sabe algo.",
        "pierde": "—",
        "motivo_blindaje": (
            "Es la SALIDA HONESTA de todo lo que se apague, y 7 redes de seguridad la llaman "
            "directo. Sin ella, un bot que inventa dinero simplemente se calla y nadie se entera."
        ),
    },
}

# ── LAS BLINDADAS: no se pueden apagar. Tres motivos DISTINTOS, y conviene no mezclarlos.
_COBRO = frozenset({"registrar_pedido", "generar_datos_pago", "registrar_comprobante"})
# `agent.py` las llama DIRECTO, fuera del bucle de tool_calls. Apagarlas no le quita una
# capacidad al modelo: le arranca el brazo a una red de seguridad.
_REDES = frozenset({"pedir_ayuda", "enviar_catalogo"})
# EL SUELO ANTIINVENCIÓN. No son "features": son la única fuente cerrada de verdad sobre los
# productos, en un sistema cuya regla nº1 es NO INVENTAR (y que ya tuvo un incidente por eso).
# Apagarlas no le quitaría una capacidad al bot — le quitaría el ancla, y empezaría a inventar.
# Si algún día se quieren abrir, es UNA línea… y hay que marcar sus 21 menciones del prompt.
_NUCLEO = frozenset({"ver_catalogo", "info_producto"})

BLINDADAS = _COBRO | _REDES | _NUCLEO
DESACTIVABLES = frozenset(TOOLS) - BLINDADAS  # hoy: 5


def _parsear(valor: str | None) -> frozenset[str]:
    """CSV → set de tools activas. FAIL-OPEN y con las blindadas re-inyectadas.

    Los tres candados están aquí a propósito, en la LECTURA — no solo en la API. Si alguien
    escribe el CSV a mano en Postgres y se deja fuera `pedir_ayuda`, el bot la tiene igual.
    """
    nombres = {x.strip() for x in (valor or "").split(",") if x.strip()}
    nombres &= set(TOOLS)  # una clave basura no puede colar una tool que no existe
    if not nombres:
        return frozenset(TOOLS)  # ausente / vacía / basura ⇒ las 12
    return frozenset(nombres | BLINDADAS)


async def leer_tools_activas() -> frozenset[str]:
    """Las herramientas que el LLM VE. Nunca lanza: cualquier fallo ⇒ las 12."""
    try:
        factory = get_session_factory()
        async with factory() as session:
            valor = (
                await session.execute(
                    select(Configuracion.valor).where(Configuracion.clave == CLAVE)
                )
            ).scalars().first()
    except Exception:  # noqa: BLE001 — un fallo de BD nunca puede dejar al bot sin herramientas
        logger.exception("No se pudo leer %s: se usan las 12 herramientas", CLAVE)
        return frozenset(TOOLS)
    return _parsear(valor)


def serializar(activas) -> str:
    """set → CSV en el orden canónico de TOOLS (estable y diffeable en la BD)."""
    activas = set(activas) | BLINDADAS
    return ",".join(n for n in TOOLS if n in activas)
