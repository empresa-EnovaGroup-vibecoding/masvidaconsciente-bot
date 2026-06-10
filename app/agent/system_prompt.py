"""System prompt del agente.

Se arma en 2 partes:
- PERSONALIDAD (editable por la dueña desde el panel; clave 'personalidad' en
  la tabla configuracion). Es la "forma de ser" del bot.
- REGLAS críticas (BLINDADAS, NO editables): protegen el flujo de cobro. Se
  anexan SIEMPRE, así editar la personalidad nunca puede romper el dinero.
"""
from sqlalchemy import select

from app.config import get_settings
from app.models import Configuracion
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
- No inventes precios ni productos: usa siempre las herramientas para consultar
- Si no encuentras un producto por su nombre exacto, usa info_producto o ver_catalogo y ofrece el más parecido. NUNCA digas "dame un segundito", "déjame revisar", "ya te digo" ni prometas mirar después: ya tienes las herramientas, ÚSALAS de una y responde con el resultado en el MISMO mensaje
- Cuando pregunten qué hay, usa ver_catalogo (todo o una categoría)
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


async def construir_system_prompt(nombre_cliente: str | None = None) -> str:
    prompt = await leer_personalidad() + "\n" + _REGLAS
    if nombre_cliente:
        prompt += f"\nEl cliente se llama {nombre_cliente}. Salúdalo por su nombre si es natural."
    return prompt
