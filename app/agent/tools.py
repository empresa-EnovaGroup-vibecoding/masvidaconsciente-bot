"""Las 12 herramientas del agente.

El número de teléfono del cliente se inyecta server-side (desde el contexto del
webhook) — el LLM nunca lo ve ni lo puede falsificar.
"""
import json
import logging
import math
import mimetypes
import re
import unicodedata
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

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
    hoy_venezuela,
    now_utc,
)
from app.services import cola_media
from app.services.db import get_session_factory
from app.services.dueno import CLAVE_COPIA, telefono_de_la_duena
from app.services.meta_client import enviar_imagen, enviar_texto, enviar_video
from app.services.redis_client import get_cache, set_cache
from app.services.tasa import obtener_tasa_bcv

logger = logging.getLogger(__name__)

# ─── Schemas que ve el LLM (formato OpenAI / OpenRouter) ──────────────

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "ver_catalogo",
            "description": "Lista DETERMINISTA de los productos que calzan con lo que pide el cliente. SIEMPRE que nombre un TIPO, INGREDIENTE, MASA o RELLENO (pan, quesillo, 'empanada de plátano', 'pan de plátano', 'galleta de chocolate', 'algo de yuca'...), USA `busqueda` con esas palabras (tipo + ingrediente). Busca en el nombre Y en los ingredientes, y devuelve SOLO los que DE VERDAD lo tienen → ofrécele únicamente esos, ni uno más (no decidas tú de tu memoria cuáles calzan). Usa `categoria` solo si pide una categoría completa. Para ver TODO / 'qué tienen' / recomendaciones, usa enviar_catalogo (PDF), no esta.",
            "parameters": {
                "type": "object",
                "properties": {
                    "busqueda": {
                        "type": "string",
                        "description": "Lo que pide el cliente en palabras clave: tipo + ingrediente/relleno (ej. 'empanada plátano', 'pan plátano', 'galleta chocolate', 'quesillo'). Filtra por nombre e ingredientes, tolerando errores de escritura y acentos.",
                    },
                    "categoria": {
                        "type": "string",
                        "enum": ["panaderia", "dulceria", "congelados", "artesanal", "harinas"],
                        "description": "Categoría completa a mostrar. Omitir si usas búsqueda o para ver todo.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "info_producto",
            "description": "Da el detalle de un producto: ingredientes, precio y presentación. Úsala cuando pregunten por un producto específico.",
            "parameters": {
                "type": "object",
                "properties": {
                    "nombre": {"type": "string", "description": "Nombre del producto a consultar"}
                },
                "required": ["nombre"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "registrar_pedido",
            "description": "Registra el pedido del cliente con TODOS los productos y cantidades en UNA sola llamada (no lo dividas en varias). El total lo calcula el código con los precios reales del catálogo y te devuelve un `resumen` (líneas + total) listo para copiarle al cliente; NUNCA sumes tú.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "variante_id": {
                                    "type": "integer",
                                    "description": (
                                        "El NÚMERO del producto/tamaño, copiado EXACTAMENTE de "
                                        "`id_para_pedir` del catálogo. Es un código de barras: "
                                        "NO te lo inventes, NO lo deduzcas y NO uses uno que no "
                                        "hayas visto en el catálogo. Si el producto tiene varios "
                                        "tamaños, PREGÚNTALE al cliente cuál quiere antes de "
                                        "registrar: cada tamaño tiene SU precio."
                                    ),
                                },
                                "producto": {
                                    "type": "string",
                                    "description": (
                                        "El nombre del producto, solo para que quede legible. "
                                        "El precio SIEMPRE sale del `variante_id`, nunca de aquí."
                                    ),
                                },
                                "cantidad": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "description": (
                                        "CUÁNTOS PAQUETES COMPLETOS (NO unidades sueltas). "
                                        "Las Empanadas se venden en paquete de 8: si el cliente "
                                        "quiere 8 empanadas, cantidad=1. Si quiere 16, cantidad=2. "
                                        "Si te pide unidades sueltas o una cantidad que no calza, "
                                        "PREGÚNTALE cuántos paquetes quiere antes de registrar."
                                    ),
                                },
                                "opciones": {
                                    "type": "string",
                                    "description": (
                                        # 🔴 EL TERCER SITIO QUE EMPUJABA A PEDIR EL SABOR (08-22).
                                        # `_REGLAS` y la personalidad se arreglaron el 08-21; ESTE
                                        # NO, y el modelo lee el schema en CADA llamada. Decía
                                        # "que la dueña necesita para cocinar" y no mencionaba en
                                        # ninguna parte que se puede registrar sin él — mientras
                                        # `required` (abajo) lleva desde siempre solo `variante_id`
                                        # y `cantidad`. Un campo que el schema declara opcional y
                                        # describe como imprescindible es una contradicción que el
                                        # modelo resuelve del lado malo: bloqueando la venta.
                                        "OPCIONAL. Lo que el cliente eligió DENTRO del paquete: "
                                        "relleno, masa, sabor o mezcla (ej. '4 de pollo y 4 de "
                                        "carne mechada', 'masa de plátano'). NO cambia el precio. "
                                        "Si el cliente ya lo dijo, pásalo con SUS palabras; si no "
                                        "lo dijo, DÉJALO VACÍO y registra igual — la dueña lo "
                                        "coordina después (el negocio trabaja bajo pedido). "
                                        "NUNCA dejes de registrar un pedido por esto."
                                    ),
                                },
                            },
                            "required": ["variante_id", "cantidad"],
                        },
                    },
                    "notas": {"type": "string", "description": "Notas del pedido (opcional)"},
                    "entrega": {
                        "type": "string",
                        "description": (
                            "CÓMO lo quiere, con las palabras del cliente: retiro o delivery, "
                            "y dónde (ej. 'delivery en Cabudare'; 'lo retiro en La Mendera'). "
                            "La hora NO se cierra aquí: la coordina la dueña después."
                        ),
                    },
                    "entrega_fecha": {
                        "type": "string",
                        "description": (
                            "La FECHA de entrega acordada, en formato AAAA-MM-DD. Tú la calculas "
                            "a partir de lo que dijo el cliente ('el sábado', 'pasado mañana') y "
                            "de la fecha de HOY, que te doy en este mismo mensaje. El CÓDIGO la "
                            "valida contra el calendario del negocio (días de entrega, feriados y "
                            "los días de anticipación que necesita cada producto): si no se puede, "
                            "te devuelve la primera fecha que SÍ y se la ofreces al cliente. "
                            "PREGÚNTALA siempre antes de cobrar."
                        ),
                    },
                    "zona_id": {
                        "type": "integer",
                        "description": (
                            "El NÚMERO de la zona, copiado EXACTO del `zona_id` de la lista de "
                            "ZONAS DE ENTREGA que te doy en cada mensaje. Es un código de barras, "
                            "igual que el del producto: el COSTO DEL ENVÍO lo pone el sistema a "
                            "partir de este id, y lo SUMA al total. TÚ NUNCA sumas ni estimas el "
                            "envío. Si el cliente lo retira, usa el id de la zona de RETIRO (sale "
                            "sin costo). Si el sitio que dice el cliente no calza claramente con "
                            "una zona, LÉELE las zonas y pregúntale en cuál está; si aun así no "
                            "calza, llama a `pedir_ayuda`. JAMÁS adivines la zona ni elijas la más "
                            "barata para cerrar la venta."
                        ),
                    },
                },
                "required": ["items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "proxima_fecha_entrega",
            # 🔴 ESTE TEXTO ES LO QUE EL MODELO LEE PARA DECIDIR LLAMARLA, así que nombra el caso
            # exacto que falló: le ofreció a la clienta "mañana domingo" (cerrado) un sábado al
            # mediodía, y después se inventó que ya habían pasado las 6 de la tarde.
            "description": (
                "🗓️ OBLIGATORIA antes de nombrar CUALQUIER día de entrega. Te dice qué día es hoy, "
                "si hoy todavía se puede, y las próximas fechas REALES en que el negocio entrega "
                "(ya con los días cerrados, los feriados y la anticipación de cada producto "
                "descontados). Úsala apenas se hable de para cuándo lo quiere — antes de decir "
                "'mañana', 'el lunes' o cualquier fecha. NUNCA cuentes los días tú: el negocio "
                "no entrega todos los días y hay productos que necesitan preparación, así que "
                "una fecha calculada de cabeza sale mal y el cliente se entera después."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "productos": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Los productos que quiere, si ya se sabe (nombres exactos del "
                            "catálogo). Afinan la fecha: el pedido va a la velocidad del más "
                            "lento. Si todavía no eligió, no lo mandes."
                        ),
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "info_negocio",
            "description": "Da información del negocio: ubicación, método de pago y redes. Úsala para dudas de ubicación, cómo pagar, etc.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_info",
            # 🔴 ESTE TEXTO ES EL QUE EL MODELO LEE PARA DECIDIR A QUIÉN LLAMAR. Antes decía
            # literalmente "ingredientes" y "¿cuánto dura?", que es trabajo de `info_producto`:
            # por eso una fila de Conocimiento le ganaba la carrera a la ficha del producto. Y si
            # el bot sigue pidiendo datos de producto aquí, la dueña se los vuelve a cargar aquí
            # — la limpieza de la 030 se desharía sola. UN dato, UN sitio.
            "description": "Busca lo que la dueña cargó SOBRE EL NEGOCIO: envíos y entrega, formas de pago y descuentos, ubicación, horarios, políticas e insumos compartidos (ej. la masa madre). Úsala para dudas GENERALES que no sean de precio/pedido (ej. '¿hacen envíos?', '¿hay descuento pagando en dólares?', '¿dónde están?'). 🔴 NO la uses para datos de UN producto —de qué está hecho, cuánto dura, si se congela, si es apto para diabéticos—: eso SIEMPRE sale de info_producto o del catálogo. Responde SOLO con lo que devuelva; si no trae nada, dilo con sinceridad y no inventes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "consulta": {
                        "type": "string",
                        "description": "La duda del cliente, en pocas palabras clave (ej. 'huevo', 'duración pan', 'diabéticos', 'envíos nacionales').",
                    }
                },
                "required": ["consulta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ver_pedidos_cliente",
            "description": "Muestra los pedidos previos de este cliente. Úsala si pregunta por su pedido o quiere repetir uno.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recordar_cliente",
            "description": "Guarda en la ficha del cliente su NOMBRE y/o un dato clave de salud o preferencia (diabético, vegano, alérgico a X, etc.) para reconocerlo y recordarlo la próxima vez. Llámala apenas el cliente te DIGA su nombre (ej. al agendar el pedido) o mencione un dato así. NO inventes: guarda SOLO lo que el cliente dijo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "nombre": {"type": "string", "description": "Nombre del cliente, si lo dijo."},
                    "nota": {
                        "type": "string",
                        "description": "Dato de salud o preferencia que el cliente mencionó (ej. 'diabético', 'vegana', 'alérgica al maní').",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generar_datos_pago",
            "description": "Genera el cobro: calcula el total en bolivares (tasa BCV del dia), devuelve un `resumen_cobro` listo para copiar y los datos de TODOS los metodos de pago (`metodos_de_pago`). Usala JUSTO despues de registrar_pedido, pasando el `pedido_id` que esa te devolvio (para cobrar ESE pedido, no uno viejo). Es la UNICA fuente de los datos de pago (cedula, telefono, cuenta, correo): dale al cliente SOLO los del metodo que el elija, copiados tal cual — JAMAS los escribas de memoria. Si el cliente pide los datos otra vez, vuelve a llamarla.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pedido_id": {
                        "type": "integer",
                        "description": "ID del pedido a cobrar. Omitelo para usar el ultimo pedido del cliente.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "registrar_comprobante",
            "description": "Registra que el cliente REPORTO su pago (dio una referencia o dice que ya pago). NO confirma el pago: el pago se verifica aparte antes de darlo por bueno. Usala cuando el cliente diga que pago o te de el numero de referencia.",
            "parameters": {
                "type": "object",
                "properties": {
                    "referencia": {
                        "type": "string",
                        "description": "Numero de referencia del Pago Movil, si el cliente lo proporciona.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "enviar_catalogo",
            "description": "Envía al cliente el CATÁLOGO en PDF (el folleto bonito) para que vea las opciones y haga su pedido. Úsala cuando el cliente quiera ver opciones, pregunte qué tienen / qué hay, pida una recomendación, diga que quiere algo (sin especificar qué), o pida el catálogo/menú/folleto. Si devuelve que no hay PDF, recién ahí usa ver_catalogo (texto).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "enviar_fotos_producto",
            "description": "Envía al cliente las FOTOS y VIDEOS de UN producto por WhatsApp. Es tu arma de venta, ÚSALA PROACTIVA: en cuanto el cliente se enfoque en UN producto concreto (lo elija, te pida su info o pregunte por él), muéstraselo SIN esperar a que pida la foto; y también cuando pida ver/mostrar ('muéstrame', 'mándame una foto', 'quiero verlo'), pregunte cómo se ve, o dude. UN producto a la vez (no mandes fotos de varios a la vez). Es la ÚNICA forma de saber si el producto tiene fotos: NO asumas que no hay sin llamarla primero. Manda las mejores (hasta 3). Si no tiene fotos cargadas, te avisa para que lo digas con sinceridad. Si el cliente ya eligió una VERSIÓN (la de yuca, la de plátano…), pásala en `etiqueta`. Si ese producto YA se le mostró antes en este chat, NO se reenvía y te aviso (repetirlas es spam); para reenviar de verdad está `reenviar`. (Para ver el menú/opciones en general usa enviar_catalogo.)",
            "parameters": {
                "type": "object",
                "properties": {
                    "nombre": {
                        "type": "string",
                        "description": "Nombre del producto del que el cliente quiere ver fotos/videos.",
                    },
                    "variante_id": {
                        "type": "integer",
                        "description": (
                            "OPCIONAL. Si el cliente pidió un TAMAÑO concreto, pon aquí su "
                            "`id_para_pedir` y se le mandan las fotos DE ESE tamaño. Si no dijo "
                            "tamaño, no lo pongas."
                        ),
                    },
                    "maximo": {
                        "type": "integer",
                        "description": (
                            "OPCIONAL, casi nunca hace falta: cuántos archivos mandar (3 por "
                            "defecto). Úsalo solo si vas a enseñar DOS productos en el mismo "
                            "turno; ahí manda 1 de cada uno para no bombardear."
                        ),
                    },
                    "etiqueta": {
                        "type": "string",
                        "description": (
                            "OPCIONAL. Si el cliente ya dijo QUÉ VERSIÓN quiere de ese producto "
                            "(la de yuca, la de plátano, de chocolate…), pon aquí SUS PALABRAS "
                            "tal cual. Si hay una foto de esa versión se le manda ESA; si no la "
                            "hay, se le mandan las generales y te aviso para que NO le digas que "
                            "la foto era de eso. Si no dijo cuál, NO lo pongas."
                        ),
                    },
                    "reenviar": {
                        "type": "boolean",
                        "description": (
                            "OPCIONAL. Ponlo en true SOLO si el cliente PIDE volver a ver las "
                            "fotos ('mándamela otra vez', 'no me llegó', 'se me borró'): con él "
                            "se reenvían aunque ya se le hubieran mostrado. Sin él, un producto "
                            "ya mostrado en este chat NO se repite (te aviso y sigues la venta)."
                        ),
                    },
                },
                "required": ["nombre"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pedir_ayuda",
            "description": (
                "Le pasa la conversación a la DUEÑA (una persona real) y deja de responder en "
                "este chat. Es tu salida honesta cuando algo NO te toca resolver a ti. "
                "LLÁMALA SIEMPRE que: (1) te pregunten el PRECIO de un producto cuyo precio dice "
                "'PRECIO DEL DÍA' o 'a consultar' (ese precio cambia y solo lo sabe la dueña: "
                "está PROHIBIDO inventarlo o usar uno viejo); (2) te pregunten algo que NO SABES "
                "y las herramientas no te lo dan (ej. envíos a otra ciudad, una política que no "
                "tienes cargada); (3) el cliente pida hablar con una PERSONA o con la dueña; "
                "(4) el cliente RECLAME de verdad (algo llegó mal, no le llegó, quiere su dinero). "
                "Después de llamarla, dile al cliente CON TUS PROPIAS PALABRAS, cálida y natural, "
                "que le confirmas eso enseguida (nunca una plantilla, y NUNCA le digas que "
                "'le preguntas a la dueña': tú ERES Whuilianny)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "motivo": {
                        "type": "string",
                        "enum": ["precio_del_dia", "no_se", "pide_persona", "reclamo"],
                        "description": "Por qué necesitas a la dueña.",
                    },
                    "detalle": {
                        "type": "string",
                        "description": (
                            "En una línea, QUÉ necesita la dueña para poder responder. "
                            "Sé concreto: 'pregunta el precio de la Torta keto de 1kg' o "
                            "'pregunta si hacen envíos a Caracas'."
                        ),
                    },
                },
                "required": ["motivo", "detalle"],
            },
        },
    },
]


# Diagnóstico de arranque: deja en los logs QUÉ herramientas trae el código desplegado.
# Sirve para confirmar de un vistazo si el deploy del worker incluyó enviar_fotos_producto.
logger.info(
    "Herramientas cargadas (%d): %s",
    len(TOOL_SCHEMAS),
    ", ".join(t["function"]["name"] for t in TOOL_SCHEMAS),
)


def schemas_para(activas) -> list[dict]:
    """Lo que el LLM VE. `_DISPATCH` NO se filtra JAMÁS (fase 4).

    🔴 LA ASIMETRÍA ES EL DISEÑO, no un descuido. `agent.py` nunca usa `TOOL_SCHEMAS` para
    ejecutar — ejecuta por `ejecutar_tool` → `_DISPATCH`. Así, con la lista del modelo recortada
    y el dispatch entero:

      · Las 7 redes de seguridad siguen llamando a `pedir_ayuda` y `enviar_catalogo` aunque el
        modelo ya no las vea.
      · El worker de visión sigue llamando a `registrar_comprobante` directo.

    Si se filtrara el dispatch, apagar una herramienta desde el panel le arrancaría el brazo a
    una red de seguridad. El gate correcto es "qué VE el modelo", no "qué puede ejecutar el
    código".
    """
    return [t for t in TOOL_SCHEMAS if t["function"]["name"] in activas]


# ─── Implementaciones ────────────────────────────────────────────────

def monto_en_efectivo(total, envio) -> Decimal:
    """Lo que se cobra pagando en DÓLARES — efectivo, Zelle o Binance—: 20% de descuento sobre
    los PRODUCTOS y el delivery por cuenta de la casa (decisión de Maired del 2026-08-24, que
    extiende a Zelle/Binance la regla del 22-ago; el nombre `monto_en_efectivo` queda como
    reliquia histórica — renombrarlo arrastraría a sus dos llamadores sin ganar nada).

    🔴 UNA SOLA FUENTE, Y ESA ES LA RAZÓN DE QUE EXISTA. Esta cuenta vivía DUPLICADA en dos
    sitios que tienen que dar EXACTAMENTE lo mismo: `generar_datos_pago` (lo que se le cobra al
    cliente) y `registrar_comprobante` (el monto contra el que se compara su captura). Lo único
    que las mantenía sincronizadas era un comentario pidiéndolo — y un comentario no es un
    candado. Si se separan, el comprobante no calza y **cada venta en dólares sale marcada como
    "no cuadra"**, que es el carril del dinero fallando en silencio.

    ⚠️ El delivery NO se suma. Hasta el 2026-08-22 sí se sumaba, con este motivo escrito: "si no,
    la dueña paga el flete de su bolsillo". Lo paga a propósito — es la palanca para cobrar en
    efectivo. Cuesta $3 en la zona centro y $5 en la oeste.
    """
    productos = Decimal(str(total)) - Decimal(str(envio or 0))
    return (productos * Decimal("0.80")).quantize(Decimal("0.01"))


def _fmt_usd(x) -> str:
    """Monto USD listo para mostrar: '$16' si es entero, '$16.50' si no.
    None -> 'a consultar'. El cobro NUNCA lo calcula el modelo: estos strings
    se arman aquí (en código) para que el bot solo los copie."""
    if x is None:
        return "a consultar"
    d = Decimal(str(x))
    if d == d.to_integral_value():
        return f"${int(d)}"
    return f"${d.quantize(Decimal('0.01'))}"


def _fmt_bs(x) -> str:
    """Monto en bolívares estilo Venezuela: 9718.28 -> '9.718,28'."""
    entero, _, dec = f"{Decimal(str(x)):.2f}".partition(".")
    miles = f"{int(entero):,}".replace(",", ".")
    return f"{miles},{dec}"


def _sin_acentos(s: str) -> str:
    t = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


# Palabras vacías que NO deben usarse para filtrar (no son ni tipo ni ingrediente).
_STOP_BUSQUEDA = {
    "con", "sin", "los", "las", "una", "uno", "unos", "unas", "por", "para", "del",
    "que", "tienes", "tienen", "tiene", "quiero", "hay", "algo", "dame", "tipo",
    "producto", "productos", "relleno", "rellenos", "sabor", "sabores", "masa",
}


def _palabras_busqueda(consulta: str) -> list[str]:
    """Palabras significativas de la consulta (sin acentos, sin 'stop words').
    'empanada de plátano' -> ['empanada', 'platano']; 'algo de yuca' -> ['yuca']."""
    limpio = _sin_acentos(consulta).replace(",", " ").replace(".", " ").replace("/", " ")
    return [w for w in limpio.split() if len(w) > 2 and w not in _STOP_BUSQUEDA]


def _tokens_producto(prod, extra: str = "") -> list[str]:
    """Todas las palabras (sin acentos) de nombre + descripción/ingredientes (+ los SABORES de
    sus tamaños, que llegan en `extra`).
    Ese es el 'texto real' del producto contra el que se filtra por ingrediente.
    La CATEGORÍA NO se incluye a propósito: 'pan' no debe calzar con la categoría
    'panadería' (haría que 'pan de almendra' trajera empanadas de panadería).

    ⚠️ `extra` NO es opcional en la práctica: al fusionar las dos Kombuchas, los sabores del
    tamaño de 700ml (cúrcuma, flor de jamaica) dejaron de vivir en `descripcion` y pasaron al
    TAMAÑO. Sin pasarlos aquí, "quiero la kombucha de flor de jamaica" NO ENCONTRARÍA NADA y la
    regla antiinvención obligaría al bot a decir "de eso no tengo" sobre algo que SÍ se vende.
    """
    texto = f"{prod.nombre} {prod.descripcion or ''} {extra}"
    limpio = (
        _sin_acentos(texto)
        .replace(",", " ").replace(".", " ").replace(":", " ")
        .replace("/", " ").replace("(", " ").replace(")", " ")
    )
    return limpio.split()


def _coincide_texto(prod, palabras: list[str], extra: str = "") -> bool:
    """True si CADA palabra buscada es el INICIO de alguna palabra del producto
    (nombre + ingredientes + los SABORES de sus tamaños). Determinista: 'plátano' calza con la
    descripción 'masa de plátano', pero 'empanada plátano' NO calza con las
    Empanadas Horneadas (yuca/garbanzo). Prefijo de PALABRA (no substring): 'pan'
    calza con 'Pan de Sándwich' pero NO con em-PAN-adas."""
    tokens = _tokens_producto(prod, extra)
    return all(any(t.startswith(w) for t in tokens) for w in palabras)


# ─── QUÉ FOTO SE MANDA (funciones PURAS, a propósito) ───────────────────────────────
# Las Empanadas (producto 5) se hacen de plátano O de yuca: MISMO precio, MISMA variante, DOS
# fotos. La dueña le pone NOMBRE a cada una en el panel (`producto_media.etiqueta`) y aquí se
# elige cuál va. Puras porque así el banco las prueba SIN escribir una fila en la base.


def _calza_etiqueta(etiqueta: str | None, pedido: str | None) -> bool:
    """True si el nombre que la dueña le puso a la foto responde a lo que pidió el cliente.
    Prefijo de PALABRA y sin acentos: el MISMO criterio de `_coincide_texto` y por la misma
    razón — con substring, 'yuca' calzaría dentro de cualquier palabra que la contenga."""
    limpio = (
        _sin_acentos(etiqueta or "")
        .replace(",", " ").replace(".", " ").replace(":", " ")
        .replace("/", " ").replace("-", " ").replace("(", " ").replace(")", " ")
    )
    tokens = limpio.split()
    palabras = _palabras_busqueda(pedido or "")
    return bool(tokens and palabras and all(any(t.startswith(w) for t in tokens) for w in palabras))


def _elegir_medios(todos, variante_id, etiqueta):
    """Cuáles de las fotos del producto se mandan. Devuelve
    (medios, etiqueta_enviada, etiquetas_disponibles)."""
    ets = sorted({(m.etiqueta or "").strip() for m in todos if (m.etiqueta or "").strip()})
    medios = list(todos)
    if variante_id is not None:
        # Primero las de ESE tamaño; luego las neutras (las que no tienen tamaño asignado). Las
        # de OTRO tamaño NO se mandan: enviar la de 350ml cuando piden la de 700ml es
        # exactamente el error que se arregló en su día.
        medios = (
            [m for m in medios if m.variante_id == variante_id]
            + [m for m in medios if m.variante_id is None]
        )
    pedido = (etiqueta or "").strip()
    if not pedido:
        # 🔴 SIN PEDIDO NO SE FILTRA NADA — se comporta EXACTAMENTE como antes de existir esta
        # columna. Si aquí se filtrara, el día que la dueña le ponga nombre a las DOS fotos de
        # las Empanadas, "muéstrame las empanadas" mandaría CERO fotos y el bot diría que no
        # tiene ninguna. Es la trampa de este cambio: no la muevas.
        return medios, None, ets
    con_et = [m for m in medios if _calza_etiqueta(m.etiqueta, pedido)]
    neutras = [m for m in medios if not (m.etiqueta or "").strip()]
    if con_et:
        # Las de OTRA etiqueta JAMÁS salen: mandar la de plátano cuando pidieron la de yuca es
        # justo el error que se está arreglando (misma doctrina que `variante_id`).
        return con_et + neutras, (con_et[0].etiqueta or "").strip(), ets
    # Ninguna es de eso: van las generales (y el resultado avisa para que el bot NO mienta).
    return neutras, None, ets


async def _buscar_productos_difuso(
    session, consulta, *, limite=12, umbral=0.3, solo_disponibles=True, con_descripcion=False,
    umbral_descripcion=0.6,
):
    """Búsqueda TOLERANTE a errores de tipeo y acentos (pg_trgm + unaccent).
    Encuentra 'galletas' aunque escriban 'galetas', y 'limón' aunque pongan 'limon'.
    Devuelve productos ordenados del más parecido al menos. Si pg_trgm aún no está
    o la consulta falla, devuelve [] y el llamador cae a la búsqueda exacta de
    siempre: NUNCA rompe el flujo (la búsqueda difusa es una mejora, no un requisito).

    🔴 `con_descripcion` ES OPT-IN, Y NO ES UN CAPRICHO. Esta función la comparten los DOS
    carriles, y buscar también en la descripción los afecta al revés:

      · ver_catalogo (ASESORÍA) → BIEN: la descripción del Kéfir dice "Bebida láctea
        fermentada", así que 'bebidas' lo encuentra. Traer de más aquí es gratis.
      · _buscar_producto (COBRO) → MAL: con la descripción encendida, `_buscar_producto('bebidas')`
        devolvía el Kéfir — o sea, el bot podía COBRAR un producto porque la palabra aparecía
        en su descripción. Verificado: en `master` devuelve None (correcto) y al encenderla
        pasaba a devolver el Kéfir. Es la misma familia del bug de las Empanadas ($12 vs $14).

    Por eso el DEFAULT es False (el comportamiento de siempre, el del cobro) y solo la asesoría
    la enciende.

    🔴 Y POR ESO LA DESCRIPCIÓN LLEVA SU PROPIO UMBRAL, MÁS ALTO (`umbral_descripcion=0.6`).
    Medido el 2026-08-23 contra la base real, buscando lo que la plantilla de Maired ofrece y el
    catálogo NO tiene:

        'hogaza'   → similitud con el NOMBRE 0.000 · con la DESCRIPCIÓN 0.429 → Arepas Andinas
        'rusticos' → similitud con el NOMBRE 0.000 · con la DESCRIPCIÓN 0.444 → Yogurt Kéfirado

    O sea: cero parecido con el nombre de nada, y aun así un calce contra una descripción LARGA de
    ingredientes ('hogaza'↔'harina', 'rusticos'↔'probióticos'). Una descripción larga infla
    `word_similarity` porque hay muchas palabras contra las que calzar.

    🔴 EL DAÑO NO ES "ENCONTRAR DE MÁS": ES QUE UN CALCE ESPURIO ES PEOR QUE NINGUNO. Con CERO
    calces, la búsqueda cae al escalón bueno y la nota dice *"calzan varios: nómbrale los TIPOS y
    pregúntale de cuál quiere"* — que es exactamente la respuesta correcta a "tienes hogaza?" ("eso
    no, mira lo que sí hay"). Con UN calce espurio, la nota pasa a *"Calza UN solo producto:
    preséntalo"* y el bot contesta "tienes hogaza?" con **Arepas Andinas**, en tono de certeza.
    Es el camino de `pizza`/`sushi` —que funciona bien— secuestrado por un falso positivo.

    Los calces LEGÍTIMOS por descripción están muy por encima del ruido, así que el corte no
    pierde ninguno (medido):

        'bebidas' → Kéfir  0.750   (su descripción dice "Bebida láctea fermentada")
        'limon'   → tortas 1.000   (el sabor está escrito en la descripción)
        ── ruido ──────────────────
        'rusticos' 0.444 · 'hogaza' 0.429 · 'limon'→Caldo de Huesos 0.333

    De paso arregla otra cosa que nadie había mirado: 'limon' devolvía **15** productos, entre
    ellos Caldo de Huesos y Pan de Hamburguesa (ruido de 0.333). Ahora devuelve los 5 que de
    verdad lo mencionan.
    """
    q = (consulta or "").strip()
    if len(q) < 2:
        return []
    cond = "AND disponible IS TRUE" if solo_disponibles else ""
    # El nombre lleva un bonus (+0.2) para que un calce por NOMBRE gane siempre a uno por
    # descripción, aunque la descripción se parezca más.
    sim = (
        "GREATEST("
        "  word_similarity(unaccent(lower(:q)), unaccent(lower(nombre))) + 0.2,"
        "  CASE WHEN word_similarity(unaccent(lower(:q)),"
        "            unaccent(lower(COALESCE(descripcion, '')))) >= :umbral_desc"
        "       THEN word_similarity(unaccent(lower(:q)),"
        "            unaccent(lower(COALESCE(descripcion, '')))) ELSE 0 END"
        ")"
        if con_descripcion
        else "word_similarity(unaccent(lower(:q)), unaccent(lower(nombre)))"
    )
    extra_where = (
        " OR word_similarity(unaccent(lower(:q)), unaccent(lower(COALESCE(descripcion, ''))))"
        " >= :umbral_desc"
        if con_descripcion
        else ""
    )
    sql = text(
        f"""
        SELECT id, {sim} AS sim
        FROM productos
        WHERE (word_similarity(unaccent(lower(:q)), unaccent(lower(nombre))) >= :umbral
               OR unaccent(lower(nombre)) LIKE '%' || unaccent(lower(:q)) || '%'
               {extra_where})
              {cond}
        ORDER BY sim DESC, id
        LIMIT :lim
        """
    )
    try:
        rows = (
            await session.execute(
                sql,
                {"q": q, "umbral": umbral, "lim": limite, "umbral_desc": umbral_descripcion},
            )
        ).all()
    except Exception as e:  # noqa: BLE001 — sin pg_trgm: el llamador usa la búsqueda exacta
        # 🔴 ANTES ESTO ERA UN `return []` MUDO. Si a un cliente nuevo le faltaba `pg_trgm`
        # (CREATE EXTENSION normalmente exige superusuario), la difusa fallaba en CADA
        # llamada, en silencio, para siempre — y nadie se enteraba. Ahora grita en el log.
        await session.rollback()
        logger.warning("Búsqueda difusa CAÍDA (¿falta pg_trgm/unaccent?): %s", e)
        return []
    ids = [r.id for r in rows]
    if not ids:
        return []
    prods = (
        await session.execute(select(Producto).where(Producto.id.in_(ids)))
    ).scalars().all()
    orden = {pid: i for i, pid in enumerate(ids)}
    prods.sort(key=lambda p: orden.get(p.id, 10_000))
    return prods


# ══════════════════════════════════════════════════════════════════════════════════════
#  EL BUSCADOR DE LA ASESORÍA — los escalones que el carril del DINERO **no** tiene
# ══════════════════════════════════════════════════════════════════════════════════════
#
# 🔴 POR QUÉ ESTO VIVE APARTE Y NO DENTRO DE `_coincide_texto`:
#
# `_coincide_texto` lo comparten DOS carriles, y quieren cosas OPUESTAS:
#
#   · `ver_catalogo`     → la ASESORÍA. Aquí conviene ser GENEROSO: encontrar un producto de
#                          más solo cuesta que el bot lo ofrezca. Es un error barato.
#   · `_buscar_producto` → el COBRO. Aquí ser generoso CUESTA DINERO: si 'pan' calzara con la
#                          categoría 'panaderia', `_buscar_producto('pan')` traería las
#                          **Empanadas Keto** (categoria=panaderia) y el bot podría COBRAR el
#                          producto equivocado. Es literalmente el bug del 2026-07-11 ($12 vs
#                          $14) que documenta CLAUDE.md.
#
# Aflojar el filtro compartido arreglaría la asesoría **y rompería el cobro a la vez**. Por eso
# el filtro estricto se queda INTACTO, y lo que sigue son ESCALONES que solo usa la asesoría.

# Sinónimos COMERCIALES: lo que el cliente DICE no siempre es lo que está ESCRITO en el catálogo.
# El cliente pide "bebidas"; en la base pone "Kombucha", "Kéfir de Leche de cabra…", "Yogurt
# Kéfirado". Ninguna de las tres contiene la palabra "bebidas": el filtro devolvía CERO y el
# código le ordenaba al bot decir "de eso no tengo" — sobre tres productos que SÍ vende.
#
# La dueña lo edita desde el panel (clave `sinonimos_busqueda`). Formato: una línea por término,
# "termino: palabra1, palabra2". La derecha pueden ser palabras del catálogo O una CATEGORÍA.
# FAIL-OPEN: sin clave, se usa este default (mismo criterio que `dias_entrega`).
_SINONIMOS_DEFAULT = """
bebida: kombucha, kefir, yogurt
bebidas: kombucha, kefir, yogurt
tomar: kombucha, kefir, yogurt
postre: dulceria
postres: dulceria
dulce: dulceria
snack: galleta, tequeno, barra, ponquesito
snacks: galleta, tequeno, barra, ponquesito
merienda: galleta, tequeno, barra, ponquesito
desayuno: pan, arepa, wafle, granola
almuerzo: empanada, tequeno, caldo
cena: empanada, tequeno, caldo
"""

# Palabras con las que el cliente pregunta por un producto APTO PARA DIABÉTICOS. No es una
# palabra del catálogo: es el campo `apto_diabeticos` (lo tienen 24 de los 31 productos).
_PALABRAS_DIABETICO = ("diabetic", "diabete", "glicemia", "glucosa", "azucar en sangre")


def _parsear_sinonimos(texto: str) -> dict[str, list[str]]:
    """'bebidas: kombucha, kefir' → {'bebidas': ['kombucha', 'kefir']}. Nunca lanza."""
    mapa: dict[str, list[str]] = {}
    for linea in (texto or "").splitlines():
        if ":" not in linea:
            continue
        clave, _, valores = linea.partition(":")
        clave = _sin_acentos(clave.strip())
        palabras = [_sin_acentos(v.strip()) for v in valores.split(",") if v.strip()]
        if clave and palabras:
            mapa[clave] = palabras
    return mapa


async def _leer_sinonimos(session) -> dict[str, list[str]]:
    """Los sinónimos que editó la dueña, o el default. Cualquier fallo cae al default."""
    try:
        valor = (
            await session.execute(
                select(Configuracion.valor).where(Configuracion.clave == "sinonimos_busqueda")
            )
        ).scalars().first()
    except Exception:  # noqa: BLE001 — sin sinónimos el bot sigue buscando
        return _parsear_sinonimos(_SINONIMOS_DEFAULT)
    mapa = _parsear_sinonimos(valor) if valor and valor.strip() else {}
    return mapa or _parsear_sinonimos(_SINONIMOS_DEFAULT)


def _calza_categoria(palabra: str, categoria: str) -> bool:
    """¿La palabra buscada ES esta categoría? ('dulces' → 'dulceria', 'harinas' → 'harinas')

    🔴 EXIGE ≥5 LETRAS PARA EL CALCE POR PREFIJO, y no es un capricho: con menos, **'pan'
    calzaría con 'panaderia'** y la asesoría de "pan" traería las Empanadas Keto (que son de
    panadería). Es el mismo veneno que el bug del cobro, servido en el otro carril.
    """
    p, c = _singular(palabra), _singular(_sin_acentos(categoria or ""))
    if not p or not c:
        return False
    return p == c or (len(palabra) >= 5 and c.startswith(p))


def _es_apto_diabeticos(prod) -> bool:
    """El campo dice que sí ('si', 'si.', 'si, con stevia'). 'no' y vacío NO cuentan."""
    return _sin_acentos(prod.apto_diabeticos or "").strip().startswith("si")


def _cobertura(prod, palabras: list[str], extra: str = "") -> int:
    """CUÁNTAS de las palabras buscadas calzan con este producto (no todas: cuántas).

    Es el escalón que salva 'pan sin gluten'. El filtro estricto es un AND: 'pan' calza con 4
    productos, pero **'gluten' no calza con ninguno** —ninguna de las 31 descripciones contiene
    esa palabra; "todo es sin gluten" vive en la personalidad, no en el catálogo— así que el AND
    tiraba TODO a cero y el bot negaba tener pan. Aquí se ordena por cuántas calzan y ganan los
    panes: 1 de 2. Lo que no está, no se inventa; simplemente no puntúa.
    """
    tokens = _tokens_producto(prod, extra)
    return sum(1 for w in palabras if any(t.startswith(w) for t in tokens))


# 🔴 RESTRICCIONES ALIMENTARIAS QUE EL CATÁLOGO **NO** SABE RESPONDER.
#
# `apto_diabeticos` sí es un campo real (24 de 32 productos lo tienen), y por eso "algo para
# diabéticos" se resuelve bien en el escalón 4. **Estas otras no existen en ninguna columna**, así
# que la búsqueda las manda a los escalones difusos… y ahí pasa esto (medido el 2026-08-22):
#
#     ver_catalogo(busqueda='vegano')  →  ["Pan de Sándwich", "Wafles Dulces"]
#       · Pan de Sándwich  lleva MANTECA DE COCHINO y huevo
#       · Wafles Dulces    llevan HUEVOS e HÍGADO DESHIDRATADO
#     ver_catalogo(busqueda='celiaco') →  los 31 productos, con la nota de un acierto normal
#
# El escalón difuso lleva escrito "en la asesoría encontrar de más es gratis". Es verdad para un
# typo ('galetas' → Galletas). **Es falso para una restricción alimentaria**: ahí encontrar de más
# es ofrecerle hígado a un vegano y pan con gluten a una celíaca. Y la Conversación 3 de la
# plantilla de negocio es, justamente, una clienta vegana comprando para su hijo.
_RESTRICCION_SIN_CAMPO = (
    "vegano", "vegana", "veganos", "veganas", "vegetarian",
    "celiac", "gluten", "trigo", "avena", "centeno", "cebada",
    "lactosa", "lacteo", "leche", "queso",
    "alergi", "alerg", "mani", "cacahuate", "almendra", "merey", "nuez", "nueces",
    "frutos secos", "huevo", "soya", "maiz",
)


def _es_restriccion_alimentaria(busqueda: str) -> str | None:
    """Devuelve la restricción detectada, o None. Solo mira lo que NO tiene campo en la BD."""
    t = _sin_acentos((busqueda or "").lower())
    for w in _RESTRICCION_SIN_CAMPO:
        if w in t:
            return w
    return None


async def ver_catalogo(session, telefono, categoria=None, busqueda=None):
    """El catálogo para ASESORAR. La lista vacía ya no existe.

    🔴 EL BUG QUE ESTO MATA (auditoría 2026-07-14, verificado ejecutando el filtro real):
    esta función devolvía `productos: []` en **6 de 19 consultas normales** de cliente —
    "pan sin gluten", "bebidas", "postres", "algo para diabéticos", "desayuno", "snacks"— y
    con la lista vacía mandaba esta nota:

        "no tienes ningún producto que calce con 'X'; dile con sinceridad que de eso no tienes"

    Combinado con la regla ANTIINVENCIÓN del prompt, **el código le ORDENABA al bot negar
    productos que el negocio SÍ vende.** El bot no desobedecía: obedecía un bug.

    Ahora la búsqueda baja por ESCALONES deterministas, y el último garantiza que SIEMPRE hay
    algo que ofrecer. Ninguno adivina: si un producto sale, es porque el CÓDIGO lo emparejó.
    """
    stmt = select(Producto).where(Producto.disponible.is_(True)).order_by(Producto.id)
    # ORDER BY estable: sin él Postgres devuelve el orden que le da la gana y dos servidores
    # con el MISMO código contestan distinto. Es la misma familia del bug de las Empanadas.
    if categoria:
        stmt = stmt.where(Producto.categoria == categoria.lower())
    productos = (await session.execute(stmt)).scalars().all()
    como = "exacto"  # por qué escalón salieron (gobierna la NOTA de más abajo)

    if busqueda and productos:
        palabras = _palabras_busqueda(busqueda)
        # Los SABORES viven en el TAMAÑO desde la migración 022 (la kombucha de 700ml tiene
        # cúrcuma y flor de jamaica; la de 350ml, no). Se los damos al filtro o "flor de
        # jamaica" no encontraría nada.
        _sab = await _tamanos_de(session, [p.id for p in productos])
        _extra = {pid: " ".join(v.sabores or "" for v in vs) for pid, vs in _sab.items()}
        todos = list(productos)

        def _con(fn):
            return [p for p in todos if fn(p)]

        # ── ESCALÓN 1 · EXACTO. El AND estricto de siempre (el mismo del cobro).
        #    'empanada plátano' trae SOLO las de plátano, nunca las Horneadas de yuca.
        hallados = (
            _con(lambda p: _coincide_texto(p, palabras, _extra.get(p.id, "")))
            if palabras
            else todos
        )

        # ── ESCALÓN 2 · CATEGORÍA. 'harinas', 'dulces', 'congelados'… son categorías reales,
        #    y NUNCA fueron buscables (`_tokens_producto` las excluye a propósito, y con razón:
        #    ver `_calza_categoria`). Aquí se resuelven aparte, sin contaminar el filtro.
        if not hallados and palabras:
            hallados = _con(lambda p: any(_calza_categoria(w, p.categoria) for w in palabras))
            if hallados:
                como = "categoria"

        # ── ESCALÓN 3 · SINÓNIMO COMERCIAL. 'bebidas' → kombucha/kéfir/yogurt.
        #    Lo que el cliente DICE no es lo que está ESCRITO en el catálogo.
        if not hallados and palabras:
            mapa = await _leer_sinonimos(session)
            expandidas = [w for p in palabras for w in mapa.get(_singular(p), mapa.get(p, []))]
            if expandidas:
                hallados = _con(
                    lambda p: any(
                        _coincide_texto(p, [w], _extra.get(p.id, "")) or _calza_categoria(w, p.categoria)
                        for w in expandidas
                    )
                )
                if hallados:
                    como = "sinonimo"

        # ── 🔴 FRENO DE SEGURIDAD ALIMENTARIA — va ANTES de los escalones que adivinan.
        #    Si lo que pide es una restricción que el catálogo no sabe responder (vegano,
        #    celíaco, alergias…), NO se le ofrece "lo más parecido": el parecido aquí es lo que
        #    le pone manteca de cochino delante a un vegano. Se corta la búsqueda y se le dice
        #    al modelo qué hacer — mirar la ficha producto por producto, y escalar si no consta.
        # ⚠️ SOLO cuando la consulta es la restricción A SECAS. Si además nombra un producto o
        #    una categoría ("pan sin gluten"), es una búsqueda normal y los escalones de abajo la
        #    resuelven bien — frenarla dejaría al bot sin nada que ofrecer y cortaría la venta.
        #    Lo cazó `probar_buscador`, que tenía justo ese caso escrito.
        _restriccion = _es_restriccion_alimentaria(busqueda) if busqueda else None
        if _restriccion and palabras:
            _nombra_producto = any(
                _con(lambda p, w=w: _coincide_texto(p, [w], _extra.get(p.id, "")))
                or any(_calza_categoria(w, p.categoria) for p in todos)
                for w in palabras
            )
            if _nombra_producto:
                _restriccion = None
        if not hallados and _restriccion:
            return {
                "productos": [],
                "nota": (
                    f"🔴 '{busqueda}' es una RESTRICCIÓN ALIMENTARIA y el catálogo NO tiene ese "
                    "dato cargado, así que NO tengo con qué responderte. **NO le ofrezcas "
                    "productos parecidos ni le digas que algo es apto**: aquí equivocarse le "
                    "hace daño a una persona. Dile con calidez que se lo confirmas con "
                    "seguridad antes de que compre, y llama a `pedir_ayuda` para que "
                    "Whuilianny te diga cuáles sirven. Si quiere ver el catálogo entero "
                    "mientras tanto, mándaselo — pero SIN afirmar que alguno cumple."
                ),
            }

        # ── ESCALÓN 4 · ATRIBUTO. "algo para diabéticos" no es una palabra del catálogo: es el
        #    campo `apto_diabeticos`, que tienen 24 de los 31 productos.
        if not hallados:
            t = _sin_acentos(busqueda)
            if any(w in t for w in _PALABRAS_DIABETICO):
                hallados = _con(_es_apto_diabeticos)
                if hallados:
                    como = "diabeticos"

        # ── ESCALÓN 5 · DIFUSA. Typos y acentos: 'galetas' → Galletas New York.
        #    Aquí SÍ se mira la descripción (`con_descripcion=True`): en la asesoría encontrar de
        #    más es gratis. En el cobro NO se enciende jamás — ver `_buscar_productos_difuso`.
        if not hallados:
            difusos = await _buscar_productos_difuso(
                session, busqueda, limite=12, umbral=0.4, solo_disponibles=True,
                con_descripcion=True,
            )
            if categoria:
                difusos = [p for p in difusos if (p.categoria or "") == categoria.lower()]
            if difusos:
                hallados, como = difusos, "difusa"

        # ── ESCALÓN 6 · MEJOR COBERTURA. El que salva "pan sin gluten": 'pan' calza con 4
        #    productos y 'gluten' con ninguno, así que el AND lo tiraba todo a cero. Aquí gana
        #    el que más palabras cubre. No inventa nada: lo que no calza, no puntúa.
        if not hallados and len(palabras) > 1:
            puntuados = [(_cobertura(p, palabras, _extra.get(p.id, "")), p) for p in todos]
            mejor = max((n for n, _ in puntuados), default=0)
            if mejor > 0:
                hallados = [p for n, p in puntuados if n == mejor]
                como = "parcial"

        # ── ESCALÓN 7 · NUNCA VACÍO. Si de verdad no hay nada que se parezca, el bot recibe el
        #    CATÁLOGO ENTERO y una nota honesta. Puede (y debe) decir que ESO no lo tiene —
        #    pero con algo real en la mano, no cortando la venta con un "no tengo" a secas.
        if not hallados:
            hallados, como = todos, "nada"

        productos = hallados

    if not productos:
        # Solo llega aquí si el catálogo está VACÍO de verdad, o la categoría no existe.
        return {
            "productos": [],
            "nota": (
                "no hay NINGÚN producto cargado en el catálogo (ni siquiera para ofrecer otra "
                "cosa). Dile con cariño que ahorita no tienes nada disponible y llama a "
                "`pedir_ayuda`."
                if not busqueda
                else "no hay productos en esa categoría"
            ),
        }
    _nota_interno = (
        "El precio_usd y 'trae' (unidades) son INTERNOS: dilos SOLO si el cliente los "
        "pregunta o ya está comprando."
    )
    # ── EL AVISO DEL ESCALÓN. El bot obedece la `nota`, así que aquí está el arreglo de verdad:
    #    cuando el calce NO fue exacto, se le dice la VERDAD (no calzó del todo) y a la vez se le
    #    PROHÍBE el "de eso no tengo" a secas. Antes, no calzar significaba lista vacía + una
    #    orden de negar. Ahora: honestidad SIN cortar la venta.
    _aviso = ""
    if como in ("parcial", "difusa"):
        _aviso = (
            f" OJO: no hay nada que calce EXACTO con '{busqueda}'. Esto es LO MÁS PARECIDO que "
            "sí tienes. Ofrécelo como tal, con naturalidad. NO afirmes que es exactamente lo que "
            "pidió, y NO le digas que no tienes nada. "
        )
    elif como == "nada":
        _aviso = (
            f" 🔴 NADA en el catálogo se parece a '{busqueda}'. Dile con cariño y SIN RODEOS que "
            "ESO puntual no lo tienes — pero NUNCA cortes ahí: de esta lista (que es TODO lo que "
            "vendes) ofrécele lo que mejor encaje con lo que buscaba. Un 'no tengo' a secas mata "
            "la venta; un 'eso no, pero mira esto' la salva. "
        )

    if len(productos) > 1:
        # VARIOS productos calzan (ej. 'empanadas' = 3 familias): NO soltar el folleto.
        # El CÓDIGO decide (por el conteo) que se nombren solo los tipos y se retenga el
        # 'de_que_es' hasta que el cliente elija — así el agente no lista todos los rellenos.
        nota = (
            "Calzan VARIOS productos. NO sueltes un folleto: nómbrale SOLO los TIPOS por su "
            "nombre (SIN el 'de_que_es' de cada uno) y pregúntale de cuál quiere saber. El "
            "'de_que_es' (rellenos/ingredientes) se lo das de UNO, DESPUÉS, cuando el cliente "
            "elija cuál. NO agregues otros productos aunque tengan nombre parecido. "
            # 🔴 SIGUE EL HILO también aquí (31-ago): esta rama ORDENABA preguntar "de cuál"
            # sin excepción — si el cliente YA había elegido uno y el modelo re-consultaba la
            # familia, la orden fresca le ganaba a la elección vieja (la mecánica de la masa).
            "PERO SIGUE EL HILO: si el cliente YA eligió uno de estos (míralo en el chat y en "
            "EL HILO DE LA VENTA del sistema), NO le repreguntes cuál — sigue con ESE. " + _nota_interno
        )
    else:
        # UN solo producto: preséntalo corto y sigue el hilo (no sumar otra variante).
        nota = (
            "Calza UN solo producto. Preséntalo corto: su nombre y de qué es. SIGUE EL HILO: si "
            "el cliente ya dijo una masa/variante (ej. plátano), quédate SOLO en esa y ofrécele "
            "lo que aún no eligió (ej. el relleno). NO agregues otros productos. " + _nota_interno
        )
    # LOS TAMAÑOS, con su precio de HOY y su `id_para_pedir` (la lista CERRADA con la que el
    # bot registra: sin esto no puede vender, y con esto no puede cobrar mal).
    por_prod = await _tamanos_de(session, [p.id for p in productos])
    salida = []
    sin_precio = []
    for p in productos:
        vs = por_prod.get(p.id) or []
        tamanos = []
        for v in vs:
            precio = await _precio_efectivo(session, v)
            tamanos.append({
                "id_para_pedir": v.id,
                "tamano": v.presentacion,
                "precio_usd": float(precio) if precio is not None else "el precio de hoy no lo sabes: pide_ayuda",
                # 🔴 `precio_texto` NO es decoración: es lo que hace VISIBLE el precio para la RED
                # DEL DINERO. `autorizados_por_moneda` (agent.py) solo reconoce cifras con MARCA de
                # dinero ($25, 25 Bs); un `precio_usd: 25.0` pelado NO entra en la lista blanca.
                # Hoy el bot se salva solo porque `_catalogo_bloque` mete "$25.00" en el system
                # prompt — pero ese bloque COLAPSA a categorías si el catálogo pasa de 60 productos
                # (`_CATALOGO_INLINE_MAX`). El día que un cliente tenga catálogo grande, el bot no
                # podría decir NINGÚN precio sin que saltara "DINERO INVENTADO". Esto lo desactiva.
                "precio_texto": _fmt_usd(precio) if precio is not None else None,
                "sabores": v.sabores,
                "agotado": (not v.disponible) or (not p.disponible),
            })
        ficha = {
            "nombre": p.nombre,
            "categoria": p.categoria,
            "de_que_es": p.descripcion,
            "tamanos": tamanos,
        }
        if not tamanos:
            # Un producto SIN tamaños no se puede vender: no hay precio ni `id_para_pedir`. No
            # debería pasar (la migración le da uno a cada uno), pero si pasa el bot NO improvisa.
            # El prompt ya lo avisa en su bloque de catálogo; la herramienta también, ahora.
            ficha["NO_SE_PUEDE_VENDER"] = "sin precio cargado: no lo ofrezcas ni lo registres"
            sin_precio.append(p.nombre)
        elif len(tamanos) == 1:
            # Un solo tamaño: se ve IGUAL que siempre (la palabra "tamaño" ni aparece).
            ficha["precio_usd"] = tamanos[0]["precio_usd"]
            ficha["precio_texto"] = tamanos[0]["precio_texto"]
            ficha["trae"] = None if vs[0].presentacion == "única" else vs[0].presentacion
            ficha["id_para_pedir"] = tamanos[0]["id_para_pedir"]
        salida.append(ficha)
    nota += _aviso
    if any(len(f["tamanos"]) > 1 for f in salida):
        nota += (
            " OJO: alguno tiene VARIOS TAMAÑOS con precios distintos — PREGÚNTALE al cliente "
            "cuál quiere antes de registrar, y usa el `id_para_pedir` de ESE tamaño. Salvo que "
            "YA te lo haya dicho: entonces usa ESE sin repreguntar."
        )
    if sin_precio:
        nota += (
            f" ⚠️ Estos NO se pueden vender (sin precio cargado): {', '.join(sin_precio)}. "
            "No los ofrezcas ni los registres."
        )
    return {"productos": salida, "nota": nota}


def _nombre_norm(texto: str) -> str:
    """Nombre comparable: sin acentos, minúsculas, sin espacios de más."""
    return " ".join(_sin_acentos(texto or "").split())


async def _precio_efectivo(session, variante):
    """El precio que se puede COBRAR HOY por ESTE TAMAÑO.

    El precio vive en el TAMAÑO, no en el producto: la Kombucha de 350ml cuesta $4 y la de
    700ml $7. Antes el precio colgaba del producto y la dueña tuvo que crear DOS productos con
    el mismo nombre; el buscador devolvía siempre el primero y el bot SIEMPRE COBRABA $4.

    - Tamaño con precio fijo -> ese precio.
    - Tamaño de PRECIO DEL DÍA (precio vacío A PROPÓSITO: tortas, premezclas… cuyo costo cambia
      de un día a otro en Venezuela) -> el que la dueña dio HOY **para ese tamaño**.
    - Si aún no lo dio hoy -> None. El bot NO puede cobrarlo ni inventarlo: llama a
      `pedir_ayuda`. Un precio de AYER jamás se reutiliza (por eso `hoy_venezuela()`: con el
      reloj del servidor, a las 8 de la noche de Cabudare el precio del día DESAPARECÍA).
    """
    if variante.precio is not None:
        return variante.precio
    return (
        await session.execute(
            select(PrecioDia.precio).where(
                PrecioDia.variante_id == variante.id, PrecioDia.fecha == hoy_venezuela()
            )
        )
    ).scalar_one_or_none()


def _tiene_varios(prod, variante) -> bool:
    """¿Hace falta nombrar el tamaño? Solo si el producto tiene más de uno."""
    return (variante.presentacion or "") not in ("", "única")


async def _lista_para_pedir(session) -> list[dict]:
    """La LISTA CERRADA de lo que se puede pedir, con su id. Es lo que se le devuelve al
    modelo cuando manda un id que no existe o está agotado: para que corrija con uno REAL,
    nunca con uno "parecido"."""
    filas = (
        await session.execute(
            select(Producto, ProductoVariante)
            .join(ProductoVariante, ProductoVariante.producto_id == Producto.id)
            .where(Producto.disponible.is_(True), ProductoVariante.disponible.is_(True))
            .order_by(Producto.nombre, ProductoVariante.orden)
        )
    ).all()
    out = []
    for prod, v in filas:
        nombre = prod.nombre
        if _tiene_varios(prod, v):
            nombre += f" ({v.presentacion})"
        out.append({"id_para_pedir": v.id, "producto": nombre})
    return out


async def _tamanos_de(session, producto_ids: list[int]) -> dict[int, list]:
    """Los tamaños de cada producto, en orden. {producto_id: [ProductoVariante, ...]}"""
    if not producto_ids:
        return {}
    filas = (
        await session.execute(
            select(ProductoVariante)
            .where(ProductoVariante.producto_id.in_(producto_ids))
            .order_by(ProductoVariante.orden, ProductoVariante.id)
        )
    ).scalars().all()
    out: dict[int, list] = {}
    for v in filas:
        out.setdefault(v.producto_id, []).append(v)
    return out


def _singular(texto: str) -> str:
    """Quita la 's' final de cada palabra larga: 'empanadas keto' → 'empanada keto'.
    Así 'empanada' (singular) calza con 'Empanadas' pero NUNCA con 'Empanadas Keto'."""
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in texto.split())


async def _buscar_producto(session, nombre: str, solo_disponibles: bool = False):
    """Busca UN producto para emparejarlo en el pedido (camino del DINERO).

    REGLA DE ORO: el nombre EXACTO manda y NUNCA se elige al azar. 'Empanadas' es un
    producto DISTINTO de 'Empanadas Keto': pedir "Empanadas" jamás puede cobrar las Keto.

    Escalones, de más a menos preciso (gana el primero que resuelve):
    1) nombre EXACTO (sin acentos ni mayúsculas).
    2) el texto pedido CONTIENE el nombre completo de un producto → gana el MÁS ESPECÍFICO
       (nombre más largo): "quiero Empanadas Keto" → Empanadas Keto, no Empanadas.
    3) cada palabra pedida es PREFIJO DE PALABRA del producto (nombre + ingredientes),
       reusando el filtro determinista del catálogo: 'pan' calza con 'Pan de Sándwich'
       pero NO con em-PAN-adas, y 'empanada plátano' NO calza con las Keto (almendra).
       Si calzan varios, gana el nombre más corto (el "a secas") y luego el id menor.
    4) último recurso DIFUSO con umbral ALTO (typos): 'galetas' → 'Galletas New York'.

    DETERMINISTA a propósito. Antes usaba `ilike('%nombre%')` + `.first()` SIN ORDER BY:
    (a) 'Empanadas' calzaba por substring con 'Empanadas Keto'/'Horneadas' y Postgres
    devolvía uno ARBITRARIO — el 2026-07-11 el servidor viejo cobraba 'Empanadas Keto'
    ($12/4u) y el nuevo 'Empanadas' ($14/8u) con la MISMA consulta y el MISMO código;
    (b) 'pan' calzaba con em-PAN-adas. Ver SESIONES 2026-07-12.
    """
    objetivo = _nombre_norm(nombre)
    if not objetivo:
        return None

    stmt = select(Producto).order_by(Producto.id)  # orden ESTABLE en cualquier servidor
    if solo_disponibles:
        stmt = stmt.where(Producto.disponible.is_(True))
    prods = (await session.execute(stmt)).scalars().all()
    if not prods:
        return None

    # 1) EXACTO — lo que el catálogo le dio al agente; es el caso normal.
    for p in prods:
        if _nombre_norm(p.nombre) == objetivo:
            return p

    # 1b) EXACTO ignorando singular/plural: 'empanada' → 'Empanadas' (y NUNCA las Keto).
    objetivo_sg = _singular(objetivo)
    exactos_sg = [p for p in prods if _singular(_nombre_norm(p.nombre)) == objetivo_sg]
    if len(exactos_sg) == 1:
        return exactos_sg[0]

    # 2) El pedido trae el nombre completo de un producto dentro → el MÁS específico.
    contenidos = [p for p in prods if _nombre_norm(p.nombre) and _nombre_norm(p.nombre) in objetivo]
    if contenidos:
        return max(contenidos, key=lambda p: (len(_nombre_norm(p.nombre)), -p.id))

    # 3) Prefijo de PALABRA (mismo filtro determinista que usa ver_catalogo).
    #    Si calza UNO solo, ese es. Si calzan VARIOS ('pan' → Pan Keto / de Sándwich /
    #    de Hamburguesa, con precios distintos) NO se adivina: se devuelve None y el que
    #    llama le pasa al agente la lista para que le PREGUNTE al cliente cuál quiere.
    palabras = _palabras_busqueda(objetivo)
    if palabras:
        calzan = [p for p in prods if _coincide_texto(p, palabras)]
        if len(calzan) == 1:
            return calzan[0]
        if len(calzan) > 1:
            return None  # ambiguo de verdad: preguntar, jamás cobrar a la suerte

    # 4) Difuso con umbral ALTO. Solo un parecido MUY claro (typo); jamás otro producto.
    candidatos = await _buscar_productos_difuso(
        session, nombre, limite=1, umbral=0.6, solo_disponibles=solo_disponibles
    )
    return candidatos[0] if candidatos else None


def _frase_comparable(texto: str) -> str:
    """Un texto en la forma en que se compara contra nombres de producto: minúsculas, sin
    acentos, SOLO letras y números (la puntuación se vuelve espacio) y en singular.

    La puntuación se limpia ANTES de `_singular` a propósito: en "te recomiendo las empanadas,"
    la coma pegada hace que la palabra no termine en 's' y el singular no se aplique — y ahí
    'Empanadas' no calzaría con su propia mención. Mismo `_singular` que usa `_buscar_producto`."""
    plano = re.sub(r"[^a-z0-9]+", " ", _sin_acentos(texto or ""))
    return _singular(" ".join(plano.split()))


def _formas_de_un_nombre(nombre: str) -> list[str]:
    """Las formas comparables en que un texto puede nombrar este producto.

    Para un nombre simple es una sola: el nombre entero. Para un nombre COMPUESTO con ' o '
    —UN producto con varias versiones, como el REAL del taller: "Empanadas de masa de yuca o
    de masa de plátano" (un solo producto, dos fotos etiquetadas)— el bot nunca lo dice entero:
    confirma "las Empanadas de masa de plátano". Sin estas formas, la red de la foto era CIEGA
    justo al producto estrella (hueco encontrado por Erwin con el bot real, 2026-08-08). Se
    aceptan además del nombre entero:
    - cada alternativa con la CABEZA del nombre delante ("empanada de masa de platano");
    - la alternativa sola, SOLO si trae ≥2 palabras de contenido (con una sola, " de yuca "
      calzaría con cualquier frase que mencione yuca);
    - la cabeza sola ("las Empanadas" = este producto)… si ningún OTRO producto la reclama:
      esa colisión la resuelve `_productos_nombrados_en`, no esta función.
    """
    base = _frase_comparable(nombre)
    if not base:
        return []
    formas = {base}
    if " o " in base:
        partes = [p.strip() for p in base.split(" o ") if p.strip()]
        cabeza = base.split()[0]
        for p in partes:
            formas.add(p if p.startswith(f"{cabeza} ") or p == cabeza else f"{cabeza} {p}")
            if len(_palabras_busqueda(p)) >= 2:
                formas.add(p)
        if len(cabeza) >= 4:
            formas.add(cabeza)
    return sorted(formas)


# Palabras que convierten lo que viene detrás en un INGREDIENTE, no en una oferta:
# "vienen CON harina de almendra", "LLEVAN azúcar de coco", "ENDULZADAS con alulosa",
# "RELLENO de queso de cabra", "a BASE de yuca".
_MARCA_INGREDIENTE = frozenset({
    "con", "lleva", "llevan", "lleve", "lleven", "base", "hecho", "hecha", "hechos", "hechas",
    "contiene", "contienen", "endulzado", "endulzada", "endulzados", "endulzadas",
    "relleno", "rellenos", "rellena", "rellenas", "sabor", "sabores",
})


def _solo_como_ingrediente(campo: str, marca: str) -> bool:
    """True si TODAS las apariciones de `marca` en el texto van detrás de un marcador de
    ingrediente — o sea, el bot la nombró describiendo de qué está hecho algo, no ofreciéndola.

    🔴 EL BUG QUE TAPA (medido el 2026-08-21 con el smoke de asesoría). El bot cerró así:

        "Listo, 1 paquete de Galletas New York, vienen con HARINA DE ALMENDRA y coco…"

    Y "Harina de Almendra" **es un producto del catálogo**. `producto_enfocado` encontraba DOS
    menciones, concluía "el cliente sigue eligiendo" y **la RED DE LA FOTO no disparaba**: 0 fotos
    en las 5 vueltas del smoke, con el producto ya elegido y con foto en la base.

    Lo peor es que las dos redes se PELEABAN: la del PITCH (08-08) obliga al bot a tejer datos
    REALES de la ficha —o sea, ingredientes—, y justo eso apagaba la de la foto. Cuanto mejor
    vendía, menos fotos mandaba. Y el catálogo está lleno de insumos que son a la vez producto e
    ingrediente ("Harina de Almendra", "Harina de Yuca"), así que no era un caso raro.

    Conservador a propósito: basta UNA aparición fuera de contexto de ingrediente para que cuente
    como oferta ("quieres las galletas o la harina de almendra?"). Mejor no mandar una foto que
    mandar la equivocada — doctrina $12/$14.
    """
    trozos = campo.split(marca)
    for antes in trozos[:-1]:            # el texto que precede a cada aparición
        previas = antes.split()[-2:]     # las dos palabras de delante
        if not any(p in _MARCA_INGREDIENTE for p in previas):
            return False                 # esta aparición SÍ es una oferta
    return True


def _productos_nombrados_en(texto: str, nombres: list[str]) -> list[str]:
    """Qué productos aparecen NOMBRADOS en un texto libre. El más específico primero.

    Es la semántica del bug $12/$14 (`_buscar_producto`, CLAUDE.md §8) aplicada al texto del
    BOT en vez de al pedido del cliente:
    - Palabra completa, jamás substring: 'Pan' NO calza dentro de em-PAN-adas (la limpieza de
      `_frase_comparable` deja las palabras separadas por espacios y se busca " pan ").
    - El más específico se queda con su trozo de texto: 'Empanadas Keto' en la frase NO cuenta
      también como 'Empanadas' (el trozo calzado se tacha antes de buscar los nombres cortos).
      Pero "tenemos Empanadas y Empanadas Keto" sí son DOS menciones: cada una tiene su trozo.
    - Singular/plural con `_singular`: el bot escribe "la empanada keto" y el producto se llama
      'Empanadas Keto'.
    - Un nombre COMPUESTO con ' o ' cuenta por CUALQUIERA de sus formas (`_formas_de_un_nombre`)
      y sus dos versiones son EL MISMO producto: "de plátano" y "de yuca" en la misma frase NO
      son ambigüedad — ambigüedad es solo entre productos DISTINTOS.
    - Una forma que reclaman DOS productos distintos se DESCARTA entera: con "Empanadas" y
      "Empanadas de masa de yuca o …" en el mismo catálogo, "las empanadas" a secas no calza
      con ninguno — mejor ninguna foto que la del producto equivocado.
    """
    campo = f" {_frase_comparable(texto)} "
    # forma comparable → índice del ÚNICO producto que la reclama (colisiones fuera).
    duenos: dict[str, int] = {}
    repetidas: set[str] = set()
    for i, nombre in enumerate(nombres):
        for forma in _formas_de_un_nombre(nombre):
            if forma in duenos and duenos[forma] != i:
                repetidas.add(forma)
            else:
                duenos[forma] = i
    encontrados: list[str] = []
    vistos: set[int] = set()
    # Orden: forma más LARGA primero (más específica), y alfabético de desempate para que el
    # resultado no dependa del orden en que llegó la lista.
    for forma in sorted(duenos, key=lambda f: (-len(f), f)):
        if forma in repetidas:
            continue
        marca = f" {forma} "
        if marca in campo and _solo_como_ingrediente(campo, marca):
            # Nombrado SOLO como ingrediente: se tacha para que no vuelva a calzar, pero NO
            # cuenta como producto ofrecido.
            campo = campo.replace(marca, " § ")
            continue
        if marca in campo:
            # Se TACHA lo calzado (todas sus apariciones): ese trozo ya es de ESTE producto y
            # una forma más corta no puede volver a calzar dentro.
            campo = campo.replace(marca, " § ")
            i = duenos[forma]
            if i not in vistos:
                vistos.add(i)
                encontrados.append(nombres[i])
    return encontrados


def _versiones_distintivas(nombre: str) -> list[set[str]]:
    """Los tokens que DISTINGUEN cada versión de un nombre COMPUESTO (' o '), una entrada por
    versión: "Empanadas de masa de yuca o de masa de plátano" → `[{'yuca'}, {'platano'}]`.

    Fuera la CABEZA del nombre ("las empanadas" no elige masa) y fuera lo COMÚN a todas las
    versiones ("masa" está en las dos). Lista VACÍA si el producto no tiene versiones: ahí no
    hay nada que elegir — ni, por tanto, nada que recordar.
    """
    base = _frase_comparable(nombre or "")
    if " o " not in base:
        return []
    partes = [p.strip() for p in base.split(" o ") if p.strip()]
    if len(partes) < 2:
        return []
    cabeza = base.split()[0]
    tokens_por_version = [set(_palabras_busqueda(p)) for p in partes]
    comunes = set.intersection(*tokens_por_version)
    return [(t - comunes) - {cabeza} for t in tokens_por_version]


def _versiones_tocadas(distintivos: list[set[str]], mensaje: str) -> list[set[str]]:
    """Qué versiones TOCA un mensaje, y con qué palabras. Una entrada por versión mencionada.

    Vacío = el mensaje no habla de ninguna versión. DOS entradas = las nombró las dos ("mejor
    la de yuca… no, la de plátano") y no hay elección que leer. Distinguir esos dos casos es lo
    que permite que el turno ACTUAL mande sobre lo recordado incluso cuando es ambiguo.
    """
    pedido = set(_palabras_busqueda(_frase_comparable(mensaje or "")))
    if not pedido:
        return []
    return [pedido & d for d in distintivos if pedido & d]


def etiqueta_del_cliente(nombre: str, mensaje_cliente: str) -> str | None:
    """Si el producto es COMPUESTO (' o ') y las palabras del CLIENTE calzan con UNA sola de
    sus versiones, devuelve esas palabras distintivas — listas para `enviar_fotos_producto`
    (parámetro `etiqueta`: "las PALABRAS DEL CLIENTE", dice su docstring, y `_calza_etiqueta`
    las compara por prefijo de palabra contra el nombre que la dueña le puso a cada foto).

    Se devuelven SOLO los tokens distintivos ("platano"), no el mensaje crudo: `_calza_etiqueta`
    exige que TODAS las palabras del pedido calcen, así que un "la de platano porfa" entero
    haría fallar el filtro por el "porfa" y el cliente recibiría la foto general (o ninguna).

    None cuando no aplica — producto simple, el cliente no dijo versión, o nombró LAS DOS
    ("mejor la de yuca… no, la de plátano"): ahí van las fotos sin filtrar, como siempre.
    La CABEZA del nombre no distingue ("las empanadas" no elige masa), y los tokens que
    comparten todas las versiones tampoco ("masa" está en ambas).

    ⚠️ Mira SOLO el mensaje de ESTE turno. Lo que el cliente eligió turnos atrás lo recuerda
    `etiqueta_recordada` — y el turno actual siempre le gana.
    """
    tocadas = _versiones_tocadas(_versiones_distintivas(nombre), mensaje_cliente)
    if len(tocadas) != 1:
        return None
    return " ".join(sorted(tocadas[0]))


# Cuántos turnos del CLIENTE hacia atrás se recuerda la versión que eligió. CORTO A PROPÓSITO:
# recordar "de plátano" de hace 20 turnos es peor que no recordar nada — a esa distancia ya no
# es memoria, es adivinar. Con 3 sobra para el caso real (la elección quedó 2 turnos atrás) y
# el peor error posible sigue siendo el de hoy: mandar las fotos generales.
_TURNOS_ETIQUETA_RECORDADA = 3


def etiqueta_recordada_en(
    nombre: str, mensaje_actual: str, historial: list | None, nombres_catalogo: list[str]
) -> str | None:
    """La versión que el cliente eligió HACE POCO y que sigue valiendo para ESTE producto.
    Función PURA (el catálogo entra por parámetro): así el test la prueba sin tocar la BD.

    🔴 POR QUÉ EXISTE (medido contra el bot real, 2026-08-09). `etiqueta_del_cliente` solo mira
    el turno actual, y la conversación real no repite la elección:

        cliente: "de platano"  ·  cliente: "que relleno hay?"  ·  cliente: "de carne mechada,
        1 paquete"  → aquí dispara la foto y salían LAS DOS masas, porque en ESE mensaje ya no
        aparecía "platano".

    Las reglas, en el orden en que se aplican:
    1. **El turno ACTUAL manda.** Si en este mensaje el cliente tocó alguna versión, la decisión
       ya la tomó `etiqueta_del_cliente` — una sola ⇒ esa; las dos ⇒ generales A PROPÓSITO.
       Aquí se devuelve None para no pisarla con memoria vieja.
    2. **La más reciente gana:** se recorre el historial de atrás hacia delante, así que un
       "mejor la de yuca" posterior tapa al "de platano" de antes.
    3. **La memoria NO CRUZA un cambio de producto.** Un turno (de quien sea) que nombre otro
       producto CORTA el recorrido: una etiqueta solo vale para su producto. Sin esto, el
       "de yuca" de las empanadas se le pegaría al siguiente producto que también tenga yuca.
    4. **Ventana corta** (`_TURNOS_ETIQUETA_RECORDADA` turnos del cliente). En el RETOMAR el
       mensaje del cliente ya viene DENTRO del historial, así que ahí la ventana efectiva es un
       turno más corta — y está bien: recordar de menos es el error barato.
    5. **Nunca se inventa una etiqueta:** solo salen tokens que DISTINGUEN una versión de ESTE
       nombre. Si además ninguna foto se llama así, `_elegir_medios` manda las generales y el
       resultado avisa al modelo — igual que hoy. Doctrina $12/$14: ante la duda, no adivinar.
    """
    distintivos = _versiones_distintivas(nombre)
    if not distintivos:
        return None
    if _versiones_tocadas(distintivos, mensaje_actual):
        return None
    vistos = 0
    for h in reversed(historial or []):
        if not isinstance(h, dict):
            continue
        contenido = str(h.get("content") or "")
        if any(n != nombre for n in _productos_nombrados_en(contenido, nombres_catalogo)):
            return None
        if h.get("role") != "user":
            continue
        tocadas = _versiones_tocadas(distintivos, contenido)
        if tocadas:
            # Una sola versión ⇒ esa es la que eligió. Dos ⇒ dudó, y una duda vieja no se
            # resuelve por mayoría: se cae a las generales.
            return " ".join(sorted(tocadas[0])) if len(tocadas) == 1 else None
        vistos += 1
        if vistos >= _TURNOS_ETIQUETA_RECORDADA:
            return None
    return None


async def etiqueta_recordada(
    nombre: str, mensaje_actual: str, historial: list | None
) -> str | None:
    """`etiqueta_recordada_en` con el catálogo puesto. La usa la RED DE LA FOTO (`_asegurar_foto`).

    Las tres guardas baratas van ANTES de abrir sesión, y no es cosmética: la inmensa mayoría de
    los productos no son compuestos, así que en el caso normal esta función no consulta NADA.
    Abre su propia sesión (mismo patrón que `producto_enfocado`) y ante cualquier fallo devuelve
    None: sin memoria se manda lo de hoy (las fotos generales), que nunca es una mentira.
    """
    if not historial:
        return None
    distintivos = _versiones_distintivas(nombre)
    if not distintivos or _versiones_tocadas(distintivos, mensaje_actual):
        return None
    try:
        factory = get_session_factory()
        async with factory() as session:
            nombres = (
                await session.execute(
                    select(Producto.nombre).where(Producto.disponible.is_(True))
                )
            ).scalars().all()
        return etiqueta_recordada_en(nombre, mensaje_actual, historial, list(nombres))
    except Exception:  # noqa: BLE001 — sin memoria se sigue como hoy; jamás tumba el turno
        logger.exception(
            "etiqueta_recordada: no se pudo leer el catálogo; van las fotos generales"
        )
        return None


# Cuántos turnos del CLIENTE hacia atrás mira EL HILO DE LA VENTA (la línea de estado que el
# código inyecta al prompt con lo que el cliente YA eligió). MÁS LARGA que la de las fotos
# (_TURNOS_ETIQUETA_RECORDADA = 3) a propósito, porque el costo del error es el CONTRARIO:
# la foto de más molesta (mejor recordar de menos), pero el estado que se evapora a media venta
# ES el bug que esta línea tapa. Diez turnos del cliente cubren una venta entera, y el tope
# real sigue siendo el historial de Redis (20 renglones).
_TURNOS_HILO_VENTA = 10


def hilo_de_la_venta_en(
    mensaje_usuario: str, historial: list | None, nombres_catalogo: list[str]
) -> list[tuple[str, str]]:
    """Las elecciones de VERSIÓN que siguen vigentes en esta conversación, más reciente
    primero: [("Empanadas de masa de yuca o de masa de plátano", "yuca")]. Función PURA.

    🔴 POR QUÉ EXISTE (caso real cazado por Maired, 2026-08-31 3:50-3:54pm, taller): la clienta
    dijo "Me gustarían las empanadas de yucas" y dos turnos después, al pedir los rellenos, el
    bot contestó "Y recuerda que puedes elegir la masa de yuca o de plátano. ¿Cuál prefieres?".
    La elección SÍ viajaba en el historial (a 4 renglones del final) y la regla SIGUE EL HILO
    SÍ viajaba en el prompt — y el modelo repreguntó igual, porque la ficha fresca de la
    herramienta (consultada por los rellenos) le reabrió las dos masas. Es la moraleja de
    siempre: el prompt SUGIERE; lo que se quiera garantizar se entrega como ESTADO. Esta
    función destila la elección del chat crudo para que `responder()` la inyecte como hecho —
    el mismo patrón de `_estado_cliente_texto` (la cifra en Bs) y de `etiqueta_recordada`
    (las fotos): el estado vive en la capa que EJECUTA, no en la memoria del modelo.

    Las reglas se parecen a las de `etiqueta_recordada_en`, con DOS diferencias a consciencia:
    - Ventana de `_TURNOS_HILO_VENTA` turnos del cliente (no 3): el estado que se esfuma a
      media venta es exactamente el bug. El error contrario (recordar una elección vieja) se
      cura solo: la línea inyectada ordena que lo último que diga el cliente mande.
    - NO se corta al nombrarse otro producto: la elección es POR PRODUCTO (un pitch del bot o
      un segundo producto en la conversación no pueden borrar la masa ya elegida). El cruce
      entre productos se evita ATRIBUYENDO cada elección: los tokens de versión solo cuentan
      si ese mensaje nombra al producto, o si ningún otro compuesto reclama esos tokens.
    - Igual que allá: la más reciente gana, y un mensaje que toque LAS DOS versiones deja al
      producto SIN elección (una duda no se resuelve por mayoría) — y bloquea las anteriores.
    """
    compuestos = [
        (n, d) for n in (nombres_catalogo or []) if (d := _versiones_distintivas(n))
    ]
    if not compuestos:
        return []
    turnos = [
        str(h.get("content") or "")
        for h in (historial or [])
        if isinstance(h, dict) and h.get("role") == "user"
    ]
    turnos.append(mensaje_usuario or "")
    decididos: dict[str, str | None] = {}
    for contenido in reversed(turnos[-_TURNOS_HILO_VENTA:]):
        pendientes = [(n, d) for n, d in compuestos if n not in decididos]
        if not pendientes:
            break
        tocados = {
            n: t for n, d in pendientes if (t := _versiones_tocadas(d, contenido))
        }
        if not tocados:
            continue
        nombrados = set(_productos_nombrados_en(contenido, nombres_catalogo))
        for n, tocadas in tocados.items():
            # La elección es de ESTE producto si el mensaje lo nombra ("las empanadas de
            # yuca"), o si nadie más la reclama (el "de platano" pelado del caso 2026-08-09:
            # un solo compuesto tocado y ningún OTRO producto nombrado en el mensaje).
            es_suya = n in nombrados or (len(tocados) == 1 and not (nombrados - {n}))
            if not es_suya:
                continue
            decididos[n] = " ".join(sorted(tocadas[0])) if len(tocadas) == 1 else None
    return [(n, e) for n, e in decididos.items() if e]


async def hilo_de_la_venta(
    mensaje_usuario: str, historial: list | None
) -> list[tuple[str, str]]:
    """`hilo_de_la_venta_en` con el catálogo puesto. La usa `responder()` para inyectar el
    estado de lo ya elegido en la parte DINÁMICA del prompt, cada turno.

    Abre su propia sesión (mismo patrón que `etiqueta_recordada`) y ante cualquier fallo
    devuelve [] — sin hilo el bot queda exactamente como hoy: la línea de estado es un
    empujón de venta y jamás puede tumbar un turno.
    """
    if not (mensaje_usuario or "").strip() and not historial:
        return []
    try:
        factory = get_session_factory()
        async with factory() as session:
            nombres = (
                await session.execute(
                    select(Producto.nombre).where(Producto.disponible.is_(True))
                )
            ).scalars().all()
        return hilo_de_la_venta_en(mensaje_usuario, historial, list(nombres))
    except Exception:  # noqa: BLE001 — sin hilo se sigue como hoy; jamás tumba el turno
        logger.exception("hilo_de_la_venta: no se pudo leer el catálogo; va sin línea de hilo")
        return []


async def tamanos_hermanos(variante_id) -> dict | None:
    """(SOLO LECTURA) El tamaño que el modelo eligió y los HERMANOS que competían con él.

    La usa la RED DEL TAMAÑO ADIVINADO (`agent.py`). Devuelve None —o sea, "aquí no hay nada que
    vigilar"— en cuanto el producto tiene UN solo tamaño vendible: si no hay elección, no se
    puede adivinar. Hoy, en esta base, solo tres productos entran (Tortas keto, Kombucha y Torta
    baja en carbohidratos), y son justo los que más dinero mueven por equivocarse: la Kombucha es
    la fuga de $3 de la cirugía del 2026-07-13, y las tortas van de 250g a 1kg.

    Ante CUALQUIER fallo devuelve None y la venta sigue como hasta hoy: una red que se cae no
    puede tumbar un pedido. (Mismo criterio que `etiqueta_recordada` y `producto_enfocado`.)
    """
    try:
        vid = int(variante_id)
    except (TypeError, ValueError):
        return None
    try:
        factory = get_session_factory()
        async with factory() as session:
            elegida = await session.get(ProductoVariante, vid)
            if elegida is None:
                return None   # id inexistente: eso ya lo rechaza `registrar_pedido`
            prod = await session.get(Producto, elegida.producto_id)
            if prod is None:
                return None
            hermanas = (
                await session.execute(
                    select(ProductoVariante.presentacion)
                    .where(
                        ProductoVariante.producto_id == elegida.producto_id,
                        ProductoVariante.disponible.is_(True),
                    )
                    .order_by(ProductoVariante.id)
                )
            ).scalars().all()
    except Exception:  # noqa: BLE001 — una red que falla NO puede tumbar una venta
        logger.exception("tamanos_hermanos: no se pudo leer el catálogo; la venta sigue")
        return None
    vendibles = [str(h) for h in hermanas if str(h or "").strip() not in ("", "única")]
    if len(vendibles) < 2:
        return None
    return {
        "producto": prod.nombre,
        "elegido": str(elegida.presentacion or ""),
        "tamanos": vendibles,
    }


async def productos_enfocados(texto: str, maximo: int = 2) -> list[str]:
    """Los productos DISPONIBLES que un texto nombra con todas sus letras, resueltos — hasta
    `maximo`. Lista VACÍA si no nombra ninguno **o si nombra más de `maximo`**.

    Es el hermano en plural de `producto_enfocado`, y existe por lo que pidió Erwin el 2026-08-21:
    cuando el bot ofrece DOS opciones ("Empanadas de masa de plátano o Empanadas Horneadas"),
    enseñar las dos fotos ayuda a decidir — a puro texto no engancha. Con TRES o más sí es spam, y
    encima quema la calidad del número con Meta: por eso el tope, y por eso pasado el tope se
    devuelve vacío en vez de recortar (mostrar 2 de 5 sería elegir por el cliente).

    Cada nombre se resuelve con `_buscar_producto` —exacto primero, el camino del DINERO— para que
    la foto que salga sea la de ESE producto y jamás la de uno parecido. Un nombre que no resuelve
    se cae de la lista sin tumbar el resto.
    """
    if not (texto or "").strip() or maximo < 1:
        return []
    try:
        factory = get_session_factory()
        async with factory() as session:
            nombres = (
                await session.execute(
                    select(Producto.nombre).where(Producto.disponible.is_(True))
                )
            ).scalars().all()
            menciones = _productos_nombrados_en(texto, list(nombres))
            if not menciones or len(menciones) > maximo:
                return []
            resueltos = []
            for mencion in menciones:
                prod = await _buscar_producto(session, mencion)
                if prod is not None:
                    resueltos.append(prod.nombre)
            return resueltos
    except Exception:  # noqa: BLE001 — sin producto no hay red, y el texto del bot sale igual
        logger.exception("No se pudieron resolver los productos enfocados")
        return []


async def horas_de_silencio(telefono: str) -> float:
    """Cuántas HORAS lleva callado este cliente (según `clientes.ultima_interaccion`).

    Para la red del saludo (`_asegurar_saludo`, agent.py): un "buenas tardes" después de un día
    de silencio merece que se le devuelva; el mismo "hola" dos minutos después, no.

    Se lee `ultima_interaccion`, que en mitad de un turno todavía tiene la marca del turno
    ANTERIOR: `_guardar_en_panel` la pisa al FINAL, cuando el turno ya se contestó. Por eso vale
    para medir el hueco sin guardar nada nuevo.

    Cliente que no existe todavía, o cualquier fallo ⇒ **0.0** (como si acabara de escribir). El
    fallo seguro es NO forzar el saludo: si la red se equivoca por exceso, el bot saluda dos veces
    en la misma conversación, y eso se lee como un bot.
    """
    if not telefono:
        return 0.0
    try:
        factory = get_session_factory()
        async with factory() as session:
            ultima = (
                await session.execute(
                    select(Cliente.ultima_interaccion).where(Cliente.telefono == telefono)
                )
            ).scalars().first()
        if ultima is None:
            return 0.0
        if ultima.tzinfo is None:
            ultima = ultima.replace(tzinfo=UTC)
        return max(0.0, (datetime.now(UTC) - ultima).total_seconds() / 3600.0)
    except Exception:  # noqa: BLE001 — medir el silencio jamás puede tumbar un turno
        logger.exception("No se pudo medir el silencio de %s", telefono)
        return 0.0


async def producto_enfocado(texto: str) -> str | None:
    """El ÚNICO producto disponible que un texto del bot nombra con todas sus letras — o None.

    Para la RED DE LA FOTO (`_asegurar_foto`, agent.py): si el texto nombra DOS o más productos,
    el cliente sigue eligiendo y no hay foco (no se dispara); si no nombra ninguno con su nombre
    completo, no hay certeza de cuál es (tampoco). El nombre que queda se resuelve con
    `_buscar_producto` —exacto primero, el camino del DINERO— para que la foto que salga sea la
    de ESE producto y jamás la de uno parecido.

    Solo productos DISPONIBLES: empujar la foto de algo que no se vende hoy es empujar una venta
    que no puede cerrarse.

    Abre su propia sesión (mismo patrón que `avisar_relevo_caido`: la llama el agente directo,
    no el dispatch). Ante cualquier fallo devuelve None — sin producto no hay red y el texto del
    bot sale igual: esta red es un empujón de venta, nunca puede tumbar un turno.
    """
    if not (texto or "").strip():
        return None
    try:
        factory = get_session_factory()
        async with factory() as session:
            nombres = (
                await session.execute(
                    select(Producto.nombre).where(Producto.disponible.is_(True))
                )
            ).scalars().all()
            menciones = _productos_nombrados_en(texto, list(nombres))
            if len(menciones) != 1:
                return None
            prod = await _buscar_producto(session, menciones[0])
            return prod.nombre if prod is not None else None
    except Exception:  # noqa: BLE001 — sin producto no hay red; el turno sigue intacto
        logger.exception("producto_enfocado: no se pudo resolver; la red de la foto no dispara")
        return None


async def info_producto(session, telefono, nombre):
    prod = await _buscar_producto(session, nombre)
    if prod is None:
        disponibles = (
            await session.execute(
                select(Producto.nombre).where(Producto.disponible.is_(True)).limit(40)
            )
        ).scalars().all()
        return {
            "encontrado": False,
            # 🔴 CON GUARDIA DE HILO (31-ago): esta rama volcaba hasta 40 nombres frescos en
            # plena venta (bastaba que el propio modelo tipeara mal el nombre) y la orden de
            # "ofrecer" podía sacar la conversación del producto ya elegido.
            "nota": (
                f"no hay un producto que calce exacto con '{nombre}'. Si YA estaban hablando "
                "de un producto, corrige el nombre y consulta ESE — NO le reofrezcas el "
                "catálogo entero. Solo si de verdad no había producto en juego, ofrece el más "
                "parecido de la lista"
            ),
            "productos_disponibles": disponibles,
        }
    vs = (await _tamanos_de(session, [prod.id])).get(prod.id) or []
    # LOS NOMBRES QUE LA DUEÑA LE PUSO A CADA FOTO ("base de plátano"). Van aquí y no en el
    # prompt: esta es la ficha completa, es resultado de HERRAMIENTA (el canal sancionado) y
    # viaja en el MISMO payload que `descripcion`, así que no puede desincronizarse.
    _etiquetas_media = (
        await session.execute(
            select(ProductoMedia.etiqueta).where(ProductoMedia.producto_id == prod.id)
        )
    ).scalars().all()
    fotos_etiquetadas = sorted({(e or "").strip() for e in _etiquetas_media if (e or "").strip()})
    fotos_sin_etiqueta = sum(1 for e in _etiquetas_media if not (e or "").strip())
    tamanos = []
    for v in vs:
        precio = await _precio_efectivo(session, v)
        tamanos.append({
            "id_para_pedir": v.id,
            "tamano": v.presentacion,
            "precio_usd": float(precio) if precio is not None else "el precio de hoy no lo sabes: pide_ayuda",
            # 🔴 MISMO MOTIVO QUE EN `ver_catalogo` (ver el comentario largo allá): un
            # `precio_usd: 25.0` pelado NO lleva marca de dinero, así que `autorizados_por_moneda`
            # no lo mete en la lista blanca. Sin esta clave, en modo DOS el precio de un producto
            # concreto le llegaba VACÍO a la Voz — `hoja.py:_renderizar` busca literalmente
            # `precio_texto`, y aquí no existía. `ver_catalogo` sí la traía, así que el bug solo
            # aparecía al preguntar por UN producto, no al ver el catálogo.
            "precio_texto": _fmt_usd(precio) if precio is not None else None,
            "sabores": v.sabores,
            "agotado": (not v.disponible) or (not prod.disponible),
        })
    return {
        "encontrado": True,
        "nombre": prod.nombre,
        "categoria": prod.categoria,
        "descripcion": prod.descripcion,
        # El precio vive en el TAMAÑO. Con uno solo se ve igual que siempre; con varios, el bot
        # TIENE que preguntar cuál quiere (cada uno cuesta distinto).
        "tamanos": tamanos,
        # 🔴 La orden de repreguntar iba EMBEBIDA EN EL DATO (31-ago): "pregúntale cuál
        # quiere" sin excepción de hilo contradecía la nota de abajo si el cliente ya lo dijo.
        "precio_usd": tamanos[0]["precio_usd"] if len(tamanos) == 1 else "depende del tamaño: si ya te dijo cuál, usa ese; si no, pregúntale cuál quiere",
        # Izada al nivel de la ficha con UN solo tamaño, igual que hace `ver_catalogo` — es de aquí
        # de donde la hoja del modo dos lee el precio.
        "precio_texto": tamanos[0]["precio_texto"] if len(tamanos) == 1 else None,
        "presentacion": (vs[0].presentacion if len(vs) == 1 and vs[0].presentacion != "única" else None),
        "duracion": prod.duracion,
        "se_congela": prod.se_congela,
        "apto_diabeticos": prod.apto_diabeticos,
        "info": prod.info,
        "disponible": prod.disponible,
        # Los nombres de las fotos: sirven SOLO para escoger cuál mandar (ver la nota).
        "fotos_etiquetadas": fotos_etiquetadas,
        "fotos_sin_etiqueta": fotos_sin_etiqueta,
        "nota": (
            "Responde sobre ESTE producto SOLO con estos datos. Si el cliente pregunta algo "
            "que aquí está vacío/None (ej. duración, si se congela), NO lo inventes ni copies "
            "de otro producto: dile con calidez que lo confirmas con la dueña. "
            "Las `fotos_etiquetadas` son los nombres que la dueña le puso a CADA FOTO: sirven "
            "SOLO para escoger cuál mandarle (pásalo en `etiqueta` de enviar_fotos_producto). "
            "NO son la lista de opciones del producto: lo que se puede pedir está en "
            "`descripcion` y en los tamaños. NO ofrezcas una versión que no esté ahí. "
            # 🔴 El mismo recordatorio que ya traía ver_catalogo con UN producto — aquí faltaba,
            # y ESTA ficha fue la que reabrió las dos masas en el caso real del 31-ago: el
            # cliente pidió los rellenos, la ficha fresca trajo "yuca o plátano", y el modelo
            # repreguntó la masa ya elegida. El aviso viaja PEGADO al dato que tienta.
            "SIGUE EL HILO: si el cliente ya dijo una masa/versión (mira EL HILO DE LA VENTA "
            "del sistema y el chat), NO se la vuelvas a preguntar ni le reofrezcas la otra: "
            "quédate en la suya y ofrécele SOLO lo que aún no eligió (ej. el relleno)."
        ),
    }


# ─── EL CALENDARIO DEL NEGOCIO (una sola fuente de verdad) ──────────────────────────
#
# El horario vivía SOLO en el texto de la personalidad y el bot lo ignoraba: probado en vivo,
# aceptó un pedido "para el domingo", cobró y pidió el comprobante. Y buscar la palabra
# "domingo" tampoco alcanza: si el cliente dice "para el 19" (que cae domingo), no se entera.
# Por eso el bot pasa una FECHA REAL y el CÓDIGO la valida contra el calendario del negocio:
#   · qué días se entrega   (configuración `dias_entrega`, la edita la dueña)
#   · feriados / vacaciones (tabla `feriados`, los pone la dueña)
#   · cuánta anticipación necesita CADA producto (`productos.dias_anticipacion`)
# Y si la fecha no sirve, el código CALCULA la primera que sí — no lo adivina el modelo.

_DIAS_SEMANA = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
_DIAS_BONITO = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _fecha_larga(f: date) -> str:
    """La fecha como la diría una persona: "sábado 18 de julio"."""
    return f"{_DIAS_BONITO[f.weekday()]} {f.day} de {_MESES[f.month - 1]}"


async def _dias_de_entrega(session) -> set[str]:
    """Los días en que el negocio entrega (normalizados, sin acentos)."""
    valor = (
        await session.execute(
            select(Configuracion.valor).where(Configuracion.clave == "dias_entrega")
        )
    ).scalars().first()
    dias = {_sin_acentos(d.strip().lower()) for d in (valor or "").split(",") if d.strip()}
    # Sin configurar = se entrega todos los días (no bloquear un negocio por falta de dato).
    return dias or set(_DIAS_SEMANA)


async def _anticipacion_del_pedido(session, items_pedido) -> int:
    """Los días de anticipación que necesita el pedido = el MÁS lento de sus productos.
    (Si lleva empanadas congeladas —0 días— y una torta —2 días—, el pedido necesita 2.)"""
    nombres = [it["producto"] for it in items_pedido]
    if not nombres:
        return 0
    dias = (
        await session.execute(
            select(Producto.dias_anticipacion).where(Producto.nombre.in_(nombres))
        )
    ).scalars().all()
    return max([int(d or 0) for d in dias] or [0])


async def _primera_fecha_valida(session, desde: date, dias_ok: set[str], anticipacion: int) -> date:
    """La primera fecha en que SÍ se puede entregar este pedido. La calcula el CÓDIGO."""
    feriados = set(
        (await session.execute(select(Feriado.fecha))).scalars().all()
    )
    f = desde + timedelta(days=anticipacion)
    for _ in range(60):  # tope de seguridad: 2 meses
        if _DIAS_SEMANA[f.weekday()] in dias_ok and f not in feriados:
            return f
        f += timedelta(days=1)
    return f


async def _config_hora(session, clave: str, por_defecto: str) -> str:
    valor = (
        await session.execute(select(Configuracion.valor).where(Configuracion.clave == clave))
    ).scalars().first()
    return (valor or "").strip() or por_defecto


def _ahora_venezuela():
    return datetime.now(UTC) - timedelta(hours=4)


async def _paso_la_hora_de_corte(session) -> bool:
    """¿Ya es demasiado tarde para pedir algo para HOY? Sin esta regla, un cliente puede pedir
    'para hoy mismo' a las 11 de la noche y el bot se lo acepta. La hora la pone la dueña."""
    corte = await _config_hora(session, "hora_corte", "18:00")
    try:
        h, m = (int(x) for x in corte.split(":")[:2])
    except ValueError:
        return False
    ahora = _ahora_venezuela()
    return (ahora.hour, ahora.minute) >= (h, m)


async def _validar_entrega(session, fecha: date, items_pedido) -> dict | None:
    """Devuelve None si la fecha SIRVE; si no, el motivo + la primera fecha buena."""
    hoy = hoy_venezuela()
    dias_ok = await _dias_de_entrega(session)
    anticipacion = await _anticipacion_del_pedido(session, items_pedido)
    feriados = dict(
        (await session.execute(select(Feriado.fecha, Feriado.motivo))).all()
    )

    motivo = None
    desde = hoy  # el primer día que se podría, antes de mirar el calendario
    if fecha < hoy:
        motivo = "esa fecha ya pasó"
    elif fecha < hoy + timedelta(days=anticipacion):
        motivo = (
            f"ese pedido necesita {anticipacion} día(s) de anticipación "
            f"(hay productos que la dueña prepara por encargo)"
        )
    elif fecha == hoy and await _paso_la_hora_de_corte(session):
        # HOY ya no se puede: pasó la hora de corte. El próximo día empieza mañana.
        corte = await _config_hora(session, "hora_corte", "18:00")
        # 🔴 "YA NO HAY ENTREGAS HOY", NUNCA "EL NEGOCIO CIERRA". Lo corrigió Maired el 22-ago:
        # *"Ellos no cierran. Ya no se hacen entregas después de las 6 pm"*. El negocio es ONLINE
        # y el bot sigue atendiendo y vendiendo a cualquier hora — lo que se acaba a las 6 son las
        # ENTREGAS. Decir "cerramos" es echar al cliente de una tienda que sigue abierta.
        motivo = (
            f"hoy ya no salen entregas (van hasta las {corte}), pero seguimos tomando pedidos "
            "para el próximo día disponible"
        )
        desde = hoy + timedelta(days=1)
    elif _DIAS_SEMANA[fecha.weekday()] not in dias_ok:
        motivo = f"los {_DIAS_SEMANA[fecha.weekday()]} el negocio NO entrega"
    elif fecha in feriados:
        extra = f" ({feriados[fecha]})" if feriados.get(fecha) else ""
        motivo = f"ese día el negocio está cerrado{extra}"

    if motivo is None:
        return None

    # Si ya pasó la hora de corte, "hoy" tampoco sirve como primera fecha válida.
    if desde == hoy and await _paso_la_hora_de_corte(session):
        desde = hoy + timedelta(days=1)
    return {
        "motivo": motivo,
        "primera_fecha_valida": await _primera_fecha_valida(session, desde, dias_ok, anticipacion),
    }


async def proxima_fecha_entrega(session, telefono, productos=None):
    """🗓️ LA FECHA LA CALCULA EL CÓDIGO, NO EL MODELO — y esta tool existe por un caso medido.

    El 2026-08-22, un sábado a las 12:44 del mediodía, el bot le ofreció a Maired entregar
    **"mañana domingo"** (el negocio NO entrega domingos) y, al reclamárselo, se inventó la excusa
    de que **"ya pasaron las 6 de la tarde"** — teniendo la hora correcta inyectada en su prompt.

    La maquinaria para no equivocarse YA EXISTÍA entera (`_validar_entrega`), pero solo se dispara
    **al REGISTRAR el pedido**. Mientras el bot CONVERSA sobre cuándo entregar —que es cuando el
    cliente decide si compra— no había nada: el modelo sumaba días de cabeza. Por eso la
    conversación se contradecía sola, con el invento chocando después contra la verdad del código.

    Es exactamente la doctrina del dinero, aplicada al calendario: *las cifras se copian, no se
    piensan*. Una fecha es una cifra. El modelo pregunta; el código responde.

    `productos` es opcional y solo afina la anticipación (el pedido va a la velocidad de su
    producto más lento). Sin él, responde el calendario del negocio, que ya sirve para el 90%
    de los turnos.
    """
    hoy = hoy_venezuela()
    dias_ok = await _dias_de_entrega(session)
    items = [{"producto": n} for n in (productos or []) if n]
    anticipacion = await _anticipacion_del_pedido(session, items) if items else 0
    paso_corte = await _paso_la_hora_de_corte(session)
    corte = await _config_hora(session, "hora_corte", "18:00")

    # Desde cuándo se puede empezar a contar: si ya pasó la hora de corte, HOY está descartado.
    desde = hoy + timedelta(days=1) if paso_corte else hoy
    primera = await _primera_fecha_valida(session, desde, dias_ok, anticipacion)

    feriados = dict((await session.execute(select(Feriado.fecha, Feriado.motivo))).all())
    # Las siguientes fechas buenas, para que el bot pueda ofrecer alternativas sin calcular nada.
    proximas, cursor = [], primera
    for _ in range(45):
        if len(proximas) >= 4:
            break
        if _DIAS_SEMANA[cursor.weekday()] in dias_ok and cursor not in feriados:
            proximas.append({"fecha": cursor.isoformat(), "cuando": _fecha_larga(cursor)})
        cursor += timedelta(days=1)

    hoy_sirve = (
        not paso_corte
        and anticipacion == 0
        and _DIAS_SEMANA[hoy.weekday()] in dias_ok
        and hoy not in feriados
    )
    return {
        "ok": True,
        "hoy_es": _fecha_larga(hoy),
        "hoy_se_puede_entregar": hoy_sirve,
        "hora_de_corte": corte,
        "ya_paso_la_hora_de_corte": paso_corte,
        "dias_anticipacion_del_pedido": anticipacion,
        "primera_fecha": {"fecha": primera.isoformat(), "cuando": _fecha_larga(primera)},
        "proximas_fechas": proximas,
        "dias_que_se_entrega": [d for d in _DIAS_BONITO if _sin_acentos(d) in dias_ok],
        "nota": (
            "Estas son LAS ÚNICAS fechas que puedes ofrecer. No sumes ni restes días por tu "
            "cuenta, no supongas que mañana se entrega, y no inventes una hora: si el cliente "
            "pide un día que no está en esta lista, dile con cariño cuál es el más cercano que "
            "sí puedes y ofréceselo. La HORA exacta no la cierras tú: la coordina Whuilianny. "
            # 🔴 GUARDIA DE HILO (31-ago): re-consultar el calendario (por una duda o un
            # producto nuevo) traía 4 fechas frescas sin memoria de la ya acordada — y el
            # modelo podía repreguntar "¿para cuándo?" con la fecha firme en la conversación.
            "Y si YA habían acordado una fecha (mírala en el chat y en ESTADO DEL CLIENTE) y "
            "sigue apareciendo aquí, dala por FIJA y NO la repreguntes; solo si dejó de servir "
            "ofrécele la primera_fecha."
        ),
    }


async def _lista_de_zonas(session) -> list[dict]:
    """La lista CERRADA de zonas: el bot elige un `zona_id` de aquí. No puede escribir otro."""
    from app.models import ZonaEntrega

    filas = (
        await session.execute(
            select(ZonaEntrega)
            .where(ZonaEntrega.disponible.is_(True))
            .order_by(ZonaEntrega.orden, ZonaEntrega.id)
        )
    ).scalars().all()
    return [
        {
            "zona_id": z.id,
            "zona": z.nombre,
            "costo": float(z.costo),
            "es_retiro": z.es_retiro,
            "referencias": z.referencias,
        }
        for z in filas
    ]


def _firma_items(items: list[dict[str, object]]) -> list[tuple[int, int, str]]:
    firma: list[tuple[int, int, str]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        firma.append(
            (
                int(item.get("variante_id") or 0),
                int(item.get("cantidad") or 0),
                str(item.get("opciones") or "").strip(),
            )
        )
    return firma


def _mismo_pedido_esperando(
    pedido: Pedido,
    items: list[dict[str, object]],
    fecha_entrega: date | None,
    zona_id: int | None,
    notas: str | None,
) -> bool:
    if pedido.estado != "esperando_pago" or _firma_items(pedido.items or []) != _firma_items(items):
        return False
    if fecha_entrega is not None and pedido.entrega_fecha != fecha_entrega:
        return False
    if zona_id is not None and pedido.zona_id != zona_id:
        return False
    nota = str(notas or "").strip()
    return not nota or nota == str(pedido.notas or "").strip()


def _resumen_del_pedido(pedido: Pedido) -> str:
    lineas: list[str] = []
    for item in pedido.items or []:
        precio = item["precio_unitario"]
        subtotal = Decimal(str(precio)) * item["cantidad"] if precio is not None else None
        linea = f"{item['producto']} x{item['cantidad']}"
        if item.get("presentacion"):
            linea += f" (paquete de {item['presentacion']})"
        if item.get("opciones"):
            linea += f" — {item['opciones']}"
        lineas.append(f"{linea} = {_fmt_usd(subtotal)}")
    if pedido.zona_nombre:
        if pedido.costo_envio and Decimal(str(pedido.costo_envio)) > 0:
            lineas.append(f"Envío a {pedido.zona_nombre} = {_fmt_usd(pedido.costo_envio)}")
        else:
            lineas.append(f"{pedido.zona_nombre} — sin costo")
    resumen = "\n".join(lineas) + f"\nTotal: {_fmt_usd(pedido.total)}"
    entrega = []
    if pedido.entrega_fecha:
        entrega.append(_fecha_larga(pedido.entrega_fecha))
    if pedido.entrega:
        entrega.append(pedido.entrega)
    return resumen + ("\nEntrega: " + ", ".join(entrega) if entrega else "")


def _respuesta_registro(
    pedido: Pedido,
    nuevo: bool,
    sin_cambios: bool = False,
) -> dict[str, object]:
    estado = "SIN CAMBIOS: ya esperaba pago" if sin_cambios else ("NUEVO" if nuevo else "ACTUALIZADO")
    return {
        "ok": True,
        "pedido_id": pedido.id,
        "items": pedido.items,
        "total_usd": float(pedido.total) if pedido.total is not None else None,
        "resumen": _resumen_del_pedido(pedido),
        "nota": (
            f"pedido #{pedido.id} {estado}. "
            "Dile al cliente EXACTAMENTE este `resumen` (cópialo, NO recalcules el total). "
            "Para cobrar, llama a generar_datos_pago con este mismo `pedido_id`."
        ),
    }


async def registrar_pedido(
    session, telefono, items, notas=None, entrega=None, entrega_fecha=None, zona_id=None
):
    """Registra el pedido. El TOTAL lo suma el CÓDIGO: productos + envío.

    🔴 EL ENVÍO ES DINERO, así que va por el mismo "código de barras" que los productos: el bot
    manda un `zona_id` de una lista CERRADA y **el costo lo pone el código**. Nunca lo escribe él.
    (El 2026-07-13 el bot sumó $20 + $3 de cabeza y le dijo a una clienta REAL que el total en
    bolívares eran "$23 USD". El prompt se lo prohibía dos veces. Lo que vive en el texto se rompe.)
    """
    from app.models import ZonaEntrega

    cliente = (
        await session.execute(select(Cliente).where(Cliente.telefono == telefono))
    ).scalar_one_or_none()
    if cliente is None:
        session.add(Cliente(telefono=telefono))

    # ── LA ZONA (si la mandó): de la lista CERRADA, y su costo lo pone el CÓDIGO ──
    zona = None
    if zona_id is not None:
        try:
            zona = await session.get(ZonaEntrega, int(zona_id))
        except (TypeError, ValueError):
            zona = None
        if zona is None or not zona.disponible:
            return {
                "ok": False,
                "nota": (
                    f"La zona {zona_id!r} no existe o no está disponible. NO la inventes ni "
                    "deduzcas el costo: elige un `zona_id` EXACTO de la lista y vuelve a registrar "
                    "el pedido COMPLETO. Si el sitio del cliente no calza con ninguna zona, "
                    "pregúntale en cuál está; si sigue sin calzar, llama a `pedir_ayuda`."
                ),
                "zonas": await _lista_de_zonas(session),
            }

    items_pedido = []
    total = Decimal("0")
    for it in items:
        # ══ EL CÓDIGO DE BARRAS ══
        # El pedido ya NO se empareja por un nombre en texto libre: se pide por `variante_id`,
        # un número de una lista CERRADA que el propio código le inyectó al modelo en el
        # catálogo. El modelo NO PUEDE escribir un id que no le dimos, y el precio lo resuelve
        # el código a partir de ese id. Antes bastaba con que el buscador devolviera el
        # producto equivocado (dos "Kombucha") para cobrar $4 en vez de $7.
        try:
            variante_id = int(it.get("variante_id"))
        except (TypeError, ValueError):
            variante_id = 0
        variante = await session.get(ProductoVariante, variante_id) if variante_id else None
        prod = await session.get(Producto, variante.producto_id) if variante else None

        if variante is None or prod is None:
            return {
                "ok": False,
                "nota": (
                    f"El id {it.get('variante_id')!r} no existe. NO lo inventes: usa el "
                    "`id_para_pedir` EXACTO que ves en el catálogo y vuelve a registrar el "
                    "pedido COMPLETO. Las opciones_validas son SOLO para corregir ese id — "
                    "NO son un catálogo para reofrecerle cosas al cliente."
                ),
                "opciones_validas": await _lista_para_pedir(session),
            }

        # AGOTADO: manda el del producto (apaga todos sus tamaños) y el del tamaño.
        if not prod.disponible or not variante.disponible:
            que = prod.nombre if not prod.disponible else f"{prod.nombre} ({variante.presentacion})"
            return {
                "ok": False,
                "nota": (
                    f"'{que}' está AGOTADO: no se puede vender. Díselo al cliente con cariño y "
                    "ofrécele otra cosa. NO lo registres."
                ),
                "opciones_validas": await _lista_para_pedir(session),
            }

        # PRECIO DEL DÍA: si ese TAMAÑO no tiene precio fijo y la dueña no lo ha dado HOY, NO
        # se cobra (antes se colaba como $0 y el pedido salía gratis). Jamás inventar ni
        # reutilizar el de ayer.
        precio_hoy = await _precio_efectivo(session, variante)
        if precio_hoy is None:
            cual = f"{prod.nombre} ({variante.presentacion})" if _tiene_varios(prod, variante) else prod.nombre
            return {
                "ok": False,
                "nota": (
                    f"El precio de '{cual}' CAMBIA y hoy la dueña todavía no lo ha dado. "
                    "NO lo inventes, NO uses uno viejo y NO lo registres. Llama a `pedir_ayuda` "
                    f"con motivo='precio_del_dia' y detalle='pregunta el precio de {cual}'."
                ),
                "necesita_ayuda": True,
            }
        # La CANTIDAD es el otro factor del dinero (precio × cantidad). El schema pide
        # entero >= 1, pero el modelo puede mandar 0, "2", -1 o basura y nada lo validaba:
        # con cantidad=0 el ítem entraba en $0 y el pedido podía cerrarse GRATIS. Aquí se
        # rechaza — no se "corrige" en silencio: el agente tiene que volver a preguntar.
        try:
            cantidad = int(it.get("cantidad", 1))
        except (TypeError, ValueError):
            cantidad = 0
        if cantidad < 1:
            return {
                "ok": False,
                "nota": (
                    f"la cantidad de '{prod.nombre}' no es válida ({it.get('cantidad')!r}). "
                    "Pregúntale al cliente CUÁNTOS quiere (mínimo 1) y vuelve a registrar el "
                    "pedido completo. NO registres cantidades en 0."
                ),
            }
        subtotal = precio_hoy * cantidad
        total += subtotal
        # `opciones` = lo que el cliente eligió DENTRO del paquete (relleno, masa, sabor,
        # mezcla). NO toca el precio, pero la dueña lo necesita para COCINAR: antes se perdía
        # (en el panel quedaba solo "Empanadas" y había que leerse el chat entero).
        opciones = str(it.get("opciones") or "").strip() or None
        items_pedido.append(
            {
                "producto": prod.nombre,
                # El "código de barras" queda GRABADO en el pedido: así el panel y el recibo
                # saben EXACTAMENTE qué tamaño se vendió (y no se despacha la de 250g habiendo
                # pagado la de 1kg).
                "variante_id": variante.id,
                "cantidad": cantidad,  # PAQUETES completos, nunca unidades sueltas
                "precio_unitario": float(precio_hoy),  # el de HOY (fijo o precio del día)
                "presentacion": variante.presentacion,
                "opciones": opciones,
            }
        )

    # UN pedido por venta. El agente vuelve a llamar a esta herramienta cada vez que el cliente
    # agrega o quita algo (así lo ordena el prompt: "vuelve a registrar el pedido COMPLETO"), y
    # antes se creaba un pedido NUEVO cada vez: en el ensayo, 12 conversaciones dejaron 18
    # pedidos y una sola venta de $136 aparecía TRES veces en el panel (la dueña veía $408).
    # Ahora se REUTILIZA el pedido abierto de ese cliente y se actualiza.
    #
    # Excepción (el dinero manda): si el pedido abierto YA tiene un pago reportado/confirmado,
    # NO se toca —ese dinero ya está en juego— y se abre un pedido nuevo.
    abierto = (
        await session.execute(
            select(Pedido)
            .where(
                Pedido.cliente_telefono == telefono,
                Pedido.estado.in_(("pendiente", "esperando_pago")),
            )
            .order_by(Pedido.created_at.desc())
        )
    ).scalars().first()
    if abierto is not None:
        tiene_pago = (
            await session.execute(
                select(Pago.id).where(
                    Pago.pedido_id == abierto.id,
                    Pago.estado.in_(("reportado", "confirmado", "parcial")),
                )
            )
        ).scalars().first()
        if tiene_pago is not None:
            abierto = None

    entrega_txt = str(entrega or "").strip() or None

    # ══ LA SUMA DEL ENVÍO LA HACE EL CÓDIGO ══
    # `total` hasta aquí = solo los productos. El envío se suma AQUÍ, con el costo que sale de la
    # BD (nunca del modelo). Se guarda `subtotal_productos` porque el 20% de descuento en divisas
    # se aplica SOLO a los productos: el flete se cobra completo, o la dueña estaría pagando el
    # delivery de su bolsillo en cada venta pagada en dólares.
    #
    # 🔴 SI EL MODELO NO REENVÍA LA ZONA, SE CONSERVA LA QUE EL PEDIDO YA TENÍA — igual que se
    # conservan `notas`, `entrega` y la fecha unas líneas más abajo. El prompt le ordena "vuelve a
    # registrar el pedido COMPLETO" cada vez que el cliente agrega algo, y omitir `zona_id` es
    # facilísimo. Antes eso reescribía `total` SIN el flete mientras `zona_id` y `costo_envio`
    # seguían congelados en la fila, y el daño era triple: (1) el envío desaparecía del cobro,
    # (2) el candado de `generar_datos_pago` —que mira `zona_id`— no se enteraba de nada, y
    # (3) el recibo se autocontradecía, imprimiendo "Envío a X = $3" debajo de "Total: $20".
    # Encima `generar_datos_pago` resta el envío para el 20% de divisas: restaba un flete que
    # ya no estaba sumado, así que el descuento salía sobre una base falsa.
    subtotal_productos = total
    if zona is not None:
        costo_envio = Decimal(str(zona.costo))
    elif abierto is not None and abierto.zona_id is not None:
        costo_envio = Decimal(str(abierto.costo_envio or 0))
    else:
        costo_envio = Decimal("0")
    total = subtotal_productos + costo_envio

    # CANDADO DE LA ENTREGA (por FECHA REAL, no por palabras). El bot pasa la fecha que
    # acordó con el cliente y el CÓDIGO la valida contra el calendario del negocio. Si no
    # sirve, le devuelve el motivo y LA PRIMERA FECHA BUENA (calculada aquí, no por el modelo).
    fecha_entrega = None
    if entrega_fecha:
        try:
            fecha_entrega = (
                entrega_fecha
                if isinstance(entrega_fecha, date)
                else date.fromisoformat(str(entrega_fecha).strip()[:10])
            )
        except ValueError:
            return {
                "ok": False,
                "nota": (
                    f"la fecha '{entrega_fecha}' no es una fecha válida. Pásala como AAAA-MM-DD "
                    "(hoy en Venezuela te lo digo en este mismo mensaje)."
                ),
            }
        problema = await _validar_entrega(session, fecha_entrega, items_pedido)
        if problema is not None:
            return {
                "ok": False,
                "nota": (
                    f"NO se puede entregar esa fecha: {problema['motivo']}. NO se lo prometas "
                    f"al cliente. Díselo con cariño, con TUS palabras, y ofrécele la primera "
                    f"fecha en que SÍ se puede: {problema['primera_fecha_valida'].isoformat()}. "
                    f"Cuando el cliente acepte, vuelve a registrar el pedido COMPLETO con esa fecha."
                ),
                "primera_fecha_valida": problema["primera_fecha_valida"].isoformat(),
            }
    zona_solicitada_id = zona.id if zona is not None else None
    if abierto is not None and _mismo_pedido_esperando(
        abierto,
        items_pedido,
        fecha_entrega,
        zona_solicitada_id,
        notas,
    ):
        # El cliente solo eligió cómo pagar y el modelo intentó registrar TODO otra vez.
        # No reabrimos ni recalculamos: conserva el recibo y el precio que ya vio.
        return _respuesta_registro(abierto, nuevo=False, sin_cambios=True)
    if abierto is not None:
        pedido = abierto
        pedido.items = items_pedido
        pedido.total = total
        if notas:
            pedido.notas = notas
        if entrega_txt:
            pedido.entrega = entrega_txt
        if fecha_entrega:
            pedido.entrega_fecha = fecha_entrega
        if zona is not None:
            # CONGELADOS en el pedido: si mañana sube el envío, este pedido no cambia de precio.
            pedido.zona_id = zona.id
            pedido.zona_nombre = zona.nombre
            pedido.costo_envio = costo_envio
        pedido.estado = "pendiente"  # vuelve a estar en armado; el cobro se genera de nuevo
        nuevo = False
    else:
        pedido = Pedido(
            cliente_telefono=telefono, items=items_pedido, total=total,
            notas=notas, entrega=entrega_txt, entrega_fecha=fecha_entrega,
            zona_id=(zona.id if zona else None),
            zona_nombre=(zona.nombre if zona else None),
            costo_envio=costo_envio,
        )
        session.add(pedido)
        nuevo = True
    await session.commit()
    await session.refresh(pedido)
    return _respuesta_registro(pedido, nuevo=nuevo)


async def info_negocio(session, telefono):
    filas = (await session.execute(select(Configuracion))).scalars().all()
    config = {f.clave: f.valor for f in filas}
    return {
        "nombre": config.get("negocio_nombre", "masvidaconsciente"),
        "ubicacion": config.get("negocio_ubicacion", "Cabudare, Venezuela"),
        "pago": config.get("negocio_pago", "Pago Móvil"),
        "instagram": config.get("negocio_instagram", "@masvidaconsciente"),
        # 🔴 Era la ÚNICA herramienta sin nota (31-ago): una pregunta inocente ("¿dónde
        # están?") a mitad de cobro volcaba `pago` y tentaba a reofrecer el método ya elegido.
        # Los datos de cuentas siguen saliendo SOLO de generar_datos_pago, como siempre.
        "nota": (
            "Si el cliente ya está pagando o ya eligió cómo pagar, NO le reofrezcas formas de "
            "pago con esto: responde su duda y sigue. Los datos de las cuentas salen SOLO de "
            "generar_datos_pago."
        ),
    }


def _coseno(a, b) -> float:
    """Similitud coseno entre dos vectores (1 = igual significado, 0 = nada que ver)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    punto = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return punto / (na * nb)


async def _buscar_info_lexical(session, q: str):
    """Búsqueda por PALABRAS (tolerante a typos/acentos, pg_trgm). Filas (id, titulo,
    contenido). Si pg_trgm no está, cae a un LIKE simple. Nunca rompe."""
    # 🔴 EL PARÉNTESIS NO ES ESTILO, ES EL FILTRO. `activo IS TRUE` va DELANTE y las tres ramas
    # del OR van ENTRE PARÉNTESIS. Pegado al final (`… OR C >= :umbral AND activo IS TRUE`) la
    # precedencia AND > OR lo leería como `A OR B OR (C AND activo)`: las dos primeras ramas
    # seguirían devolviendo lo que la dueña RETIRÓ, sin error y sin log. Comprobado contra la base
    # del taller (030). `IS TRUE` y no `= TRUE`: inmune a NULL si alguien afloja el NOT NULL.
    sql = text(
        """
        SELECT id, titulo, contenido,
               GREATEST(
                 similarity(unaccent(lower(coalesce(titulo, ''))), unaccent(lower(:q))),
                 word_similarity(
                     unaccent(lower(:q)),
                     unaccent(lower(coalesce(titulo, '') || ' ' || coalesce(contenido, '')))
                 )
               ) AS sim
        FROM conocimiento
        WHERE activo IS TRUE
          AND (
                  unaccent(lower(coalesce(titulo, '') || ' ' || coalesce(contenido, '')))
                      LIKE '%' || unaccent(lower(:q)) || '%'
               OR word_similarity(
                      unaccent(lower(:q)),
                      unaccent(lower(coalesce(titulo, '') || ' ' || coalesce(contenido, '')))
                  ) >= :umbral
               OR similarity(unaccent(lower(coalesce(titulo, ''))), unaccent(lower(:q))) >= :umbral
              )
        ORDER BY sim DESC
        LIMIT 4
        """
    )
    try:
        return (await session.execute(sql, {"q": q, "umbral": 0.25})).all()
    except Exception:  # noqa: BLE001 — sin pg_trgm: respaldo por substring simple
        return (
            await session.execute(
                # El filtro va en un `.where()` APARTE del `|`: dos `.where()` encadenados son un
                # AND, así que el OR de adentro queda solo. Metido dentro del mismo `.where` junto
                # al `|` vuelve el bug del paréntesis con otra cara.
                select(Conocimiento.id, Conocimiento.titulo, Conocimiento.contenido)
                .where(Conocimiento.activo.is_(True))
                .where(Conocimiento.titulo.ilike(f"%{q}%") | Conocimiento.contenido.ilike(f"%{q}%"))
                .limit(4)
            )
        ).all()


async def _buscar_info_semantico(session, q: str):
    """Búsqueda por SIGNIFICADO (embeddings): así 'apto celíacos' encuentra 'sin gluten'
    aunque no compartan palabras. Compara el embedding de la consulta con los guardados
    (coseno). Filas (id, titulo, contenido). [] si no hay embeddings o falla (fail-safe)."""
    from app.services.embeddings import obtener_embedding

    vec = await obtener_embedding(q)
    if not vec:
        return []
    try:
        filas = (
            await session.execute(
                select(
                    Conocimiento.id,
                    Conocimiento.titulo,
                    Conocimiento.contenido,
                    Conocimiento.embedding,
                )
                # Retirada = fuera también por SIGNIFICADO. Sin esto una fila apagada seguiría
                # ganando por coseno aunque el camino léxico ya no la devuelva.
                .where(Conocimiento.activo.is_(True), Conocimiento.embedding.isnot(None))
                .limit(500)
            )
        ).all()
    except Exception:  # noqa: BLE001
        return []
    puntuadas = [(_coseno(vec, f.embedding), f) for f in filas]
    # 0.30 descarta lo claramente no relacionado (umbral típico para este modelo).
    relevantes = [(sim, f) for sim, f in puntuadas if sim >= 0.30]
    relevantes.sort(key=lambda p: p[0], reverse=True)
    return [f for _, f in relevantes[:4]]


async def buscar_info(session, telefono, consulta):
    """Busca en la base de Conocimiento (lo que la dueña carga en el panel) las entradas
    MÁS relacionadas con la duda del cliente. HÍBRIDO: por SIGNIFICADO (embeddings) y por
    PALABRAS (pg_trgm). Devuelve SOLO lo relevante (no toda la base) → escala a cientos de
    entradas. Si los embeddings no están disponibles, usa solo lo léxico (nunca se rompe)."""
    q = (consulta or "").strip()
    if len(q) < 2:
        return {"resultados": [], "nota": "consulta vacía; pídele al cliente que aclare su duda"}
    semanticos = await _buscar_info_semantico(session, q)
    lexicales = await _buscar_info_lexical(session, q)
    vistos: set = set()
    resultados = []
    for fila in list(semanticos) + list(lexicales):
        if fila.id in vistos:
            continue
        vistos.add(fila.id)
        resultados.append({"tema": fila.titulo, "info": fila.contenido})
        if len(resultados) >= 4:
            break
    if not resultados:
        return {
            "resultados": [],
            "nota": (
                f"no hay información cargada sobre '{q}'. Dilo con sinceridad y, si aplica, "
                "ofrece consultarlo con la dueña; NO te lo inventes"
            ),
        }
    return {
        "resultados": resultados,
        "nota": (
            "Usa esto SOLO si de verdad responde lo que preguntó el cliente. Si es un tema "
            "PARECIDO pero DISTINTO (ej. te preguntan por envío NACIONAL / a otra ciudad y esto "
            "es la entrega LOCAL), NO lo des como la respuesta: dile que eso puntual se lo "
            "confirmas. No confundas un tema con otro. "
            # 🔴 GUARDIA DE HILO (31-ago): estas entradas son texto libre de la dueña y pueden
            # traer listas de opciones (masas, sabores, métodos) — consultadas a mitad de venta
            # reabrían lo ya elegido, igual que la ficha reabrió las dos masas.
            "Y SIGUE EL HILO: si el cliente ya eligió una versión/opción, responde su duda SIN "
            "reofrecerle las otras opciones que este texto mencione."
        ),
    }


async def ver_pedidos_cliente(session, telefono):
    pedidos = (
        await session.execute(
            select(Pedido).where(Pedido.cliente_telefono == telefono).order_by(Pedido.created_at.desc()).limit(5)
        )
    ).scalars().all()
    return {
        "pedidos": [
            {"id": p.id, "estado": p.estado, "items": p.items, "total_usd": float(p.total) if p.total else None}
            for p in pedidos
        ]
    }


async def recordar_cliente(session, telefono, nombre=None, nota=None):
    """Guarda en la ficha del cliente su NOMBRE y/o un dato clave (salud/preferencias)
    para reconocerlo y recordarlo la próxima vez. Solo guarda lo que el cliente dijo."""
    cliente = (
        await session.execute(select(Cliente).where(Cliente.telefono == telefono))
    ).scalar_one_or_none()
    if cliente is None:
        cliente = Cliente(telefono=telefono)
        session.add(cliente)
    guardado = []
    if nombre and nombre.strip():
        cliente.nombre = nombre.strip()[:80]
        guardado.append(f"nombre={cliente.nombre}")
    if nota and nota.strip():
        n = nota.strip()[:200]
        actuales = (cliente.notas or "").strip()
        if n.lower() not in actuales.lower():  # no duplicar
            cliente.notas = f"{actuales}\n{n}".strip() if actuales else n
            guardado.append("nota")
    await session.commit()
    return {"ok": True, "guardado": guardado or "nada nuevo"}


# ─── Cobro: datos de Pago Movil y registro de comprobante ────────────

async def get_pedido_esperando_pago(session, telefono):
    """El ultimo pedido de este cliente que esta esperando pago.

    Clave del diseno: el comprobante se amarra al pedido por TELEFONO + ESTADO
    en la base de datos, NO por la memoria del LLM (que no persiste entre turnos).
    """
    return (
        await session.execute(
            select(Pedido)
            .where(Pedido.cliente_telefono == telefono, Pedido.estado == "esperando_pago")
            .order_by(Pedido.created_at.desc())
        )
    ).scalars().first()


async def generar_datos_pago(session, telefono, pedido_id=None):
    """Calcula el monto en Bs (tasa del dia), deja el pedido en 'esperando_pago'
    y devuelve los datos de Pago Movil para que el bot los presente."""
    # Un pedido CERRADO no se vuelve a cobrar. Antes, si el cliente pedía "los datos otra
    # vez" y el modelo omitía el pedido_id, el código agarraba el ÚLTIMO pedido de CUALQUIER
    # estado —incluso uno ya PAGADO— y lo devolvía a 'esperando_pago' (línea de abajo), con
    # lo que el siguiente comprobante se le pegaba encima con el monto viejo y se creaba un
    # SEGUNDO pago sobre una venta ya cerrada.
    _CERRADOS = ("pagado", "entregado", "cancelado")
    _COBRABLES = ("pendiente", "esperando_pago", "confirmado", "preparando")

    if pedido_id is not None:
        pedido = await session.get(Pedido, int(pedido_id))
        if pedido is None or pedido.cliente_telefono != telefono:
            return {"ok": False, "nota": "no encontre ese pedido para este cliente"}
        if pedido.estado in _CERRADOS:
            return {
                "ok": False,
                "nota": (
                    f"ese pedido ya esta '{pedido.estado}': NO se cobra de nuevo. Si el cliente "
                    "quiere comprar otra vez, registra un pedido NUEVO."
                ),
            }
    else:
        pedido = (
            await session.execute(
                select(Pedido)
                .where(
                    Pedido.cliente_telefono == telefono,
                    Pedido.estado.in_(_COBRABLES),  # nunca uno pagado/entregado/cancelado
                )
                .order_by(Pedido.created_at.desc())
            )
        ).scalars().first()
        if pedido is None:
            return {"ok": False, "nota": "este cliente no tiene ningun pedido abierto para cobrar"}

    # Un pedido que YA tiene un pago confirmado no se re-cobra (aunque su estado diga otra cosa).
    pago_ok = (
        await session.execute(
            select(Pago.id).where(Pago.pedido_id == pedido.id, Pago.estado == "confirmado")
        )
    ).scalars().first()
    if pago_ok is not None:
        return {
            "ok": False,
            "nota": (
                "ese pedido ya tiene un pago confirmado: NO lo cobres de nuevo. Si quiere "
                "comprar mas, registra un pedido NUEVO."
            ),
        }

    if pedido.total is None:
        return {"ok": False, "nota": "el pedido no tiene un total definido para cobrar"}

    # NO SE COBRA UN PEDIDO QUE NO SE SABE SI SE PUEDE ENTREGAR. En el ensayo del 2026-07-12 el
    # bot le pasó los datos del banco a una clienta de CARACAS después de ignorar tres veces su
    # pregunta de si hacían envíos nacionales. Sin fecha de entrega acordada, no hay cobro.
    if pedido.entrega_fecha is None:
        return {
            "ok": False,
            "nota": (
                "todavía NO le puedes cobrar: falta acordar PARA CUÁNDO es la entrega. "
                "Pregúntale al cliente qué día la quiere (y si es retiro o delivery), registra "
                "el pedido con esa fecha (`entrega_fecha`) y recién entonces cobra."
            ),
        }

    # 🔴 CANDADO DEL ENVÍO (el bug de la clienta, 2026-07-13): NO SE COBRA SIN SABER SI ES RETIRO
    # O DELIVERY — y a qué zona. Si no, el total sale SIN el envío (la dueña regala el flete) o el
    # bot lo suma de cabeza (que es exactamente lo que hizo: "$20 + $3 = $23"). El candado va aquí,
    # en la CAJA, y no solo en el registro: así ningún pedido viejo ni ningún camino raro se cuela.
    if pedido.zona_id is None:
        zonas = await _lista_de_zonas(session)
        return {
            "ok": False,
            "nota": (
                "todavía NO le puedes cobrar: falta saber CÓMO lo recibe. Pregúntale si lo retira "
                "o si quiere delivery, y en ese caso EN QUÉ ZONA está (léele las zonas con su "
                "costo). Después vuelve a registrar el pedido COMPLETO pasando el `zona_id` que "
                "corresponda. NUNCA sumes tú el envío ni lo estimes: el costo lo pone el sistema. "
                "Si el sitio del cliente no calza con ninguna zona, llama a `pedir_ayuda`."
            ),
            "zonas": zonas,
        }

    try:
        tasa = await obtener_tasa_bcv()
    except Exception:  # noqa: BLE001
        return {"ok": False, "nota": "ahora mismo no puedo calcular el monto en bolivares"}

    monto_usd = Decimal(str(pedido.total))
    monto_bs = (monto_usd * tasa).quantize(Decimal("0.01"))
    # 20% de descuento sobre los PRODUCTOS + DELIVERY GRATIS, por pagar en DÓLARES — efectivo,
    # Zelle o Binance. En Bs (Pago Móvil/transferencia) NO aplica: va el precio completo.
    #
    # 🔴 CAMBIO DE REGLA — decisión de Maired del 2026-08-24, que REVIERTE parcialmente la del
    # 22-ago. La plantilla del 22-ago decía "dólares físicos" y le negaba el 20% a Zelle y
    # Binance (por su comisión); Maired lo corrigió en vivo el 24-ago: **el descuento se ata a
    # la MONEDA, no a la vía** — cualquier pago en dólares (efectivo, Zelle, Binance) lleva el
    # 20% y el delivery gratis; los bolívares pagan completo. La historia completa y el mapa del
    # cambio: SESIONES.md entrada 2026-08-24 (3).
    #
    # Lo que NO cambió (sigue siendo la plantilla del 22-ago, a propósito):
    #   · La casa asume el flete como PALANCA para cobrar en dólares (sin reverso, sin tasa de
    #     por medio). El costo queda escrito para que nadie lo descubra por sorpresa: **$3 en la
    #     zona centro, $5 en la oeste.**
    #   · El descuento es sobre los PRODUCTOS, no sobre el total: el flete no se descuenta, se
    #     REGALA (ver monto_en_efectivo).
    #
    # ⚠️ El nombre `monto_usd_divisas` (y su columna `cotizado_usd_divisas`) se CONSERVA a
    # propósito: renombrarlo obligaría a una migración y a tocar `_montos_cobrados`, que compara
    # el comprobante contra esa cotización. Cambia lo que vale, no cómo se llama.
    envio = Decimal(str(pedido.costo_envio or 0))
    monto_usd_divisas = monto_en_efectivo(monto_usd, envio)

    pedido.estado = "esperando_pago"
    await session.commit()

    config = {
        f.clave: f.valor
        for f in (await session.execute(select(Configuracion))).scalars().all()
    }

    # 🔴 LOS DATOS DE PAGO SALEN DE AQUÍ (la tabla `metodos_pago`, la que edita el panel y la
    # MISMA contra la que la visión valida el beneficiario del comprobante) — NO del texto de
    # la personalidad. Antes vivían escritos en ese texto y el modelo los pegaba SIN que
    # hubiera pedido (le pasó a una clienta real el 2026-07-13); y peor: eran una SEGUNDA
    # copia de la verdad (si la dueña cambiaba la cuenta en el panel, el bot dictaba la
    # vieja). Una sola fuente. La red de datos bancarios (agent.py) frena cualquier dato
    # que no haya salido de una herramienta en ese turno.
    metodos = (
        await session.execute(
            select(MetodoPago)
            .where(MetodoPago.activo.is_(True))
            .order_by(MetodoPago.orden, MetodoPago.id)
        )
    ).scalars().all()
    metodos_datos = []
    for m in metodos:
        d = {"metodo": m.titulo or m.tipo}
        for campo in ("titular", "banco", "telefono", "cedula", "cuenta", "correo", "wallet", "instrucciones"):
            v = getattr(m, campo, None)
            if v:
                d[campo] = v
        metodos_datos.append(d)

    # Compatibilidad: las llaves sueltas de Pago Móvil que ya usaba el bot. Se toman del
    # MISMO método de la tabla (una sola verdad); las claves de configuracion quedan solo
    # como respaldo si la tabla estuviera vacía.
    pm = next(
        (m for m in metodos if "movil" in _sin_acentos(f"{m.tipo} {m.titulo}")), None
    )

    # Guarda la cotizacion para amarrarla al comprobante cuando llegue.
    #
    # 🔴 EN LOS DOS SITIOS, Y A PROPÓSITO (migración 027). Redis es la vía rápida, pero su TTL es
    # de 24h y aquí cotizar un día y pagar al siguiente es lo NORMAL: los pedidos van con días de
    # anticipación. Cuando la clave expiraba, el comprobante se recalculaba con la tasa de HOY y
    # "Monto distinto" le reclamaba al cliente una diferencia que no debía. Un dato del que depende
    # el dinero no puede vivir solo en una caché con caducidad.
    pedido.cotizado_bs = monto_bs
    pedido.cotizado_usd = monto_usd
    pedido.cotizado_usd_divisas = monto_usd_divisas
    pedido.tasa_cotizada = tasa
    pedido.cotizado_at = now_utc()
    await session.commit()
    try:
        await set_cache(
            f"cobro:{telefono}",
            json.dumps({
                "pedido_id": pedido.id,
                "monto_usd": str(monto_usd),
                "tasa": str(tasa),
                "monto_bs": str(monto_bs),
                "monto_usd_divisas": str(monto_usd_divisas),
            }),
            86400,
        )
    except Exception:  # noqa: BLE001
        pass

    # Cobro YA ARMADO para copiar tal cual (USD y Bs los calculó el código, no el modelo).
    #
    # OJO — el "ya con el 20% de descuento" iba al FINAL de la frase y se leía como si aplicara
    # también a los bolívares. En el ensayo del 2026-07-12 le pasó a 7 de 12 clientes: el bot
    # prometía un descuento en Pago Móvil que NO existe (los Bs son el precio COMPLETO), y una
    # clienta lo reclamó. El descuento del 20% es SOLO en divisas. Ahora la frase lo separa.
    #
    # 🔴 Y DESDE EL 2026-08-24 vuelve a nombrar las TRES vías (efectivo, Zelle o Binance): el
    # 20% se ata a la MONEDA, no a la vía (ver el bloque del cálculo, arriba). Nombrar solo
    # "efectivo" aquí sería negarle el descuento a quien sí lo tiene — el espejo exacto del
    # error del 2026-07-12 con los bolívares.
    # El "delivery por nuestra cuenta" solo se nombra si de verdad hay flete que perdonar: en un
    # retiro no hay envío, y presumir de regalar algo que no existe suena a vendedor de feria.
    _flete_gratis = " y el delivery corre por nuestra cuenta" if envio > 0 else ""
    resumen_cobro = (
        f"Por Pago Móvil o transferencia son {_fmt_bs(monto_bs)} Bs (precio completo). "
        f"Si pagas en dólares (efectivo, Zelle o Binance) son {_fmt_usd(monto_usd_divisas)}, "
        f"con el 20% de descuento{_flete_gratis}"
    )
    # 🧾 EL DESGLOSE DEL EFECTIVO, línea por línea. Lo pide el documento con esas palabras:
    # «El sistema debe mostrar subtotal, descuento, delivery en USD 0 y total final».
    #
    # 🔴 Y RESUELVE UNA CONFUSIÓN REAL, no es adorno. Maired reportó dos veces que el bot
    # "saca mal la cuenta" porque veía $17 arriba y $11.20 abajo, sin el paso intermedio: la
    # resta no se veía por ningún lado. Con el desglose delante, el 20% se sigue con el dedo.
    # Cuando el dinero sorprende, el problema no suele ser la cifra: es que no se ve de dónde sale.
    _productos = monto_usd - envio
    _descuento = (_productos * Decimal("0.20")).quantize(Decimal("0.01"))
    desglose_efectivo = [
        f"Productos: {_fmt_usd(_productos)}",
        f"Descuento 20%: -{_fmt_usd(_descuento)}",
    ]
    if envio > 0:
        desglose_efectivo.append(f"Delivery: $0 (normalmente {_fmt_usd(envio)}, va por nuestra cuenta)")
    desglose_efectivo.append(f"Total en dólares: {_fmt_usd(monto_usd_divisas)}")

    return {
        "ok": True,
        "pedido_id": pedido.id,
        "monto_usd": float(monto_usd),
        "monto_usd_divisas": float(monto_usd_divisas),
        "tasa_bcv": float(tasa),
        "monto_bs": float(monto_bs),
        "resumen_cobro": resumen_cobro,
        # El desglose va aparte del resumen: el modelo lo copia TAL CUAL cuando el cliente
        # pregunta por la cuenta o elige pagar en dólares (efectivo, Zelle o Binance).
        # Nunca lo recalcula.
        "desglose_efectivo": desglose_efectivo,
        "banco": (pm.banco if pm else None) or config.get("pago_movil_banco"),
        "cedula": (pm.cedula if pm else None) or config.get("pago_movil_cedula"),
        "telefono_pago": (pm.telefono if pm else None) or config.get("pago_movil_telefono"),
        "titular": (pm.titular if pm else None) or config.get("pago_movil_titular"),
        "metodos_de_pago": metodos_datos,
        "nota": (
            "presenta el cobro copiando EXACTO `resumen_cobro` (NO recalcules). Los datos "
            "de las cuentas están en `metodos_de_pago`: dale al cliente SOLO los del método "
            "que ÉL elija, copiados TAL CUAL (si aún no eligió, pregúntale cómo prefiere "
            "pagar nombrándole los métodos, sin soltar todos los datos). "
            # 🔴 NOMBRA EL MÉTODO Y ETIQUETA CADA DATO (del análisis de las conversaciones
            # reales de Whuilianny, 2026-08-21). Ella los manda "secos" —"Datos. [cédula]
            # [teléfono] Banesco"— porque al otro lado hay una persona que ya sabe qué es cada
            # número. El bot le escribe a gente que NO lo sabe: tres números sin etiqueta y sin
            # decir que eso es un Pago Móvil es la forma más fácil de que el cliente pague mal
            # (o no pague). El dato sigue saliendo TAL CUAL de la herramienta: esto es solo
            # ponerle su nombre delante.
            "Al entregarlos, DI QUÉ MÉTODO ES por su nombre (el campo `metodo`: 'Pago Móvil', "
            "'Zelle'…) y pon cada dato con su etiqueta en su propia línea (cédula, teléfono, "
            "banco…), no tres números pegados. Pide la captura del comprobante."
        ),
    }


_MOTIVO_TITULO = {
    "precio_del_dia": "💰 Te piden un PRECIO del día",
    "no_se": "❓ El bot no sabe algo",
    "pide_persona": "🙋 El cliente pide hablar con una persona",
    "reclamo": "⚠️ El cliente está RECLAMANDO",
}


# Motivos por los que el bot SÍ se calla y espera a la dueña: el cliente pide una persona o
# reclama. Los otros (`precio_del_dia`, `no_se`) dejan aviso pero NO callan al bot: sigue vendiendo.
_MOTIVOS_DE_PAUSA = {"pide_persona", "reclamo"}


async def pedir_ayuda(session, telefono, motivo: str, detalle: str = ""):
    """RELEVO A LA HUMANA. El bot se topó con algo que NO le toca resolver (un precio que
    cambia, algo que no sabe, un cliente que pide una persona, un reclamo). En vez de
    inventar: PAUSA este chat, deja el aviso en la bandeja del panel, y le manda un
    WhatsApp a la dueña. Ella entra al chat del negocio y responde.

    Nunca falla el turno: si el aviso por WhatsApp no sale (número sin configurar, ventana
    de 24h de Meta), el chat igual queda pausado y el aviso queda EN EL PANEL."""
    motivo = (motivo or "no_se").strip()
    if motivo not in _MOTIVO_TITULO:
        motivo = "no_se"
    detalle = (detalle or "").strip()

    # 1) El bot se calla en ESTE chat (la dueña toma el control).
    cliente = (
        await session.execute(select(Cliente).where(Cliente.telefono == telefono))
    ).scalar_one_or_none()
    if cliente is None:
        cliente = Cliente(telefono=telefono)
        session.add(cliente)
    # 🔴 SOLO PAUSA (calla al bot) cuando el cliente necesita de VERDAD a una persona: pide hablar
    # con alguien (`pide_persona`) o reclama (`reclamo`). Para un PRECIO DEL DÍA que el bot no sabe,
    # o un dato puntual (`no_se`), deja el aviso en la bandeja pero el bot SIGUE VENDIENDO: muestra
    # la foto, ofrece otros productos, toma el pedido. Quedarse MUDO por no saber UN precio mata la
    # venta (caso real: pidieron "y la torta qué tal", el bot escaló el precio, se pausó, y ya no
    # contestó ni "tienes foto"). La dueña carga el precio del día en el panel y el bot lo usa en
    # el siguiente mensaje. Cuando SÍ pausa, lo pausa 'bot' (no 'dueña'): su mensaje de despedida
    # igual sale (ver migración 020).
    if motivo in _MOTIVOS_DE_PAUSA:
        cliente.bot_pausado = True
        cliente.pausado_por = "bot"

    # 2) Lo último que dijo el cliente (para que la dueña entienda sin abrir nada).
    ultimo = (
        await session.execute(
            select(Mensaje.contenido)
            .where(Mensaje.cliente_telefono == telefono, Mensaje.rol == "user")
            .order_by(Mensaje.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    # 3) UN SOLO AVISO VIVO POR CHAT — pero que NO SE TRAGUE EL PROBLEMA NUEVO.
    #
    # 🔴 Esta regla nació para no inundar la bandeja y hasta hoy DESCARTABA TODO: con un aviso
    # 'pendiente' delante, la segunda escalada de ese cliente no dejaba fila, no mandaba WhatsApp
    # y —lo peor— TIRABA el `detalle` del problema NUEVO. La dueña seguía viendo el aviso viejo
    # ("te piden un precio") mientras el cliente ya estaba RECLAMANDO.
    #
    # Y ese aviso se quedaba 'pendiente' PARA SIEMPRE en cuanto ella hacía justo lo que este
    # mismo aviso le pide más abajo ("Entra al WhatsApp del negocio y respóndele tú"): el eco de
    # su celular pausaba el chat y guardaba la burbuja, pero NUNCA tocaba `intervenciones`
    # (webhook/router.py). Desde ese momento, CADA escalada futura de ese cliente se perdía
    # entera — el bot le decía "eso te lo confirmo enseguida" y NADIE se enteraba nunca.
    #
    # Ahora el aviso vivo se ENRIQUECE con el detalle nuevo, y si el motivo AGRAVA (el cliente
    # pasó de "no sé algo" a pedir una persona o a reclamar) sube de motivo y se vuelve a pingar.
    # (La otra mitad del arreglo vive en `_procesar_eco`: cerrar el viejo y dejar el `chat_tomado`.)
    ya_hay = (
        await session.execute(
            select(Intervencion)
            .where(
                Intervencion.cliente_telefono == telefono,
                Intervencion.estado == "pendiente",
            )
            .order_by(Intervencion.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    # AGRAVA = el problema nuevo SÍ calla al bot y el aviso vivo no lo hacía. Es justo lo que
    # ella tiene que saber YA, aunque ya tuviera un aviso abierto por otra cosa.
    agrava = motivo in _MOTIVOS_DE_PAUSA and (
        ya_hay is None or ya_hay.motivo not in _MOTIVOS_DE_PAUSA
    )

    if ya_hay is None:
        session.add(
            Intervencion(
                cliente_telefono=telefono,
                motivo=motivo,
                detalle=detalle or None,
                mensaje_cliente=ultimo,
            )
        )
    else:
        # ⚠️ `chat_tomado` NO CAMBIA DE MOTIVO NUNCA. Ese aviso no describe un problema: ES EL
        # BOTÓN que le devuelve el chat al bot (`resolver_intervencion` con reactivar=True), y
        # el barredor lo respeta por su motivo EXACTO (`i.motivo <> 'chat_tomado'`,
        # services/barredor.py). Pisárselo aquí lo dejaría sin identidad de botón Y elegible para
        # que `cerrar_avisos_ya_atendidos` lo cerrara a los 5 minutos: chat mudo para siempre,
        # que es el desastre exacto que ese aviso vino a evitar. El detalle sí se le añade y el
        # WhatsApp sale igual si agrava — que es lo que ella necesita saber.
        if agrava and ya_hay.motivo != "chat_tomado":
            ya_hay.motivo = motivo
        ya_hay.mensaje_cliente = ultimo or ya_hay.mensaje_cliente
        nuevo = (detalle or _MOTIVO_TITULO[motivo]).strip()
        if nuevo and nuevo not in (ya_hay.detalle or ""):
            ya_hay.detalle = (f"{ya_hay.detalle}\n· {nuevo}" if ya_hay.detalle else nuevo)[-1500:]
    await session.commit()

    # 4) El ping a la dueña. Si no sale, NO rompe nada: el aviso ya está en el panel.
    if ya_hay is None or agrava:
        await _avisar_intervencion(session, telefono, motivo, detalle, ultimo)

    return {
        "ok": True,
        "nota": (
            "Listo: la dueña ya fue avisada y este chat quedó en sus manos. Ahora dile al "
            "cliente, CON TUS PROPIAS PALABRAS (cálida, natural, distinta cada vez), que eso "
            "se lo confirmas enseguida. NO inventes el dato, NO des un precio, y NUNCA digas "
            "que 'le preguntas a la dueña' ni la menciones como si fuera otra persona: tú "
            "ERES Whuilianny. Después de este mensaje NO sigas respondiendo en este chat."
        ),
    }


# La COPIA DE SEGURIDAD del teléfono de la dueña, fuera de Postgres. Se reescribe cada vez que
# la base SÍ respondió, para que `avisar_relevo_caido` (abajo) tenga a quién escribirle el día
# que no responda. Prefijo `cache:` por la convención de `redis_client.py:102-104`.
#
# 🔴 Desde 2026-08-03 la clave la define `services/dueno.py`, que es el resolvedor ÚNICO: el alias
# se conserva porque este módulo (y `probar_relevo.py`) ya la nombraban así. Es LA MISMA clave, no
# una copia — que fuesen dos era justamente el bug del cerebro partido.
_CLAVE_DUENO = CLAVE_COPIA


async def _recordar_dueno(destino: str) -> None:
    """Deja el número de la dueña en Redis. Nunca lanza: es una copia de cortesía."""
    try:
        await set_cache(_CLAVE_DUENO, destino, 30 * 86400)
    except Exception:  # noqa: BLE001 — sin Redis se sigue avisando por la vía normal
        pass


async def _avisar_intervencion(session, telefono, motivo, detalle, mensaje_cliente) -> None:
    """Le manda a la dueña el 'el bot te necesita' por WhatsApp. Best-effort: si no hay
    número configurado o Meta rechaza (ventana de 24h), se loguea y ya — el aviso vive
    en la bandeja del panel, que nunca falla.

    🔴 EL `try` ABRAZA TODO, INCLUIDAS LAS DOS LECTURAS A LA BASE. Hasta hoy solo protegía el
    `enviar_texto`: si la base moría en el `select(Configuracion)` o en el `select(Cliente.nombre)`,
    la excepción subía, mataba a `pedir_ayuda` DESPUÉS del commit (la Intervencion YA escrita), y
    `ejecutar_tool` la volvía un `{"error": ...}` mudo. El reintento encontraba `ya_hay`, no
    recreaba nada, no reenviaba el aviso, devolvía ok — y la dueña se quedaba con un chat pausado
    del que nadie le avisó. 'Best-effort' tiene que serlo de verdad: este aviso NO puede tumbar el
    turno por ninguna puerta. De eso depende que el `{"ok": True}` de `pedir_ayuda` sea fiable, que
    es justo lo que el bucle del agente se cree para no volver a escalar.
    """
    try:
        config = {
            f.clave: f.valor
            for f in (await session.execute(select(Configuracion))).scalars().all()
        }
        destino = config.get("dueno_telefono") or get_settings().dueno_telefono
        if not destino:
            logger.warning(
                "pedir_ayuda: no hay dueno_telefono configurado; el aviso queda SOLO en el panel"
            )
            return
        await _recordar_dueno(destino)

        nombre = (
            await session.execute(select(Cliente.nombre).where(Cliente.telefono == telefono))
        ).scalar_one_or_none()
        quien = f"{nombre} ({telefono})" if nombre else telefono
        cuerpo = f"🔔 *EL BOT TE NECESITA*\n\n{_MOTIVO_TITULO[motivo]}\nCliente: {quien}"
        if detalle:
            cuerpo += f"\n\n👉 {detalle}"
        if mensaje_cliente:
            cuerpo += f'\n\nÉl escribió: "{mensaje_cliente[:180]}"'
        cuerpo += (
            "\n\nEl bot ya le dijo que le confirmas enseguida y *se quedó callado* en ese chat."
            "\nEntra al WhatsApp del negocio y respóndele tú."
            "\nCuando termines, reactiva el bot desde el panel."
        )
        await enviar_texto(destino, cuerpo)
    except Exception:  # noqa: BLE001 — un aviso que falla no puede tumbar el turno
        logger.exception("pedir_ayuda: no se pudo avisar por WhatsApp; queda en el panel")


# ── EL ÚLTIMO TESTIGO ───────────────────────────────────────────────────────────────────
#
# 🔴 `pedir_ayuda` escribe TODO en Postgres: la pausa del chat, la fila de la bandeja y (desde
# ahí) el WhatsApp a la dueña — que además necesita la base para saber A QUIÉN escribir. Si lo
# que falla ES Postgres, los tres se caen JUNTOS y no queda nadie que sepa que hay un cliente
# esperando. Eso es, literalmente, la semana de mensajes mudos.
#
# Esta es la única vía que NO toca la base: el número sale del ENTORNO, o de la copia que dejó
# `_recordar_dueno` la última vez que la base sí respondió. Redis y Postgres son procesos
# distintos: cuando uno se cae, el otro suele seguir en pie.
#
# ⚠️ NO es un envío proactivo a un CLIENTE (la regla dura de Meta, CLAUDE.md §3): va a la DUEÑA,
# igual que `_avisar_intervencion`, `_avisar_duena` y el aviso de los bancos rojos.

async def avisar_relevo_caido(telefono: str, motivo: str, detalle: str) -> bool:
    """El aviso de emergencia cuando `pedir_ayuda` no pudo dejar rastro. True si salió."""
    if (telefono or "").startswith("__"):
        # Simulador del panel y bancos de prueba: no hay WhatsApp real del otro lado. Mismo
        # candado que ya usan `enviar_catalogo` y `enviar_fotos_producto`.
        return False

    # 🔴 EL ORDEN IMPORTA, Y ANTES ESTABA AL REVÉS (arreglo del cerebro partido, 2026-08-03).
    # Esto pedía primero el ENTORNO y solo caía a la copia si venía vacío. Pero el entorno es la
    # SEMILLA que se pone el día que se monta la caja: en el taller está VACÍO, y la copia solo la
    # escribían `_avisar_intervencion` y `_avisar_duena` — o sea, únicamente cuando de verdad ya
    # había SALIDO un aviso. En una caja recién montada, donde nunca salió ninguno, este testigo
    # se quedaba sin nadie a quien escribirle justo el día de la caída.
    # `sin_base=True` porque esto corre CON POSTGRES CAÍDO: va a la copia de Redis y al entorno,
    # que son los dos sitios que sobreviven a esa caída. Y ahora la copia la reescribe el webhook
    # con cada mensaje que entra, así que existe desde el primer minuto.
    destino = await telefono_de_la_duena(sin_base=True)
    if not destino:
        logger.error(
            "RELEVO CAÍDO y SIN a quién avisar (ni DUENO_TELEFONO ni caché): cliente=%s "
            "motivo=%s detalle=%s", telefono, motivo, detalle,
        )
        return False

    # ANTI-INUNDACIÓN. Si la base está caída, CADA turno de CADA cliente pasa por aquí. Un
    # WhatsApp por turno le quema la calidad al número — y eso arriesga la cuenta de Meta de
    # TODOS los clientes (regla dura). Uno por chat cada 30 min basta: el aviso dice "entra al
    # chat", y ella solo tiene que entrar una vez.
    clave_candado = f"cache:relevo_caido:{telefono}"
    try:
        if await get_cache(clave_candado):
            logger.error(
                "RELEVO CAÍDO otra vez para %s (motivo=%s) — no se reenvía el WhatsApp (anti-spam)",
                telefono, motivo,
            )
            return False
        await set_cache(clave_candado, "1", 1800)
    except Exception:  # noqa: BLE001 — sin Redis se avisa igual: un aviso de más > ninguno
        pass

    cuerpo = (
        "🔴 *EL BOT NO PUDO DEJARTE EL AVISO EN EL PANEL*\n\n"
        f"Cliente: {telefono}\n"
        f"{_MOTIVO_TITULO.get(motivo, _MOTIVO_TITULO['no_se'])}\n"
        + (f"\n👉 {detalle[:300]}\n" if detalle else "")
        + "\nLa bandeja NO tiene esta fila y el chat NO quedó pausado (falló la base de datos). "
        "Entra al WhatsApp del negocio y respóndele tú."
    )
    try:
        # `forzar`: este aviso NO tiene bandeja donde caer —la bandeja ES lo que falló—, así que
        # el portón de la ventana de 24h no puede dejarlo sin intentar. Ver `enviar_texto`.
        await enviar_texto(destino, cuerpo, forzar=True)
        logger.error(
            "RELEVO CAÍDO para %s: se avisó a la dueña por WhatsApp, fuera de la base", telefono
        )
        return True
    except Exception:  # noqa: BLE001 — si esto tampoco sale, al menos queda el log
        logger.exception(
            "RELEVO CAÍDO para %s y el WhatsApp de emergencia TAMPOCO salió", telefono
        )
        return False


async def _avisar_duena(session, pedido, pago) -> None:
    """Relevo a la humana: avisa por WhatsApp a la duena que entro un pago.

    Reusa enviar_texto (free-form). OJO ventana de 24h: WhatsApp solo permite
    mensajes free-form dentro de las 24h desde el ultimo mensaje de la duena;
    fuera de esa ventana hara falta una plantilla aprobada (fast-follow).
    """
    config = {
        f.clave: f.valor
        for f in (await session.execute(select(Configuracion))).scalars().all()
    }
    destino = config.get("dueno_telefono") or get_settings().dueno_telefono
    if not destino:
        logger.warning("No hay dueno_telefono configurado; no se envia aviso del pago")
        return
    # Cada aviso que SÍ sale deja el número guardado fuera de Postgres, para el día que la base
    # no responda y el ÚLTIMO TESTIGO tenga que escribir sin poder consultarla.
    await _recordar_dueno(destino)
    monto_usd = f"${pago.monto_usd}" if pago.monto_usd is not None else "?"
    monto_bs = f"Bs {pago.monto_bs}" if pago.monto_bs is not None else "?"
    detalle = (
        f"\nReferencia: {pago.referencia}"
        if pago.referencia
        else "\nComprobante: imagen recibida"
    )
    mensaje = (
        f"🔔 *Nuevo pago reportado* — Pedido #{pedido.id}\n"
        f"Cliente: {pedido.cliente_telefono}\n"
        f"Total: {monto_usd} ({monto_bs}){detalle}\n\n"
        f"Verifícalo en tu panel (sección *Pagos*) para confirmar y despachar."
    )
    try:
        await enviar_texto(destino, mensaje)
    except Exception:  # noqa: BLE001 — un fallo de aviso no debe romper el registro del pago
        logger.exception("No se pudo avisar a la duena del pago del pedido %s", pedido.id)


async def registrar_comprobante(
    session, telefono, referencia=None, comprobante_media_id=None, comprobante_url=None,
    avisar=False, monto_leido=None,
):
    """Registra el pago REPORTADO (estado 'reportado'). NO lo confirma: eso lo
    hace la duena desde el dashboard. Amarra al pedido en 'esperando_pago'.

    `monto_leido` = el monto que la VISION leyo en la imagen del comprobante. Sirve para
    saber COMO pago el cliente: si calza con el monto en divisas (20% de descuento), el pago
    se registra por ESE monto y como 'divisas'. Antes el pago se guardaba SIEMPRE por el
    precio COMPLETO en Bs: quien pagaba $36 con su descuento legitimo aparecia en el panel
    debiendo $45, y la duena podia rechazarle un pago bueno o perseguirla por una deuda
    que no existe."""
    pedido = await get_pedido_esperando_pago(session, telefono)
    if pedido is None:
        return {"ok": False, "nota": "este cliente no tiene un pedido esperando pago"}

    # Idempotencia: si ya existe un pago con ese comprobante, no duplicar.
    if comprobante_media_id:
        existente = (
            await session.execute(
                select(Pago).where(Pago.comprobante_media_id == comprobante_media_id)
            )
        ).scalars().first()
        if existente is not None:
            return {"ok": True, "pago_id": existente.id, "nota": "ese comprobante ya estaba registrado"}

    # Un solo pago 'reportado' por pedido: si ya hay, lo enriquecemos y NO re-avisamos.
    reportado = (
        await session.execute(
            select(Pago).where(Pago.pedido_id == pedido.id, Pago.estado == "reportado")
        )
    ).scalars().first()
    if reportado is not None:
        cambiado = False
        if comprobante_media_id and not reportado.comprobante_media_id:
            reportado.comprobante_media_id = comprobante_media_id
            reportado.comprobante_url = comprobante_url
            cambiado = True
        if referencia and not reportado.referencia:
            reportado.referencia = referencia
            cambiado = True
        if cambiado:
            await session.commit()
        return {"ok": True, "pago_id": reportado.id, "nota": "ya habia un pago reportado para este pedido"}

    monto_usd = Decimal(str(pedido.total)) if pedido.total is not None else None
    tasa = None
    monto_bs = None
    try:
        guardado = await get_cache(f"cobro:{telefono}")
        if guardado:
            d = json.loads(guardado)
            # La caché guarda el cobro que se le DIO al cliente (monto en Bs y tasa usada),
            # pero es por TELÉFONO: si el cliente cambió de pedido (ej. de la Kombucha de $4
            # a la de $7), traía el monto del cobro VIEJO y el pago quedaba registrado en $4
            # sobre una venta de $7. Solo vale si es el cobro de ESTE MISMO pedido; si no,
            # se recalcula desde el total real del pedido.
            if int(d.get("pedido_id", 0)) == pedido.id:
                if d.get("monto_usd"):
                    monto_usd = Decimal(str(d["monto_usd"]))
                if d.get("tasa"):
                    tasa = Decimal(str(d["tasa"]))
                if d.get("monto_bs"):
                    monto_bs = Decimal(str(d["monto_bs"]))
            else:
                logger.info(
                    "registrar_comprobante: la caché de cobro es del pedido %s pero el "
                    "comprobante es del %s → se recalcula desde el pedido",
                    d.get("pedido_id"), pedido.id,
                )
        # 🔁 RESPALDO DURADERO (migración 027): si Redis no tenía la cotización —lo normal cuando
        # el cliente cotiza un día y paga al siguiente, con TTL de 24h— se usa la que quedó grabada
        # en el PEDIDO. Sin esto, abajo se recalculaba `monto_bs` con la tasa de HOY: el cliente
        # pagaba los Bs que se le pidieron el viernes y el pago se grababa contra la tasa del
        # domingo, así que "Monto distinto" le reclamaba una diferencia que no debía.
        if tasa is None and pedido.tasa_cotizada is not None:
            tasa = Decimal(str(pedido.tasa_cotizada))
            if pedido.cotizado_bs is not None:
                monto_bs = Decimal(str(pedido.cotizado_bs))
            if pedido.cotizado_usd is not None:
                monto_usd = Decimal(str(pedido.cotizado_usd))
            logger.info(
                "registrar_comprobante: sin caché en Redis; se usa la cotización grabada en el "
                "pedido %s (tasa %s del %s)", pedido.id, tasa, pedido.cotizado_at,
            )
    except Exception:  # noqa: BLE001
        pass

    if monto_bs is None and monto_usd is not None:
        try:
            tasa = await obtener_tasa_bcv()
            monto_bs = (monto_usd * tasa).quantize(Decimal("0.01"))
        except Exception:  # noqa: BLE001
            tasa = None
            monto_bs = None

    # ¿Pagó en DIVISAS (con el 20% de descuento) o en bolívares (precio completo)?
    # Lo decide el MONTO que la visión leyó en el comprobante, no el modelo.
    metodo = "pago_movil"
    if monto_leido is not None and monto_usd is not None:
        try:
            leido = Decimal(str(monto_leido))
            # ⚠️ LA MISMA FUNCIÓN que usa `generar_datos_pago` para cobrar — no una copia de su
            # fórmula. Antes eran dos cuentas escritas a mano que se pedían por comentario no
            # desincronizarse; ahora es imposible que difieran (ver `monto_en_efectivo`).
            _envio = Decimal(str(getattr(pedido, "costo_envio", 0) or 0))
            en_divisas = monto_en_efectivo(monto_usd, _envio)
            # Tolerancia del 2% (redondeos). Se compara contra el monto en DÓLARES: si el
            # comprobante viene en Bs, el número es mil veces mayor y no calza con ninguno.
            def _calza(a: Decimal, b: Decimal) -> bool:
                return abs(a - b) <= max(Decimal("0.50"), b * Decimal("0.02"))

            if _calza(leido, en_divisas) and not _calza(leido, monto_usd):
                metodo = "divisas"
                monto_usd = en_divisas  # lo que de verdad se acordó cobrar
                monto_bs = None  # no hay Bs que comparar: pagó en dólares
                logger.info(
                    "registrar_comprobante: %s pagó en DIVISAS con el 20%% de descuento "
                    "(leído %s ≈ %s)", telefono, leido, en_divisas,
                )
        except Exception:  # noqa: BLE001 — nunca tumbar el registro del pago por esto
            logger.exception("registrar_comprobante: no se pudo interpretar el monto leído")

    pago = Pago(
        pedido_id=pedido.id,
        metodo=metodo,
        monto_usd=monto_usd,
        monto_bs=monto_bs,
        # 💡 LO QUE EL CLIENTE MANDÓ, ADEMÁS DE LO QUE SE LE COBRÓ (auditoría 2026-08-02, DIN-3).
        # `monto_usd`/`monto_bs` son lo COBRADO. Sin esta línea, el monto que la visión leyó en la
        # captura se usaba solo para detectar si pagó en divisas y después SE TIRABA: el panel le
        # enseñaba a la dueña Bs 16.591 (lo cobrado) en grande, con "Confirmar pago" al lado, para
        # un comprobante que decía Bs 5.000. Un clic y el pedido quedaba pagado con Bs 11.591 sin
        # cobrar. La señal existía; solo había que guardarla.
        monto_recibido=(Decimal(str(monto_leido)) if monto_leido is not None else None),
        tasa_usada=tasa,
        referencia=referencia,
        comprobante_media_id=comprobante_media_id,
        comprobante_url=comprobante_url,
        estado="reportado",
    )
    session.add(pago)
    try:
        await session.commit()
    except IntegrityError:
        # Carrera con otro reintento concurrente de Meta: el UNIQUE de
        # comprobante_media_id ya existe. Devolvemos el pago existente (idempotente).
        await session.rollback()
        if comprobante_media_id:
            existente = (
                await session.execute(
                    select(Pago).where(Pago.comprobante_media_id == comprobante_media_id)
                )
            ).scalars().first()
            if existente is not None:
                return {"ok": True, "pago_id": existente.id, "nota": "ese comprobante ya estaba registrado"}
        # La OTRA carrera (migración 026): dos capturas seguidas del mismo cliente, procesadas en
        # paralelo por dos workers. Ambas pasaron el `SELECT ... estado='reportado'` de arriba
        # porque ninguna había commiteado todavía; el UNIQUE parcial `ux_pago_reportado_por_pedido`
        # deja pasar a una sola. Aquí se recoge a la perdedora y se le devuelve el pago que SÍ
        # quedó: el resultado es el mismo que si hubieran llegado en fila. (EST-2.)
        vivo = (
            await session.execute(
                select(Pago).where(Pago.pedido_id == pedido.id, Pago.estado == "reportado")
            )
        ).scalars().first()
        if vivo is not None:
            logger.info(
                "Comprobante de %s: otro worker registró el pago #%s primero (carrera); "
                "se reutiliza en vez de duplicar", telefono, vivo.id,
            )
            return {"ok": True, "pago_id": vivo.id, "nota": "ya habia un pago reportado para este pedido"}
        raise
    await session.refresh(pago)
    # Aviso a la duena: DESACTIVADO por defecto (su banco ya le avisa de los pagos).
    # Se puede reactivar pasando avisar=True (p.ej. plantilla HSM fuera de la ventana 24h).
    if avisar:
        await _avisar_duena(session, pedido, pago)
    return {
        "ok": True,
        "pago_id": pago.id,
        "pedido_id": pedido.id,
        "nota": "comprobante registrado; agradécele, dile que recibiste su pago y que coordinas la entrega, y queda atenta por si quiere algo mas. NO afirmes que verificaste el dinero en el banco ni que esta 'confirmado'.",
    }


async def _la_duena_tomo_el_chat(session, telefono: str) -> bool:
    """¿Le tiene la dueña tomado el chat a este cliente? (el freno anti-atropello, para la MEDIA).

    🔴 Por qué existe (auditoría 2026-08-02, META-5): el docstring de `_enviar_en_partes`
    (workers/tasks.py) afirma ser *"el único embudo por el que salen las 4 respuestas del bot"*.
    Es falso: cubre los cuatro carriles de TEXTO y **no cubre la media**. Las fotos y el catálogo
    salían por su cuenta, sin mirar nada. El caso real: el bot tarda ~20 s en contestar; en ese
    rato la dueña toma el chat desde el panel. Su texto se frena (bien) pero el cliente recibe
    igual TRES FOTOS de un bot que debía estar callado, mientras ella le está escribiendo.

    ⚠️ Es el gemelo de `_lo_paso_una_persona` y la lógica está repetida a sabiendas: importar
    `app.workers.tasks` desde aquí metería Celery dentro del contenedor de la API (que también
    importa este módulo) e invertiría las capas — la herramienta pasaría a depender del worker.
    Se copian las DIEZ líneas, no las doscientas. Si un día cambia la regla de la pausa, cambia
    en los dos sitios: por eso este comentario nombra al gemelo.

    Ante cualquier duda o error devuelve True (NO se envía): mismo lado seguro que el gemelo.
    Callarse de más cuesta una foto; hablarle encima a la dueña delante del cliente cuesta la venta.
    Los teléfonos internos ("__…", simulador del panel y bancos) nunca están tomados.
    """
    if (telefono or "").startswith("__"):
        return False
    try:
        cliente = (
            await session.execute(select(Cliente).where(Cliente.telefono == telefono))
        ).scalar_one_or_none()
    except Exception:  # noqa: BLE001
        logger.exception("No sé quién pausó a %s → la media NO sale (lado seguro)", telefono)
        return True
    if cliente is None:
        return False
    # CONTACTO PRIVADO (migración 031): a la familia no le salen ni fotos ni catálogo del negocio.
    # 🔴 ESTE ES EL SEXTO PUNTO, y no lo cubría el cambio de `_estado_pausa`: este gemelo lee la
    # fila de `Cliente` por su cuenta (ver el ⚠️ del docstring, que avisa de que la regla vive en
    # DOS sitios). Hoy es el día que anunciaba. Si faltara, un chat privado que llegue a ejecutar
    # una tool de media recibiría las fotos aunque el texto se hubiera frenado — que es
    # exactamente el agujero META-5 que este gemelo vino a tapar, con otro disfraz.
    if cliente.privado:
        return True
    if not cliente.bot_pausado:
        return False
    # `pausado_por='bot'` = el propio bot escaló: sus envíos en curso SÍ salen (migración 020).
    return cliente.pausado_por != "bot"


async def _guardar_media_saliente(
    *, telefono: str, tipo: str, contenido: str, url: str, respuesta: dict | None
) -> None:
    """Mete en el hilo del panel la FOTO/VIDEO/PDF que el bot le acaba de mandar al cliente.

    🔴 POR QUÉ EXISTE (auditoría 2026-07-14, verificado contra la BD de producción):
    el bot SÍ enviaba la multimedia por WhatsApp —eso funcionaba— pero **NO la guardaba**.
    `enviar_fotos_producto` y `enviar_catalogo` hacían el POST a Meta y se acababa ahí. Las 130
    filas de `mensajes` eran TODAS `tipo='text'` y NINGUNA tenía `media_url`, aunque el esquema
    admite `image`/`video`/`document` desde la migración 021. La dueña abría el chat interno y
    veía una conversación donde el bot "nunca" mandó una foto — cuando sí la había mandado.

    El molde es `_guardar_media_en_hilo` (workers/tasks.py), que hace esto BIEN para el
    ENTRANTE. Esto es su gemelo para el SALIENTE.

    ⚠️ SESIÓN PROPIA Y EXCEPCIÓN TRAGADA, a propósito: la foto YA salió hacia el cliente. Si
    escribir la burbuja fallara y la excepción subiera, `ejecutar_tool` la convertiría en
    `{"error": …}` y el LLM creería que el envío falló — y le diría al cliente que no pudo
    mandarle la foto que sí recibió. Un fallo cosmético del panel jamás puede romper el envío.
    """
    from app.models import Mensaje
    from app.services.db import get_session_factory

    # META-1: la marca de "este mensaje es MÍO" se pone ANTES de tocar la base, porque el eco
    # puede llegar mientras esta fila se está escribiendo. Nunca lanza.
    from app.services.meta_client import marcar_mensaje_propio, wa_message_id

    await marcar_mensaje_propio(wa_message_id(respuesta))

    try:
        mime, _ = mimetypes.guess_type(url)
        factory = get_session_factory()
        async with factory() as session:
            session.add(
                Mensaje(
                    cliente_telefono=telefono,
                    rol="assistant",
                    tipo=tipo,               # image | video | document (lo admite el CHECK de la 021)
                    contenido=contenido,     # el pie de la burbuja: 'mensajes.contenido' es NOT NULL
                    media_url=url,
                    media_mime=mime,
                    wa_message_id=wa_message_id(respuesta),  # ← antes se TIRABA
                    estado="enviado",
                )
            )
            await session.commit()
    except Exception:  # noqa: BLE001 — la burbuja es cosmética; el envío YA ocurrió
        logger.exception("No se pudo meter en el hilo la media saliente de %s", telefono)


async def media_ya_mostrada(
    telefono: str, nombre: str, etiqueta: str | None = None, *, si_falla: bool = True
) -> bool:
    """¿Este cliente YA recibió una foto/video de ESTE producto? (el candado de la memoria)

    POR QUÉ SE MIRA LA TABLA `mensajes` Y NO EL HISTORIAL DE REDIS: la media NUNCA entra al
    historial (decisión del 2026-08-08 — el historial es conversación, y la media ya la narra
    el propio texto del bot), así que ahí no hay nada que buscar. En cambio
    `_guardar_media_saliente` escribe UNA fila por cada foto/video que sale, con el nombre del
    producto en el pie —"(foto de Pan Keto)"— y también en el simulador. Es el único registro
    durable de lo que el cliente de verdad RECIBIÓ, y sobrevive al recorte del historial.

    Que el candado abarque TODO el hilo (no solo "esta conversación") es a propósito y es el
    lado barato del error: repetir una foto no pedida es spam que le baja la calidad al número;
    si el cliente la quiere otra vez, la PIDE y se reenvía (`reenviar=True` en la herramienta,
    que el código enciende solo cuando el cliente pide ver — `_ejecutar_con_guardas`).

    Con `etiqueta` (rama fotos-con-memoria) la pregunta se afina a ESA versión: el pie guarda
    "(foto de Empanadas — base de yuca)", así que quien vio la de yuca NO queda bloqueado para
    la de plátano — versiones distintas son fotos distintas.

    `si_falla` es la dirección del fail-safe, porque el lado seguro DEPENDE DE QUIÉN LLAMA:
    - La RED DE LA FOTO (empuja sin que nadie pida) usa el default True = ante la duda, callarse
      — el mismo criterio que `_la_duena_tomo_el_chat`.
    - La HERRAMIENTA (responde al modelo en plena venta) pasa False = ante la duda, enviar: un
      hipo de Postgres jamás puede dejar al bot "sin fotos para siempre".
    """
    if not (telefono or "") or not (nombre or "").strip():
        return si_falla
    # El nombre (y la etiqueta) los escribió la dueña: se escapan los comodines de LIKE por si
    # un día traen '%' o '_' (el patrón lo armamos nosotros, no ella). Con etiqueta se busca el
    # pie exacto que arma `_envio_de_un_archivo`: "nombre — etiqueta".
    objetivo = f"{nombre} — {etiqueta}" if (etiqueta or "").strip() else nombre
    escapado = objetivo.replace("\\", r"\\").replace("%", r"\%").replace("_", r"\_")
    try:
        factory = get_session_factory()
        async with factory() as session:
            fila = (
                await session.execute(
                    select(Mensaje.id)
                    .where(
                        Mensaje.cliente_telefono == telefono,
                        Mensaje.rol == "assistant",
                        Mensaje.tipo.in_(("image", "video")),
                        Mensaje.contenido.ilike(f"%{escapado}%", escape="\\"),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            return fila is not None
    except Exception:  # noqa: BLE001 — el lado seguro lo decide quien llama (ver docstring)
        logger.exception(
            "media_ya_mostrada: no se pudo leer mensajes; se asume que %s",
            "SÍ se mostró (la red no empuja)" if si_falla else "NO se mostró (la tool sí envía)",
        )
        return si_falla


async def _memoria_de_fotos_apagada(session) -> bool:
    """El interruptor de la garantía de la memoria (clave `fotos_memoria` en `configuracion`).

    Solo un 'off' escrito con todas sus letras la apaga; clave ausente, con basura o BD tosiendo
    ⇒ la garantía queda PUESTA (mismo criterio que `tools_activas`: un fallo de configuración
    jamás cambia la conducta). Existe por la doctrina del repo —cada garantía con su
    interruptor— y se maneja desde la BD sin desplegar, como el centinela 'todos' de la lista
    blanca. OJO: apaga solo la memoria ENTRE turnos; el de-dup del mismo turno (no encolar dos
    veces el mismo archivo) no es una garantía apagable, es corrección a secas.
    """
    try:
        valor = (
            await session.execute(
                select(Configuracion.valor).where(Configuracion.clave == "fotos_memoria")
            )
        ).scalars().first()
        return (valor or "").strip().lower() == "off"
    except Exception:  # noqa: BLE001 — config rota ⇒ la garantía sigue puesta
        logger.exception("fotos_memoria: no se pudo leer el interruptor; la memoria sigue PUESTA")
        return False


async def enviar_catalogo(session, telefono):
    """Envía el catálogo en PDF (guardado en la BD). El cliente lo recibe como
    archivo. Si no hay PDF cargado, avisa para que el agente use ver_catalogo."""
    fila = await session.get(CatalogoPdf, 1)
    if fila is None or not fila.contenido:
        return {"ok": False, "nota": "no hay un catalogo PDF cargado; usa ver_catalogo (texto)"}

    settings = get_settings()
    link = f"{settings.public_base_url.rstrip('/')}/api/catalogo/archivo"
    # 🧪 SIMULADOR: no hay WhatsApp real; se simula el envío para que la dueña lo pruebe.
    if (telefono or "").startswith("__"):
        await _guardar_media_saliente(
            telefono=telefono, tipo="document", contenido="(catálogo en PDF)",
            url=link, respuesta=None,
        )
        return {"ok": True, "nota": "(SIMULADOR) le enviaste el catálogo PDF; confírmaselo con calidez"}

    # ÚLTIMA MIRADA AL FRENO, con el PDF ya en la mano (META-5). Sin esto, el catálogo salía
    # aunque la dueña hubiera tomado el chat mientras el bot pensaba.
    if await _la_duena_tomo_el_chat(session, telefono):
        logger.info("No se envía el catálogo: la dueña tomó el chat de %s (relevo)", telefono)
        return {
            "ok": False,
            "nota": (
                "no se envió el catálogo (la dueña está atendiendo este chat). NO le digas al "
                "cliente que se lo mandaste ni intentes mandarlo otra vez"
            ),
        }

    # El archivo lo guarda y lo SIRVE el bot (su propia URL pública), no el worker.
    # Worker y bot no comparten disco, así que aquí NO revisamos el archivo local:
    # basta el flag en BD, y Meta descarga el PDF de la URL pública del bot.
    from app.services.meta_client import enviar_documento

    async def _mandar_pdf() -> None:
        resp = await enviar_documento(telefono, link, "Catalogo.pdf")
        # El catálogo que el cliente recibió ahora SÍ aparece en el chat interno de la dueña.
        await _guardar_media_saliente(
            telefono=telefono,
            tipo="document",
            contenido="(catálogo en PDF)",
            url=link,
            respuesta=resp,
        )

    # 🔴 EL TEXTO SALE PRIMERO, y este es EL caso canónico de los documentos de Whuilianny:
    #     [00:54] "Hola carlos buenas noches bendiciones."
    #     [00:54] "Por aquí te dejo nuestro catálogo. Por aquí a la orden."
    #     [00:54] (documento)
    # Ella ANUNCIA y DESPUÉS MUESTRA. Con la cola abierta el PDF sale solo, detrás del texto.
    # Ver `services/cola_media.py`.
    if cola_media.encolar("catálogo en PDF", _mandar_pdf):
        return {
            "ok": True,
            "nota": (
                "el catálogo SALE SOLO justo DESPUÉS de tu mensaje (lo manda el código; NO "
                "vuelvas a llamar a la herramienta). Anúnciaselo en tu texto con TUS palabras, "
                "como quien se lo está dejando ahí, y sigue la conversación"
            ),
        }
    try:
        await _mandar_pdf()
    except Exception:  # noqa: BLE001
        # El PDF EXISTE (el flag de BD lo dice) y Meta lo rechazó: el cliente NO lo recibió, y
        # hasta hoy eso no dejaba rastro en ningún sitio — ni aquí, ni en la red de arriba.
        logger.exception(
            "enviar_catalogo: Meta rechazó el PDF de %s; el cliente NO lo recibió", telefono
        )
        return {"ok": False, "nota": "no se pudo enviar el catalogo PDF; usa ver_catalogo (texto)"}
    return {"ok": True, "nota": "catalogo PDF enviado al cliente; confirmaselo con calidez"}


def _envio_de_un_archivo(
    *, telefono: str, producto: str, url: str, cap: str, es_video: bool, etiqueta: str
):
    """Empaqueta UN archivo ya resuelto (el envío a Meta + su burbuja en el panel) para que la
    cola lo suelte DESPUÉS del texto. Devuelve una corutina sin argumentos.

    🔴 VIVE FUERA DEL BUCLE A PROPÓSITO, y esto no es estilo: un `async def` escrito DENTRO del
    `for` de `enviar_fotos_producto` captura la VARIABLE del bucle, no su valor. Como la cola se
    vacía cuando el `for` ya terminó, las tres fotos saldrían con **la url y el caption de la
    última** — tres veces la misma imagen, y con el pie de foto equivocado. Los parámetros por
    nombre fuerzan la captura por valor.

    (Está a nivel de módulo también para poder PROBARLO: la primera versión era una closure
    interna, la reversión de la trampa salió VERDE y ese hueco de tests fue el que la sacó
    aquí. Probar la pieza que puede romperse exige poder alcanzarla.)
    """
    async def _hacerlo() -> None:
        resp = (
            await enviar_video(telefono, url, cap) if es_video
            else await enviar_imagen(telefono, url, cap)
        )
        logger.info(
            "enviar_fotos_producto: enviado %s de %s (url=%s)",
            "video" if es_video else "image", producto, url,
        )
        # 🔴 LA FILA QUE FALTABA. El cliente recibía la foto y la dueña, en su chat interno, no
        # veía NADA: el bot parecía no haberla mandado nunca. Ahora la burbuja existe.
        await _guardar_media_saliente(
            telefono=telefono,
            tipo="video" if es_video else "image",
            contenido=(
                f"({'video' if es_video else 'foto'} de {producto}"
                + (f" — {etiqueta}" if etiqueta else "")
                + ")"
            ),
            url=url,
            respuesta=resp,
        )

    return _hacerlo


async def enviar_fotos_producto(
    session, telefono, nombre, variante_id=None, etiqueta=None, maximo=3, reenviar=False
):
    """Envía al cliente las fotos/videos de UN producto por WhatsApp (cuando las pide).

    `maximo` = cuántos archivos mandar (3 por defecto, como siempre). 🔴 Existe desde el
    2026-08-21: la RED DE LA FOTO pasó a mandar hasta DOS productos en un turno, y con 3 archivos
    cada uno el cliente recibía **hasta 6 seguidos** — 5 en la primera prueba real. Eso es
    exactamente el bombardeo que se quería evitar, y arriesga la calidad del número con Meta (regla
    dura de Tech Provider). Con varios productos la red pide **1 de cada uno**; con uno solo sigue
    mandando hasta 3, que ahí sí es enseñar el producto desde varios ángulos.

    Con `variante_id` manda PRIMERO las de ESE tamaño (si piden la kombucha de 700ml, la de
    700ml — antes mandaba siempre la de 350ml porque eran dos productos y el buscador devolvía
    el primero) y completa con las NEUTRAS (las que no tienen tamaño asignado).

    Con `etiqueta` (las PALABRAS DEL CLIENTE: "de yuca") manda la foto que la dueña nombró así.
    Sin `etiqueta` no se filtra NADA: se comporta igual que siempre.

    🔒 `reenviar` (rama fotos-con-memoria, caso Omaira 2026-08-29): desde hoy la herramienta
    TIENE MEMORIA — un producto ya mostrado en este chat no se repite, se avisa y la venta
    sigue. `reenviar=True` es la válvula del reenvío legítimo ("mándamela otra vez", "no me
    llegó"): la pone el modelo, y el código la enciende solo cuando el cliente pide ver
    (`_ejecutar_con_guardas`), para que la garantía no dependa de ningún modelo.

    Usa el link público de R2 que Meta descarga. Si el producto no tiene media cargada, lo dice
    con sinceridad (NUNCA afirmar que se envió algo que no se envió)."""
    from app.services import r2

    logger.info(
        "enviar_fotos_producto LLAMADA: nombre=%r variante_id=%r etiqueta=%r reenviar=%r",
        nombre, variante_id, etiqueta, reenviar,
    )
    # El tope anti-spam es del CÓDIGO, no del schema: `maximo` lo manda el modelo, y sin este
    # techo un maximo=50 alucinado serían 50 archivos seguidos (el log de abajo ya prometía
    # "se envían los 3 primeros" — ahora es verdad venga el número que venga).
    try:
        maximo = max(1, min(int(maximo), 3))
    except (TypeError, ValueError):
        maximo = 3
    prod = None
    variante = None
    if variante_id:
        try:
            variante = await session.get(ProductoVariante, int(variante_id))
        except (TypeError, ValueError):
            variante = None
        if variante is not None:
            prod = await session.get(Producto, variante.producto_id)
    if prod is None:
        prod = await _buscar_producto(session, nombre)
    if prod is None:
        logger.info("enviar_fotos_producto: producto %r NO encontrado", nombre)
        return {"enviadas": 0, "nota": f"no encontré el producto '{nombre}'; ofrece los que sí hay"}
    todos = (
        await session.execute(
            select(ProductoMedia)
            .where(ProductoMedia.producto_id == prod.id)
            .order_by(ProductoMedia.orden, ProductoMedia.id)
        )
    ).scalars().all()
    # Qué fotos van: el tamaño (como siempre) y, si el cliente dijo cuál versión quiere, la que
    # la dueña nombró así. Toda la decisión vive en `_elegir_medios`, que es PURA y está probada.
    _vid = variante.id if (variante is not None and variante.producto_id == prod.id) else None
    medios, et_enviada, ets_disp = _elegir_medios(todos, _vid, etiqueta)
    # Lo que el cliente pidió, ya limpio: es lo que se le repite al modelo en los avisos.
    _pedido = (etiqueta or "").strip()
    logger.info(
        "enviar_fotos_producto: producto=%s id=%s media=%d r2_config=%s",
        prod.nombre, prod.id, len(medios), r2.configurado(),
    )
    if not todos:
        return {
            "enviadas": 0,
            "nota": (
                f"'{prod.nombre}' no tiene fotos ni videos cargados. Dile con sinceridad que "
                "por ahora no tienes fotos de ese y ofrécele el catálogo o más info; NO digas "
                "que se las enviaste"
            ),
        }
    if not medios:
        # 🔴 TIENE FOTOS, PERO NINGUNA ES DE ESO. Sin esta rama caería en el mensaje de arriba y
        # le diría al cliente que el producto "no tiene fotos" — mentira sobre un producto que
        # tiene dos. Mejor ninguna que la equivocada, pero DICHO con la verdad.
        # (Sin `etiqueta` esto solo pasa si TODAS son de OTRO tamaño; ahí se dice así y no
        # "ninguna es de None", que es lo que saldría al copiar el texto a lo bruto.)
        _de_eso = f"de '{_pedido}'" if _pedido else "del tamaño que pidió"
        _hay = (
            f" Ofrécele la(s) que sí hay ({', '.join(ets_disp)}) o el catálogo."
            if ets_disp
            else " Ofrécele el catálogo o más info."
        )
        return {
            "enviadas": 0,
            "producto": prod.nombre,
            "etiquetas_disponibles": ets_disp,
            "nota": (
                f"'{prod.nombre}' SÍ tiene fotos, pero NINGUNA es {_de_eso} y no hay generales. "
                f"Dilo con sinceridad.{_hay} JAMÁS mandes otra diciendo que es {_de_eso}"
            ),
        }
    # 🧪 EL SIMULADOR DEL PANEL (teléfono "__simulador__…") NO tiene un WhatsApp real al que
    # mandar: Meta rechaza el número falso con un 400 y el bot decía "no se pudieron enviar" —
    # haciendo creer a la dueña que las fotos están rotas cuando en WhatsApp real SÍ funcionan.
    # Aquí se SIMULA el envío: cuenta las fotos como enviadas (sin llamar a Meta) y las guarda en
    # el hilo para que la dueña las VEA en el simulador. La cuenta de verdad es a números reales.
    if (telefono or "").startswith("__"):
        for m in medios[:maximo]:
            url = r2.url_publica(m.clave)
            if url:
                # La etiqueta también AQUÍ: el simulador es el ÚNICO sitio desde donde la dueña
                # puede comprobar esto sin un WhatsApp real. Sin ella vería dos burbujas
                # idénticas y concluiría que no funciona.
                _et = (m.etiqueta or "").strip()
                await _guardar_media_saliente(
                    telefono=telefono,
                    tipo="video" if m.tipo == "video" else "image",
                    contenido=(
                        f"({'video' if m.tipo=='video' else 'foto'} de {prod.nombre}"
                        + (f" — {_et}" if _et else "")
                        + ")"
                    ),
                    url=url, respuesta=None,
                )
        n = min(len(medios), 3)
        return {
            "enviadas": n, "producto": prod.nombre,
            "etiqueta_enviada": et_enviada,
            "etiquetas_disponibles": ets_disp,
            "nota": (
                f"(SIMULADOR) le mostraste {n} foto(s) de '{prod.nombre}'"
                + (f" — la(s) de {et_enviada}" if et_enviada else "")
                + ". En WhatsApp real le llegan de verdad. Coméntale cálido que ahí las tiene "
                "y sigue la venta."
                + (
                    ""
                    if (et_enviada or not _pedido)
                    else f" ⚠️ NO tienes foto de '{_pedido}': le mostraste la(s) general(es). "
                         f"NO le digas que esa foto es de '{_pedido}'."
                )
            ),
        }

    # ÚLTIMA MIRADA AL FRENO, con las fotos ya elegidas (META-5). El envío de media es el
    # más ruidoso que tiene el bot —hasta 3 archivos seguidos— y era justo el que no miraba nada.
    if await _la_duena_tomo_el_chat(session, telefono):
        logger.info("No se envían fotos: la dueña tomó el chat de %s (relevo)", telefono)
        return {
            "enviadas": 0,
            "nota": (
                "no se enviaron las fotos (la dueña está atendiendo este chat). NO le digas al "
                "cliente que se las mandaste ni intentes mandarlas otra vez"
            ),
        }

    # 🔒 LA MEMORIA DE LA HERRAMIENTA (2026-08-29, caso Omaira: las MISMAS fotos 3 veces en una
    # venta). El de-duplicador vivía SOLO en la RED DE LA FOTO — pero cuando el MODELO llama esta
    # herramienta directo (Sonnet obedece "ÚSALA PROACTIVA" cada turno), ese camino no pasaba por
    # ningún candado y se reenviaba ciego. La garantía va aquí, en la capa que EJECUTA, para que
    # no dependa del modelo. Va DESPUÉS del simulador a propósito: el teléfono fijo del panel
    # ("__simulador__") acumula filas para siempre y cada demo de la dueña parecería rota.
    # `reenviar=True` la salta (el cliente PIDIÓ verlas de nuevo — la válvula prometida), y con
    # `et_enviada` la memoria pregunta por ESA versión: ver la de yuca no bloquea la de plátano.
    if (
        not reenviar
        and not await _memoria_de_fotos_apagada(session)
        and await media_ya_mostrada(telefono, prod.nombre, et_enviada, si_falla=False)
    ):
        logger.info(
            "MEMORIA DE FOTOS: '%s'%s ya se le mostró a %s — no se reenvía (el modelo puede "
            "reenviar con reenviar=true si el cliente lo pide)",
            prod.nombre, f" ({et_enviada})" if et_enviada else "", telefono,
        )
        _otras = [e for e in ets_disp if e != et_enviada]
        return {
            "enviadas": 0,
            "ya_mostrado": True,
            "producto": prod.nombre,
            "etiquetas_disponibles": ets_disp,
            "nota": (
                f"a este cliente YA le mostraste '{prod.nombre}'"
                + (f" — la(s) de {et_enviada}" if et_enviada else "")
                + " antes en esta conversación y NO se reenviaron (repetirlas es spam). Las "
                "fotos están más arriba en el chat: NO digas que se las acabas de mandar; sigue "
                "la venta donde va. Si el cliente pide verlas OTRA VEZ o dice que no le "
                "llegaron, llama de nuevo con reenviar=true"
                + (
                    f". Si pregunta por OTRA versión ({', '.join(_otras)}), llama con esa "
                    "`etiqueta`"
                    if _otras
                    else ""
                )
            ),
        }

    enviadas = 0
    sin_url = 0
    repetidas_en_turno = 0  # archivos que YA estaban en la cola de ESTE mismo turno
    diferidas = 0  # cuántas quedaron EN LA COLA para salir después del texto
    # PIE DE FOTO (caption): SOLO el nombre del producto (y su etiqueta, si la tiene). Sin precio
    # —el precio vive en el tamaño y lo dice el cobro— y desde el 2026-08-22, TAMPOCO la ficha.
    #
    # 🔴 Lo pidió Maired mirando un turno real: «le manda toda la información de lo que tiene
    # guardado en el catálogo. No debería… solamente decirle: "Estas son las galletas Nueva York"».
    # Tenía toda la razón y era peor de lo que parece: el pie llevaba los primeros 140 caracteres
    # de `descripcion`, y en las Galletas New York eso es la LISTA DE INGREDIENTES entera
    # ("harina de almendra y coco, azúcar de coco o alulosa, vainilla natural, manteca de cerdo…").
    # Una foto con la ficha pegada debajo no es una vendedora enseñando su producto: es un folleto.
    # Si el cliente quiere saber de qué está hecho, pregunta — y para eso está `info_producto`.
    # Tope de 3 (antes 8): ocho archivos de golpe es una descarga de spam y le baja la calidad
    # al número. LOS VIDEOS CUENTAN dentro del tope.
    if len(medios) > 3:
        logger.info(
            "enviar_fotos_producto: %s tiene %d archivos, se envían los 3 primeros (tope anti-spam)",
            prod.nombre, len(medios),
        )

    for m in medios[:maximo]:
        url = r2.url_publica(m.clave)
        if not url:
            sin_url += 1
            logger.warning(
                "enviar_fotos_producto: URL vacía (¿falta R2_PUBLIC_URL en el worker?) media=%s",
                m.id,
            )
            continue
        es_video = m.tipo == "video"
        # EL NOMBRE DE LA FOTO VA EN TODAS (aquí SÍ hay que repetirlo: es lo único que las
        # distingue cuando son dos del mismo producto al mismo precio). No es texto del modelo:
        # lo escribe el código copiando lo que puso la dueña. Sigue SIN precio.
        #
        # 🔴 CON ETIQUETA, EL PIE ES LA ETIQUETA A SECAS (31-ago, lo pidió Maired viendo el chat
        # real): "Empanadas de masa de yuca o de masa de plátano — empanada de yuca" era un
        # trabalenguas para el cliente; "empanada de yuca" dice todo. La etiqueta distingue
        # mejor que el nombre (para eso existe). OJO: esto es SOLO el caption de WhatsApp; el
        # registro interno del panel (`_guardar_media_saliente`, "(foto de {producto} — {et})")
        # NO se toca — ahí el nombre del producto es la única referencia que la MEMORIA de
        # fotos puede buscar (media_ya_mostrada busca por nombre en ese pie).
        _et = (m.etiqueta or "").strip()
        cap = _et if _et else prod.nombre
        envio = _envio_de_un_archivo(
            telefono=telefono, producto=prod.nombre, url=url, cap=cap,
            es_video=es_video, etiqueta=_et,
        )
        # La descripción lleva el id del archivo para que sea ÚNICA: así el candado de abajo
        # distingue "los 3 ángulos de esta llamada" (ids distintos, pasan) de "el MISMO archivo
        # encolado por una llamada anterior de este turno" (id repetido, se frena).
        desc = f"{prod.nombre}" + (f" ({_et})" if _et else "") + f" · media {m.id}"
        # 🔒 EL CANDADO INTRA-TURNO (rama fotos-con-memoria): la fila de `mensajes` se escribe
        # recién al VACIAR la cola —después del texto—, así que si el modelo llama la herramienta
        # dos veces en el MISMO turno la memoria de arriba no ve nada en BD. La cola sí lo sabe.
        if cola_media.ya_encolada(desc):
            repetidas_en_turno += 1
            logger.info("enviar_fotos_producto: %s ya está en la cola de este turno — no se duplica", desc)
            continue
        # 🔴 EL TEXTO SALE PRIMERO. Si `tasks.py` abrió la cola de este turno, la foto NO se manda
        # aquí: se encola y sale justo DESPUÉS del texto del bot — que es como lo hace Whuilianny
        # ("por aquí te dejo nuestro catálogo" y ENTONCES el archivo). Ver `services/cola_media.py`.
        # Sin cola abierta (worker de visión, avisos) se envía en el momento, como siempre.
        if cola_media.encolar(desc, envio):
            enviadas += 1
            diferidas += 1
            continue
        try:
            await envio()
            enviadas += 1
        except Exception as e:  # noqa: BLE001 — si una falla, intentamos las demás
            logger.warning("No se pudo enviar media %s de %s: %s", m.id, prod.nombre, e)
            continue
    if sin_url and enviadas == 0:
        # R2 sin configurar en el worker: hasta hoy se saltaba en SILENCIO y el bot decía que el
        # producto "no tiene fotos" — mentira: las tiene, pero no se pudieron construir las URLs.
        logger.error(
            "enviar_fotos_producto: %s TIENE %d archivo(s) pero R2_PUBLIC_URL no está puesta: "
            "no se envió ninguno", prod.nombre, sin_url,
        )
    if enviadas == 0 and repetidas_en_turno:
        # Todo lo que tocaba mandar YA está en la cola de ESTE turno: no es un fallo, es la
        # segunda llamada del mismo turno. Se le dice la verdad al modelo y la venta sigue.
        return {
            "enviadas": 0,
            "ya_mostrado": True,
            "producto": prod.nombre,
            "nota": (
                f"las fotos de '{prod.nombre}' YA van saliendo con ESTE mismo mensaje (las "
                "encolaste hace un momento): no se duplican. Anúncialas UNA sola vez y sigue "
                "la venta"
            ),
        }
    if enviadas == 0:
        return {
            "enviadas": 0,
            "nota": f"no se pudieron enviar las fotos de '{prod.nombre}' ahora; ofrece el catálogo o seguir por texto",
        }
    return {
        "enviadas": enviadas,
        "producto": prod.nombre,
        # De QUÉ era la foto que salió (None = las generales), y qué nombres hay en total. Con
        # esto el bot puede decir la verdad en el mismo turno en vez de dar por hecho.
        "etiqueta_enviada": et_enviada,
        "etiquetas_disponibles": ets_disp,
        "nota": (
            # 🔴 DOS VERDADES DISTINTAS, y decir la que no es hace mentir al bot.
            # Con la cola abierta (el turno normal) los archivos AÚN NO han salido: salen solos
            # justo después de tu texto. Entonces el bot los ANUNCIA — que es justo lo que hace
            # Whuilianny ("por aquí te dejo nuestro catálogo" y ENTONCES el archivo). Si dijera
            # "ahí los tienes" estaría afirmando algo que todavía no pasó.
            (
                f"Las {enviadas} foto(s)/video(s) de '{prod.nombre}'"
                + (f" — la(s) de {et_enviada}" if et_enviada else "")
                + " SALEN SOLAS justo DESPUÉS de tu mensaje (las manda el código; tú no tienes "
                "que hacer nada más ni volver a llamar a la herramienta). Así que anúncialas en "
                "tu texto como quien se las está dejando ahí, con TUS palabras y distinto cada "
                "vez, y sigue la venta"
                if diferidas
                else (
                    f"YA le enviaste {enviadas} archivo(s) de '{prod.nombre}'"
                    + (f" — la(s) de {et_enviada}" if et_enviada else "")
                    + ". Coméntale cálido que ahí los tiene y sigue la venta. NO digas que vas a "
                    "enviarlos: ya están enviados"
                )
            )
            + (
                ""
                if (et_enviada or not _pedido)
                else f". ⚠️ NO tienes foto de '{_pedido}': le mandaste la(s) general(es). NO le "
                     f"digas que esa foto es de '{_pedido}'"
            )
        ),
    }


_DISPATCH = {
    "ver_catalogo": ver_catalogo,
    "enviar_fotos_producto": enviar_fotos_producto,
    "info_producto": info_producto,
    "registrar_pedido": registrar_pedido,
    "info_negocio": info_negocio,
    "buscar_info": buscar_info,
    "recordar_cliente": recordar_cliente,
    "ver_pedidos_cliente": ver_pedidos_cliente,
    "generar_datos_pago": generar_datos_pago,
    "registrar_comprobante": registrar_comprobante,
    "enviar_catalogo": enviar_catalogo,
    "pedir_ayuda": pedir_ayuda,
    "proxima_fecha_entrega": proxima_fecha_entrega,
}


# Herramientas cuyo fallo NO es "un tropiezo del que el modelo se recupera", sino una PÉRDIDA:
# `pedir_ayuda` es la RED DEL RELEVO (si revienta no hay Intervencion, no sale el WhatsApp y NADIE
# sabe que hay un cliente esperando) y las tres del cobro escriben el dinero. Van en ERROR; las
# demás en WARNING. La diferencia no es cosmética: es lo que se busca en el log.
_TOOLS_CRITICAS = frozenset(
    {"pedir_ayuda", "registrar_pedido", "generar_datos_pago", "registrar_comprobante"}
)

# Qué parámetros DECLARA cada tool, sacado de los propios `TOOL_SCHEMAS` (una sola fuente: si
# mañana alguien añade un parámetro al schema, aquí entra solo). Lo usa `_solo_lo_declarado`.
_PARAMS_DECLARADOS: dict[str, set[str]] = {
    (t.get("function") or {}).get("name", ""): set(
        ((t.get("function") or {}).get("parameters") or {}).get("properties", {}) or {}
    )
    for t in TOOL_SCHEMAS
}


def _args_para_log(args: dict) -> str:
    """Los args en UNA línea y CORTOS: un `detalle` largo o la URL de un comprobante llenan el
    log justo el día que hay que leerlo."""
    try:
        return json.dumps(args or {}, ensure_ascii=False, default=str)[:200]
    except Exception:  # noqa: BLE001
        return repr(args)[:200]


def _solo_lo_declarado(nombre: str, args: dict) -> dict:
    """Los argumentos del modelo, RECORTADOS a lo que el schema de esa tool declara.

    🔴 EL AGUJERO QUE TAPA (auditoría 2026-08-21, y estaba acotado a UNA tool — la del dinero).
    `ejecutar_tool` hacía `fn(session, telefono, **args)` con los args TAL CUAL los manda el LLM, y
    ningún schema lleva `additionalProperties: false` (0 de 12). Se comparó firma por firma contra
    lo declarado, y solo `registrar_comprobante` acepta parámetros que su schema NO enseña:

        avisar · comprobante_media_id · comprobante_url · monto_leido

    **`monto_leido` es el monto que la VISIÓN leyó del comprobante**: el modelo podía FABRICARLO
    sin que ninguna visión hubiera mirado la imagen. Y `avisar=False` habría registrado un pago sin
    avisarle a la dueña. Las otras once tools estaban limpias.

    Va aquí y no en los schemas a propósito: `additionalProperties: false` es una SUGERENCIA al
    proveedor (y cambia el strict mode del ruteo), mientras que esto lo IMPIDE. Es la doctrina del
    repo: el prompt sugiere, el código impide.

    ⚠️ Y no le arranca el brazo a nadie, que era el riesgo: el worker de visión llama a
    `registrar_comprobante` **directo** (`tasks.py`, import de la función), no por esta puerta; y
    las redes de seguridad que sí entran por aquí (`pedir_ayuda`, `enviar_catalogo`,
    `enviar_fotos_producto`) solo usan parámetros declarados. Verificado antes de tocar.
    """
    declarados = _PARAMS_DECLARADOS.get(nombre)
    if declarados is None:          # tool sin schema: no hay nada contra lo que recortar
        return args or {}
    limpios = {k: v for k, v in (args or {}).items() if k in declarados}
    descartados = sorted(set(args or {}) - declarados)
    if descartados:
        logger.warning(
            "ARGS NO DECLARADOS: el modelo le pasó %s a %s y se DESCARTARON (schema: %s)",
            descartados, nombre, sorted(declarados),
        )
    return limpios


async def ejecutar_tool(nombre: str, args: dict, telefono: str, session_factory=None):
    fn = _DISPATCH.get(nombre)
    if fn is None:
        logger.warning("TOOL DESCONOCIDA: el modelo llamó a %r para %s", nombre, telefono)
        return {"error": f"herramienta desconocida: {nombre}"}
    if session_factory is None:
        session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            return await fn(session, telefono, **_solo_lo_declarado(nombre, args))
        except Exception as e:  # noqa: BLE001 — devolver el error al LLM para que se recupere
            # 🔴 ESTE `except` NO ESCRIBÍA NI UNA LÍNEA, y por eso el sistema podía fallar MUDO.
            # Un timeout de BD dentro de `pedir_ayuda` salía por aquí como un `{"error": ...}`
            # que SOLO VEÍA EL MODELO: no había Intervencion, no salía el WhatsApp a la dueña, el
            # chat no quedaba pausado, y el bot igual le decía al cliente "eso te lo confirmo
            # enseguida". Una semana de mensajes mudos empezó así.
            # El `{"error": ...}` SE QUEDA (el modelo tiene que poder recuperarse: un TypeError
            # por args mal formados es justo el caso en que devolvérselo es lo correcto). Y como
            # NINGUNA herramienta devuelve `error` como resultado normal (los 'no encontrado' usan
            # `{"enviadas": 0, …}` o `{"ok": False, …}`), ese shape es señal 100% fiable de que la
            # tool REVENTÓ — de ahí se cuelgan las redes de arriba. Lo que se SUMA es el testigo.
            (logger.error if nombre in _TOOLS_CRITICAS else logger.warning)(
                "TOOL REVENTÓ: %s(%s) para %s → %s: %s",
                nombre, _args_para_log(args), telefono, type(e).__name__, e,
                exc_info=True,
            )
            return {"error": str(e)}
