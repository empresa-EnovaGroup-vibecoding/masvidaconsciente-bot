"""Redis: idempotencia, buffer de mensajes, historial de conversación y locks.

Patrón tomado del sistema de referencia (clínica), simplificado:
- idempotencia: no procesar dos veces el mismo message_id (Meta reenvía)
- buffer: juntar mensajes rápidos del mismo cliente antes de responder
- historial: contexto reciente de la conversación (con TTL)
- lock: que solo un worker procese el buffer de un cliente a la vez
"""
import json
from datetime import datetime, timezone
from functools import lru_cache

import redis.asyncio as redis

from app.config import get_settings

settings = get_settings()


@lru_cache
def _client() -> redis.Redis:
    return redis.from_url(settings.redis_url, decode_responses=True)


# ─── Idempotencia ────────────────────────────────────────────────────

async def ya_procesado(message_id: str) -> bool:
    """True si el message_id ya se vio antes. Marca el id por 24h."""
    creado = await _client().set(f"msg:{message_id}", "1", nx=True, ex=86400)
    return creado is None


# ─── Buffer de mensajes ──────────────────────────────────────────────

async def agregar_a_buffer(telefono: str, texto: str) -> None:
    c = _client()
    await c.rpush(f"buffer:{telefono}", texto)
    await c.expire(f"buffer:{telefono}", 3600)


async def vaciar_buffer(telefono: str) -> list[str]:
    c = _client()
    clave = f"buffer:{telefono}"
    async with c.pipeline(transaction=True) as pipe:
        pipe.lrange(clave, 0, -1)
        pipe.delete(clave)
        mensajes, _ = await pipe.execute()
    return mensajes or []


# ─── Lock de procesamiento ───────────────────────────────────────────

async def adquirir_lock(telefono: str, ttl: int = 120) -> bool:
    creado = await _client().set(f"lock:{telefono}", "1", nx=True, ex=ttl)
    return creado is not None


async def liberar_lock(telefono: str) -> None:
    await _client().delete(f"lock:{telefono}")


# ─── Historial de conversación ───────────────────────────────────────

async def guardar_historial(telefono: str, rol: str, contenido: str) -> None:
    c = _client()
    clave = f"hist:{telefono}"
    await c.rpush(clave, json.dumps({"role": rol, "content": contenido}))
    await c.ltrim(clave, -20, -1)  # solo los últimos 20 turnos
    await c.expire(clave, settings.conversacion_ttl)


async def obtener_historial(telefono: str) -> list[dict]:
    filas = await _client().lrange(f"hist:{telefono}", 0, -1)
    return [json.loads(f) for f in filas]


# ─── Cache generico con TTL ──────────────────────────────────────────
# Usar siempre claves con prefijo 'cache:' (ej. 'cache:tasa:bcv') para no
# chocar con msg:/buffer:/lock:/hist: que comparten la misma base de Redis.

async def get_cache(clave: str) -> str | None:
    """Lee un valor de cache. Devuelve None si no existe o ya expiro."""
    return await _client().get(clave)


async def set_cache(clave: str, valor: str, ttl: int) -> None:
    """Guarda un valor en cache con expiracion (segundos)."""
    await _client().set(clave, valor, ex=ttl)


# ─── Idempotencia del carril de comprobantes (dinero) ────────────────
# Clave separada del carril de texto (msg:). Se marca SOLO tras procesar el
# comprobante con exito, para que un fallo transitorio de descarga no haga que
# el reintento legitimo de Meta se descarte como duplicado y se pierda el pago.

async def comprobante_procesado(message_id: str) -> bool:
    """True si este comprobante ya se proceso con exito antes (solo lectura)."""
    return await _client().get(f"comprob:{message_id}") is not None


async def marcar_comprobante(message_id: str) -> None:
    """Marca el comprobante como procesado con exito (24h)."""
    await _client().set(f"comprob:{message_id}", "1", ex=86400)


# ─── Anti-abuso / tope de gasto ──────────────────────────────────────
# Cuenta los mensajes de un cliente por dia (UTC). Frena bucles o trolls que
# dispararian costo de IA sin control. Los comprobantes NO cuentan (es dinero).

def _hoy() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


async def contar_mensaje_dia(telefono: str) -> int:
    """Incrementa y devuelve cuantos mensajes lleva HOY este cliente."""
    clave = f"abuso:{telefono}:{_hoy()}"
    n = await _client().incr(clave)
    if n == 1:
        await _client().expire(clave, 93600)  # ~26h
    return n


async def aviso_abuso_nuevo(telefono: str) -> bool:
    """True solo la PRIMERA vez del dia que se supera el tope (para avisar 1 sola vez)."""
    creado = await _client().set(
        f"abuso_avisado:{telefono}:{_hoy()}", "1", nx=True, ex=93600
    )
    return creado is not None
