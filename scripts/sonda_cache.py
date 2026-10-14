"""💾 Sonda de caché: manda la MISMA petición del turno de venta dos veces e imprime los medidores.

Para que Maired (o quien sea) vea con sus ojos el antes y el después de un arreglo de caché
(SESIONES (50), workflow `/claude-api cost-optimize`). Se corre DENTRO del contenedor del bot de
PRUEBAS, que tiene la llave de OpenRouter y la base:

    docker cp scripts/sonda_cache.py <bot>:/app/sonda_cache.py
    docker exec <bot> python sonda_cache.py                                   # 2 llamadas seguidas
    docker exec <bot> python sonda_cache.py --esperar 390                     # la 2ª a los 6,5 min
    docker exec <bot> python sonda_cache.py --esperar 390 --proveedor anthropic
    docker exec <bot> python sonda_cache.py --modelo anthropic/claude-sonnet-5.5 --sin-temperatura

Qué hace y qué NO hace:
  · Arma el prompt REAL (herramientas + parte estable con cache_control + parte dinámica), igual que
    `responder()`, y lo manda con el MISMO cuerpo que `_llamar_openrouter`.
  · Las dos peticiones son idénticas byte a byte (se arma una vez y se reutiliza).
  · NO ejecuta herramientas, NO escribe en la base, NO manda WhatsApp, NO registra en `llamadas_ia`.
  · Gasta dinero real de OpenRouter: ~$0,10-0,15 la llamada que ESCRIBE caché, ~$0,01 la que la LEE.

Los cuatro medidores: entrada total, lectura de caché, escritura de caché, salida (+ el costo que
cobra OpenRouter). Sale con código 1 si la 2ª llamada no leyó NADA de caché.
"""
import argparse
import asyncio
import json
import sys
import time

import httpx

from app.agent.agent import _CACHE_CONTROL, OPENROUTER_URL
from app.agent.system_prompt import construir_partes_prompt, leer_modelo_ia
from app.agent.tools import schemas_para
from app.config import get_settings
from app.services.tools_config import leer_tools_activas


def _medidores(datos: dict) -> dict:
    u = datos.get("usage") or {}
    det = u.get("prompt_tokens_details") or {}
    sal = u.get("completion_tokens_details") or {}
    return {
        "proveedor": datos.get("provider"),
        "modelo": datos.get("model"),
        "entrada_total": u.get("prompt_tokens"),
        "cache_lectura": det.get("cached_tokens"),
        # El nombre del campo de ESCRITURA varía según el proveedor: se imprime el que venga.
        "cache_escritura": det.get("cache_write_tokens", det.get("cache_creation_input_tokens")),
        "salida": u.get("completion_tokens"),
        "salida_pensando": sal.get("reasoning_tokens"),
        "costo_usd": u.get("cost"),
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--esperar", type=int, default=0, help="segundos entre la 1ª y la 2ª llamada")
    ap.add_argument("--proveedor", default="", help="fija el proveedor de OpenRouter (p. ej. 'anthropic')")
    ap.add_argument("--modelo", default="", help="por defecto, el `modelo_ia` del panel")
    ap.add_argument("--sin-temperatura", action="store_true",
                    help="no manda `temperature` (Sonnet 5.5 no acepta otra que la de fábrica)")
    ap.add_argument("--max-tokens", type=int, default=60, help="acota la salida; no toca el prefijo")
    a = ap.parse_args()

    s = get_settings()
    activas = await leer_tools_activas()
    tools = schemas_para(activas)
    estable, dinamico = await construir_partes_prompt("Sonda", "__sonda__", activas=activas)
    modelo = a.modelo or await leer_modelo_ia()
    proveedor = {"require_parameters": True}
    if a.proveedor:
        proveedor.update({"order": [a.proveedor], "allow_fallbacks": False})
    cuerpo = {
        "model": modelo,
        "messages": [
            {"role": "system", "content": [
                {"type": "text", "text": estable, "cache_control": _CACHE_CONTROL},
                {"type": "text", "text": dinamico},
            ]},
            {"role": "user", "content": "hola, tienen torta de chocolate?"},
        ],
        "tools": tools,
        "provider": proveedor,
        "max_tokens": a.max_tokens,
    }
    if not a.sin_temperatura:
        cuerpo["temperature"] = 0.15
    print(f"modelo={modelo}  cache_control={_CACHE_CONTROL}  proveedor={proveedor}  "
          f"temperatura={'(de fábrica)' if a.sin_temperatura else 0.15}")
    print(f"prompt estable: {len(estable):,} caracteres · dinámico: {len(dinamico):,} · "
          f"tools: {len(json.dumps(tools)):,}")

    filas = []
    async with httpx.AsyncClient(timeout=120) as cli:
        for i in (1, 2):
            if i == 2 and a.esperar:
                print(f"… esperando {a.esperar} s antes de la 2ª llamada")
                await asyncio.sleep(a.esperar)
            t0 = time.monotonic()
            r = await cli.post(OPENROUTER_URL, json=cuerpo,
                               headers={"Authorization": f"Bearer {s.openrouter_api_key}"})
            if r.status_code >= 400:
                print(f"\nLLAMADA {i}: HTTP {r.status_code} → {r.text[:400]}")
                return 2
            datos = r.json()
            m = _medidores(datos)
            m["ms"] = int((time.monotonic() - t0) * 1000)
            filas.append(m)
            print(f"\nLLAMADA {i}: " + json.dumps(m, ensure_ascii=False))
            print("  usage crudo: " + json.dumps(datos.get("usage"), ensure_ascii=False))

    leida = filas[1]["cache_lectura"] or 0
    print("\nVEREDICTO:", "✅ la 2ª llamada LEYÓ caché" if leida else "❌ la 2ª llamada NO leyó caché")
    return 0 if leida else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
