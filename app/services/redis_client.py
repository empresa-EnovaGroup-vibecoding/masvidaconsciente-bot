"""Redis: idempotencia, buffer de mensajes, historial de conversación y locks.

Patrón tomado del sistema de referencia (clínica), simplificado:
- idempotencia: no procesar dos veces el mismo message_id (Meta reenvía)
- buffer: juntar mensajes rápidos del mismo cliente antes de responder
- historial: contexto reciente de la conversación (con TTL)
- lock: que solo un worker procese el buffer de un cliente a la vez
"""
import json
import time
from datetime import UTC, datetime
from functools import lru_cache

import redis.asyncio as redis

from app.config import get_settings

settings = get_settings()


@lru_cache
def _client() -> redis.Redis:
    return redis.from_url(settings.redis_url, decode_responses=True)


def _ahora() -> float:
    """El reloj del buffer, en segundos de PARED (epoch UTC).

    Está aparte por dos motivos. (1) Tiene que ser comparable ENTRE PROCESOS: quien escribe la
    marca es la API (webhook) y quien la lee es el worker, que son dos contenedores distintos —
    `time.monotonic()` no valdría aquí (cada proceso arranca su cuenta donde quiere), aunque sí
    valga en el barredor, que compara consigo mismo. (2) Así los tests inyectan el reloj y salen
    deterministas sin un solo `sleep`.
    """
    return time.time()


# ─── Idempotencia ────────────────────────────────────────────────────

async def ya_procesado(message_id: str) -> bool:
    """True si el message_id ya se vio antes. Marca el id por 24h."""
    creado = await _client().set(f"msg:{message_id}", "1", nx=True, ex=86400)
    return creado is None


# ─── Buffer de mensajes ──────────────────────────────────────────────

async def agregar_a_buffer(telefono: str, texto: str) -> None:
    """Mete el texto en el buffer del cliente y DEJA LAS DOS MARCAS DE TIEMPO del buffer actual.

    Las marcas (`buffer_ts:{telefono}`, un hash con `primero` y `ultimo`) son lo que permite el
    DEBOUNCE de verdad en `_procesar`: `ultimo` se pisa en cada mensaje (la ventana se REINICIA
    con cada uno) y `primero` solo lo escribe el que estrena el buffer (`HSETNX`), que es contra
    lo que se mide el TOPE anti-inanición. Mismo TTL que el buffer y muerte conjunta en
    `vaciar_buffer`: una marca sin buffer haría esperar de más, y un buffer sin marca se procesa
    ya (que es el fallo seguro, ver `_espera_del_buffer`).
    """
    c = _client()
    ahora = _ahora()
    clave, marcas = f"buffer:{telefono}", f"buffer_ts:{telefono}"
    async with c.pipeline(transaction=True) as pipe:
        pipe.rpush(clave, texto)
        pipe.expire(clave, 3600)
        pipe.hsetnx(marcas, "primero", ahora)
        pipe.hset(marcas, "ultimo", ahora)
        pipe.expire(marcas, 3600)
        await pipe.execute()


async def marcas_de_buffer(telefono: str) -> tuple[float, float] | None:
    """(instante del PRIMER mensaje del buffer actual, instante del ÚLTIMO). None si no hay marca.

    None significa "no sé desde cuándo": buffer de antes de este despliegue, Redis reiniciado o
    hash a medias. Quien llama tiene que PROCESAR YA en ese caso — nunca dejar un mensaje colgado
    por un dato ausente.
    """
    primero, ultimo = await _client().hmget(f"buffer_ts:{telefono}", ["primero", "ultimo"])
    try:
        return float(primero), float(ultimo)
    except (TypeError, ValueError):
        return None


async def vaciar_buffer(telefono: str) -> list[str]:
    c = _client()
    clave = f"buffer:{telefono}"
    async with c.pipeline(transaction=True) as pipe:
        pipe.lrange(clave, 0, -1)
        pipe.delete(clave)
        pipe.delete(f"buffer_ts:{telefono}")
        mensajes, _, _ = await pipe.execute()
    return mensajes or []


# ─── Lock de procesamiento ───────────────────────────────────────────

async def adquirir_lock(telefono: str, ttl: int = 120) -> bool:
    creado = await _client().set(f"lock:{telefono}", "1", nx=True, ex=ttl)
    return creado is not None


async def liberar_lock(telefono: str) -> None:
    await _client().delete(f"lock:{telefono}")


# ─── Historial de conversación ───────────────────────────────────────

# Cuántos mensajes recuerda el agente. Vive aquí porque aquí está el `ltrim` que lo aplica, y
# `services/memoria.py` lo importa para que el historial RESCATADO de Postgres tenga exactamente
# el mismo tamaño que el vivo (dos límites distintos = dos comportamientos según de dónde venga).
MAX_TURNOS_HISTORIAL = 20


async def guardar_historial(telefono: str, rol: str, contenido: str) -> None:
    c = _client()
    clave = f"hist:{telefono}"
    await c.rpush(clave, json.dumps({"role": rol, "content": contenido}))
    await c.ltrim(clave, -MAX_TURNOS_HISTORIAL, -1)  # solo los últimos 20 turnos
    await c.expire(clave, settings.conversacion_ttl)


async def obtener_historial(telefono: str) -> list[dict]:
    filas = await _client().lrange(f"hist:{telefono}", 0, -1)
    return [json.loads(f) for f in filas]


async def sembrar_historial(telefono: str, mensajes: list[dict]) -> None:
    """Deja en Redis un historial reconstruido desde Postgres (ver `services/memoria.py`).

    🔴 NO PISA lo que ya hubiera: si entre la lectura y esta llamada alguien escribió en `hist:`,
    la siembra se ABANDONA. Sembrar encima duplicaría los mismos mensajes en la memoria del
    agente, que es peor que no sembrar (el bot leería dos veces el mismo turno). El caso normal
    está protegido por el LOCK del teléfono; esto cubre el borde.

    Se usa el MISMO `ltrim` + `expire` que `guardar_historial`: lo sembrado no es un ciudadano de
    segunda, envejece y se recorta igual que lo vivo.
    """
    if not mensajes:
        return
    c = _client()
    clave = f"hist:{telefono}"
    if await c.exists(clave):
        return
    # `async with … transaction=True`, igual que `agregar_a_buffer`: el context manager devuelve
    # la conexión al pool (sin él se queda colgada) y MULTI/EXEC evita que otro proceso vea la
    # lista a medio sembrar.
    async with c.pipeline(transaction=True) as pipe:
        pipe.rpush(clave, *[json.dumps(m) for m in mensajes])
        pipe.ltrim(clave, -MAX_TURNOS_HISTORIAL, -1)
        pipe.expire(clave, settings.conversacion_ttl)
        await pipe.execute()


async def borrar_memoria(telefono: str) -> None:
    """Borra TODA la caché de conversación de un cliente en Redis: historial, buffer, lock,
    contadores anti-abuso de HOY y el COBRO EN CURSO (`cobro:`, el estado transitorio de un
    cobro a medias). Se usa al 'Borrar chat' desde el panel: el bot arranca realmente limpio
    con esa persona. NO toca el REGISTRO del dinero (los pedidos y pagos viven en Postgres,
    no aquí; los comprobantes en `comprob:`): solo el estado transitorio del chat."""
    await _client().delete(
        f"hist:{telefono}",
        f"buffer:{telefono}",
        f"buffer_ts:{telefono}",  # las marcas mueren con el buffer que describen
        f"lock:{telefono}",
        f"abuso:{telefono}:{_hoy()}",
        f"abuso_avisado:{telefono}:{_hoy()}",
        f"cobro:{telefono}",
    )


async def borrar_cobro(telefono: str) -> None:
    """Tira la COTIZACIÓN en curso de un cliente (`cobro:`), sin tocar nada más de su memoria.

    Se usa cuando el pedido cambia por detrás (la dueña corrige los items desde el panel): los
    montos cacheados son los del pedido ANTERIOR y el próximo comprobante se validaría contra
    ellos. NO toca el registro del dinero (pedidos y pagos viven en Postgres)."""
    await _client().delete(f"cobro:{telefono}")


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


# ─── Candado del RETOMAR (la duena devolvio el chat al bot) ──────────
# Dos clicks seguidos en "Devolver al bot" —o los dos caminos de resume a la vez (el boton de
# la pausa y el de la bandeja)— dispararian DOS respuestas del bot al mismo cliente, encimadas.
# Este candado deja pasar la primera y descarta las demas durante 30s. Mismo patron que
# `ya_procesado`, con clave propia (no comparte prefijo con msg:/comprob:).

async def candado_retomar(telefono: str, ttl: int = 30) -> bool:
    """True si ESTE disparo es el que contesta. False = ya hay uno en curso (doble click)."""
    creado = await _client().set(f"retomar:{telefono}", "1", nx=True, ex=ttl)
    return creado is not None


# ─── Anti-abuso / tope de gasto ──────────────────────────────────────
# Cuenta los mensajes de un cliente por dia (UTC). Frena bucles o trolls que
# dispararian costo de IA sin control. Los comprobantes NO cuentan (es dinero).

def _hoy() -> str:
    return datetime.now(UTC).strftime("%Y%m%d")


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


# ─── Candado antiinundación de avisos a la dueña ─────────────────────
# Mismo patrón que `aviso_abuso_nuevo` (arriba), pero con clave LIBRE: lo usan las redes que
# avisan de una AVERÍA, no de un cliente. Prefijo propio `aviso:` a propósito, como `comprob:`
# y `retomar:`: no es caché (no se lee su valor, solo se compite por crearla).


async def aviso_unico(clave: str, ttl: int) -> bool:
    """True solo la PRIMERA vez en `ttl` segundos. Para que UNA avería no se convierta en una
    LLUVIA de avisos a la dueña.

    🔴 Por qué (auditoría 2026-08-02, SIL-10/SIL-15/SIL-6): las redes nuevas avisan cuando el bot
    no pudo contestar, cuando el panel no aceptó una escritura o cuando la visión no pudo leer un
    comprobante. Todas esas averías son de las que duran RATO (OpenRouter sin saldo, Postgres
    reiniciando): sin candado, una caída de una hora le manda a la dueña un WhatsApp por cada
    mensaje de cada cliente, y el aviso importante se ahoga entre doscientos iguales.

    La clave la elige quien avisa, y ahí está la gracia: `f"sin_respuesta:{telefono}"` avisa una
    vez POR CLIENTE (cada cliente perdido importa), `"panel_incompleto"` a secas avisa una vez
    para TODO el sistema (la avería es una sola, la base).
    """
    return await _client().set(f"aviso:{clave}", "1", nx=True, ex=ttl) is not None
