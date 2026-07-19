"""Las 5 herramientas del agente.

El número de teléfono del cliente se inyecta server-side (desde el contexto del
webhook) — el LLM nunca lo ve ni lo puede falsificar.
"""
import json
import logging
import math
import unicodedata
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.models import (
    CatalogoPdf,
    Cliente,
    Conocimiento,
    Configuracion,
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
    session, consulta, *, limite=12, umbral=0.3, solo_disponibles=True
):
    """Búsqueda TOLERANTE a errores de tipeo y acentos (pg_trgm + unaccent).
    Encuentra 'galletas' aunque escriban 'galetas', y 'limón' aunque pongan 'limon'.
    Devuelve productos ordenados del más parecido al menos. Si pg_trgm aún no está
    o la consulta falla, devuelve [] y el llamador cae a la búsqueda exacta de
    siempre: NUNCA rompe el flujo (la búsqueda difusa es una mejora, no un requisito)."""
    q = (consulta or "").strip()
    if len(q) < 2:
        return []
    cond = "AND disponible IS TRUE" if solo_disponibles else ""
    sql = text(
        f"""
        SELECT id, word_similarity(unaccent(lower(:q)), unaccent(lower(nombre))) AS sim
        FROM productos
        WHERE (word_similarity(unaccent(lower(:q)), unaccent(lower(nombre))) >= :umbral
               OR unaccent(lower(nombre)) LIKE '%' || unaccent(lower(:q)) || '%')
              {cond}
        ORDER BY sim DESC
        LIMIT :lim
        """
    )
    try:
        rows = (await session.execute(sql, {"q": q, "umbral": umbral, "lim": limite})).all()
    except Exception:  # noqa: BLE001 — sin pg_trgm: el llamador usa la búsqueda exacta
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


async def ver_catalogo(session, telefono, categoria=None, busqueda=None):
    stmt = select(Producto).where(Producto.disponible.is_(True))
    if categoria:
        stmt = stmt.where(Producto.categoria == categoria.lower())
    productos = (await session.execute(stmt)).scalars().all()
    if busqueda:
        # Filtro DETERMINISTA por nombre + INGREDIENTES: 'empanada plátano' trae SOLO
        # las que de verdad son de plátano (no las Horneadas de yuca). El CÓDIGO decide
        # el match (mira la descripción), no el modelo. 'pan' = panes, NO em-PAN-adas.
        palabras = _palabras_busqueda(busqueda)
        # Los SABORES viven en el TAMAÑO desde la migración 022 (la kombucha de 700ml tiene
        # cúrcuma y flor de jamaica; la de 350ml, no). Se los damos al filtro o "flor de
        # jamaica" no encontraría nada.
        _sab = await _tamanos_de(session, [p.id for p in productos])
        _extra = {
            pid: " ".join(v.sabores or "" for v in vs) for pid, vs in _sab.items()
        }
        exactos = (
            [p for p in productos if _coincide_texto(p, palabras, _extra.get(p.id, ""))]
            if palabras
            else productos
        )
        if exactos:
            productos = exactos
        else:
            # Nada calzó (typo o nombre suelto): DIFUSA tolerante a errores/acentos (por nombre).
            difusos = await _buscar_productos_difuso(
                session, busqueda, limite=12, umbral=0.4, solo_disponibles=True
            )
            if categoria:
                difusos = [p for p in difusos if (p.categoria or "") == categoria.lower()]
            productos = difusos
    if not productos:
        nota = (
            f"no tienes ningún producto que calce con '{busqueda}'; dile con sinceridad que de eso no tienes y ofrécele lo que sí hay"
            if busqueda
            else "no hay productos en esa categoría"
        )
        return {"productos": [], "nota": nota}
    _nota_interno = (
        "El precio_usd y 'trae' (unidades) son INTERNOS: dilos SOLO si el cliente los "
        "pregunta o ya está comprando."
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
    for p in productos:
        vs = por_prod.get(p.id) or []
        tamanos = []
        for v in vs:
            precio = await _precio_efectivo(session, v)
            tamanos.append({
                "id_para_pedir": v.id,
                "tamano": v.presentacion,
                "precio_usd": float(precio) if precio is not None else "el precio de hoy no lo sabes: pide_ayuda",
                "sabores": v.sabores,
                "agotado": (not v.disponible) or (not p.disponible),
            })
        ficha = {
            "nombre": p.nombre,
            "categoria": p.categoria,
            "de_que_es": p.descripcion,
            "tamanos": tamanos,
        }
        if len(tamanos) == 1:
            # Un solo tamaño: se ve IGUAL que siempre (la palabra "tamaño" ni aparece).
            ficha["precio_usd"] = tamanos[0]["precio_usd"]
            ficha["trae"] = None if vs[0].presentacion == "única" else vs[0].presentacion
            ficha["id_para_pedir"] = tamanos[0]["id_para_pedir"]
        salida.append(ficha)
    if any(len(f["tamanos"]) > 1 for f in salida):
        nota += (
            " OJO: alguno tiene VARIOS TAMAÑOS con precios distintos — PREGÚNTALE al cliente "
            "cuál quiere antes de registrar, y usa el `id_para_pedir` de ESE tamaño."
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
    return datetime.now(timezone.utc) - timedelta(hours=4)


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
    }


def _coseno(a, b) -> float:
    """Similitud coseno entre dos vectores (1 = igual significado, 0 = nada que ver)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    punto = sum(x * y for x, y in zip(a, b))
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


async def enviar_catalogo(session, telefono):
    """Envía el catálogo en PDF (guardado en la BD). El cliente lo recibe como
    archivo. Si no hay PDF cargado, avisa para que el agente use ver_catalogo."""
    fila = await session.get(CatalogoPdf, 1)
    if fila is None or not fila.contenido:
        return {"ok": False, "nota": "no hay un catalogo PDF cargado; usa ver_catalogo (texto)"}

    # El archivo lo guarda y lo SIRVE el bot (su propia URL pública), no el worker.
    # Worker y bot no comparten disco, así que aquí NO revisamos el archivo local:
    # basta el flag en BD, y Meta descarga el PDF de la URL pública del bot.
    from app.services.meta_client import enviar_documento

    settings = get_settings()
    link = f"{settings.public_base_url.rstrip('/')}/api/catalogo/archivo"
    try:
        await enviar_documento(telefono, link, "Catalogo.pdf")
        return {"ok": True, "nota": "catalogo PDF enviado al cliente; confirmaselo con calidez"}
    except Exception:  # noqa: BLE001
        return {"ok": False, "nota": "no se pudo enviar el catalogo PDF; usa ver_catalogo (texto)"}


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
    enviadas = 0
    # Tope de 3 (antes 8): ocho archivos de golpe es una descarga de spam y le baja la calidad
    # al número. LOS VIDEOS CUENTAN dentro del tope.
    for m in medios[:3]:
        url = r2.url_publica(m.clave)
        if not url:
            logger.warning(
                "enviar_fotos_producto: URL vacía (¿falta R2_PUBLIC_URL en el worker?) media=%s",
                m.id,
            )
            continue
        try:
            if m.tipo == "video":
                await enviar_video(telefono, url)
            else:
                await enviar_imagen(telefono, url)
            enviadas += 1
            logger.info("enviar_fotos_producto: enviado %s de %s (url=%s)", m.tipo, prod.nombre, url)
        except Exception as e:  # noqa: BLE001 — si una falla, intentamos las demás
            logger.warning("No se pudo enviar media %s de %s: %s", m.id, prod.nombre, e)
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
