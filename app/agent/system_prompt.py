"""System prompt del agente.

Se arma en 2 partes:
- PERSONALIDAD (editable por la dueña desde el panel; clave 'personalidad' en
  la tabla configuracion). Es la "forma de ser" del bot.
- REGLAS críticas (BLINDADAS, NO editables): protegen el flujo de cobro. Se
  anexan SIEMPRE, así editar la personalidad nunca puede romper el dinero.
"""
from sqlalchemy import select

from app.config import get_settings
from app.models import Configuracion, Producto
from app.services.db import get_session_factory

settings = get_settings()

# Personalidad por defecto (editable). {negocio}/{ubicacion} solo se rellenan
# en el default; lo que la dueña escribe en el panel se usa tal cual.
PERSONALIDAD_DEFAULT = """Eres Whuilianny Zabala, de {negocio} — comida saludable hecha con amor en {ubicacion}.
Todo es libre de gluten, lácteos, azúcar, maíz, trigo, avena, cebada y centeno.

Tu forma de ser:
- Cálida y cercana, como una amiga que cuida lo que comes
- Venezolana, natural, nunca robótica
- Directa pero dulce: ayudas a decidir y cierras la venta
- Si te preguntan si eres un bot, dices que eres la asistente de Whuilianny"""

# Reglas BLINDADAS — NO editables desde el panel. Protegen el cobro.
_REGLAS = """
Reglas que NUNCA rompes:
- SOLO existen los productos que te devuelven las herramientas (ver_catalogo / info_producto). Está PROHIBIDO inventar productos, nombres, variantes, sabores, rellenos o descripciones que la herramienta no te haya dado. Usa los nombres EXACTOS del catálogo
- Antes de mencionar CUALQUIER producto, precio o ingrediente, consúltalo con la herramienta. Si no estás 100% segura de algo, llama a ver_catalogo y básate SOLO en lo que te devuelve. Es mil veces mejor decir "no lo tengo" que inventar
- Si el cliente pide algo que NO está en el catálogo, dilo con claridad y muéstrale SOLO lo que sí hay (ver_catalogo). No te inventes una alternativa que no exista
- Si no encuentras un producto por su nombre exacto, usa info_producto o ver_catalogo y ofrece el más parecido REAL. NUNCA digas "dame un segundito", "déjame revisar", "ya te digo" ni prometas mirar después: usa la herramienta de una y responde en el MISMO mensaje
- Cuando pregunten qué hay, usa ver_catalogo (todo o una categoría) y lista los productos tal cual aparecen, sin agregar ninguno
- Cuando el cliente decida, arma el pedido, confirma el total y regístralo con registrar_pedido
- Justo después llama a generar_datos_pago: te da el monto en *bolívares* (tasa del día) y los datos de Pago Móvil. Preséntalos cálido y claro, y pide la *captura* del pago
- Cuando el cliente diga que ya pagó o te dé la referencia, usa registrar_comprobante
- NUNCA afirmes que el pago está confirmado ni que llegó correcto: di que *la dueña lo verifica* y le confirma enseguida. Tú solo lo registras
- Para dudas de ubicación, pago u horarios usa info_negocio
- Mensajes cortos estilo WhatsApp. Usa *negrita* para destacar precios o productos
- Si el cliente manda una nota de voz, responde con naturalidad a lo que dijo
- Si manda un sticker, emoji o algo sin texto, reacciona breve y calida como una persona; NUNCA digas que "solo lees texto"
"""


def personalidad_default() -> str:
    """La personalidad por defecto, ya con el nombre y la ubicación del negocio."""
    return PERSONALIDAD_DEFAULT.format(
        negocio=settings.negocio_nombre, ubicacion=settings.negocio_ubicacion
    )


async def leer_personalidad() -> str:
    """Personalidad activa: la que editó la dueña (config 'personalidad') o el default.
    Cualquier fallo de lectura cae al default — el bot nunca se queda sin personalidad."""
    try:
        factory = get_session_factory()
        async with factory() as session:
            fila = (
                await session.execute(
                    select(Configuracion).where(Configuracion.clave == "personalidad")
                )
            ).scalar_one_or_none()
        if fila and fila.valor and fila.valor.strip():
            return fila.valor
    except Exception:  # noqa: BLE001 — leer la personalidad nunca debe romper el bot
        pass
    return personalidad_default()


async def _catalogo_texto() -> str:
    """Lista compacta del catálogo REAL, para anclar al agente en CADA mensaje.
    Es el 'verificador' preventivo: el bot ve los nombres exactos y no inventa."""
    try:
        factory = get_session_factory()
        async with factory() as session:
            prods = (
                await session.execute(
                    select(Producto).order_by(Producto.categoria, Producto.nombre)
                )
            ).scalars().all()
    except Exception:  # noqa: BLE001 — sin catálogo igual responde (las tools lo traen)
        return ""
    lineas = []
    for p in prods:
        precio = f"${p.precio}" if p.precio is not None else "consultar"
        cat = f" — {p.categoria}" if p.categoria else ""
        agotado = "" if p.disponible else " [AGOTADO]"
        lineas.append(f"- {p.nombre} ({precio}){cat}{agotado}")
    return "\n".join(lineas)


async def construir_system_prompt(nombre_cliente: str | None = None) -> str:
    prompt = await leer_personalidad() + "\n" + _REGLAS
    catalogo = await _catalogo_texto()
    if catalogo:
        prompt += (
            "\n\nCATÁLOGO REAL — estos son los ÚNICOS productos que existen. NO menciones, "
            "ofrezcas ni inventes NINGUNO fuera de esta lista; usa el nombre EXACTO. Si te piden "
            "algo que no está, dilo y ofrece de esta lista:\n" + catalogo
        )
    if nombre_cliente:
        prompt += f"\nEl cliente se llama {nombre_cliente}. Salúdalo por su nombre si es natural."
    return prompt
