"""LEVANTAMIENTO · A2 — transcribe las notas de voz de la DUEÑA a un archivo LOCAL (nunca a la BD).

Por qué existe (SESIONES (31), 15-sep-2026): Whuilianny vende mucho por audio — 871 notas de voz a sus
clientas desde julio — y el sistema transcribe las de las clientas pero no las de ella. Ahí está la
mitad de cómo cierra. Meta borra los audios a las pocas semanas, así que esto corre HOY y de la más
reciente hacia atrás.

Corre DENTRO del contenedor del bot (ahí están la sesión de BD, el token de Meta y la llave de
OpenRouter) y escribe UNA LÍNEA JSON POR AUDIO en stdout, para que el resultado quede en la máquina
de Maired y no en el servidor:

    docker exec -i -w /app -e PYTHONPATH=/app <bot> python scripts/levantamiento/transcribir_audios_duena.py \
        --solo-contar                       # paso 0: cuántos audios por semana, sin gastar
    docker exec -i -w /app -e PYTHONPATH=/app <bot> python scripts/levantamiento/transcribir_audios_duena.py \
        --hechos < ya_hechos.txt > transcripciones.jsonl   # reanudable: ids ya transcritos por stdin

Reutiliza `descargar_media` (meta_client) y `transcribir_audio` (agent): las mismas dos funciones que
usa el worker con los audios de las clientas. Un audio que Meta ya borró se anota como `caducado` y
se sigue: no es un error nuestro. NO escribe en `mensajes` a propósito: el bot lee esa tabla como
historial y cambiarla sería tocar producción fuera de un plan; el relleno hacia adelante va en un PR
aparte (`eco-transcribe-voz-duena`).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter

from sqlalchemy import select

from app.agent.agent import transcribir_audio
from app.models import Mensaje
from app.services.db import get_session_factory
from app.services.meta_client import descargar_media


def _fecha(iso: str):
    """'2026-09-08' → datetime consciente (UTC). La columna es timestamptz: comparar con texto falla."""
    from datetime import UTC, datetime

    d = datetime.fromisoformat(iso)
    return d if d.tzinfo else d.replace(tzinfo=UTC)


async def _audios(desde: str | None, hasta: str | None) -> list[tuple[int, str, str, str]]:
    factory = get_session_factory()
    async with factory() as s:
        q = (
            select(Mensaje.id, Mensaje.media_id, Mensaje.created_at, Mensaje.cliente_telefono)
            .where(Mensaje.rol == "owner", Mensaje.tipo == "audio", Mensaje.media_id.is_not(None))
            .order_by(Mensaje.created_at.desc())
        )
        if desde:
            q = q.where(Mensaje.created_at >= _fecha(desde))
        if hasta:
            q = q.where(Mensaje.created_at < _fecha(hasta))
        filas = (await s.execute(q)).all()
    return [(f[0], f[1], f[2].isoformat(), f[3]) for f in filas]


async def _uno(sem: asyncio.Semaphore, fila: tuple[int, str, str, str]) -> dict:
    mid, media_id, fecha, tel = fila
    base = {"id": mid, "media_id": media_id, "created_at": fecha, "cliente_telefono": tel}
    async with sem:
        try:
            contenido, mime = await descargar_media(media_id)
        except Exception as e:  # noqa: BLE001 — Meta ya lo borró (400/404) o falló la red
            msg = str(e)
            estado = "caducado" if ("400" in msg or "404" in msg or "does not exist" in msg) else "error_descarga"
            return {**base, "estado": estado, "detalle": msg[:160]}
        try:
            texto = await transcribir_audio(contenido, mime or "audio/ogg")
        except Exception as e:  # noqa: BLE001
            return {**base, "estado": "error_transcripcion", "detalle": str(e)[:160]}
        return {**base, "estado": "ok" if texto else "vacio", "bytes": len(contenido), "texto": texto}


async def main(args: argparse.Namespace) -> int:
    filas = await _audios(args.desde, args.hasta)
    if args.solo_contar:
        por_semana = Counter(f[2][:10] for f in filas)
        print(json.dumps({"total": len(filas), "por_dia": dict(sorted(por_semana.items()))}, ensure_ascii=False))
        return 0
    hechos: set[int] = set()
    if args.hechos and not sys.stdin.isatty():
        for linea in sys.stdin:
            linea = linea.strip()
            if linea.isdigit():
                hechos.add(int(linea))
    pendientes = [f for f in filas if f[0] not in hechos]
    if args.max:
        pendientes = pendientes[: args.max]
    print(f"audios: {len(filas)} · ya hechos: {len(hechos)} · a procesar: {len(pendientes)}", file=sys.stderr)
    sem = asyncio.Semaphore(args.concurrencia)
    resumen: Counter = Counter()
    caducados_seguidos = 0
    for i in range(0, len(pendientes), args.concurrencia * 2):
        lote = pendientes[i : i + args.concurrencia * 2]
        resultados = await asyncio.gather(*(_uno(sem, f) for f in lote))
        for r in resultados:
            resumen[r["estado"]] += 1
            print(json.dumps(r, ensure_ascii=False), flush=True)
        caducados_seguidos = caducados_seguidos + len(lote) if all(r["estado"] == "caducado" for r in resultados) else 0
        print(f"  {i + len(lote)}/{len(pendientes)} · {dict(resumen)}", file=sys.stderr)
        # Van de la más reciente a la más vieja: cuando llevamos muchos caducados seguidos, lo que
        # sigue es más viejo todavía. Se corta para no pedirle 500 veces a Meta lo que ya borró.
        if caducados_seguidos >= args.corte_caducados:
            print(f"  corte: {caducados_seguidos} caducados seguidos; lo anterior es más viejo aún", file=sys.stderr)
            break
    print(json.dumps({"_resumen": dict(resumen)}, ensure_ascii=False))
    return 0


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Transcribe las notas de voz de la dueña a stdout (JSONL)")
    p.add_argument("--solo-contar", action="store_true", help="solo cuenta por día, no gasta")
    p.add_argument("--desde", help="ISO, inclusive (ej. 2026-08-25)")
    p.add_argument("--hasta", help="ISO, exclusive")
    p.add_argument("--max", type=int, default=0, help="tope de audios a procesar (0 = todos)")
    p.add_argument("--concurrencia", type=int, default=3)
    p.add_argument("--hechos", action="store_true", help="leer por stdin los ids ya transcritos")
    p.add_argument("--corte-caducados", type=int, default=40, help="parar tras N caducados seguidos")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(_args())))
