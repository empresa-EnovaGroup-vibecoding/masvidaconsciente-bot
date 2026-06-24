"""Las 5 herramientas del agente.

El número de teléfono del cliente se inyecta server-side (desde el contexto del
webhook) — el LLM nunca lo ve ni lo puede falsificar.
"""
import json
import logging
import math
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.models import (
    CatalogoPdf,
    Cliente,
    Conocimiento,
    Configuracion,
    Pago,
    Pedido,
    Producto,
    ProductoMedia,
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
            "description": "Lista productos en TEXTO. Cuando el cliente nombra un TIPO o producto (pan, quesillo, galleta, torta...), USA el parámetro `busqueda` con esa palabra para traer SOLO eso (ej. 'pan' trae solo los panes, NO toda la panadería). Usa `categoria` solo si pide una categoría completa explícita. Para ver TODO / 'qué tienen' / recomendaciones, usa enviar_catalogo (PDF), no esta.",
            "parameters": {
                "type": "object",
                "properties": {
                    "busqueda": {
                        "type": "string",
                        "description": "Palabra o tipo que pide el cliente (ej. 'pan', 'quesillo', 'galleta'). Trae los productos cuyo nombre se parece, tolerando errores de escritura y acentos (ej. 'galetas' o 'limon' igual encuentran).",
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
                                "producto": {"type": "string"},
                                "cantidad": {"type": "integer"},
                            },
                            "required": ["producto", "cantidad"],
                        },
                    },
                    "notas": {"type": "string", "description": "Notas del pedido (opcional)"},
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
            "name": "generar_datos_pago",
            "description": "Genera el cobro: calcula el total en bolivares (tasa BCV del dia) y devuelve los datos de Pago Movil y un `resumen_cobro` listo para copiar. Usala JUSTO despues de registrar_pedido, pasando el `pedido_id` que esa te devolvio (para cobrar ESE pedido, no uno viejo).",
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
                    }
                },
                "required": ["nombre"],
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


def _coincide_busqueda(nombre: str, palabras: list[str]) -> bool:
    """True si CADA palabra buscada es el INICIO de alguna palabra del nombre.
    Así 'pan' calza con 'Pan de Sándwich' (palabra 'Pan') pero NO con 'Empanadas'
    (donde 'pan' solo aparece por dentro). Evita el falso positivo de em-PAN-adas."""
    tokens = nombre.lower().replace(",", " ").replace(".", " ").split()
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
        # 1) Búsqueda PRECISA por prefijo de palabra: 'pan' = panes, NO empanadas.
        palabras = [w for w in busqueda.lower().split() if len(w) > 2]
        exactos = (
            [p for p in productos if _coincide_busqueda(p.nombre, palabras)]
            if palabras
            else productos
        )
        if exactos:
            productos = exactos
        else:
            # 2) Nada calzó (typo o nombre suelto): DIFUSA tolerante a errores/acentos.
            difusos = await _buscar_productos_difuso(
                session, busqueda, limite=12, umbral=0.4, solo_disponibles=True
            )
            if categoria:
                difusos = [p for p in difusos if (p.categoria or "") == categoria.lower()]
            productos = difusos
    if not productos:
        nota = (
            f"no tienes ningún producto que sea '{busqueda}'; dile con sinceridad que de eso no tienes y ofrécele lo que sí hay"
            if busqueda
            else "no hay productos en esa categoría"
        )
        return {"productos": [], "nota": nota}
    return {
        "productos": [
            {
                "nombre": p.nombre,
                "categoria": p.categoria,
                "precio_usd": float(p.precio) if p.precio is not None else "a consultar",
                "presentacion": p.presentacion,
            }
            for p in productos
        ]
    }


async def _buscar_producto(session, nombre: str, solo_disponibles: bool = False):
    """Busca UN producto para emparejarlo en el pedido (camino del DINERO).
    Orden de prioridad, de más a menos preciso:
    1) frase completa por substring; 2) todas las palabras significativas presentes;
    3) último recurso DIFUSO con umbral ALTO (typos), para no cobrar el equivocado.
    Así 'empanada carne mechada' calza con 'Empanada de carne mechada' y 'galetas'
    con 'Galletas New York', pero un parecido lejano NO se acepta (devuelve None)."""
    def base():
        s = select(Producto)
        return s.where(Producto.disponible.is_(True)) if solo_disponibles else s

    prod = (
        await session.execute(base().where(Producto.nombre.ilike(f"%{nombre}%")))
    ).scalars().first()
    if prod is not None:
        return prod
    palabras = [p for p in nombre.lower().split() if len(p) > 2]
    if palabras:
        stmt = base()
        for p in palabras:
            stmt = stmt.where(Producto.nombre.ilike(f"%{p}%"))
        prod = (await session.execute(stmt)).scalars().first()
        if prod is not None:
            return prod
    # Último recurso: difuso con umbral ALTO (0.6). Solo acepta un parecido MUY claro,
    # para que un typo se resuelva pero jamás se cobre un producto distinto.
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
    return {
        "encontrado": True,
        "nombre": prod.nombre,
        "categoria": prod.categoria,
        "descripcion": prod.descripcion,
        "precio_usd": float(prod.precio) if prod.precio is not None else "a consultar",
        "presentacion": prod.presentacion,
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


async def registrar_pedido(session, telefono, items, notas=None):
    cliente = (
        await session.execute(select(Cliente).where(Cliente.telefono == telefono))
    ).scalar_one_or_none()
    if cliente is None:
        session.add(Cliente(telefono=telefono))

    items_pedido = []
    total = Decimal("0")
    for it in items:
        prod = await _buscar_producto(session, it["producto"], solo_disponibles=True)
        if prod is None:
            return {"ok": False, "nota": f"no encontré el producto '{it['producto']}'"}
        cantidad = int(it.get("cantidad", 1))
        subtotal = (prod.precio or Decimal("0")) * cantidad
        total += subtotal
        items_pedido.append(
            {
                "producto": prod.nombre,
                "cantidad": cantidad,
                "precio_unitario": float(prod.precio) if prod.precio is not None else None,
                "presentacion": prod.presentacion,
            }
        )

    pedido = Pedido(cliente_telefono=telefono, items=items_pedido, total=total, notas=notas)
    session.add(pedido)
    await session.commit()
    await session.refresh(pedido)

    # Recibo YA ARMADO (línea por línea + total) para que el bot lo copie tal cual.
    # El total lo calculó el código (arriba), NO el modelo: cero sumas de cabeza.
    lineas = []
    for it in items_pedido:
        pu = it["precio_unitario"]
        subtotal = Decimal(str(pu)) * it["cantidad"] if pu is not None else None
        lineas.append(f"{it['producto']} x{it['cantidad']} = {_fmt_usd(subtotal)}")
    resumen = "\n".join(lineas) + f"\nTotal: {_fmt_usd(total)}"

    return {
        "ok": True,
        "pedido_id": pedido.id,
        "items": items_pedido,
        "total_usd": float(total),
        "resumen": resumen,
        "nota": (
            f"pedido NUEVO #{pedido.id} con SOLO estos items (NO arrastres pedidos anteriores ya cerrados). "
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
    return {"resultados": resultados}


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
    if pedido_id is not None:
        pedido = await session.get(Pedido, int(pedido_id))
        if pedido is None or pedido.cliente_telefono != telefono:
            return {"ok": False, "nota": "no encontre ese pedido para este cliente"}
    else:
        pedido = (
            await session.execute(
                select(Pedido)
                .where(Pedido.cliente_telefono == telefono)
                .order_by(Pedido.created_at.desc())
            )
        ).scalars().first()
        if pedido is None:
            return {"ok": False, "nota": "este cliente todavia no tiene un pedido para cobrar"}

    if pedido.total is None:
        return {"ok": False, "nota": "el pedido no tiene un total definido para cobrar"}

    try:
        tasa = await obtener_tasa_bcv()
    except Exception:  # noqa: BLE001
        return {"ok": False, "nota": "ahora mismo no puedo calcular el monto en bolivares"}

    monto_usd = Decimal(str(pedido.total))
    monto_bs = (monto_usd * tasa).quantize(Decimal("0.01"))
    # 20% de descuento por pagar en DIVISAS (Zelle, Binance o efectivo en dólares).
    # En Bs (Pago Móvil/transferencia) NO aplica: va el precio completo.
    monto_usd_divisas = (monto_usd * Decimal("0.80")).quantize(Decimal("0.01"))

    pedido.estado = "esperando_pago"
    await session.commit()

    config = {
        f.clave: f.valor
        for f in (await session.execute(select(Configuracion))).scalars().all()
    }

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
    resumen_cobro = (
        f"Son {_fmt_bs(monto_bs)} Bs por Pago Móvil o transferencia, o "
        f"{_fmt_usd(monto_usd_divisas)} en dólares (Zelle, Binance o efectivo), "
        f"ya con el 20% de descuento"
    )

    return {
        "ok": True,
        "pedido_id": pedido.id,
        "monto_usd": float(monto_usd),
        "monto_usd_divisas": float(monto_usd_divisas),
        "tasa_bcv": float(tasa),
        "monto_bs": float(monto_bs),
        "resumen_cobro": resumen_cobro,
        "banco": config.get("pago_movil_banco"),
        "cedula": config.get("pago_movil_cedula"),
        "telefono_pago": config.get("pago_movil_telefono"),
        "titular": config.get("pago_movil_titular"),
        "nota": "presenta el cobro copiando EXACTO `resumen_cobro` (NO recalcules) junto con los datos de Pago Movil; pide la captura del comprobante",
    }


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
    session, telefono, referencia=None, comprobante_media_id=None, comprobante_url=None, avisar=False
):
    """Registra el pago REPORTADO (estado 'reportado'). NO lo confirma: eso lo
    hace la duena desde el dashboard. Amarra al pedido en 'esperando_pago'."""
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
            if d.get("monto_usd"):
                monto_usd = Decimal(str(d["monto_usd"]))
            if d.get("tasa"):
                tasa = Decimal(str(d["tasa"]))
            if d.get("monto_bs"):
                monto_bs = Decimal(str(d["monto_bs"]))
    except Exception:  # noqa: BLE001
        pass

    if monto_bs is None and monto_usd is not None:
        try:
            tasa = await obtener_tasa_bcv()
            monto_bs = (monto_usd * tasa).quantize(Decimal("0.01"))
        except Exception:  # noqa: BLE001
            tasa = None
            monto_bs = None

    pago = Pago(
        pedido_id=pedido.id,
        metodo="pago_movil",
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


async def enviar_fotos_producto(session, telefono, nombre):
    """Envía al cliente las fotos/videos de UN producto por WhatsApp (cuando las pide).
    Usa el link público de R2 que Meta descarga. Si el producto no tiene media cargada,
    lo dice con sinceridad (NUNCA afirmar que se envió algo que no se envió)."""
    from app.services import r2

    logger.info("enviar_fotos_producto LLAMADA: nombre=%r", nombre)
    prod = await _buscar_producto(session, nombre)
    if prod is None:
        logger.info("enviar_fotos_producto: producto %r NO encontrado", nombre)
        return {"enviadas": 0, "nota": f"no encontré el producto '{nombre}'; ofrece los que sí hay"}
    medios = (
        await session.execute(
            select(ProductoMedia)
            .where(ProductoMedia.producto_id == prod.id)
            .order_by(ProductoMedia.orden, ProductoMedia.id)
        )
    ).scalars().all()
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
    for m in medios[:8]:  # tope de seguridad
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
    "ver_pedidos_cliente": ver_pedidos_cliente,
    "generar_datos_pago": generar_datos_pago,
    "registrar_comprobante": registrar_comprobante,
    "enviar_catalogo": enviar_catalogo,
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
