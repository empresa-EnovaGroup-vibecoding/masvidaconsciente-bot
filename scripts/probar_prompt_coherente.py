"""BANCO: el PROMPT y el CÓDIGO no se pelean (auditoría 2026-08-02, BLOQUE 5).

Las 7 redes de seguridad son la última pared entre el modelo y el dinero de la dueña. Pero una
red que frena lo que el propio prompt ORDENA decir no protege nada: **mata el turno bueno**. Y
eso no se ve en ningún otro banco, porque todos prueban la red por su lado y el prompt por el
suyo. Aquí se prueban JUNTOS: cada caso es una frase que el bot dice *obedeciendo* una regla, y
lo que se comprueba es que la red la deja pasar — y que la mentira de verdad sigue frenándose.

    "El bot se pelea con su propio prompt."  — el título del bloque 5

No llama a OpenRouter, ni a Meta, ni a Redis, ni a la BD: el `llm` y el `ejecutar` son dobles y
el prompt se sustituye por un texto fijo (mismo patrón que `probar_recibo_visible.py`). Que el
prompt sea fijo AQUÍ es a propósito: así la lista blanca del dinero es la que dice este fichero
y los casos son deterministas. El TEXTO real del prompt se comprueba aparte, en el bloque 7.
"""
from __future__ import annotations

import asyncio

from app.agent import agent as ag
from app.agent.agent import (
    RESPUESTA_SEGURA,
    _afirma_envio_fotos,
    _afirma_pedido_registrado,
    _asegurar_saludo,
    _dinero_inventado,
    _frase_prohibida,
    _pide_fotos,
    _pide_media_explicita,
    _promete_averiguar,
    frase_prohibida_siempre,
)
from app.agent.system_prompt import (
    _REGLAS,
    _aplicar_marcas,
    _filtrar_por_agente,
    _limites_texto,
)

TEL = "__banco_prompt_coherente__"
HISTORIAL = [
    {"role": "user", "content": "hola"},
    {"role": "assistant", "content": "Hola, buenas tardes 💚"},
]
TODAS_LAS_TOOLS = {
    "enviar_fotos_producto", "buscar_info", "info_negocio",
    "ver_pedidos_cliente", "recordar_cliente",
}

_fallos = 0


def check(nombre: str, condicion: bool, detalle: str = "") -> None:
    global _fallos
    if not condicion:
        _fallos += 1
    print(f"   [{'OK ' if condicion else 'MAL'}] {nombre}")
    if not condicion and detalle:
        print(f"          → {detalle}")


# ── Dobles del modelo y de las herramientas ──────────────────────────────────────────
def respuesta(texto: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": texto}}]}


def tool_call(ident: str, nombre: str, args: str = "{}") -> dict:
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": ident, "type": "function",
                    "function": {"name": nombre, "arguments": args},
                }],
            }
        }]
    }


def _guion(*turnos: dict):
    """Un `llm` de mentira que devuelve los turnos en orden."""
    it = iter(turnos)

    async def llm(messages, tools, model):
        return next(it)

    return llm


def _tools(resultados: dict[str, dict], visto: list[str]):
    """Un `ejecutar` de mentira. Apunta cada llamada en `visto`."""

    async def ejecutar(nombre, args, telefono):
        visto.append(nombre)
        return resultados.get(nombre, {"ok": True})

    return ejecutar


# ══════════════════════════════════════════════════════════════════════════════════════
async def bloque_1_catalogo_no_es_una_foto() -> None:
    """[PRM-1] «me puedes mostrar lo que tienen?» — el prompt manda el PDF, la red lo mataba.

    `_pide_fotos` matchea «mostrar», así que el cliente que pide el CATÁLOGO quedaba marcado
    como si hubiera pedido FOTOS. El bot obedecía la regla 58 (enviar_catalogo), decía la
    verdad —"ya te lo envié"— y la red del ENVÍO FANTASMA DE FOTOS lo frenaba: el cliente
    tenía el PDF abierto y recibía "Dame un momentito y te confirmo".
    """
    print("\n1) 📄 EL CATÁLOGO NO ES UNA FOTO (PRM-1)")
    check("«me puedes mostrar lo que tienen?» sigue contando como 'quiere ver'",
          _pide_fotos("me puedes mostrar lo que tienen?"))
    check("…pero NO como una foto pedida con todas sus letras",
          not _pide_media_explicita("me puedes mostrar lo que tienen?"))
    check("«mandame la foto de la torta keto» SÍ es media explícita",
          _pide_media_explicita("mandame la foto de la torta keto"))

    # La red, caso por caso: (bot, pidió_ver, pidió_foto_explícita, catálogo_enviado, ¿frena?)
    casos = [
        ("Listo, ya te lo envié 💚 Dime qué te llama la atención", True, False, True, False),
        ("Ya te lo envié 💚", True, False, True, False),
        ("Ya te lo envié 💚 Ahí tienes el catálogo completo", True, False, False, False),
        # 🔴 EL CASO REAL DEL 2026-07-14 NO SE AFLOJA: pidió FOTO y no salió ninguna.
        ("Ya te la envié hace poco 💚", True, True, False, True),
        ("Ya te la envié hace poco 💚", True, True, True, True),
        ("Ahí las tienes 💚", True, True, False, True),
        # Nombrar la media manda por encima de todo lo demás.
        ("Ahí tienes las fotos de la Torta Keto 💚", False, False, True, True),
        # Sin catálogo enviado, un "ya te lo envié" pelado sigue siendo un envío fantasma.
        ("Ya te lo envié 💚", True, False, False, True),
    ]
    for texto, pidio, media, catalogo, frena in casos:
        got = _afirma_envio_fotos(
            texto, pidio, pidio_media_explicita=media, catalogo_enviado=catalogo,
        )
        check(f"{'FRENA' if frena else 'pasa '} | cat={catalogo!s:5} foto={media!s:5} | {texto[:44]}",
              got is frena, f"dio {got}")

    # Y el turno COMPLETO, con el loop real del modo `uno`.
    visto: list[str] = []
    texto = await ag.responder(
        TEL, "me puedes mostrar lo que tienen?", HISTORIAL,
        llm=_guion(
            tool_call("t1", "enviar_catalogo"),
            respuesta("Listo, ya te lo envié 💚 Cuéntame qué te provoca"),
        ),
        ejecutar=_tools({"enviar_catalogo": {"ok": True}}, visto),
    )
    check("el turno del catálogo LLEGA al cliente (antes moría en RESPUESTA_SEGURA)",
          RESPUESTA_SEGURA not in texto and "ya te lo envié" in texto, repr(texto)[:120])
    check("y NO se le manda a la dueña un aviso falso de envío fantasma",
          "pedir_ayuda" not in visto, str(visto))


# ══════════════════════════════════════════════════════════════════════════════════════
def bloque_2_un_precio_no_es_un_total() -> None:
    """[PRM-2] La red dependía del VERBO, no del hecho.

    La regla 5 del bloque del catálogo ORDENA dar el precio de una, inline, sin llamar a
    ninguna herramienta ("¿a cómo el pan?" → "queda en 25$"). Y `usd_de_herramienta` está
    VACÍO justo entonces, así que la frase se marcaba como "dijo que es un TOTAL y NO lo
    calculó el sistema" — pero solo si estaba conjugada con "queda en" o "sería". Con
    "cuesta", el MISMO hecho pasaba limpio.
    """
    print("\n2) 💵 UN PRECIO DEL CATÁLOGO NO ES UN TOTAL INVENTADO (PRM-2)")
    usd_ok = {25.0, 14.0, 20.0}   # precios reales, inyectados por el catálogo
    for texto, frena, nota in [
        ("El pan keto queda en 25$", False, "🔴 el caso del bloque 5: antes FRENABA"),
        ("El pan keto sería 25$", False, "🔴 ídem, otra conjugación"),
        ("El paquete de empanadas quedaría en $14", False, "🔴 ídem"),
        ("El pan keto cuesta 25$", False, "el MISMO hecho, y este siempre pasó"),
        ("Son $14 el paquete de 8 unidades", False, "precio suelto, autorizado"),
        # Y lo que SÍ es afirmar una suma sigue frenándose (el total lo pone una herramienta):
        ("El total es 25$", True, "se llama a sí mismo un total"),
        ("En total son 25$", True, "ídem"),
        ("Todo junto te queda en 25$", True, "agrega, aunque no diga 'total'"),
        ("Tu pedido queda en 25$", True, "habla del PEDIDO: eso es una suma"),
        ("Por todo son $25", True, "ídem"),
        ("Sumando serían $25", True, "ídem"),
        ("El total en bolívares es de $25 USD", True, "🔴 la frase asesina, intacta"),
    ]:
        got = bool(_dinero_inventado(texto, usd_ok, set(), set()))
        check(f"{'FRENA' if frena else 'pasa '} | {texto:<38} ({nota})", got is frena,
              f"detectados={_dinero_inventado(texto, usd_ok, set(), set())}")

    # Un total INVENTADO que no calza con nada lo sigue cazando el chequeo 1, se diga como se diga.
    check("un monto que no existe se frena igual, sin la palabra 'total'",
          bool(_dinero_inventado("El pan keto queda en 99$", usd_ok, set(), set())))


# ══════════════════════════════════════════════════════════════════════════════════════
async def bloque_3_la_regla_79_se_puede_cumplir() -> None:
    """[PRM-3] «dile que RECIBISTE su pago» — la orden y el castigo, en el mismo sistema.

    El guard existía SOLO en el modo `dos` (`hoja.pago_registrado`). En el modo `uno` —el que
    corre HOY— la red aplicaba SIEMPRE la lista larga, así que **todo comprobante reportado
    por TEXTO** ("ya pagué, ref 004512") caía en la trampa: el bot obedecía la regla 79, la red
    lo frenaba, y el regaño le decía justo lo contrario de lo que el prompt le ordena.
    """
    print("\n3) 🧾 LA REGLA 79 SE PUEDE CUMPLIR — TAMBIÉN EN EL MODO `uno` (PRM-3)")
    frase = "Listo, recibí tu pago 💚 Ya coordino tu entrega. Algo más?"
    check("en la CHARLA, 'recibí tu pago' sigue siendo mentira",
          _frase_prohibida(frase) is not None)
    check("…pero NO es de las que ninguna situación puede volver ciertas",
          frase_prohibida_siempre(frase) is None)

    visto: list[str] = []
    texto = await ag.responder(
        TEL, "ya pagué, la referencia es 004512", HISTORIAL,
        llm=_guion(
            tool_call("t1", "registrar_comprobante", '{"referencia": "004512"}'),
            respuesta(frase),
        ),
        ejecutar=_tools({"registrar_comprobante": {"ok": True, "pago_id": 7}}, visto),
    )
    check("con el comprobante REGISTRADO, el mensaje de la regla 79 SALE",
          "recibí tu pago" in texto and RESPUESTA_SEGURA not in texto, repr(texto)[:120])
    check("y no se escala nada",
          "pedir_ayuda" not in visto, str(visto))

    # SIN comprobante registrado, la misma frase sigue siendo una mentira y NO sale.
    visto = []
    texto = await ag.responder(
        TEL, "hola, ya te pagué?", HISTORIAL,
        llm=_guion(respuesta(frase), respuesta(frase)),
        ejecutar=_tools({"pedir_ayuda": {"ok": True}}, visto),
    )
    check("SIN comprobante registrado, 'recibí tu pago' NO sale",
          texto == RESPUESTA_SEGURA, repr(texto)[:120])

    # Y las mentiras que NINGUNA situación vuelve ciertas siguen frenadas aunque haya comprobante.
    visto = []
    texto = await ag.responder(
        TEL, "ya pagué, ref 004512", HISTORIAL,
        llm=_guion(
            tool_call("t1", "registrar_comprobante", '{"referencia": "004512"}'),
            respuesta("Ya revisé en mi banco y ahí está tu pago 💚"),
            respuesta("Ya revisé en mi banco y ahí está tu pago 💚"),
        ),
        ejecutar=_tools(
            {"registrar_comprobante": {"ok": True, "pago_id": 7}, "pedir_ayuda": {"ok": True}},
            visto,
        ),
    )
    check("🔴 'revisé en mi banco' sigue PROHIBIDO aunque el pago esté registrado",
          texto == RESPUESTA_SEGURA, repr(texto)[:120])
    check("…y esa sí se le avisa a la dueña", "pedir_ayuda" in visto, str(visto))


# ══════════════════════════════════════════════════════════════════════════════════════
async def bloque_4_un_cupo_por_red() -> None:
    """[PRM-12] Tres redes, tres cupos — no uno compartido.

    `corregido` era UNO solo para el dinero, los datos sensibles y las frases prohibidas.
    La segunda red que disparara en el turno no tenía ninguna oportunidad y escalaba de
    frente, aunque cada docstring promete "una oportunidad de corregirse". Y la coincidencia
    es de lo más normal: un mensaje de cobro lleva a la vez el monto y los datos del banco.
    """
    print("\n4) 🎟️ UNA OPORTUNIDAD DE CORREGIRSE **POR RED** (PRM-12)")
    visto: list[str] = []
    texto = await ag.responder(
        TEL, "cuanto es todo?", HISTORIAL,
        llm=_guion(
            respuesta("El total es $37"),                       # red 1: dinero inventado
            respuesta("Ya me llegó tu pago, gracias 💚"),        # red 3: frase prohibida
            respuesta("Cuéntame cuántos paquetes quieres 💚"),   # limpio
        ),
        ejecutar=_tools({"pedir_ayuda": {"ok": True}}, visto),
    )
    check("la 2ª red que dispara TAMBIÉN tiene su oportunidad (antes escalaba de frente)",
          texto != RESPUESTA_SEGURA and "cuántos paquetes" in texto, repr(texto)[:120])
    check("y no hubo aviso a la dueña: nadie llegó a la reincidencia",
          "pedir_ayuda" not in visto, str(visto))

    # Reincidir en la MISMA red sigue costando el turno: el cupo es uno, no infinito.
    visto = []
    texto = await ag.responder(
        TEL, "cuanto es todo?", HISTORIAL,
        llm=_guion(respuesta("El total es $37"), respuesta("Son $37, te lo dejo listo")),
        ejecutar=_tools({"pedir_ayuda": {"ok": True}}, visto),
    )
    check("🔴 reincidir en la misma red sigue frenando el mensaje",
          texto == RESPUESTA_SEGURA, repr(texto)[:120])
    check("…y la dueña se entera", "pedir_ayuda" in visto, str(visto))


# ══════════════════════════════════════════════════════════════════════════════════════
def bloque_5_el_relevo_ve_lo_que_el_prompt_empuja() -> None:
    """[PRM-13] El prompt empujaba justo a la formulación que la red NO veía.

    La regla 67 dice "La HORA no la cierres tú: la coordina la dueña después", así que el bot
    escribe lo natural en español — "La hora te LA confirmo luego" — y `_promete_averiguar`
    devolvía False, porque solo miraba el pronombre `lo`. Paradoja: la redacción de
    intermediaria que la regla 70 PROHÍBE ("le pregunto a la dueña y te aviso enseguida") sí
    disparaba la red. Premiaba lo prohibido y castigaba lo que el prompt ordena.
    """
    print("\n5) 📣 EL RELEVO VE LA FRASE QUE EL PROMPT EMPUJA (PRM-13)")
    for texto, avisa, nota in [
        ("La hora te la confirmo luego", True, "🔴 el caso del bloque 5 (regla 67)"),
        ("La hora te la confirmo más tarde", True, "🔴 ídem"),
        ("Te la confirmo enseguidita 💚", True, "🔴 el diminutivo de esta voz"),
        ("La fecha te la confirmo apenas hable con ella", True, "🔴 ídem"),
        ("Eso te lo confirmo enseguida", True, "el que ya funcionaba"),
        ("Déjame que te la verifique y te digo", True, "pronombre en el 'déjame'"),
        ("Whuilianny te atiende en un momento 💚", True, "prometer una PERSONA"),
        # Frenar de más también rompe la venta: estas NO son promesas de averiguar.
        ("Perfecto, te confirmo el pedido: 2 paquetes de empanadas", False, "confirma el PEDIDO"),
        ("¿Te confirmo entonces 2 paquetes?", False, "pregunta"),
        ("Listo, te lo tengo para el lunes 💚", False, "no promete averiguar nada"),
        ("Te la mando ahorita", False, "manda una foto, no averigua"),
        ("La dueña hace las tortas por encargo", False, "habla DE ella"),
    ]:
        got = _promete_averiguar(texto)
        check(f"{'AVISA' if avisa else 'pasa '} | {texto:<46} ({nota})", got is avisa,
              f"dio {got}")


# ══════════════════════════════════════════════════════════════════════════════════════
def bloque_6_cerrar_sin_mentir() -> None:
    """[PRM-15] «ASUME EL SÍ» chocaba de frente con la red del PEDIDO FANTASMA.
    [PRM-16] `_asegurar_saludo` escribía justo los signos que la regla 92 prohíbe.
    """
    print("\n6) 🤝 CERRAR SIN MENTIR, Y SALUDAR COMO PIDE LA REGLA 92 (PRM-15, PRM-16)")
    check("la regla 48 ya avisa que los verbos de REGISTRO van DESPUÉS de registrar",
          "los verbos de REGISTRO" in _REGLAS and "YA quedó registrado de verdad" in _REGLAS)
    # Las formulaciones que la regla 48 propone NO disparan la red del pedido fantasma…
    for texto in ("Te preparo 3 paquetes, ¿para cuándo te los dejo?",
                  "Con 3 paquetes te llevas 12 empanadas keto 💚 ¿Para cuándo te las preparo?",
                  "Te lo tengo para el sábado, ¿te sirve?"):
        check(f"pasa  | {texto[:52]}", not _afirma_pedido_registrado(texto))
    # …y las que ahora prohíbe siguen frenando (la red no se afloja: se le quita el empujón).
    for texto in ("Perfecto, anoto 3 paquetes.", "Listo, te lo agendé para el martes 💚"):
        check(f"FRENA | {texto[:52]}", _afirma_pedido_registrado(texto))

    saludo = _asegurar_saludo("Tenemos empanadas keto 💚", "hola buenas", "Ana")
    check("🔴 el saludo inyectado NO lleva el '¡' que la regla 92 prohíbe",
          "¡" not in saludo, repr(saludo))
    check("…ni el signo de admiración de cierre", "!" not in saludo, repr(saludo))
    check("pero sigue saludando por su nombre y con la franja horaria",
          "Hola, Ana" in saludo and any(f in saludo for f in ("días", "tardes", "noches")),
          repr(saludo))


# ══════════════════════════════════════════════════════════════════════════════════════
def bloque_7_el_texto_del_prompt_no_se_contradice() -> None:
    """[PRM-8] «Eres una persona» ↔ el código castiga decir que eres una persona.
    [PRM-5]  La regla 89 mandaba copiar de una "HOJA DE HECHOS" que no existía en ningún prompt.
    [PRM-9]  La regla del "dame un segundito" es de VOZ y estaba marcada `!a`.
    [PRM-14] La Voz nunca recibía "LO QUE HOY NO PUEDES HACER".
    [PRM-17] Los "ganchos" que ordenaban las reglas 49/60 no son campos de ninguna ficha.
    [PRM-18] Con `buscar_info` apagada, la regla 70 quedaba gramaticalmente rota.
    """
    print("\n7) 🧩 EL TEXTO DEL PROMPT NO SE CONTRADICE (PRM-5, 8, 9, 14, 17, 18)")
    voz = _aplicar_marcas(_filtrar_por_agente(_REGLAS, "voz"), TODAS_LAS_TOOLS)
    operador = _aplicar_marcas(_filtrar_por_agente(_REGLAS, "operador"), TODAS_LAS_TOOLS)
    uno = _aplicar_marcas(_filtrar_por_agente(_REGLAS, "uno"), TODAS_LAS_TOOLS)

    # PRM-8 — la contradicción literal de identidad.
    check("🔴 el prompt ya NO le ordena 'Eres una persona'",
          "eres una persona" not in _REGLAS.lower())
    check("…y la idea que sí quería (no repetirse) sobrevive",
          "Escribe como una persona" in voz)
    check("la regla que prohíbe jurar que es humana sigue en la VOZ",
          "PROHIBIDO jurar que eres humana" in voz)

    # PRM-5 — el bloque al que apunta la regla 89 tiene que existir de verdad.
    check("🔴 la regla 89 ya no manda copiar de una 'HOJA DE HECHOS' inexistente",
          "HOJA DE HECHOS" not in _REGLAS)
    check("…ahora apunta al bloque que la Voz SÍ recibe ('LO QUE ES VERDAD')",
          "LO QUE ES VERDAD" in voz)

    # PRM-9 — quien puede romper la regla tiene que recibirla.
    check("🔴 la VOZ ya recibe la prohibición del 'dame un segundito'",
          "dame un segundito" in voz)
    check("y el OPERADOR se queda con la parte de ACCIÓN (usar la herramienta de una)",
          "usa info_producto o ver_catalogo" in operador)
    check("el modo `uno` (el que corre hoy) sigue recibiendo las dos mitades",
          "dame un segundito" in uno and "usa info_producto o ver_catalogo" in uno)

    # PRM-17 — los ganchos que ORDENAN las reglas 49 y 60 tienen que ser datos que existan.
    # "antiinflamatorio" y "rinde bien" no son campos de NINGUNA ficha, así que ninguna red los
    # caza y la contradicción con la regla 51 (ANTIINVENCIÓN) se resolvía siempre a favor de
    # AFIRMAR. ⚠️ La regla 88 (sin promesas médicas) conserva "antiinflamatoria" a propósito:
    # ahí va CONDICIONADA ("si la personalidad lo indica"), que es una fuente real y autorizada.
    check("🔴 ninguna regla ordena ya usar el gancho inventado 'rinde bien'",
          "rinde bien" not in _REGLAS.lower())
    check("🔴 'antiinflamator…' solo sobrevive UNA vez, y condicionada a la personalidad",
          _REGLAS.lower().count("antiinflamator") == 1
          and "si la personalidad lo indica" in _REGLAS)
    check("…y las reglas 49 y 60 exigen que el gancho esté en la FICHA o en el CATÁLOGO",
          _REGLAS.count("si es apto para diabéticos") >= 2)

    # PRM-14 — restar una capacidad SIN declararla es peor que no restarla.
    sin_fotos = TODAS_LAS_TOOLS - {"enviar_fotos_producto"}
    limites_voz = _limites_texto(sin_fotos, voz=True)
    check("🔴 la VOZ ya recibe 'LO QUE HOY NO PUEDES HACER'",
          "NO PUEDES enviar fotos" in limites_voz, repr(limites_voz)[:120])
    check("…y sin pedirle que llame a una herramienta que no tiene",
          "pedir_ayuda" not in limites_voz, repr(limites_voz)[:200])
    check("el OPERADOR sí conserva la salida por `pedir_ayuda`",
          "pedir_ayuda" in _limites_texto(sin_fotos))
    check("con todas las capacidades encendidas, el bloque no existe",
          _limites_texto(TODAS_LAS_TOOLS, voz=True) == "")

    # PRM-18 — la regla 70 tiene que leerse bien con la herramienta encendida Y apagada.
    def _caso_2(activas) -> str:
        texto = _aplicar_marcas(_filtrar_por_agente(_REGLAS, "uno"), activas)
        for linea in texto.split("\n"):
            if "(2) NO SABES algo" in linea:
                i, j = linea.find("(2) NO SABES"), linea.find("(3)")
                return linea[i:j].strip()
        return ""

    con, sin = _caso_2(TODAS_LAS_TOOLS), _caso_2(TODAS_LAS_TOOLS - {"buscar_info"})
    check("con `buscar_info` encendida la regla 70 se lee bien",
          "usa primero buscar_info y, si no trae la respuesta, pide ayuda" in con, con)
    check("🔴 y APAGADA también (antes quedaba 'si no trae la respuesta' sin sujeto)",
          "buscar_info" not in sin and sin.endswith("pide ayuda en vez de improvisar;"), sin)


# ══════════════════════════════════════════════════════════════════════════════════════
async def main() -> int:
    print("\n🧩 EL PROMPT Y EL CÓDIGO NO SE PELEAN — bloque 5 de la auditoría 2026-08-02")
    # El prompt real habla con la BD (catálogo, zonas, calendario). Aquí se sustituye por un
    # texto fijo SIN ninguna cifra: así el único dinero autorizado es el que devuelvan los
    # dobles de las herramientas, y los casos del bloque 4 son deterministas.
    original_prompt, original_modelo = ag.construir_partes_prompt, ag.leer_modelo_ia

    async def prompt(nombre=None, telefono=None, **kwargs) -> tuple[str, str]:
        return "reglas de prueba", "estado de prueba"

    async def modelo() -> str:
        return "modelo/de-prueba"

    ag.construir_partes_prompt, ag.leer_modelo_ia = prompt, modelo
    try:
        await bloque_1_catalogo_no_es_una_foto()
        bloque_2_un_precio_no_es_un_total()
        await bloque_3_la_regla_79_se_puede_cumplir()
        await bloque_4_un_cupo_por_red()
    finally:
        ag.construir_partes_prompt, ag.leer_modelo_ia = original_prompt, original_modelo
    bloque_5_el_relevo_ve_lo_que_el_prompt_empuja()
    bloque_6_cerrar_sin_mentir()
    bloque_7_el_texto_del_prompt_no_se_contradice()
    print()
    if _fallos:
        print(f"   🔴 {_fallos} CASO(S) MAL — el bot vuelve a pelearse con su prompt")
    else:
        print("   ✅ EL PROMPT ORDENA Y LAS REDES DEJAN OBEDECER")
    return _fallos


if __name__ == "__main__":
    raise SystemExit(1 if asyncio.run(main()) else 0)
