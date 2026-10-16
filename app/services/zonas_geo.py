"""📍 LA ZONA DEL ENVÍO SALE DEL PIN (16-oct, decisión de Maired; SESIONES (53)).

Objetivo de Whuilianny: olvidarse del envío. La clienta manda su ubicación de WhatsApp y Alejandra dice sola
"tu ubicación queda en Barquisimeto Este" y cobra el envío de esa zona (la tarifa del repartidor, la del panel).

En internet no existe un mapa de "Barquisimeto Este/Centro/Oeste": es la forma en que el repartidor organiza
sus precios. Por eso no se dibuja nada. Cada zona lista sus SECTORES en el panel (campo `referencias`) y aquí:
  1. cada sector se ubica UNA vez en OpenStreetMap (Nominatim, gratis) y se guarda en `zonas_entrega.puntos`;
     los que no se encuentran quedan en `sin_ubicar` para que el panel los marque y se corrija el nombre;
  2. el pin de la clienta cae en la zona del sector conocido MÁS CERCANO (distancia en línea recta, código,
     $0, sin IA). A más de `geo_radio_km` de todo sector conocido queda FUERA: no se adivina, decide la dueña.

Nada de esto toca el dinero por su cuenta: el costo del envío lo sigue poniendo `registrar_pedido` a partir del
`zona_id`, y aquí solo se dice CUÁL es la zona correcta.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# La política de uso de Nominatim pide identificarse y no pasar de 1 consulta por segundo.
_AGENTE = "masvida-bot/1.0 (asesora de ventas por WhatsApp)"
_PAUSA_S = 1.1

# El enlace que arma `webhook/parser.py` con el pin ("https://maps.google.com/?q=10.06,-69.29") y los que
# pegan las clientas (".../@10.06,-69.29,17z", "...?q=10.06,-69.29").
_COORDENADAS = re.compile(r"(?:[?&]q=|@)(-?\d{1,2}\.\d+),\s*(-?\d{1,3}\.\d+)")


def sectores_de(referencias: str | None) -> list[str]:
    """Los sectores de una zona tal como los escribe el panel: uno por línea o separados por coma/punto y coma.
    Sin repetidos, en el orden en que se escribieron."""
    vistos: dict[str, str] = {}
    for trozo in re.split(r"[\n,;]+", referencias or ""):
        s = " ".join(trozo.split()).strip(" .")
        if s and s.lower() not in vistos:
            vistos[s.lower()] = s
    return list(vistos.values())


def coordenadas_de(texto: str | None) -> tuple[float, float] | None:
    """La latitud y la longitud de un enlace de Google Maps (el del pin o uno pegado). None si no hay."""
    m = _COORDENADAS.search(texto or "")
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def distancia_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distancia en línea recta entre dos puntos de la Tierra (fórmula del haversine)."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def zona_mas_cercana(punto: tuple[float, float], zonas, radio_km: float) -> dict | None:
    """La zona de delivery cuyo sector ubicado está más cerca del pin, si está a `radio_km` o menos.

    `zonas`: filas de `zonas_entrega` (o dobles) con `id`, `nombre`, `es_retiro`, `disponible` y `puntos`
    ([{"sector", "lat", "lon"}]). Las de retiro y las apagadas no cuentan. None si no hay ninguna cerca."""
    mejor = None
    for z in zonas:
        if getattr(z, "es_retiro", False) or not getattr(z, "disponible", True):
            continue
        for p in getattr(z, "puntos", None) or []:
            try:
                km = distancia_km(punto, (float(p["lat"]), float(p["lon"])))
            except (KeyError, TypeError, ValueError):
                continue
            if mejor is None or km < mejor["km"]:
                mejor = {"zona_id": z.id, "zona": z.nombre, "sector": p.get("sector"), "km": round(km, 2)}
    if mejor is None or mejor["km"] > radio_km:
        return None
    return mejor


async def _buscar(cliente, sector: str, contexto: str, viewbox: str) -> tuple[float, float] | None:
    """Un sector en OpenStreetMap, acotado a la ciudad. None si no aparece o si la consulta falla."""
    try:
        r = await cliente.get(NOMINATIM_URL, params={
            "q": f"{sector}, {contexto}", "format": "json", "limit": 1, "countrycodes": "ve",
            "viewbox": viewbox, "bounded": 1,
        }, headers={"User-Agent": _AGENTE})
        r.raise_for_status()
        datos = r.json()
        if datos:
            return float(datos[0]["lat"]), float(datos[0]["lon"])
    except Exception:  # noqa: BLE001 — un sector sin ubicar no rompe nada: queda en `sin_ubicar`
        logger.warning("zonas_geo: no se pudo buscar el sector %r", sector)
    return None


async def ubicar_sectores(zona, *, cliente=None, contexto: str | None = None, viewbox: str | None = None,
                          pausa_s: float = _PAUSA_S) -> tuple[list[dict], list[str]]:
    """(puntos, sin_ubicar) de una zona: reutiliza los sectores ya ubicados (mismo nombre) y busca solo los
    nuevos, uno por segundo. La zona de retiro no se ubica (no es un envío)."""
    import httpx

    from app.config import get_settings

    s = get_settings()
    contexto = contexto or s.geo_contexto
    viewbox = viewbox or s.geo_viewbox
    if getattr(zona, "es_retiro", False):
        return [], []
    ya = {str(p.get("sector") or "").lower(): p for p in (getattr(zona, "puntos", None) or [])}
    puntos, sin_ubicar = [], []
    propio = cliente is None
    cliente = cliente or httpx.AsyncClient(timeout=15)
    try:
        buscados = 0
        for sector in sectores_de(getattr(zona, "referencias", None)):
            previo = ya.get(sector.lower())
            if previo is not None:
                puntos.append(previo)
                continue
            if buscados:
                await asyncio.sleep(pausa_s)
            buscados += 1
            lugar = await _buscar(cliente, sector, contexto, viewbox)
            if lugar is None:
                sin_ubicar.append(sector)
            else:
                puntos.append({"sector": sector, "lat": lugar[0], "lon": lugar[1]})
    finally:
        if propio:
            await cliente.aclose()
    return puntos, sin_ubicar


async def ubicar_zona(zona_id: int) -> None:
    """Ubica los sectores de una zona y lo guarda. Lo llama la tarea de fondo al guardar la zona en el panel.
    Nunca lanza."""
    from app.models import ZonaEntrega, now_utc
    from app.services.db import get_session_factory

    try:
        factory = get_session_factory()
        async with factory() as session:
            zona = await session.get(ZonaEntrega, zona_id)
            if zona is None:
                return
            puntos, sin_ubicar = await ubicar_sectores(zona)
            zona.puntos = puntos or None
            zona.sin_ubicar = sin_ubicar or None
            zona.puntos_at = now_utc()
            await session.commit()
            logger.info("zonas_geo: zona %s → %d sectores ubicados, %d sin ubicar", zona_id, len(puntos),
                        len(sin_ubicar))
    except Exception:  # noqa: BLE001
        logger.exception("zonas_geo: no se pudo ubicar la zona %s", zona_id)


async def zona_del_pin(session, telefono: str) -> dict | None:
    """La zona del ÚLTIMO pin (o enlace de Maps) que mandó la clienta en esta venta, para la ficha de Alejandra
    y el candado de `registrar_pedido`.

    Devuelve None si no hay pin o si ninguna zona tiene sectores ubicados (entonces todo sigue como antes).
    Si no: {"dentro": True, "zona_id", "zona", "sector", "km"} o {"dentro": False}. Falla ABIERTA (None): sin
    lectura, Alejandra sigue como siempre (saca la zona de la dirección o pregunta)."""
    from sqlalchemy import select

    from app.agent.montos_duena import DIAS_ACUERDO, _desde
    from app.config import get_settings
    from app.models import Mensaje, ZonaEntrega

    try:
        desde = await _desde(session, telefono, DIAS_ACUERDO)
        textos = (await session.execute(
            select(Mensaje.contenido).where(
                Mensaje.cliente_telefono == telefono,
                Mensaje.rol == "user",
                Mensaje.created_at >= desde,
            ).order_by(Mensaje.created_at.desc()).limit(40)
        )).scalars().all()
        punto = next((c for c in (coordenadas_de(t) for t in textos) if c), None)
        if punto is None:
            return None
        zonas = (await session.execute(select(ZonaEntrega))).scalars().all()
        if not any(getattr(z, "puntos", None) for z in zonas if not getattr(z, "es_retiro", False)):
            return None
        cercana = zona_mas_cercana(punto, zonas, get_settings().geo_radio_km)
        return {"dentro": True, **cercana} if cercana else {"dentro": False}
    except Exception:  # noqa: BLE001
        logger.warning("zonas_geo: no se pudo leer la ubicación de %s", telefono)
        return None
