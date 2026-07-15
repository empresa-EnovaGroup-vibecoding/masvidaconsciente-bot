"""Las 12 herramientas del agente.

El número de teléfono del cliente se inyecta server-side (desde el contexto del
webhook) — el LLM nunca lo ve ni lo puede falsificar.
"""
import json
import logging
import math
import mimetypes
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
)
from app.services.db import get_session_factory
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
                                        "Lo que el cliente eligió DENTRO del paquete y que la dueña "
                                        "necesita para cocinar: relleno, masa, sabor o mezcla "
                                        "(ej. '4 de pollo y 4 de carne mechada', 'masa de plátano'). "
                                        "NO cambia el precio."
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
                            "El NÚMERO de la zona, copiado EXACTO del `id_zona` de la lista de "
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
            "name": "info_negocio",
            "description": "Da información del negocio: ubicación, método de pago y redes. Úsala para dudas de ubicación, cómo pagar, etc.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_info",
            "description": "Busca en la base de conocimiento del negocio (lo que la dueña cargó: ingredientes, alergias, conservación/duración, envíos, garantías, dudas frecuentes...). Úsala SIEMPRE que el cliente haga una pregunta general que no sea precio/pedido (ej. '¿tiene huevo?', '¿cuánto dura?', '¿es apto para diabéticos?', '¿hacen envíos?'). Responde SOLO con lo que devuelva; si no trae nada, dilo con sinceridad y no inventes.",
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
            "description": "Envía al cliente las FOTOS y VIDEOS de UN producto por WhatsApp. Llámala SIEMPRE que el cliente pida ver/mostrar un producto: 'muéstrame', 'mándame una foto', '¿tienes foto?', 'quiero verlo', 'una foto para verlo', etc. Es la ÚNICA forma de saber si el producto tiene fotos: NO asumas que no hay sin llamarla primero. Si no tiene fotos cargadas, te avisa para que lo digas con sinceridad. (Para ver el menú/opciones en general usa enviar_catalogo.)",
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


async def _buscar_productos_difuso(
    session, consulta, *, limite=12, umbral=0.3, solo_disponibles=True, con_descripcion=False
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
        "  word_similarity(unaccent(lower(:q)), unaccent(lower(COALESCE(descripcion, ''))))"
        ")"
        if con_descripcion
        else "word_similarity(unaccent(lower(:q)), unaccent(lower(nombre)))"
    )
    extra_where = (
        " OR word_similarity(unaccent(lower(:q)), unaccent(lower(COALESCE(descripcion, '')))) >= :umbral"
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
        rows = (await session.execute(sql, {"q": q, "umbral": umbral, "lim": limite})).all()
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
            "elija cuál. NO agregues otros productos aunque tengan nombre parecido. " + _nota_interno
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
            "cuál quiere antes de registrar, y usa el `id_para_pedir` de ESE tamaño."
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
            "nota": f"no hay un producto que calce exacto con '{nombre}'; ofrece el mas parecido de la lista",
            "productos_disponibles": disponibles,
        }
    vs = (await _tamanos_de(session, [prod.id])).get(prod.id) or []
    tamanos = []
    for v in vs:
        precio = await _precio_efectivo(session, v)
        tamanos.append({
            "id_para_pedir": v.id,
            "tamano": v.presentacion,
            "precio_usd": float(precio) if precio is not None else "el precio de hoy no lo sabes: pide_ayuda",
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
        "precio_usd": tamanos[0]["precio_usd"] if len(tamanos) == 1 else "depende del tamaño: pregúntale cuál quiere",
        "presentacion": (vs[0].presentacion if len(vs) == 1 and vs[0].presentacion != "única" else None),
        "duracion": prod.duracion,
        "se_congela": prod.se_congela,
        "apto_diabeticos": prod.apto_diabeticos,
        "info": prod.info,
        "disponible": prod.disponible,
        "nota": (
            "Responde sobre ESTE producto SOLO con estos datos. Si el cliente pregunta algo "
            "que aquí está vacío/None (ej. duración, si se congela), NO lo inventes ni copies "
            "de otro producto: dile con calidez que lo confirmas con la dueña."
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
        motivo = f"para HOY ya pasó la hora (solo se toman pedidos del mismo día hasta las {corte})"
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


async def _lista_de_zonas(session) -> list[dict]:
    """La lista CERRADA de zonas: el bot elige un `id_zona` de aquí. No puede escribir otro."""
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
            "id_zona": z.id,
            "zona": z.nombre,
            "costo": float(z.costo),
            "es_retiro": z.es_retiro,
            "referencias": z.referencias,
        }
        for z in filas
    ]


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
                    "deduzcas el costo: elige un `id_zona` EXACTO de la lista y vuelve a registrar "
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
                    "pedido COMPLETO."
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
    subtotal_productos = total
    costo_envio = Decimal(str(zona.costo)) if zona is not None else Decimal("0")
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

    # Recibo YA ARMADO (línea por línea + total) para que el bot lo copie tal cual.
    # El total lo calculó el código (arriba), NO el modelo: cero sumas de cabeza.
    #
    # El recibo DICE LA PRESENTACIÓN ("2 paquetes de 8 unidades") a propósito: si el bot se
    # confundió y registró PAQUETES cuando el cliente quería unidades sueltas, el propio
    # cliente lo ve en el recibo y lo canta antes de pagar. Es la red visible del "x4".
    lineas = []
    for it in items_pedido:
        pu = it["precio_unitario"]
        subtotal = Decimal(str(pu)) * it["cantidad"] if pu is not None else None
        linea = f"{it['producto']} x{it['cantidad']}"
        if it.get("presentacion"):
            linea += f" (paquete de {it['presentacion']})"
        if it.get("opciones"):
            linea += f" — {it['opciones']}"
        linea += f" = {_fmt_usd(subtotal)}"
        lineas.append(linea)

    # EL ENVÍO VA EN EL RECIBO, EN SU PROPIA LÍNEA. Sin esto el total no cuadra con las líneas y
    # el cliente NO puede cantar una zona mal elegida (cobrarle Barquisimeto $3 cuando vive en el
    # oeste, que son $5). Es la misma red visible que el "paquete de 8 unidades".
    if pedido.zona_nombre:
        if pedido.costo_envio and Decimal(str(pedido.costo_envio)) > 0:
            lineas.append(f"Envío a {pedido.zona_nombre} = {_fmt_usd(pedido.costo_envio)}")
        else:
            lineas.append(f"{pedido.zona_nombre} — sin costo")

    resumen = "\n".join(lineas) + f"\nTotal: {_fmt_usd(total)}"
    if pedido.entrega_fecha or pedido.entrega:
        # La entrega va en el RECIBO a propósito: el cliente confirma el día ANTES de pagar.
        # Si el bot entendió mal la fecha, el cliente lo canta ahí mismo (por eso la fecha va
        # escrita como la diría una persona: "sábado 18 de julio", no "2026-07-18").
        partes_entrega = []
        if pedido.entrega_fecha:
            partes_entrega.append(_fecha_larga(pedido.entrega_fecha))
        if pedido.entrega:
            partes_entrega.append(pedido.entrega)
        resumen += "\nEntrega: " + ", ".join(partes_entrega)

    return {
        "ok": True,
        "pedido_id": pedido.id,
        "items": items_pedido,
        "total_usd": float(total),
        "resumen": resumen,
        "nota": (
            f"pedido #{pedido.id} {'NUEVO' if nuevo else 'ACTUALIZADO'} con SOLO estos items. "
            "Dile al cliente EXACTAMENTE este `resumen` (cópialo, NO recalcules el total). "
            "Para cobrar, llama a generar_datos_pago con este mismo `pedido_id`."
        ),
    }


async def info_negocio(session, telefono):
    filas = (await session.execute(select(Configuracion))).scalars().all()
    config = {f.clave: f.valor for f in filas}
    return {
        "nombre": config.get("negocio_nombre", "masvidaconsciente"),
        "ubicacion": config.get("negocio_ubicacion", "Cabudare, Venezuela"),
        "pago": config.get("negocio_pago", "Pago Móvil"),
        "instagram": config.get("negocio_instagram", "@masvidaconsciente"),
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
        WHERE unaccent(lower(coalesce(titulo, '') || ' ' || coalesce(contenido, '')))
                  LIKE '%' || unaccent(lower(:q)) || '%'
           OR word_similarity(
                  unaccent(lower(:q)),
                  unaccent(lower(coalesce(titulo, '') || ' ' || coalesce(contenido, '')))
              ) >= :umbral
           OR similarity(unaccent(lower(coalesce(titulo, ''))), unaccent(lower(:q))) >= :umbral
        ORDER BY sim DESC
        LIMIT 4
        """
    )
    try:
        return (await session.execute(sql, {"q": q, "umbral": 0.25})).all()
    except Exception:  # noqa: BLE001 — sin pg_trgm: respaldo por substring simple
        return (
            await session.execute(
                select(Conocimiento.id, Conocimiento.titulo, Conocimiento.contenido)
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
                .where(Conocimiento.embedding.isnot(None))
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
            "confirmas. No confundas un tema con otro."
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
    # 20% de descuento por pagar en DIVISAS (Zelle, Binance o efectivo en dólares).
    # En Bs (Pago Móvil/transferencia) NO aplica: va el precio completo.
    #
    # 🔴 EL DESCUENTO NO TOCA EL FLETE (fuga encontrada al ATACAR el diseño, antes de construirlo):
    # si se aplicara al total, ($20 + $3) × 0,80 = $18,40 ⇒ la dueña estaría **pagando el delivery
    # de su bolsillo** en CADA venta cobrada en dólares ($0,60 en la zona de $3, $1 en la de $5).
    # El descuento es sobre lo que ella produce, no sobre lo que le cuesta el motorizado.
    envio = Decimal(str(pedido.costo_envio or 0))
    productos = monto_usd - envio
    monto_usd_divisas = (productos * Decimal("0.80")).quantize(Decimal("0.01")) + envio

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
    resumen_cobro = (
        f"Por Pago Móvil o transferencia son {_fmt_bs(monto_bs)} Bs (precio completo). "
        f"Si pagas en dólares —Zelle, Binance o efectivo— son {_fmt_usd(monto_usd_divisas)}, "
        f"con el 20% de descuento"
    )

    return {
        "ok": True,
        "pedido_id": pedido.id,
        "monto_usd": float(monto_usd),
        "monto_usd_divisas": float(monto_usd_divisas),
        "tasa_bcv": float(tasa),
        "monto_bs": float(monto_bs),
        "resumen_cobro": resumen_cobro,
        "banco": (pm.banco if pm else None) or config.get("pago_movil_banco"),
        "cedula": (pm.cedula if pm else None) or config.get("pago_movil_cedula"),
        "telefono_pago": (pm.telefono if pm else None) or config.get("pago_movil_telefono"),
        "titular": (pm.titular if pm else None) or config.get("pago_movil_titular"),
        "metodos_de_pago": metodos_datos,
        "nota": (
            "presenta el cobro copiando EXACTO `resumen_cobro` (NO recalcules). Los datos "
            "de las cuentas están en `metodos_de_pago`: dale al cliente SOLO los del método "
            "que ÉL elija, copiados TAL CUAL (si aún no eligió, pregúntale cómo prefiere "
            "pagar nombrándole los métodos, sin soltar todos los datos). Pide la captura "
            "del comprobante."
        ),
    }


_MOTIVO_TITULO = {
    "precio_del_dia": "💰 Te piden un PRECIO del día",
    "no_se": "❓ El bot no sabe algo",
    "pide_persona": "🙋 El cliente pide hablar con una persona",
    "reclamo": "⚠️ El cliente está RECLAMANDO",
}


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
    # Lo pausa EL BOT (está escalando a la humana), NO la dueña. La diferencia es crítica:
    # con 'bot', su último mensaje al cliente ("dame un momentito, te confirmo") SÍ sale;
    # con 'dueña', el bot se calla del todo. Ver migración 020.
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

    # 3) Un solo aviso vivo por chat: si ya hay uno PENDIENTE, no la inundamos.
    ya_hay = (
        await session.execute(
            select(Intervencion.id).where(
                Intervencion.cliente_telefono == telefono,
                Intervencion.estado == "pendiente",
            ).limit(1)
        )
    ).scalar_one_or_none()

    if ya_hay is None:
        session.add(
            Intervencion(
                cliente_telefono=telefono,
                motivo=motivo,
                detalle=detalle or None,
                mensaje_cliente=ultimo,
            )
        )
    await session.commit()

    # 4) El ping a la dueña. Si no sale, NO rompe nada: el aviso ya está en el panel.
    if ya_hay is None:
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


async def _avisar_intervencion(session, telefono, motivo, detalle, mensaje_cliente) -> None:
    """Le manda a la dueña el 'el bot te necesita' por WhatsApp. Best-effort: si no hay
    número configurado o Meta rechaza (ventana de 24h), se loguea y ya — el aviso vive
    en la bandeja del panel, que nunca falla."""
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
    try:
        await enviar_texto(destino, cuerpo)
    except Exception:  # noqa: BLE001 — un aviso que falla no puede tumbar el turno
        logger.exception("pedir_ayuda: no se pudo avisar por WhatsApp; queda en el panel")


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
            # ⚠️ EL MISMO DESCUENTO QUE EN `generar_datos_pago`, Y POR EL MISMO MOTIVO: el 20% NO
            # toca el flete. Si aquí se calculara sobre el total y allá sobre los productos, el
            # comprobante del cliente NO CALZARÍA con lo cobrado y el pago quedaría marcado como
            # "no cuadra" en cada venta con delivery pagada en dólares.
            _envio = Decimal(str(getattr(pedido, "costo_envio", 0) or 0))
            en_divisas = (
                ((monto_usd - _envio) * Decimal("0.80")).quantize(Decimal("0.01")) + _envio
            )
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
    from app.services.meta_client import wa_message_id

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

    # El archivo lo guarda y lo SIRVE el bot (su propia URL pública), no el worker.
    # Worker y bot no comparten disco, así que aquí NO revisamos el archivo local:
    # basta el flag en BD, y Meta descarga el PDF de la URL pública del bot.
    from app.services.meta_client import enviar_documento

    try:
        resp = await enviar_documento(telefono, link, "Catalogo.pdf")
    except Exception:  # noqa: BLE001
        return {"ok": False, "nota": "no se pudo enviar el catalogo PDF; usa ver_catalogo (texto)"}
    # El catálogo que el cliente recibió ahora SÍ aparece en el chat interno de la dueña.
    await _guardar_media_saliente(
        telefono=telefono,
        tipo="document",
        contenido="(catálogo en PDF)",
        url=link,
        respuesta=resp,
    )
    return {"ok": True, "nota": "catalogo PDF enviado al cliente; confirmaselo con calidez"}


async def enviar_fotos_producto(session, telefono, nombre, variante_id=None):
    """Envía al cliente las fotos/videos de UN producto por WhatsApp (cuando las pide).

    Con `variante_id` manda PRIMERO las de ESE tamaño (si piden la kombucha de 700ml, la de
    700ml — antes mandaba siempre la de 350ml porque eran dos productos y el buscador devolvía
    el primero) y completa con las NEUTRAS (las que no tienen tamaño asignado).

    Usa el link público de R2 que Meta descarga. Si el producto no tiene media cargada, lo dice
    con sinceridad (NUNCA afirmar que se envió algo que no se envió)."""
    from app.services import r2

    logger.info("enviar_fotos_producto LLAMADA: nombre=%r variante_id=%r", nombre, variante_id)
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
    if variante is not None and variante.producto_id == prod.id:
        # Primero las de ESE tamaño; luego las neutras (las que no tienen tamaño asignado).
        # Las de OTRO tamaño NO se mandan: enviar la de 350ml cuando piden la de 700ml es
        # exactamente el error que estamos arreglando.
        del_tamano = [m for m in todos if m.variante_id == variante.id]
        neutras = [m for m in todos if m.variante_id is None]
        medios = del_tamano + neutras
    else:
        medios = todos
    logger.info(
        "enviar_fotos_producto: producto=%s id=%s media=%d r2_config=%s",
        prod.nombre, prod.id, len(medios), r2.configurado(),
    )
    if not medios:
        return {
            "enviadas": 0,
            "nota": (
                f"'{prod.nombre}' no tiene fotos ni videos cargados. Dile con sinceridad que "
                "por ahora no tienes fotos de ese y ofrécele el catálogo o más info; NO digas "
                "que se las enviaste"
            ),
        }
    # 🧪 EL SIMULADOR DEL PANEL (teléfono "__simulador__…") NO tiene un WhatsApp real al que
    # mandar: Meta rechaza el número falso con un 400 y el bot decía "no se pudieron enviar" —
    # haciendo creer a la dueña que las fotos están rotas cuando en WhatsApp real SÍ funcionan.
    # Aquí se SIMULA el envío: cuenta las fotos como enviadas (sin llamar a Meta) y las guarda en
    # el hilo para que la dueña las VEA en el simulador. La cuenta de verdad es a números reales.
    if (telefono or "").startswith("__"):
        for m in medios[:3]:
            url = r2.url_publica(m.clave)
            if url:
                await _guardar_media_saliente(
                    telefono=telefono,
                    tipo="video" if m.tipo == "video" else "image",
                    contenido=f"({'video' if m.tipo=='video' else 'foto'} de {prod.nombre})",
                    url=url, respuesta=None,
                )
        n = min(len(medios), 3)
        return {
            "enviadas": n, "producto": prod.nombre,
            "nota": (
                f"(SIMULADOR) le mostraste {n} foto(s) de '{prod.nombre}'. En WhatsApp real le "
                "llegan de verdad. Coméntale cálido que ahí las tiene y sigue la venta."
            ),
        }

    enviadas = 0
    sin_url = 0
    # Tope de 3 (antes 8): ocho archivos de golpe es una descarga de spam y le baja la calidad
    # al número. LOS VIDEOS CUENTAN dentro del tope.
    if len(medios) > 3:
        logger.info(
            "enviar_fotos_producto: %s tiene %d archivos, se envían los 3 primeros (tope anti-spam)",
            prod.nombre, len(medios),
        )
    for m in medios[:3]:
        url = r2.url_publica(m.clave)
        if not url:
            sin_url += 1
            logger.warning(
                "enviar_fotos_producto: URL vacía (¿falta R2_PUBLIC_URL en el worker?) media=%s",
                m.id,
            )
            continue
        es_video = m.tipo == "video"
        try:
            resp = (
                await enviar_video(telefono, url) if es_video else await enviar_imagen(telefono, url)
            )
            enviadas += 1
            logger.info("enviar_fotos_producto: enviado %s de %s (url=%s)", m.tipo, prod.nombre, url)
        except Exception as e:  # noqa: BLE001 — si una falla, intentamos las demás
            logger.warning("No se pudo enviar media %s de %s: %s", m.id, prod.nombre, e)
            continue
        # 🔴 LA FILA QUE FALTABA. El cliente recibía la foto y la dueña, en su chat interno, no
        # veía NADA: el bot parecía no haberla mandado nunca. Ahora la burbuja existe.
        await _guardar_media_saliente(
            telefono=telefono,
            tipo="video" if es_video else "image",
            contenido=f"({'video' if es_video else 'foto'} de {prod.nombre})",
            url=url,
            respuesta=resp,
        )
    if sin_url and enviadas == 0:
        # R2 sin configurar en el worker: hasta hoy se saltaba en SILENCIO y el bot decía que el
        # producto "no tiene fotos" — mentira: las tiene, pero no se pudieron construir las URLs.
        logger.error(
            "enviar_fotos_producto: %s TIENE %d archivo(s) pero R2_PUBLIC_URL no está puesta: "
            "no se envió ninguno", prod.nombre, sin_url,
        )
    if enviadas == 0:
        return {
            "enviadas": 0,
            "nota": f"no se pudieron enviar las fotos de '{prod.nombre}' ahora; ofrece el catálogo o seguir por texto",
        }
    return {
        "enviadas": enviadas,
        "producto": prod.nombre,
        "nota": (
            f"YA le enviaste {enviadas} archivo(s) de '{prod.nombre}'. Coméntale cálido que ahí "
            "los tiene y sigue la venta. NO digas que vas a enviarlos: ya están enviados"
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
}


async def ejecutar_tool(nombre: str, args: dict, telefono: str, session_factory=None):
    fn = _DISPATCH.get(nombre)
    if fn is None:
        return {"error": f"herramienta desconocida: {nombre}"}
    if session_factory is None:
        session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            return await fn(session, telefono, **args)
        except Exception as e:  # noqa: BLE001 — devolver el error al LLM para que se recupere
            return {"error": str(e)}
