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

# Fuente por defecto del BCV OFICIAL (Bs por USD). Devuelve {"promedio": <tasa>},
# que _parsear_tasa ya entiende. Se puede sobreescribir con la env var TASA_API_URL.
_FUENTE_BCV_DEFAULT = "https://ve.dolarapi.com/v1/dolares/oficial"


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
    url = settings.tasa_api_url or _FUENTE_BCV_DEFAULT
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


async def _tasa_base() -> Decimal:
    """Tasa BCV CRUDA (Bs por USD), sin margen ni candado.

    Orden: cache Redis -> API en vivo -> tasa_manual (BD) -> TASA_MANUAL_DEFAULT.
    Cachea el valor de la API por `tasa_ttl` segundos.
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


async def _leer_ajustes_tasa() -> tuple[Decimal, Decimal | None, bool]:
    """Lee de la tabla configuracion: margen (%), tasa manual y si el candado
    manual esta activo. Si algo falla, devuelve valores neutros (sin margen,
    sin candado) para no romper el cobro."""
    margen = Decimal("0")
    manual_valor: Decimal | None = None
    manual_activa = False
    try:
        factory = get_session_factory()
        async with factory() as session:
            filas = (
                await session.execute(
                    select(Configuracion).where(
                        Configuracion.clave.in_(
                            ["tasa_margen_pct", "tasa_manual", "tasa_manual_activa"]
                        )
                    )
                )
            ).scalars().all()
            cfg = {f.clave: f.valor for f in filas}
        m = _a_decimal(cfg.get("tasa_margen_pct"))
        if m is not None:
            margen = m
        manual_valor = _a_decimal(cfg.get("tasa_manual"))
        manual_activa = (cfg.get("tasa_manual_activa") or "").strip().lower() in (
            "1", "true", "si", "sí", "on",
        )
    except Exception as e:  # noqa: BLE001 — leer ajustes nunca debe romper el cobro
        logger.warning("No se pudieron leer ajustes de tasa: %s", e)
    return margen, manual_valor, manual_activa


async def obtener_tasa_bcv() -> Decimal:
    """Tasa EFECTIVA que se le cobra al cliente (Bs por USD).

    - Si el CANDADO MANUAL esta activo: usa la tasa fijada por la duena (exacta).
    - Si no: tasa base (BCV) + el margen (%) que la duena configuro.

    Aditivo: sin margen ni candado configurados, devuelve exactamente la tasa
    base de siempre. Solo lanza si no hay ninguna fuente (mala configuracion).
    """
    margen, manual_valor, manual_activa = await _leer_ajustes_tasa()
    if manual_activa and manual_valor is not None:
        return manual_valor
    base = await _tasa_base()
    if margen > 0:
        return (base * (Decimal(1) + margen / Decimal(100))).quantize(Decimal("0.0001"))
    return base


async def estado_tasa() -> dict:
    """Para el panel: tasa base (BCV), margen, candado manual y tasa efectiva."""
    margen, manual_valor, manual_activa = await _leer_ajustes_tasa()
    try:
        base = await _tasa_base()
    except Exception:  # noqa: BLE001
        base = None
    try:
        efectiva = await obtener_tasa_bcv()
    except Exception:  # noqa: BLE001
        efectiva = None
    return {
        "bcv_base": float(base) if base is not None else None,
        "margen_pct": float(margen),
        "manual_valor": float(manual_valor) if manual_valor is not None else None,
        "manual_activa": manual_activa,
        "tasa_efectiva": float(efectiva) if efectiva is not None else None,
    }
