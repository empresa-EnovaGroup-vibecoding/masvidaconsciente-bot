"""💵 EL PRECIO QUE DIO WHUILIANNY MANDA (10-oct, decisión de Maired; SESIONES (48)).

Whuilianny entra al chat a guiar ("el chocolate te lo dejo en 32, nena", por texto o nota de voz) y
después Alejandra sigue la venta. Alejandra YA lee eso (`memoria.mensaje_owner_para_historial`, y
la nota de voz llega transcrita con 🎤), pero hasta hoy el código no la dejaba seguirla: el pedido
salía a precio de catálogo y la red del dinero frenaba un "$32" que nadie del código había dicho.

Este módulo es la ÚNICA fuente de "qué montos dijo ella": los números que aparecen en SUS mensajes
de los últimos días, escritos con cifras ("32", "36$", "son 64") o con palabras, como salen las
transcripciones de sus audios ("treinta y dos", "treinta y seis con cincuenta").

🔒 Sirve de CANDADO contra lo INVENTADO, no de intérprete: `registrar_pedido` acepta un precio o un total
acordado SOLO si ese número salió en la conversación de esta venta entre la clienta y Whuilianny (y ella
participó). Qué significa lo que dijeron ("¿me la dejas en 30?" + "ok nena" = sí) lo entiende Alejandra
leyendo la conversación (SESIONES (49)). Vale hasta que esa venta se pague o se entregue. $0.
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


# ─── 🧠 ENTENDER ES DE ALEJANDRA; EL CÓDIGO SOLO IMPIDE INVENTAR (Maired, 10-oct) ─────────────
#
# Hasta #76 el código interpretaba: el número tenía que estar en un mensaje de ella o ella tenía que
# usar frases fijas ("ese precio"), y un "ok nena" no contaba. Eso era encasillar: Whuilianny contesta
# corto ("ok nena", "dale", "sí bb") o por audio, de mil maneras, y ninguna lista las cubre. A QUÉ le
# está respondiendo lo entiende Alejandra, que lee la conversación completa de las dos. Aquí solo se
# comprueba que el número SALIÓ en esa conversación (de la clienta o de ella), y que ella participó.

def montos_de_la_charla(charla) -> set[float]:
    """Los montos de una charla [(rol, texto)] entre la clienta y la dueña: todos los números que
    aparecen en los mensajes de las dos, siempre que la dueña haya escrito al menos una vez. Sin
    ella en la conversación no hay nada "acordado" que seguir: vacío."""
    textos = [t for rol, t in charla if rol in ("user", "owner")]
    if not any(rol == "owner" for rol, _ in charla):
        return set()
    return montos_dichos(*textos)


def charla_del_historial(historial) -> list[tuple[str, str]]:
    """La charla clienta ↔ dueña sacada del historial que ve el modelo: los mensajes de ella van como
    `assistant` envueltos en la marca de autoría humana (memoria.py); los del bot se saltan."""
    from app.services.memoria import _FIN_OWNER, _INICIO_OWNER

    charla: list[tuple[str, str]] = []
    for h in historial or []:
        if not isinstance(h, dict):
            continue
        contenido = str(h.get("content") or "")
        if h.get("role") == "user":
            charla.append(("user", contenido))
        elif h.get("role") == "assistant" and contenido.startswith(_INICIO_OWNER):
            charla.append(("owner", contenido.replace(_INICIO_OWNER, "").replace(_FIN_OWNER, "")))
    return charla


def textos_de_la_duena_en_historial(historial) -> list[str]:
    """Solo lo que escribió (o dijo en audio) la dueña, del historial que ve el modelo."""
    return [t for rol, t in charla_del_historial(historial) if rol == "owner"]


# ─── 📖 LA LECTURA ÚNICA DEL CHAT (la usan el candado y el PUNTO DE PARTIDA) ─────────────────
#
# Vale lo que se habló DESPUÉS del último cierre de una venta de esta clienta (pago confirmado o
# pedido entregado), con tope de 14 días (Maired, 10-oct: "hasta entregar o pagar"). Así un precio
# que ella dio para una venta ya cerrada no se arrastra a la siguiente compra.

async def _desde(session, telefono: str, dias: int):
    """Desde cuándo vale lo que se habló: hace `dias`, o el último cierre si es más reciente."""
    from sqlalchemy import func, select

    from app.models import Pago, Pedido, now_utc

    desde = now_utc() - timedelta(days=dias)
    cierres = [
        (
            await session.execute(
                select(func.max(Pago.updated_at))
                .join(Pedido, Pago.pedido_id == Pedido.id)
                .where(Pedido.cliente_telefono == telefono, Pago.estado == "confirmado")
            )
        ).scalar(),
        (
            await session.execute(
                select(func.max(Pedido.updated_at)).where(
                    Pedido.cliente_telefono == telefono, Pedido.estado == "entregado"
                )
            )
        ).scalar(),
    ]
    return max([desde, *(c for c in cierres if c is not None)])


async def _leer_charla(session, telefono: str, dias: int = DIAS_ACUERDO):
    """[(rol, texto, creado)] de la clienta ('user') y la dueña ('owner') en orden, texto y 🎤, sin
    las notas de voz aún sin transcribir, desde `_desde`. Lanza si la base falla: quien llama decide."""
    from sqlalchemy import select

    from app.models import Mensaje
    from app.webhook.parser import PLACEHOLDER_AUDIO

    desde = await _desde(session, telefono, dias)
    filas = (
        await session.execute(
            select(Mensaje.rol, Mensaje.contenido, Mensaje.created_at)
            .where(
                Mensaje.cliente_telefono == telefono,
                Mensaje.rol.in_(("user", "owner")),
                Mensaje.tipo.in_(("text", "audio")),
                Mensaje.created_at >= desde,
            )
            .order_by(Mensaje.created_at.desc())
            .limit(150)
        )
    ).all()
    return [
        (str(rol), str(contenido or ""), creado)
        for rol, contenido, creado in reversed(filas)
        if str(contenido or "").strip() and str(contenido or "").strip() != PLACEHOLDER_AUDIO
    ]


async def montos_de_la_duena(session, telefono: str, dias: int = DIAS_ACUERDO) -> set[float]:
    """Los montos que valen como dichos por la dueña en ESTE chat (ver `montos_de_la_charla`).

    Falla CERRADO: si la lectura revienta, devuelve vacío y el candado no deja pasar ningún precio
    acordado (Alejandra releva). Con dinero, ante la duda, decide una persona."""
    try:
        filas = await _leer_charla(session, telefono, dias)
    except Exception:  # noqa: BLE001 — sin lectura no hay acuerdo que respetar
        logger.exception("montos_de_la_duena: no se pudo leer el chat de %s", telefono)
        return set()
    return montos_de_la_charla([(rol, texto) for rol, texto, _ in filas])


# ─── 🧭 EL PUNTO DE PARTIDA (10-oct, pregunta de Maired antes de fusionar #76) ─────────────
#
# Alejandra ve solo los últimos 20 mensajes (`MAX_TURNOS_HISTORIAL`). Si lo que Whuilianny acordó
# quedó más atrás, no sabría desde dónde arranca la venta. Aquí van sus últimos mensajes del chat,
# cada uno con lo que la clienta había dicho justo antes, para el bloque de ESTADO DEL CLIENTE. Esas
# mismas líneas las lee la red del dinero (`charla_del_estado`).

MARCA_ELLA = "la persona del negocio: «"
MARCA_CLIENTE = "el cliente: «"
_RECORTE = 300


def _limpio(texto: str) -> str:
    """Una línea, sin las comillas que delimitan el literal, recortada."""
    t = " ".join(str(texto or "").split()).replace("«", '"').replace("»", '"')
    return t if len(t) <= _RECORTE else t[: _RECORTE - 1] + "…"


async def punto_de_partida(session, telefono: str, dias: int = DIAS_ACUERDO, cuantos: int = 4) -> list[str]:
    """Las líneas del PUNTO DE PARTIDA: los últimos `cuantos` mensajes de la dueña en este chat (texto
    o 🎤), cada uno con el mensaje del cliente justo anterior. [] si ella no escribió, si la venta ya
    se cerró o si la lectura falla (sin dato, la sección no sale; el bot sigue con el historial)."""
    try:
        filas = await _leer_charla(session, telefono, dias)
    except Exception:  # noqa: BLE001 — sin lectura no hay sección; el turno sigue
        logger.exception("punto_de_partida: no se pudo leer el chat de %s", telefono)
        return []

    pares: list[tuple[str, str | None, str]] = []
    ultimo_cliente: str | None = None
    for rol, contenido, creado in filas:
        if rol == "user":
            ultimo_cliente = contenido
            continue
        try:
            fecha = (creado - timedelta(hours=4)).strftime("%d/%m")
        except (TypeError, ValueError, AttributeError):
            fecha = ""
        pares.append((fecha, ultimo_cliente, contenido))
        ultimo_cliente = None  # cada pregunta del cliente acompaña a UNA respuesta de ella
    lineas: list[str] = []
    for fecha, del_cliente, de_ella in pares[-cuantos:]:
        cuando = f"{fecha} " if fecha else ""
        if del_cliente:
            lineas.append(f"  · {cuando}{MARCA_CLIENTE}{_limpio(del_cliente)}»")
        lineas.append(f"  · {cuando}{MARCA_ELLA}{_limpio(de_ella)}»")
    return lineas


def charla_del_estado(texto: str) -> list[tuple[str, str]]:
    """La charla del PUNTO DE PARTIDA (parte dinámica del prompt), en orden, como [(rol, texto)]."""
    roles = {MARCA_CLIENTE: "user", MARCA_ELLA: "owner"}
    patron = "(" + re.escape(MARCA_CLIENTE) + "|" + re.escape(MARCA_ELLA) + r")(.*?)»"
    return [(roles[m.group(1)], m.group(2)) for m in re.finditer(patron, texto or "")]


def textos_de_la_duena_en_estado(texto: str) -> list[str]:
    """Solo lo que dijo la dueña según el PUNTO DE PARTIDA."""
    return [t for rol, t in charla_del_estado(texto) if rol == "owner"]


def montos_de_la_duena_en_el_turno(historial, dinamico: str = "") -> set[float]:
    """Los montos que valen como dichos por la dueña según lo que Alejandra tiene delante en ESTE
    turno (su historial y el PUNTO DE PARTIDA), con la misma regla que el candado. Es lo que
    autoriza la red del dinero."""
    return montos_de_la_charla(charla_del_historial(historial)) | montos_de_la_charla(
        charla_del_estado(dinamico)
    )


def lo_dijo_la_duena(monto, montos: set[float]) -> Decimal | None:
    """El monto como Decimal si es mayor que cero y ella lo dijo TAL CUAL; si no, None."""
    try:
        m = Decimal(str(monto)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if m <= 0:
        return None
    return m if any(abs(Decimal(str(x)) - m) < Decimal("0.005") for x in montos) else None
