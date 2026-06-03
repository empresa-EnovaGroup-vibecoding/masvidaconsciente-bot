"""Servicio de tasa BCV (conversion USD -> Bs).

Consulta una API JSON configurable (TASA_API_URL, p.ej. Cotizave), cachea el
valor en Redis, y SIEMPRE cae a un respaldo si la API falla: primero la clave
'tasa_manual' de la tabla configuracion, luego TASA_MANUAL_DEFAULT.

Regla de oro: el bot NUNCA se cae por culpa de la tasa. Cada paso esta
envuelto para que un fallo (API, Redis o BD) no rompa el flujo de cobro.
"""
import logging
from decimal import Decimal, InvalidOperation

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.models import Configuracion
from app.services.db import get_session_factory
from app.services.redis_client import get_cache, set_cache

logger = logging.getLogger(__name__)
settings = get_settings()

CACHE_KEY = "cache:tasa:bcv"


def _a_decimal(valor) -> Decimal | None:
    """Convierte a Decimal positivo, o None si no es un numero valido."""
    if valor is None:
        return None
    try:
        d = Decimal(str(valor).strip().replace(",", "."))
    except (InvalidOperation, ValueError, AttributeError):
        return None
    return d if d > 0 else None


def _parsear_tasa(payload: dict) -> Decimal:
    """Extrae la tasa BCV (Bs por USD) del JSON de la API.

    Cubre las formas mas comunes de las APIs de tasa venezolanas (Cotizave /
    BCV API). Si se adopta un endpoint con otra forma, ajustar aqui. Lanza
    ValueError si no encuentra una tasa valida.
    """
    if not isinstance(payload, dict):
        raise ValueError("la respuesta de tasa no es un objeto JSON")

    candidatos = [
        payload.get("bcv"),
        payload.get("usd"),
        payload.get("promedio"),
        payload.get("precio"),
        payload.get("rate"),
        payload.get("value"),
    ]
    # Estructuras anidadas: {"bcv": {"usd": 40.5}} o {"data": {...}}
    for sub in (payload.get("bcv"), payload.get("data"), payload.get("monitors")):
        if isinstance(sub, dict):
            candidatos += [
                sub.get("usd"), sub.get("bcv"), sub.get("price"),
                sub.get("precio"), sub.get("value"), sub.get("promedio"),
            ]

    for c in candidatos:
        tasa = _a_decimal(c)
        if tasa is not None:
            return tasa
    raise ValueError("no se encontro una tasa valida en la respuesta de la API")


async def _tasa_desde_api() -> Decimal:
    url = settings.tasa_api_url
    if not url:
        raise ValueError("TASA_API_URL no esta configurada")
    headers = {}
    if settings.tasa_api_key:
        headers["Authorization"] = f"Bearer {settings.tasa_api_key}"
    async with httpx.AsyncClient(timeout=8) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return _parsear_tasa(resp.json())


async def _tasa_de_respaldo() -> Decimal:
    """Respaldo: clave 'tasa_manual' de configuracion, luego TASA_MANUAL_DEFAULT."""
    candidatos: list[str] = []
    try:
        factory = get_session_factory()
        async with factory() as session:
            fila = (
                await session.execute(
                    select(Configuracion).where(Configuracion.clave == "tasa_manual")
                )
            ).scalar_one_or_none()
            if fila and fila.valor:
                candidatos.append(fila.valor)
    except Exception as e:  # noqa: BLE001 — leer la BD nunca debe romper el cobro
        logger.warning("No se pudo leer tasa_manual de configuracion: %s", e)

    if settings.tasa_manual_default:
        candidatos.append(settings.tasa_manual_default)

    for v in candidatos:
        tasa = _a_decimal(v)
        if tasa is not None:
            return tasa
    raise ValueError(
        "no hay tasa de respaldo: configura 'tasa_manual' en la BD o TASA_MANUAL_DEFAULT"
    )


async def obtener_tasa_bcv() -> Decimal:
    """Devuelve la tasa BCV (Bs por USD) como Decimal.

    Orden de resolucion: cache Redis -> API en vivo -> tasa_manual (BD) ->
    TASA_MANUAL_DEFAULT. Cachea el valor de la API por `tasa_ttl` segundos.

    Solo lanza si NO hay ninguna fuente disponible (mala configuracion); en ese
    caso la herramienta que la llama trata el error con gracia (no tumba al bot).
    """
    # 1) Cache
    try:
        cacheada = await get_cache(CACHE_KEY)
        tasa = _a_decimal(cacheada)
        if tasa is not None:
            return tasa
    except Exception as e:  # noqa: BLE001
        logger.warning("Fallo leyendo cache de tasa: %s", e)

    # 2) API en vivo (y cachear si responde)
    try:
        tasa = await _tasa_desde_api()
        try:
            await set_cache(CACHE_KEY, str(tasa), settings.tasa_ttl)
        except Exception as e:  # noqa: BLE001
            logger.warning("Fallo guardando cache de tasa: %s", e)
        return tasa
    except Exception as e:  # noqa: BLE001
        logger.warning("Tasa de la API no disponible (%s); usando respaldo", e)

    # 3) Respaldo (BD -> default)
    return await _tasa_de_respaldo()
