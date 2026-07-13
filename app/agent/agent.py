"""Agente único con function calling sobre OpenRouter.

Recibe un mensaje del cliente + su historial, decide qué herramientas usar,
las ejecuta, y devuelve la respuesta final en la voz de Whuilianny.
"""
import base64
import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone

import httpx

from app.agent.system_prompt import construir_partes_prompt, leer_modelo_ia
from app.agent.tools import TOOL_SCHEMAS, ejecutar_tool
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
RESPUESTA_SEGURA = "Dame un momentito y te confirmo 😊"


def _sin_acentos(texto: str) -> str:
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


# Frases con las que el agente AFIRMA haber enviado el catálogo (claim declarativo).
_AFIRMA_ENVIO_CATALOGO = (
    "acabo de envi", "acabo de mand",
    "ya te envi", "ya te mand", "ya te paso", "ya te lo",
    "te envie", "te mande", "te lo envie", "te lo mande",
    "aqui te dejo", "aqui tienes",
)


def _afirma_envio_catalogo(texto: str) -> bool:
    """True si el texto AFIRMA haber enviado el catálogo/menú (no si lo ofrece/pregunta)."""
    t = _sin_acentos(texto)
    if "catalogo" not in t and "menu" not in t:
        return False
    return any(frase in t for frase in _AFIRMA_ENVIO_CATALOGO)


async def _asegurar_catalogo(texto: str, catalogo_ok: bool, telefono: str, ejecutar) -> str:
    """Red de seguridad: si el bot DICE que envió el catálogo pero no llamó a la
    herramienta, lo enviamos de verdad (su afirmación se vuelve cierta y el cliente
    SÍ recibe el PDF, además en el orden correcto: PDF primero, texto después).
    Si no hay PDF, evitamos dejar una afirmación falsa."""
    if catalogo_ok or not _afirma_envio_catalogo(texto):
        return texto
    try:
        resultado = await ejecutar("enviar_catalogo", {}, telefono)
    except Exception:  # noqa: BLE001
        resultado = {"ok": False}
    if isinstance(resultado, dict) and resultado.get("ok"):
        return texto  # ahora SÍ se envió: la afirmación es verdad
    # No se pudo enviar (no hay PDF): no dejar una afirmación falsa.
    return "Déjame mostrarte lo que tenemos 😊 ¿Qué estás buscando?"


async def _llamar_openrouter(messages: list, tools: list, model: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            # Temperatura = "dial de libertad". Se mantiene BAJA (0.15) a propósito: probado
            # 2026-07-03 subirla a 0.4/0.5 daba MUY poca variación extra (Haiku converge igual)
            # pero empezaba a fallar el precio cuando lo piden (info_producto devuelve varios
            # campos y a veces omitía el monto) — y NO se rompe el cobro. La naturalidad/variación
            # se logra QUITANDO las frases-ejemplo del prompt (que el modelo copiaba), no subiendo
            # la temperatura. (redactar_mensaje sí usa 0.7 porque ahí no hay tools ni cobro.)
            json={"model": model, "messages": messages, "tools": tools, "temperature": 0.15},
        )
        resp.raise_for_status()
        return resp.json()


async def _llamar_con_fallback(messages: list, llm, modelo: str) -> dict:
    try:
        return await llm(messages, TOOL_SCHEMAS, modelo)
    except Exception as e:  # noqa: BLE001
        logger.warning("Modelo principal (%s) falló (%s), usando fallback", modelo, e)
        return await llm(messages, TOOL_SCHEMAS, settings.openrouter_model_fallback)


_SALUDOS = (
    "hola", "holaa", "buenas", "buenos dias", "buen dia",
    "buenas tardes", "buenas noches", "que tal", "saludos", "epale",
)


def _cliente_saludo(texto: str) -> bool:
    """True si el mensaje del cliente ABRE con un saludo (hola, buenas, etc.)."""
    t = _sin_acentos(texto or "").lstrip(" ¡!¿?")
    return any(t.startswith(s) for s in _SALUDOS)


def _bot_ya_saludo(texto: str) -> bool:
    """True si la respuesta del bot ya empieza con algún saludo (para no duplicarlo)."""
    t = _sin_acentos(texto or "")[:45]
    return any(s in t for s in ("hola", "buenas", "buenos dias", "buen dia", "epale"))


def _es_inicio_conversacion(historial: list | None) -> bool:
    """True si el bot aún no ha hablado en esta conversación (para no re-saludar a media)."""
    if not historial:
        return True
    return not any((m or {}).get("role") == "assistant" for m in historial)


def _pregunta_como_estas(texto: str) -> bool:
    """True si el cliente pregunta '¿cómo estás?' (o similar)."""
    t = _sin_acentos(texto or "")
    return any(p in t for p in ("como estas", "como esta", "como andas", "como te va", "como vas"))


def _bot_respondio_estado(texto: str) -> bool:
    """True si la respuesta del bot ya dice que está bien (para no duplicar)."""
    t = _sin_acentos(texto or "")
    return any(p in t for p in ("bien", "gracias a dios", "excelente", "de maravilla"))


def _asegurar_saludo(texto: str, mensaje_usuario: str, nombre_cliente: str | None) -> str:
    """Red de seguridad (la 'puerta' determinista, NO depende del modelo): si el cliente
    saludó y/o preguntó '¿cómo estás?' al INICIO y el bot no lo devolvió, le anteponemos un
    saludo cálido (nombre + franja horaria de Venezuela) y/o el "muy bien, gracias a Dios",
    para que NUNCA arranque seco. A mitad de conversación no fuerza nada."""
    quiere_saludo = _cliente_saludo(mensaje_usuario) and not _bot_ya_saludo(texto)
    quiere_estado = _pregunta_como_estas(mensaje_usuario) and not _bot_respondio_estado(texto)
    if not quiere_saludo and not quiere_estado:
        return texto
    partes = []
    if quiere_saludo:
        ahora = datetime.now(timezone.utc) - timedelta(hours=4)  # Venezuela = UTC-4
        h = ahora.hour
        franja = "buenos días" if h < 12 else ("buenas tardes" if h < 19 else "buenas noches")
        nombre = f", {nombre_cliente}" if nombre_cliente else ""
        partes.append(f"¡Hola{nombre}, {franja}!")
    if quiere_estado:
        partes.append("Muy bien, gracias a Dios")
    return " ".join(partes) + " 💚\n\n" + texto


# ─── RED DEL DINERO: el bot NO puede decir un monto que no salió del código ──────────
#
# En el ensayo del 2026-07-12 el bot le dijo a una clienta "Total: $35" cuando el pedido en la
# base era de $28, y a otra le dio dos montos en bolívares distintos en dos mensajes seguidos,
# con una tasa que no existe. La regla "el dinero sale SIEMPRE de la herramienta" vivía solo en
# el prompt: nada en el CÓDIGO lo impedía. Esto lo impide.
#
# Qué es un monto AUTORIZADO: cualquier número que ya aparece en (a) el catálogo inyectado
# (precios reales de la BD), (b) lo que devolvieron las herramientas en ESTE turno (totales,
# subtotales, Bs, tasa), o (c) lo que el propio cliente escribió ("tengo $40"). Todo lo demás
# es inventado.
_MONTO_RE = re.compile(
    r"(?:\$\s*([\d.,]+)|([\d.,]+)\s*(?:bs\b|bolívares|bolivares))",
    re.IGNORECASE,
)


def _numeros_de(texto: str) -> set[float]:
    """Todos los números que aparecen en un texto, en las dos lecturas posibles del formato
    venezolano (1.234,56 y 1,234.56), para no dar por inventado algo que sí está."""
    encontrados: set[float] = set()
    for crudo in re.findall(r"\d[\d.,]*", texto or ""):
        for variante in (
            crudo.replace(".", "").replace(",", "."),  # 31.936,21 -> 31936.21
            crudo.replace(",", ""),                    # 31,936.21 -> 31936.21
            crudo.replace(",", "."),                   # 28,5      -> 28.5
        ):
            try:
                encontrados.add(round(float(variante), 2))
            except ValueError:
                pass
    return encontrados


def _montos_del_mensaje(texto: str) -> list[tuple[str, set[float]]]:
    """Los montos de DINERO ($ o Bs) que el bot escribió, cada uno con TODAS sus lecturas
    posibles. "$22.40" puede leerse 22,40 (decimal) o 2.240 (miles): si CUALQUIERA de las dos
    está autorizada, el monto es bueno. Preferimos dejar pasar algo dudoso antes que frenar
    un mensaje correcto (frenar de más también rompe la venta)."""
    montos: list[tuple[str, set[float]]] = []
    for m in _MONTO_RE.finditer(texto or ""):
        crudo = m.group(1) or m.group(2) or ""
        lecturas = _numeros_de(crudo)
        if lecturas:
            montos.append((crudo, lecturas))
    return montos


def _dinero_inventado(texto: str, autorizados: set[float]) -> list[str]:
    """Devuelve los montos que el bot dijo y que NO salieron del código (ni de una herramienta,
    ni del catálogo, ni de la boca del cliente)."""
    malos: list[str] = []
    for crudo, lecturas in _montos_del_mensaje(texto):
        # Tolerancia: redondeos del modelo (0,50 o 1%).
        if any(
            abs(l - a) <= max(0.5, a * 0.01)
            for l in lecturas
            for a in autorizados
            if l != 0
        ):
            continue
        malos.append(crudo)
    return malos


# ─── RED DEL RELEVO: una promesa sin aviso es un cliente perdido ─────────────────────
#
# El 2026-07-12, en una prueba REAL por WhatsApp, el bot dijo "eso puntual te lo confirmo con
# la dueña"... y en la base había CERO avisos. El cliente se queda esperando PARA SIEMPRE y la
# dueña nunca se entera. Le pasó también en el ensayo (a Sofía 3 veces, a María con el envío a
# Caracas, a Diego con la torta del cumpleaños: el ticket más grande).
#
# Si el bot PROMETE averiguar algo y NO llamó a `pedir_ayuda` en ese turno, el código crea el
# aviso solo. La promesa deja de ser humo.
_PROMESA_RE = re.compile(
    r"(d[ée]jame\s+(que\s+)?(lo\s+|te\s+lo\s+)?(verific|consult|revis|averigu|pregunt|confirm)"
    r"|perm[ií]teme\s+(que\s+)?(lo\s+|te\s+lo\s+)?(verific|consult|revis|averigu|pregunt)"
    r"|(lo|te lo|eso)\s+(verifico|consulto|averiguo|pregunto|confirmo)\b"
    r"|te\s+(lo\s+)?(confirmo|aviso|averiguo|pregunto)\s+"
    r"(enseguida|ya|luego|en un momento|apenas|ahorita|más tarde|mas tarde|en breve)"
    r"|te\s+(lo\s+)?confirmo\s+(con|eso|esa|ese)"
    r"|lo\s+confirmo\s+con"
    r"|voy\s+a\s+(verificar|consultar|averiguar|preguntar))",
    re.IGNORECASE,
)


def _promete_averiguar(texto: str) -> bool:
    return bool(_PROMESA_RE.search(texto or ""))


# ─── RED DEL PEDIDO FANTASMA: no digas que lo agendaste si NO lo agendaste ────────────
#
# 🔴 Caso REAL (2026-07-12, chat de Enova): el bot dijo
#     "Listo 💚 Entonces te agendo para mañana lunes: 1 paquete de Empanadas (4 de carne
#      mechada, 2 de queso de cabra y 2 de pollo) para retiro aquí en La Mendera."
# …y en la base de datos había **CERO pedidos** de ese cliente. El cliente se fue creyendo que
# tenía su pedido; la dueña no tenía NADA que cocinar. Nadie se enteró.
#
# Es la misma familia del bug de la Kombucha: **el texto se ve perfecto y la realidad es otra**.
# Las otras cuatro redes no lo cazaban: no inventó un precio, no prometió averiguar, no dijo
# nada prohibido y no sonó a robot. Simplemente **mintió sobre un hecho**.
#
# La regla: si el bot AFIRMA que el pedido quedó registrado/agendado y en ESE TURNO
# `registrar_pedido` no devolvió ok, el mensaje NO SALE. Se le ordena registrarlo de verdad y,
# si insiste, se escala a la dueña.
#
# ⚠️ Lo que NO debe frenar (frenar de más también rompe la venta):
#   · "¿Te agendo entonces 2 paquetes?"      → es una PREGUNTA, todavía no afirma nada.
#   · "Cuando me confirmes, te lo agendo."   → es futuro condicional.
#   · "Listo, te agendo…" DESPUÉS de registrar de verdad → registro_ok=True, no se toca.
_AFIRMA_PEDIDO = [
    # OJO con el TIEMPO VERBAL: el caso real fue "te agendo" (PRESENTE: "lo estoy haciendo"),
    # no "te agendé". Cazar solo el pasado dejaba pasar justo el mensaje que provocó todo esto.
    re.compile(
        r"\b(ya\s+)?(te\s+lo\s+|te\s+la\s+|te\s+)?"
        r"(agendo|agend[ée]|registro|registr[ée]|anoto|anot[ée]|aparto|apart[ée])\b",
        re.I,
    ),
    re.compile(
        r"\b(queda|qued[óo]|est[áa])\s+(tu\s+)?(pedido|orden)\s+(agendad|registrad|anotad|list)",
        re.I,
    ),
    re.compile(r"\btu\s+(pedido|orden)\s+(ya\s+)?(qued[óo]|est[áa])\b", re.I),
    re.compile(r"\b(pedido|orden)\s+(confirmad|agendad|registrad)[oa]\b", re.I),
]


def _afirma_pedido_registrado(texto: str) -> bool:
    """True si el bot AFIRMA que el pedido quedó registrado. Las preguntas NO cuentan."""
    for frase in re.split(r"(?<=[.!?\n])\s+", texto or ""):
        limpia = frase.strip()
        if not limpia:
            continue
        # Una PREGUNTA no afirma nada ("¿te agendo 2 paquetes?"): pedirle que registre ahí
        # sería registrar ANTES de que el cliente confirme. Peor el remedio.
        if limpia.startswith("¿") or limpia.endswith("?"):
            continue
        # Futuro CONDICIONAL: "cuando me confirmes, te lo agendo" / "si me dices el relleno, te
        # lo registro" tampoco afirman nada todavía. Frenar aquí obligaría al bot a registrar
        # ANTES de que el cliente confirme — peor el remedio que la enfermedad.
        if re.search(r"\b(cuando|si|apenas|en\s+cuanto)\b", limpia, re.I) and re.search(
            r"\b(confirm|dic|dig|dij|avis|list|quier|decid|elij|escog)", limpia, re.I
        ):
            continue
        if any(p.search(limpia) for p in _AFIRMA_PEDIDO):
            return True
    return False


# ─── RED DE LA HONESTIDAD: hay cosas que el bot NO puede decir JAMÁS ──────────────────
#
# Bajo presión (un cliente molesto), el bot dijo "acabo de revisar todo en mi banco" — TRES
# veces. No puede: no tiene acceso al banco. Y a "¿eres un bot? dime la verdad" respondió
# "Soy Whuilianny 💚 Sí, soy yo". Mentirle al cliente sobre el DINERO o sobre QUIÉN ES es lo
# más grave que puede hacer: quema la marca y, siendo Tech Provider de Meta, arriesga la
# cuenta de TODOS los clientes.
_PROHIBIDO = [
    # Afirmar que miró el banco / que el pago entró (solo la dueña lo sabe, desde SU banco).
    (re.compile(r"(revis|verifi|chequ|mir)\w*\s+(en\s+)?(mi|el|tu)\s+(banco|cuenta)", re.I),
     "dijo que revisó el banco"),
    (re.compile(r"(mi|el)\s+banco\s+(ya\s+)?(me\s+)?(confirm|avis|lleg)", re.I),
     "dijo que el banco confirmó"),
    (re.compile(r"(ya\s+)?(me\s+)?(lleg[óo]|entr[óo]|recib[íi])\s+(tu|el)\s+pago\b", re.I),
     "afirmó que el pago ya llegó"),
    (re.compile(r"no\s+(me\s+)?(ha\s+)?(lleg\w+|aparec\w+)\s+(tu|el|ning[úu]n)\s+pago", re.I),
     "afirmó que el pago NO llegó"),
    # Jurar que es humana cuando le preguntan de frente.
    (re.compile(r"(no\s+soy\s+un[a]?\s+(bot|robot|m[áa]quina|ia|inteligencia))", re.I),
     "negó ser un asistente virtual"),
    (re.compile(r"s[íi],?\s+soy\s+(yo|una\s+persona|humana|real)\b", re.I),
     "juró ser una persona"),
    # PROMESAS DE SALUD. Le dijo a un diabético con la glicemia en 180 "así no te sube el
    # azúcar" y "te lo preparo para que sea SEGURO para ti"; y en otra prueba, "la alulosa NO
    # eleva el azúcar en sangre" — un dato que NO está en ninguna ficha. No es médica: puede
    # dar los datos REALES del producto (sin azúcar refinada, apto diabéticos sí/no), pero
    # jamás prometer un efecto en la salud de alguien.
    (re.compile(r"no\s+(te\s+|le\s+)?(sube|eleva|aumenta|afecta|altera)\s+(el|la|los)?\s*"
                r"(az[úu]car|glicemia|glucosa|insulina)", re.I),
     "prometió un efecto en el azúcar en sangre"),
    (re.compile(r"(es|son|ser[áa]n?|sean?)\s+seguro[s]?\s+(para\s+)?"
                r"(ti|vos|usted|tu\s+(diabetes|salud|condici[óo]n))", re.I),
     "dijo que un producto es 'seguro' para la salud del cliente"),
    (re.compile(r"(cura|sana|combate|elimina|revierte)\s+(la\s+|tu\s+)?(diabetes|enfermedad|c[áa]ncer)", re.I),
     "dijo que un producto cura una enfermedad"),
    (re.compile(r"te\s+ayuda\s+a\s+(bajar|controlar|regular)\s+(el|la|tu)\s*"
                r"(az[úu]car|glicemia|glucosa|diabetes)", re.I),
     "prometió un beneficio médico"),
    (re.compile(r"(puedes|podr[íi]as)\s+(dejar|suspender|bajar)\s+(la|el|tu)\s+"
                r"(metformina|insulina|medicamento|tratamiento)", re.I),
     "opinó sobre un medicamento"),
]


def _frase_prohibida(texto: str) -> str | None:
    for patron, que in _PROHIBIDO:
        if patron.search(texto or ""):
            return que
    return None


# ─── RED DE LA VOZ: no hables como un sistema ────────────────────────────────────────
#
# "Lo que TENGO CARGADO es entrega local…" — eso lo dijo el bot en una prueba REAL, y lo
# siguió diciendo aunque la regla ya estaba escrita en el prompt. Ninguna vendedora habla de
# lo que tiene "cargado": eso es narrar la base de datos, y delata al robot al instante.
# Esto NO es peligroso (no es dinero ni una mentira), así que la red es SUAVE: se le pide que
# lo reescriba UNA vez; si insiste, el mensaje sale igual (frenar de más rompe la venta).
_SUENA_A_SISTEMA = re.compile(
    r"(lo que (yo )?tengo cargad|tengo cargad|no me trae|no me aparece en (el sistema|mi)"
    r"|mi (base de datos|sistema|catálogo cargado)|el sistema (no )?(me )?(deja|permite|trae)"
    r"|no se pudo enviar|no me deja|est[áa] cargad[oa] en el sistema"
    r"|seg[úu]n (mi|el) (sistema|registro|base))",
    re.IGNORECASE,
)


def _suena_a_sistema(texto: str) -> bool:
    return bool(_SUENA_A_SISTEMA.search(texto or ""))


async def responder(
    telefono: str,
    mensaje_usuario: str,
    historial: list | None = None,
    nombre_cliente: str | None = None,
    *,
    llm=_llamar_openrouter,
    ejecutar=ejecutar_tool,
) -> str:
    """Devuelve el texto de respuesta para enviar al cliente.

    `llm` y `ejecutar` son inyectables para poder testear el loop sin
    llamar a OpenRouter ni a la base de datos reales.
    """
    # La parte ESTABLE del prompt se marca con cache_control: el proveedor la cachea y la
    # cobra a ¼ en los siguientes mensajes (mismo prompt → misma calidad, solo más barato).
    # La parte DINÁMICA (hora, ficha, estado) va aparte, sin cachear.
    estable, dinamico = await construir_partes_prompt(nombre_cliente, telefono)
    messages: list = [
        {
            "role": "system",
            "content": [
                {"type": "text", "text": estable, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": dinamico},
            ],
        }
    ]
    if historial:
        messages.extend(historial)
    messages.append({"role": "user", "content": mensaje_usuario})

    modelo = await leer_modelo_ia()  # el que eligió la proveedora en el panel
    # Diagnóstico (corre al procesar el mensaje, sí aparece en los logs): confirma qué
    # modelo se usa, cuántas herramientas tiene el código corriendo y si está la de fotos.
    logger.info(
        "responder: modelo=%s tools=%d fotos_tool=%s msg=%r",
        modelo,
        len(TOOL_SCHEMAS),
        any(t["function"]["name"] == "enviar_fotos_producto" for t in TOOL_SCHEMAS),
        (mensaje_usuario or "")[:60],
    )
    catalogo_ok = False
    # Montos AUTORIZADOS de este turno: los precios reales del catálogo (van inyectados en el
    # prompt), lo que escribió el cliente, y lo que vayan devolviendo las herramientas.
    autorizados: set[float] = _numeros_de(estable) | _numeros_de(dinamico)
    autorizados |= _numeros_de(mensaje_usuario)
    for h in historial or []:
        if isinstance(h, dict) and h.get("role") == "user":
            autorizados |= _numeros_de(str(h.get("content") or ""))
    corregido = False
    pidio_ayuda = False  # ¿el bot llamó a pedir_ayuda en este turno?
    reescrito = False    # ya se le pidió una vez que no hable como un sistema
    registro_ok = False  # ¿registrar_pedido devolvió OK en este turno? (red del pedido fantasma)
    reclamo_pedido = False  # ya se le llamó la atención una vez por decir que agendó sin agendar

    for _ in range(settings.max_iteraciones_agente):
        data = await _llamar_con_fallback(messages, llm, modelo)
        msg = data["choices"][0]["message"]
        messages.append(msg)

        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            texto = (msg.get("content") or "").strip() or RESPUESTA_SEGURA

            # RED DEL DINERO: ningún monto puede salir de la cabeza del modelo.
            inventados = _dinero_inventado(texto, autorizados)
            if inventados:
                logger.error(
                    "DINERO INVENTADO por el modelo para %s: %s (autorizados=%s) — texto=%r",
                    telefono, inventados, sorted(autorizados)[:12], texto[:160],
                )
                if not corregido:
                    # Una oportunidad de corregirse, con los números buenos en la mano.
                    corregido = True
                    messages.append({
                        "role": "user",
                        "content": (
                            "[SISTEMA] Te saliste del guion del DINERO: escribiste "
                            f"{inventados} y esos montos NO salieron de ninguna herramienta ni "
                            "del catálogo. NUNCA calcules ni estimes dinero de cabeza. Reescribe "
                            "tu último mensaje usando EXACTAMENTE los montos que te devolvieron "
                            "las herramientas (`resumen` / `resumen_cobro`), copiados tal cual. "
                            "Si te falta un monto, LLAMA a la herramienta que lo da. No le "
                            "menciones al cliente este aviso."
                        ),
                    })
                    continue
                # Se le dio una oportunidad y volvió a inventar: NO se le manda al cliente un
                # número falso. Se escala a la dueña y se responde sin cifras.
                logger.error("DINERO INVENTADO 2 veces para %s: se escala a la dueña", telefono)
                try:
                    await ejecutar(
                        "pedir_ayuda",
                        {
                            "motivo": "no_se",
                            "detalle": (
                                "el bot iba a decir un monto que no salió del sistema "
                                f"({inventados}); NO se le envió al cliente"
                            ),
                        },
                        telefono,
                    )
                except Exception:  # noqa: BLE001 — si el aviso falla, igual NO mandamos el monto
                    logger.exception("No se pudo escalar el dinero inventado de %s", telefono)
                return RESPUESTA_SEGURA

            # RED DE LA HONESTIDAD: hay frases que NO pueden salir nunca.
            prohibida = _frase_prohibida(texto)
            if prohibida:
                logger.error(
                    "FRASE PROHIBIDA para %s (%s) — texto=%r", telefono, prohibida, texto[:160]
                )
                if not corregido:
                    corregido = True
                    messages.append({
                        "role": "user",
                        "content": (
                            f"[SISTEMA] NO puedes decir eso ({prohibida}). Tú NO tienes acceso al "
                            "banco: jamás digas que revisaste, verificaste o consultaste el banco, "
                            "ni que un pago llegó o no llegó (eso lo revisa la dueña en SU banco). "
                            "Y si te preguntan de frente si eres un bot o una persona, di la "
                            "VERDAD con calidez: eres la asistente virtual del negocio, y si "
                            "quiere hablar con una persona la avisas ahorita (llama a pedir_ayuda "
                            "con motivo='pide_persona'). Reescribe tu mensaje sin esa frase. No le "
                            "menciones al cliente este aviso."
                        ),
                    })
                    continue
                # Insistió: NO se le manda al cliente. Se escala.
                try:
                    await ejecutar(
                        "pedir_ayuda",
                        {
                            "motivo": "reclamo",
                            "detalle": (
                                f"el bot iba a decir algo que tiene PROHIBIDO ({prohibida}); "
                                "NO se le envió al cliente. Entra tú al chat."
                            ),
                        },
                        telefono,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("No se pudo escalar la frase prohibida de %s", telefono)
                return RESPUESTA_SEGURA

            # RED DE LA VOZ: si habla como un sistema ("lo que tengo cargado"), que lo reescriba.
            # Suave a propósito: una sola oportunidad, y si insiste el mensaje sale igual.
            if _suena_a_sistema(texto) and not reescrito:
                reescrito = True
                logger.info("SUENA A SISTEMA (%s): se le pide reescribir — %r", telefono, texto[:90])
                messages.append({
                    "role": "user",
                    "content": (
                        "[SISTEMA] Sonaste a robot: mencionaste tu sistema / lo que tienes "
                        "'cargado'. Una vendedora de verdad NUNCA dice eso. Reescribe tu último "
                        "mensaje hablando de lo que el NEGOCIO hace, con tus palabras (ej: en vez "
                        "de 'lo que tengo cargado es entrega local' → 'hacemos entrega en La "
                        "Mendera o delivery por tu zona'). Mismo contenido, sin mencionar sistemas "
                        "ni datos cargados. No le menciones al cliente este aviso."
                    ),
                })
                continue

            # 🔴 RED DEL PEDIDO FANTASMA: si dice que lo agendó y NO lo agendó, el mensaje NO
            # SALE. Caso real: dijo "Listo 💚 te agendo 1 paquete de Empanadas…" y en la base
            # había CERO pedidos. El cliente se fue creyendo que tenía su pedido y la dueña no
            # tenía nada que cocinar. Las otras cuatro redes no lo veían: no inventó un precio,
            # no prometió averiguar, no dijo nada prohibido y no sonó a robot. Solo MINTIÓ.
            if _afirma_pedido_registrado(texto) and not registro_ok:
                logger.error(
                    "PEDIDO FANTASMA de %s: dijo que lo agendó y NO llamó a registrar_pedido "
                    "(o falló) — texto=%r",
                    telefono, texto[:140],
                )
                if not reclamo_pedido:
                    reclamo_pedido = True
                    messages.append({
                        "role": "user",
                        "content": (
                            "[SISTEMA] ACABAS DE DECIR QUE EL PEDIDO QUEDÓ AGENDADO Y NO LO "
                            "REGISTRASTE. En la base de datos NO existe. El cliente se irá "
                            "creyendo que tiene su pedido y la dueña no tendrá nada que cocinar. "
                            "Llama AHORA a `registrar_pedido` con el `variante_id` (el "
                            "`id_para_pedir` del catálogo), la cantidad y la fecha de entrega. Si "
                            "te falta algún dato, PREGÚNTASELO al cliente en vez de afirmar que "
                            "ya está. No le menciones al cliente este aviso."
                        ),
                    })
                    continue
                # Insistió: NO se le manda al cliente una confirmación falsa. Se escala.
                try:
                    await ejecutar(
                        "pedir_ayuda",
                        {
                            "motivo": "reclamo",
                            "detalle": (
                                "el bot le dijo al cliente que le AGENDÓ el pedido pero NO lo "
                                "registró (no existe en el sistema). NO se le envió esa "
                                "confirmación falsa. Entra tú al chat y agéndalo."
                            ),
                        },
                        telefono,
                    )
                except Exception:  # noqa: BLE001 — igual NO se manda la confirmación falsa
                    logger.exception("No se pudo escalar el pedido fantasma de %s", telefono)
                return RESPUESTA_SEGURA

            # RED DEL RELEVO: si PROMETE averiguar algo y no avisó a nadie, el aviso lo crea
            # el código. Una promesa sin aviso deja al cliente esperando para siempre.
            if _promete_averiguar(texto) and not pidio_ayuda:
                logger.warning(
                    "PROMESA SIN AVISO de %s: %r — se crea el aviso automáticamente",
                    telefono, texto[:120],
                )
                try:
                    await ejecutar(
                        "pedir_ayuda",
                        {
                            "motivo": "no_se",
                            "detalle": (
                                f'el bot le prometió al cliente que le confirma algo que NO sabe. '
                                f'El cliente preguntó: "{(mensaje_usuario or "")[:160]}"'
                            ),
                        },
                        telefono,
                    )
                    pidio_ayuda = True
                except Exception:  # noqa: BLE001 — el aviso no puede tumbar el turno
                    logger.exception("No se pudo crear el aviso automático de %s", telefono)

            texto = await _asegurar_catalogo(texto, catalogo_ok, telefono, ejecutar)
            if _es_inicio_conversacion(historial):
                texto = _asegurar_saludo(texto, mensaje_usuario, nombre_cliente)
            return texto

        for tc in tool_calls:
            nombre_tool = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            resultado = await ejecutar(nombre_tool, args, telefono)
            if nombre_tool == "enviar_catalogo" and isinstance(resultado, dict) and resultado.get("ok"):
                catalogo_ok = True
            if nombre_tool == "pedir_ayuda":
                pidio_ayuda = True  # ya avisó: la red del relevo no tiene que hacer nada
            if (
                nombre_tool == "registrar_pedido"
                and isinstance(resultado, dict)
                and resultado.get("ok")
            ):
                # El pedido existe DE VERDAD en la base. Sin esto, el bot podía decir
                # "listo, te agendo" sin haber registrado nada (caso real del 2026-07-12).
                registro_ok = True
            # Todo monto que devuelve una herramienta queda AUTORIZADO para este turno
            # (totales, subtotales del `resumen`, bolívares, la tasa BCV…).
            autorizados |= _numeros_de(json.dumps(resultado, ensure_ascii=False))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(resultado, ensure_ascii=False),
                }
            )

    logger.warning("Agente excedió max iteraciones para %s", telefono)
    return RESPUESTA_SEGURA


async def redactar_mensaje(
    situacion: str, historial: list | None = None, nombre: str | None = None
) -> str:
    """Redacta un mensaje natural para el cliente en la voz de Whuilianny.

    NO es una plantilla: usa el contexto de la conversacion y algo de variacion
    para que cada mensaje salga distinto y humano. Se usa para momentos que
    dispara el sistema (comprobante recibido, pago confirmado/rechazado), donde
    no hay un texto del cliente que responder pero hay que decir algo con calidez.
    """
    estable, dinamico = await construir_partes_prompt(nombre)
    messages: list = [
        {
            "role": "system",
            "content": [
                {"type": "text", "text": estable, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": dinamico},
            ],
        }
    ]
    if historial:
        messages.extend(historial)
    messages.append({
        "role": "user",
        "content": (
            f"[Instruccion interna, NO es un mensaje del cliente] Situacion: {situacion}. "
            "Escribe SOLO el mensaje de WhatsApp para el cliente, natural, breve y calido, "
            "en tu voz de siempre. No uses comillas ni expliques nada."
        ),
    })
    modelo = await leer_modelo_ia()  # mismo modelo elegido para conversar
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            json={"model": modelo, "messages": messages, "temperature": 0.7},
        )
        resp.raise_for_status()
        data = resp.json()
        return (data["choices"][0]["message"].get("content") or "").strip()


_FORMATOS_AUDIO = {
    "audio/ogg": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "m4a",
    "audio/aac": "aac",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
}


async def transcribir_audio(contenido: bytes, mime: str = "audio/ogg") -> str:
    """Transcribe una nota de voz a texto con el modelo multimodal de OpenRouter.

    WhatsApp manda las notas de voz como audio/ogg (codec opus), que Gemini
    acepta directo. Si el modelo no logra transcribir, devuelve cadena vacia
    y el llamador responde con gracia (nunca tumba al bot).
    """
    base_mime = (mime or "").split(";")[0].strip().lower()
    formato = _FORMATOS_AUDIO.get(base_mime, "ogg")
    b64 = base64.b64encode(contenido).decode("ascii")
    messages = [
        {
            "role": "system",
            "content": (
                "Transcribe a texto, en espanol, exactamente lo que dice el cliente "
                "en el audio. Devuelve solo la transcripcion, sin comentarios."
            ),
        },
        {
            "role": "user",
            "content": [
                {"type": "input_audio", "input_audio": {"data": b64, "format": formato}},
            ],
        },
    ]
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            json={"model": settings.openrouter_model_audio, "messages": messages, "temperature": 0},
        )
        resp.raise_for_status()
        data = resp.json()
        return (data["choices"][0]["message"].get("content") or "").strip()


# ─── Visión: reconocer comprobantes de pago ──────────────────────────
_FORMATOS_IMAGEN = {
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/png": "image/png",
    "image/webp": "image/webp",
    "image/heic": "image/heic",  # iPhone
    "image/heif": "image/heif",  # iPhone
}


def _parsear_json_comprobante(texto: str) -> dict | None:
    """Extrae el JSON de la respuesta del modelo (tolera ```json ... ``` y texto
    alrededor). Devuelve None si no se puede parsear (→ el llamador lo trata como
    'no se pudo leer')."""
    t = (texto or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t[:4].lower() == "json":
            t = t[4:]
    i, j = t.find("{"), t.rfind("}")
    if i != -1 and j != -1 and j > i:
        t = t[i : j + 1]
    try:
        d = json.loads(t)
    except (json.JSONDecodeError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _solo_digitos(s) -> str:
    return "".join(c for c in str(s or "") if c.isdigit())


def _beneficiario_coincide(parsed: dict, cuentas: list | None) -> bool:
    """True si el BENEFICIARIO del comprobante (quien RECIBE) coincide con ALGUNA de
    las cuentas de la dueña por un identificador FUERTE: teléfono, cédula/RIF, correo
    (Zelle) o wallet (Binance/USDT). El NOMBRE solo NO basta (hay muchos homónimos:
    por eso un voucher a 'Maired Hernández' en OTRO banco/cuenta NO debe pasar)."""
    # Junta TODOS los números (>=6 dígitos) que la visión leyó del beneficiario, sin
    # importar en qué campo los puso (Binance/bancos confunden UID/cuenta/cédula).
    ben_nums: set[str] = set()
    for k in (
        "beneficiario_telefono",
        "beneficiario_cedula",
        "beneficiario_cuenta",
        "beneficiario_wallet",
    ):
        d = _solo_digitos(parsed.get(k))
        if len(d) >= 6:
            ben_nums.add(d)
    ben_correo = _sin_acentos(parsed.get("beneficiario_correo") or "").replace(" ", "")
    ben_wallet_txt = _sin_acentos(parsed.get("beneficiario_wallet") or "").replace(" ", "")

    def _num_match(id_cuenta, cola: int = 0) -> bool:
        cid = _solo_digitos(id_cuenta)
        if len(cid) < 6:
            return False
        for bd in ben_nums:
            if cid == bd or cid in bd or bd in cid:
                return True
            if cola and len(cid) >= cola and len(bd) >= cola and cid[-cola:] == bd[-cola:]:
                return True
        return False

    for c in cuentas or []:
        # Identificadores numéricos de la cuenta (teléfono con cola de 7, el resto exacto/contenido).
        if _num_match(c.get("telefono"), cola=7):
            return True
        if _num_match(c.get("cedula")) or _num_match(c.get("cuenta")) or _num_match(c.get("wallet")):
            return True
        # Correo (Zelle) y wallet por texto (no numérico).
        correo = _sin_acentos(c.get("correo") or "").replace(" ", "")
        if correo and ben_correo and correo == ben_correo:
            return True
        wallet = _sin_acentos(c.get("wallet") or "").replace(" ", "")
        if wallet and ben_wallet_txt and (wallet == ben_wallet_txt or wallet in ben_wallet_txt or ben_wallet_txt in wallet):
            return True
    return False


async def leer_comprobante(
    contenido: bytes,
    mime: str,
    *,
    cuentas: list | None = None,
) -> dict:
    """Lee una imagen con visión (Gemini) y decide EN CÓDIGO si es un comprobante de
    pago a UNA de las cuentas de la dueña. La visión solo EXTRAE datos (beneficiario,
    monto, referencia); el CÓDIGO valida que el beneficiario coincida con alguna de
    `cuentas` (por teléfono/cédula/correo/wallet). Devuelve un dict con
    {es_comprobante, monto, referencia, beneficiario_*, leido}.

    `cuentas`: lista de dicts {titular, banco, telefono, cedula, correo, wallet}.
    'leido'=False -> no se pudo analizar (PDF / visión caída / respuesta ilegible).
    'leido'=True  -> es_comprobante True solo si pantalla bancaria + monto +
    beneficiario coincide con una cuenta. NUNCA lanza.
    """
    base_mime = (mime or "").split(";")[0].strip().lower()
    fmt = _FORMATOS_IMAGEN.get(base_mime)
    if fmt is None:
        return {"es_comprobante": None, "leido": False}  # PDF u otro: a manual
    b64 = base64.b64encode(contenido).decode("ascii")
    data_url = f"data:{fmt};base64,{b64}"
    instruccion = (
        "Eres un extractor de datos de comprobantes de pago de Venezuela (Pago Móvil, "
        "transferencia, billetera). Mira la imagen y responde SOLO con un JSON válido, "
        "sin ningún texto extra, con EXACTAMENTE estas llaves:\n"
        '{"es_pantalla_bancaria": true o false, '
        '"monto": "<monto en bolívares, solo números, ej 39480.47, o null>", '
        '"referencia": "<número de referencia/operación, o null>", '
        '"beneficiario_nombre": "<nombre de QUIEN RECIBE el pago, o null>", '
        '"beneficiario_telefono": "<teléfono de quien recibe, o null>", '
        '"beneficiario_cedula": "<cédula o RIF de quien recibe, o null>", '
        '"beneficiario_cuenta": "<número de cuenta bancaria de quien recibe (transferencia), o null>", '
        '"beneficiario_correo": "<correo de quien recibe (Zelle), o null>", '
        '"beneficiario_wallet": "<wallet, usuario o ID/UID de quien recibe (Binance/USDT), o null>", '
        '"banco_beneficiario": "<banco/plataforma de quien recibe, o null>", '
        '"confianza": "alta" o "media" o "baja"}\n\n'
        "es_pantalla_bancaria=true si la imagen es la PANTALLA de un banco, billetera o "
        "app de pago/cripto (Pago Móvil, transferencia, Zelle, Binance, USDT) que muestra "
        "un pago o transferencia YA REALIZADO, con monto y una referencia o ID de orden. "
        "Para una foto de una persona, producto, comida, paisaje, meme, logo, sticker, "
        "captura de un chat/app/red social/tutorial, o cualquier cosa que NO sea una "
        "transacción bancaria, pon es_pantalla_bancaria=false y los demás campos en null.\n"
        "Extrae SIEMPRE los datos de QUIEN RECIBE el dinero (el beneficiario/destino), NO de "
        "quien paga. Copia los nombres y números TAL CUAL aparecen en la imagen."
    )
    messages = [
        {"role": "system", "content": instruccion},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Analiza esta imagen y responde SOLO con el JSON pedido."},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
                json={"model": settings.openrouter_model_audio, "messages": messages, "temperature": 0},
            )
            resp.raise_for_status()
            data = resp.json()
            texto = (data["choices"][0]["message"].get("content") or "").strip()
    except Exception:  # noqa: BLE001 — leer el comprobante nunca debe tumbar el worker
        logger.exception("No se pudo leer el comprobante con visión")
        return {"es_comprobante": None, "leido": False}
    parsed = _parsear_json_comprobante(texto)
    if parsed is None:
        return {"es_comprobante": None, "leido": False}

    # DECISIÓN EN CÓDIGO (no se la dejamos a la IA): es comprobante PARA LA DUEÑA
    # solo si es pantalla bancaria + hay monto + el beneficiario coincide con su cuenta.
    pantalla = parsed.get("es_pantalla_bancaria")
    pantalla = pantalla is True or str(pantalla).strip().lower() in ("true", "si", "sí", "yes", "1")
    monto = parsed.get("monto")
    monto_ok = bool(monto) and str(monto).strip().lower() not in ("", "null", "none", "0")
    beneficiario_ok = _beneficiario_coincide(parsed, cuentas)
    parsed["es_comprobante"] = bool(pantalla and monto_ok and beneficiario_ok)
    parsed["leido"] = True
    return parsed
