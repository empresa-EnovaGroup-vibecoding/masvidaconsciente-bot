"""💵 EL PRECIO QUE DIO WHUILIANNY MANDA (10-oct, decisión de Maired; SESIONES (48)).

Whuilianny entra al chat a guiar ("el chocolate te lo dejo en 32, nena", por texto o nota de voz) y
después Alejandra sigue la venta. Alejandra YA lee eso (`memoria.mensaje_owner_para_historial`, y
la nota de voz llega transcrita con 🎤), pero hasta hoy el código no la dejaba seguirla: el pedido
salía a precio de catálogo y la red del dinero frenaba un "$32" que nadie del código había dicho.

Este módulo es la ÚNICA fuente de "qué montos dijo ella": los números que aparecen en SUS mensajes
de los últimos días, escritos con cifras ("32", "36$", "son 64") o con palabras, como salen las
transcripciones de sus audios ("treinta y dos", "treinta y seis con cincuenta").

🔒 Sirve de CANDADO, no de permiso amplio: `registrar_pedido` acepta un precio o un total acordado
SOLO si el número está aquí. Lo que propuso la CLIENTA ("¿me lo dejas en 32?") no cuenta si
Whuilianny no lo repitió (Maired, 10-oct): un "ok nena" a veces contesta otra cosa. Sin IA, $0.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import timedelta
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

# Cuánto hacia atrás se mira: lo mismo que el bloque del pedido acordado (14 días).
DIAS_ACUERDO = 14

_UNIDADES = {
    "cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12,
    "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17,
    "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiun": 21, "veintiuno": 21,
    "veintiuna": 21, "veintidos": 22, "veintitres": 23, "veinticuatro": 24, "veinticinco": 25,
    "veintiseis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29,
    "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70,
    "ochenta": 80, "noventa": 90, "cien": 100, "ciento": 100, "doscientos": 200,
    "doscientas": 200, "trescientos": 300, "trescientas": 300, "cuatrocientos": 400,
    "cuatrocientas": 400, "quinientos": 500, "quinientas": 500, "seiscientos": 600,
    "seiscientas": 600, "setecientos": 700, "setecientas": 700, "ochocientos": 800,
    "ochocientas": 800, "novecientos": 900, "novecientas": 900,
}


def _sin_acentos(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in base if not unicodedata.combining(c)).lower()


def _leer_grupo(palabras: list[str], i: int) -> tuple[int | None, int]:
    """Lee un número escrito con palabras desde `palabras[i]`. Devuelve (valor, siguiente índice);
    (None, i) si ahí no empieza ningún número."""
    valor, actual, leyo = 0, 0, False
    while i < len(palabras):
        p = palabras[i]
        if p in _UNIDADES:
            actual += _UNIDADES[p]
            leyo = True
            i += 1
        elif p == "mil":
            valor += (actual or 1) * 1000
            actual, leyo = 0, True
            i += 1
        elif p == "y" and leyo and i + 1 < len(palabras) and palabras[i + 1] in _UNIDADES:
            i += 1  # "treinta y dos": la "y" une decenas con unidades
        else:
            break
    return (valor + actual, i) if leyo else (None, i)


def numeros_en_palabras(texto: str) -> set[float]:
    """Los montos escritos con palabras: "treinta y dos" → 32, "treinta y seis con cincuenta" →
    36.5, "veinte y medio" → 20.5, "mil doscientos" → 1200. Es como salen las transcripciones."""
    palabras = re.findall(r"[a-zñ]+", _sin_acentos(texto))
    encontrados: set[float] = set()
    i = 0
    while i < len(palabras):
        valor, j = _leer_grupo(palabras, i)
        if valor is None:
            i += 1
            continue
        monto = float(valor)
        if j + 1 < len(palabras) and palabras[j] == "con":
            centavos, k = _leer_grupo(palabras, j + 1)
            if centavos is not None and centavos < 100:
                monto += centavos / 100
                j = k
        elif j + 1 < len(palabras) and palabras[j] == "y" and palabras[j + 1] in ("medio", "media"):
            monto += 0.5
            j += 2
        encontrados.add(round(monto, 2))
        i = j
    return encontrados


def montos_dichos(*textos: str) -> set[float]:
    """Todos los montos de unos textos de la dueña: con cifras (las dos lecturas del formato
    venezolano, como `agent._numeros_de`) y con palabras. Generoso a propósito: un número que ella
    escribió y no se lee es una venta frenada; uno de más solo autoriza algo que ella sí dijo."""
    from app.agent.agent import _numeros_de  # perezoso: agent importa este módulo

    montos: set[float] = set()
    for t in textos:
        montos |= _numeros_de(t) | numeros_en_palabras(t)
    return montos


def textos_de_la_duena_en_historial(historial) -> list[str]:
    """Lo que escribió (o dijo en audio) la dueña, sacado del historial que ve el modelo: ahí sus
    mensajes van como `assistant` envueltos en la marca de autoría humana (memoria.py)."""
    from app.services.memoria import _FIN_OWNER, _INICIO_OWNER

    textos = []
    for h in historial or []:
        if not isinstance(h, dict) or h.get("role") != "assistant":
            continue
        contenido = str(h.get("content") or "")
        if contenido.startswith(_INICIO_OWNER):
            textos.append(contenido.replace(_INICIO_OWNER, "").replace(_FIN_OWNER, ""))
    return textos


async def montos_de_la_duena(session, telefono: str, dias: int = DIAS_ACUERDO) -> set[float]:
    """Los montos que la dueña dijo en ESTE chat en los últimos `dias`, leídos de la BD (texto y
    notas de voz ya transcritas; la que aún dice "[nota de voz]" no tiene nada que leer).

    Falla CERRADO: si la lectura revienta, devuelve vacío y el candado no deja pasar ningún precio
    acordado (Alejandra releva). Con dinero, ante la duda, decide una persona."""
    from sqlalchemy import select

    from app.models import Mensaje, now_utc
    from app.webhook.parser import PLACEHOLDER_AUDIO

    try:
        filas = (
            await session.execute(
                select(Mensaje.contenido).where(
                    Mensaje.cliente_telefono == telefono,
                    Mensaje.rol == "owner",
                    Mensaje.tipo.in_(("text", "audio")),
                    Mensaje.created_at >= now_utc() - timedelta(days=dias),
                )
            )
        ).scalars().all()
    except Exception:  # noqa: BLE001 — sin lectura no hay acuerdo que respetar
        logger.exception("montos_de_la_duena: no se pudo leer el chat de %s", telefono)
        return set()
    return montos_dichos(*(str(c or "") for c in filas if str(c or "").strip() != PLACEHOLDER_AUDIO))


def lo_dijo_la_duena(monto, montos: set[float]) -> Decimal | None:
    """El monto como Decimal si es mayor que cero y ella lo dijo TAL CUAL; si no, None."""
    try:
        m = Decimal(str(monto)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if m <= 0:
        return None
    return m if any(abs(Decimal(str(x)) - m) < Decimal("0.005") for x in montos) else None
