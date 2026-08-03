"""Embeddings vía OpenRouter — la MISMA llave que usa el chat (endpoint /embeddings).

Un "embedding" es una lista de números que captura el SIGNIFICADO de un texto, para
poder buscar por sentido (que "celíaco" encuentre "sin gluten"), no solo por palabras.

Regla de oro: es una MEJORA, no un requisito. Si OpenRouter falla, no hay saldo, o el
modelo no responde, estas funciones devuelven None y el llamador cae a la búsqueda
léxica (pg_trgm). NUNCA deben romper el bot.
"""
import logging
import time

import httpx

from app.config import get_settings
from app.services.telemetria import registrar

logger = logging.getLogger(__name__)
settings = get_settings()

OPENROUTER_EMBEDDINGS_URL = "https://openrouter.ai/api/v1/embeddings"


async def obtener_embeddings(textos: list[str]) -> list[list[float] | None]:
    """Devuelve un embedding por cada texto (en el MISMO orden). Si algo falla,
    devuelve una lista de None del mismo tamaño (el llamador usa solo búsqueda léxica)."""
    limpios = [(t or "").strip() for t in textos]
    if not limpios or not any(limpios):
        return [None] * len(textos)
    # 📊 Telemetría (migración 032). Verificado contra la API real: el endpoint /embeddings
    # también devuelve `usage` con `cost` (6e-08 por una consulta). Son céntimos, pero corren en
    # CADA `buscar_info`, en cada alta de Conocimiento desde el panel y en el backfill de CADA
    # arranque — y hasta hoy nadie sabía cuántas llamadas eran.
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                OPENROUTER_EMBEDDINGS_URL,
                headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
                json={"model": settings.openrouter_model_embedding, "input": limpios},
            )
            resp.raise_for_status()
            crudo = resp.json()
            data = crudo.get("data", [])
        await registrar(
            paso="embedding", modelo_pedido=settings.openrouter_model_embedding, t0=t0, datos=crudo,
        )
    except Exception as e:  # noqa: BLE001 — embeddings es MEJORA; jamás debe tumbar el bot
        logger.warning("Embeddings fallaron (%s): se usará solo búsqueda léxica", e)
        await registrar(
            paso="embedding", modelo_pedido=settings.openrouter_model_embedding,
            t0=t0, ok=False, error=str(e),
        )
        return [None] * len(textos)
    # El API devuelve cada vector con su 'index'; lo respetamos por si vienen desordenados.
    vectores: list[list[float] | None] = [None] * len(limpios)
    for item in data:
        idx = item.get("index", 0)
        emb = item.get("embedding")
        if isinstance(emb, list) and 0 <= idx < len(vectores):
            vectores[idx] = emb
    return vectores


async def obtener_embedding(texto: str) -> list[float] | None:
    """Embedding de UN texto (o None si falla)."""
    return (await obtener_embeddings([texto]))[0]
