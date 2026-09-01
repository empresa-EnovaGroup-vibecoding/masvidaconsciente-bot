"""Agente único con function calling sobre OpenRouter.

Recibe un mensaje del cliente + su historial, decide qué herramientas usar,
las ejecuta, y devuelve la respuesta final en la voz de Whuilianny.
"""
import base64
import json
import logging
import re
import time
import unicodedata
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from app.agent.hoja import HojaDeHechos
from app.agent.system_prompt import (
    construir_partes_prompt,
    leer_config_agente,
    leer_modelo_ia,
)
from app.agent.tools import (
    TOOL_SCHEMAS,
    _frase_comparable,
    _palabras_busqueda,
    _productos_nombrados_en,
    _versiones_distintivas,
    _versiones_tocadas,
    avisar_relevo_caido,
    catalogo_variantes_para_hilo,
    ejecutar_tool,
    etiqueta_del_cliente,
    etiqueta_recordada,
    hilo_de_la_venta,
    horas_de_silencio,
    media_ya_mostrada,
    productos_enfocados,
    schemas_para,
    tamanos_hermanos,
)
from app.config import get_settings
from app.models import Pedido, Producto
from app.services.db import get_session_factory

# La telemetría del modelo (migración 032). `app.services.*` ya se importa aquí arriba
# (`tools_config`), así que no abre ningún ciclo: telemetria importa `models` y `db` de forma
# PEREZOSA, dentro de la función que escribe.
from app.services.telemetria import abrir_turno, registrar
from app.services.tools_config import leer_tools_activas

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


def _pide_catalogo(texto_cliente: str) -> bool:
    """True si el CLIENTE está pidiendo el catálogo/menú/carta."""
    t = _sin_acentos(texto_cliente or "")
    return any(p in t for p in ("catalogo", "menu", "carta", "lista de productos"))


def _afirma_envio_catalogo(texto: str, cliente_pidio_catalogo: bool = False) -> bool:
    """True si el texto AFIRMA haber enviado el catálogo/menú (no si lo ofrece/pregunta).

    ⚠️ La trampa del PRONOMBRE (2026-07-14, gemela de la de las fotos): si el cliente pidió
    el catálogo y el bot dice "ya te LO envié", la frase NO trae la palabra "catálogo" — el
    «lo» viene del mensaje del cliente. Por eso, si el cliente lo acaba de pedir, la
    afirmación cuenta aunque la palabra no esté."""
    t = _sin_acentos(texto)
    if "catalogo" not in t and "menu" not in t and not cliente_pidio_catalogo:
        return False
    return any(frase in t for frase in _AFIRMA_ENVIO_CATALOGO)


async def _asegurar_catalogo(
    texto: str, catalogo_ok: bool, telefono: str, ejecutar, pidio_catalogo: bool = False
) -> str:
    """Red de seguridad: si el bot DICE que envió el catálogo pero no llamó a la
    herramienta, lo enviamos de verdad (su afirmación se vuelve cierta y el cliente
    SÍ recibe el PDF, además en el orden correcto: PDF primero, texto después).
    Si no hay PDF, evitamos dejar una afirmación falsa."""
    if catalogo_ok or not _afirma_envio_catalogo(texto, pidio_catalogo):
        return texto
    try:
        resultado = await ejecutar("enviar_catalogo", {}, telefono)
    except Exception:  # noqa: BLE001
        logger.exception("RED DEL CATÁLOGO: `enviar_catalogo` lanzó para %s", telefono)
        resultado = {"ok": False}
    if isinstance(resultado, dict) and resultado.get("ok"):
        return texto  # ahora SÍ se envió: la afirmación es verdad
    # No se pudo enviar (no hay PDF): no dejar una afirmación falsa.
    # 🔴 Y QUE QUEDE ESCRITO. Esta rama TIRA el mensaje que el modelo había redactado y le manda
    # al cliente un cambio de tema; si eso pasa a menudo (PDF sin cargar, Meta rechazando el
    # documento), la dueña tiene que poder VERLO en el log en vez de adivinar por qué el bot
    # contesta raro. Dos capas mudas encadenadas eran dos sitios donde mirar y no encontrar nada.
    logger.error(
        "RED DEL CATÁLOGO: el bot dijo que envió el catálogo a %s y NO se pudo enviar "
        "(resultado=%r) — se le manda el texto de reemplazo", telefono, resultado,
    )
    return "Déjame mostrarte lo que tenemos 😊 ¿Qué estás buscando?"


# ─── RED DE LA FOTO: ver vende, y el modelo describe en vez de mostrar ────────────────
#
# 🔴 POR QUÉ EXISTE (smoke de 7 turnos contra el bot real, 2026-08-08, corrido DOS veces con el
# mismo resultado): CERO fotos de producto en toda la conversación, con una clienta que llegó a
# decir "ok esa quiero". La regla que ordena mostrarlas YA existe en el prompt (la de
# FOTOS/VIDEO, enorme, con 🔥 y "ÚSALA PROACTIVA") — y el modelo la ignora. Esa regla ignorada
# es exactamente la evidencia de que esto va en código: el prompt SUGIERE, el código IMPIDE.
# Familia de `_asegurar_catalogo`: corre cuando el texto final ya existe, y lo que garantiza es
# que el CLIENTE VEA lo que el bot le está vendiendo.
#
# Solo palabras de pura cortesía: si TODAS las palabras del mensaje están aquí, no hay intención
# de producto ("hola buenas tardes", "gracias!", "chao"). "ok esa quiero" NO es charla pura
# ("esa" y "quiero" no están) — y ese es justo el turno del smoke donde la foto tenía que salir.
_CHARLA_PURA = frozenset({
    "hola", "holaa", "holaaa", "buenas", "buenos", "dias", "dia", "tardes", "noches",
    "que", "tal", "saludos", "epale", "hey", "como", "estas", "esta", "andas", "va", "todo",
    "bien", "muy", "gracias", "mil", "muchas", "chao", "chaito", "adios", "hasta", "luego",
    "pronto", "feliz", "amen", "bendiciones", "dios", "ok", "okey", "oki", "dale", "vale",
    "listo", "perfecto", "genial", "excelente", "chevere", "fino", "y", "tu", "usted",
})


def _es_charla_pura(texto: str) -> bool:
    """True si el mensaje del cliente es SOLO saludo/gracias/despedida (o solo emojis):
    ahí no se empuja ninguna foto. Cualquier palabra con contenido lo saca de esta lista."""
    palabras = re.findall(r"[a-z0-9]+", _sin_acentos(texto or ""))
    return not palabras or all(p in _CHARLA_PURA for p in palabras)


# Si el bot pregunta "¿cuál…?" está ofreciendo a ELEGIR: el cliente sigue entre varios y
# todavía no hay UN producto que mostrar. `\b` a ambos lados porque 'cual'/'cuales' son
# palabras completas, no raíces ("cualquiera" no calza y no debe calzar).
# "¿cuál…?" en el texto del bot. 🔴 YA NO se usa como guarda en la RED DE LA FOTO (ver el porqué
# dentro de `_asegurar_foto`), y su otro usuario —la RED DEL PITCH— se quitó el 2026-08-24.
# 🪦 Se conserva porque el comentario de `_asegurar_foto` (:179) cuenta la lección de la guarda
# que se quitó, y porque cualquier red futura que ofrezca opciones la va a volver a necesitar.
_OFRECE_OPCIONES = re.compile(r"\bcual(es)?\b")

# Cuántas fotos como MÁXIMO manda la red en un turno. DOS porque lo pidió Erwin el 2026-08-21:
# cuando el bot ofrece dos opciones, enseñar las dos ayuda a decidir — a puro texto no engancha.
# Con TRES o más NO se manda nada (ni recortado a dos: elegir 2 de 5 es decidir por el cliente),
# porque ahí sí es spam y quema la calidad del número con Meta, que es una regla dura.
_MAX_FOTOS_POR_TURNO = 2


async def _asegurar_foto(
    texto: str, telefono: str, mensaje_cliente: str, ejecutar,
    *, puede_fotos: bool, hubo_media: bool, historial: list | None = None,
) -> None:
    """Red de seguridad: si el turno quedó ENFOCADO en un solo producto y no salió ninguna
    media, la foto la manda el código. No toca el texto JAMÁS (ya es el final y es válido):
    esta red suma una foto o no hace nada.

    Guardas, en orden de lo barato a lo caro:
    - la herramienta apagada o media ya enviada en este turno ⇒ la red no existe;
    - saludo/gracias/despedida o pedir el catálogo ⇒ no es un turno de producto;
    - el bot pregunta "¿cuál…?" ⇒ el cliente sigue eligiendo entre varios;
    - el texto no nombra UN único producto resoluble (`producto_enfocado`, con la semántica
      exacto-primero de `_buscar_producto`) ⇒ sin certeza no se dispara;
    - ese producto ya se le mostró en este chat (`media_ya_mostrada`) ⇒ repetirla es spam.

    Las guardas de SIMULADOR y de RELEVO (la dueña tomó el chat) NO se duplican aquí a
    propósito: viven dentro de `enviar_fotos_producto` y se respetan llamándola por la misma
    puerta que usa el modelo (`ejecutar`). Si la herramienta responde "no hay fotos", no pasa
    nada: el texto ya salió con la verdad y esta red no promete nada que no hizo.
    """
    if not puede_fotos or hubo_media:
        return
    if _es_charla_pura(mensaje_cliente) or _pide_catalogo(mensaje_cliente):
        return
    # 🔴 AQUÍ ESTABA LA GUARDA `if _OFRECE_OPCIONES.search(texto): return`, Y SE QUITÓ EL
    # 2026-08-21 tras medirla. Era `re.compile(r"\bcual(es)?\b")`: **cualquier "cuál" en la
    # respuesta apagaba la red entera**. La intención era buena ("si sigue eligiendo, no
    # bombardear") pero no distinguía las dos preguntas:
    #
    #   "¿CUÁL de estos PRODUCTOS?"  → sigue eligiendo, no mostrar   ✔ (hoy lo cubre el TOPE)
    #   "¿CUÁL RELLENO?"             → el producto YA está elegido    ✘ y se apagaba igual
    #
    # El turno que lo destapó (Erwin, con el bot real): la clienta preguntó *"¿tienen empanadas de
    # plátano?"* y el bot contestó *"Tenemos de carne mechada, pollo o queso de cabra. ¿Cuál
    # prefieres?"*. Producto ya elegido, pregunta por el relleno, y CERO fotos — justo en el turno
    # de más interés. Y pesa mucho, porque el bot cierra con pregunta casi siempre (11 de 12
    # turnos, L5). Quien decide ahora es el TOPE de productos, que es la señal de verdad: si el
    # texto nombra 3+ productos, el cliente sigue eligiendo y no sale nada.
    try:
        # 1) Lo que nombra el BOT. 2) Si no nombra ninguno, lo que pidió el CLIENTE — el bot no
        #    repite el nombre cuando ya se sobreentiende, y eso dejaba la red ciega.
        nombres = await productos_enfocados(texto, _MAX_FOTOS_POR_TURNO)
        de_donde = "el bot lo nombró"
        if not nombres:
            nombres = await productos_enfocados(mensaje_cliente, _MAX_FOTOS_POR_TURNO)
            de_donde = "lo pidió el cliente"
        if not nombres:
            return
        # 🔴 Un STRING suelto aquí sería un desastre silencioso: `for nombre in nombres` iteraría
        # sus CARACTERES y mandaría una llamada por letra (45 en la primera prueba, con un doble
        # de test mal escrito). La red que evita el spam no puede ser la que lo provoque.
        if isinstance(nombres, str):
            nombres = [nombres]
        # Las que ya se le mostraron se caen aquí: repetirlas es spam, pero no impiden mandar
        # la otra (antes un `return` seco mataba las dos).
        pendientes = [n for n in nombres if not await media_ya_mostrada(telefono, n)]
        if not pendientes:
            logger.info(
                "RED DE LA FOTO: %s ya se le mostró a %s — no se repite", nombres, telefono
            )
            return
        # 🔴 CUÁNTOS ARCHIVOS por producto. Con DOS productos se manda UNO de cada uno: la tool
        # manda hasta 3 por defecto, y 2 × 3 = **6 archivos seguidos** — 5 salieron en la primera
        # prueba real, y eso es el bombardeo que la regla quería evitar (y que arriesga la calidad
        # del número con Meta). Con un solo producto se mantienen los 3 de siempre: ahí es
        # enseñarlo desde varios ángulos, no spam.
        por_producto = 1 if len(pendientes) > 1 else 3
        for nombre in pendientes:
            args: dict = {"nombre": nombre, "maximo": por_producto}
            # Si el producto es COMPUESTO ("… de masa de yuca o de masa de plátano") y el cliente
            # dijo cuál versión quiere ("de platano"), la etiqueta va en la llamada: la herramienta
            # manda la foto que la dueña nombró así y JAMÁS la de la otra masa (hueco encontrado
            # por Erwin con el bot real: salía la confirmación de la de plátano y ninguna foto).
            etiqueta = etiqueta_del_cliente(nombre, mensaje_cliente)
            if not etiqueta:
                # …y si en ESTE mensaje no la dijo, la que eligió hace pocos turnos. La foto sale
                # en el turno del CIERRE ("de carne mechada, 1 paquete"), y para entonces la
                # elección de masa quedó dos turnos atrás: sin memoria salían LAS DOS masas
                # (medido contra el bot real, 2026-08-09). El turno actual siempre gana: esto solo
                # corre cuando aquí no hubo elección — ver `etiqueta_recordada`.
                etiqueta = await etiqueta_recordada(nombre, mensaje_cliente, historial)
            if etiqueta:
                args["etiqueta"] = etiqueta
            resultado = await ejecutar("enviar_fotos_producto", args, telefono)
            enviadas = resultado.get("enviadas") if isinstance(resultado, dict) else None
            logger.info(
                "RED DE LA FOTO (%s): el modelo no mostró '%s' a %s y el código lo intentó "
                "→ enviadas=%s", de_donde, nombre, telefono, enviadas,
            )
    except Exception:  # noqa: BLE001 — la foto es un empujón de venta, jamás tumba el turno
        logger.exception("RED DE LA FOTO: falló el envío proactivo para %s", telefono)
        return


async def _escalar(
    ejecutar, telefono: str, motivo: str, detalle: str, *, ya_fallo: bool = False
) -> bool:
    """Escala a la dueña Y DICE SI LO LOGRÓ. True solo si el aviso quedó registrado de verdad.

    🔴 POR QUÉ EXISTE (auditoría 2026-08-02). Había SEIS `try/except` idénticos alrededor de los
    `return RESPUESTA_SEGURA` del modo uno, y otro más en el modo dos, y ninguno servía para lo
    que parecía servir: `ejecutar_tool` atrapa TODA excepción y devuelve `{"error": ...}`, así que
    ese `except` NO podía dispararse por un fallo de BD DENTRO de `pedir_ayuda` — pero el
    `return RESPUESTA_SEGURA` de abajo salía IGUAL, con el aviso sin crear. El bot le decía al
    cliente "eso te lo confirmo enseguida" y NADIE tenía encargo de contestarle: ni Intervencion,
    ni WhatsApp, ni una línea de log.

    El `try` SE QUEDA (`ejecutar` es INYECTABLE y los bancos pasan dobles que sí lanzan:
    `probar_recibo_visible.py` hace `raise AssertionError` ante una tool inesperada). Lo que se
    AÑADE es (1) mirar el resultado, (2) REINTENTAR una vez —`ejecutar_tool` abre una sesión NUEVA
    en cada llamada, así que un timeout o un pool agotado suele pasar al segundo intento— y (3) si
    tampoco, avisar por la ÚNICA vía que no depende de la base (`avisar_relevo_caido`).

    `ya_fallo`: en ESTE turno la escalada ya fracasó contra la base. No se vuelve a intentar. Con
    Postgres caído, un turno hacía 1 llamada del bucle + 2 de aquí + 2 más de la red del relevo =
    5 aperturas de sesión y 2 WhatsApps POR TURNO Y POR CLIENTE. El aviso ya salió por
    `avisar_relevo_caido` (que tiene su propio candado de 30 min por chat): insistir no salva a
    nadie, solo hunde más la base que ya se está ahogando.
    """
    if ya_fallo:
        logger.error(
            "RELEVO YA CAÍDO en este turno: NO se reintenta la escalada (%s) de %s — %s",
            motivo, telefono, (detalle or "")[:120],
        )
        return False
    args = {"motivo": motivo, "detalle": detalle}
    for intento in (1, 2):
        try:
            resultado = await ejecutar("pedir_ayuda", args, telefono)
        except Exception:  # noqa: BLE001 — el aviso no puede tumbar el turno
            logger.exception(
                "No se pudo escalar (%s) para %s [intento %d]", motivo, telefono, intento
            )
            resultado = None
        if isinstance(resultado, dict) and resultado.get("ok"):
            return True
        logger.error(
            "RELEVO CAÍDO: la escalada (%s) de %s NO quedó registrada [intento %d] — resultado=%r",
            motivo, telefono, intento, resultado,
        )
    await avisar_relevo_caido(telefono, motivo, detalle)
    return False


async def _llamar_openrouter(messages: list, tools: list, model: str) -> dict:
    # 🔴 LA FIRMA NO SE TOCA, Y ES DELIBERADO. Esta función es INYECTABLE (`llm=` en `responder`) y
    # media docena de bancos le pasan dobles con EXACTAMENTE estos tres parámetros —
    # `probar_dos_agentes` incluso vigila que no cambien. Por eso el turno, el cliente y el carril
    # llegan por CONTEXTO (`app/services/telemetria.py`) y no como argumentos nuevos.
    #
    # Y por eso el apunte va AQUÍ y no en `_llamar_con_fallback`: los dobles de los bancos no
    # pasan por esta función, así que correr los bancos NO escribe ni una fila en la BD real del
    # taller. Lo que se anota es lo que de verdad se le pagó a OpenRouter.
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
                # Temperatura = "dial de libertad". Se mantiene BAJA (0.15) a propósito: probado
                # 2026-07-03 subirla a 0.4/0.5 daba MUY poca variación extra (Haiku converge igual)
                # pero empezaba a fallar el precio cuando lo piden (info_producto devuelve varios
                # campos y a veces omitía el monto) — y NO se rompe el cobro. La naturalidad/variación
                # se logra QUITANDO las frases-ejemplo del prompt (que el modelo copiaba), no subiendo
                # la temperatura. (redactar_mensaje sí usa 0.7 porque ahí no hay tools ni cobro.)
                # provider.require_parameters: OpenRouter SOLO rutea a proveedores que soporten
                # TODO lo que mandamos (en especial las `tools`). Sin esto podía caer en un proveedor
                # que IGNORA las herramientas en silencio → el bot "dice" que agendó/cobró SIN llamar
                # a la herramienta (el bug del "te agendo" por la puerta del proveedor). Blinda el cobro.
                json={
                    "model": model,
                    "messages": messages,
                    "tools": tools,
                    "temperature": 0.15,
                    "provider": {"require_parameters": True},
                },
            )
            resp.raise_for_status()
            datos = resp.json()
        except Exception as e:  # noqa: BLE001 — se ANOTA el fallo y se RELANZA tal cual
            # No se cambia el comportamiento: `_llamar_con_fallback` sigue viendo la misma
            # excepción y sigue tirando del modelo de respaldo. Lo único nuevo es que ahora queda
            # ESCRITO cuál falló y por qué (402 sin saldo, 429, timeout, modelo inexistente) —
            # hasta hoy eso vivía en un `logger.warning` de un contenedor que se recrea.
            await registrar(paso="agente", modelo_pedido=model, t0=t0, ok=False, error=str(e))
            raise
    # 🔴 FUERA del `async with` (revisión cruzada): dentro, el apunte mantenía abierto el socket
    # HTTP hasta 2 s por cada una de las ~6 llamadas del turno.
    await registrar(paso="agente", modelo_pedido=model, t0=t0, datos=datos)
    return datos


def _codigo_http(e: Exception) -> int | None:
    """El código HTTP de un fallo de httpx, si lo hay. Nunca se adivina por el TEXTO del error:
    un `str(e)` que contenga '402' puede ser cualquier cosa, y aquí se decide si se enciende una
    alarma."""
    return getattr(getattr(e, "response", None), "status_code", None)


async def _anotar_si_es_falta_de_saldo(e: Exception, modelo: str) -> None:
    """🔴 EL 402 DEJA DE SER MUDO (2026-08-03) — sin segunda cuenta, pero con testigo.

    El fallback usa la MISMA llave que el principal (`config.py:26` y `:31`), así que un 402 de
    CUENTA los tumba a los dos: es la semana muda del 10-17 de julio, y hoy sigue igual de posible.
    Con una sola cuenta no hay a dónde caerse; lo que sí se puede es que se SEPA. Aquí se estampa
    el testigo que `/salud` lee (`services/salud.marcar_402`) y el 402 pasa de días de silencio a
    una alarma en segundos.

    Solo 401 y 402. Un timeout, un 500 o un 429 NO son quedarse sin dinero, y meterlos aquí sería
    encender la alarma por lo que no es — un detector con falsos positivos se acaba ignorando.
    """
    if _codigo_http(e) not in (401, 402):
        return
    logger.error(
        "🔴 OPENROUTER RECHAZÓ POR DINERO O POR LA LLAVE (%s) con el modelo %s. El respaldo usa la "
        "MISMA llave, así que NO salva: si es un 402 de cuenta, el bot se queda MUDO. Queda "
        "estampado para que /salud lo cante.", _codigo_http(e), modelo,
    )
    try:
        from app.services.salud import marcar_402

        await marcar_402(f"HTTP {_codigo_http(e)} con {modelo}")
    except Exception:  # noqa: BLE001 — el testigo JAMÁS puede tumbar el turno que vino a vigilar
        logger.exception("No se pudo estampar el testigo del rechazo de OpenRouter")


async def _llamar_con_fallback(messages: list, llm, modelo: str, tools: list) -> dict:
    """El ÚNICO sitio por el que el agente le habla al LLM con herramientas.

    `tools` es lo que el modelo VE — ya filtrado por lo que la proveedora dejó activo (fase 4).
    `_DISPATCH` sigue entero: las redes de seguridad y el worker de visión pueden ejecutar
    CUALQUIER herramienta aunque el modelo ya no la vea.

    ⚠️ EL FALLBACK SE SIGUE INTENTANDO AUNQUE EL PRIMERO DÉ 402, Y ESO ES A PROPÓSITO. Tentaba
    ahorrarse la segunda llamada (misma llave ⇒ mismo resultado), pero OpenRouter también devuelve
    402 cuando el saldo NO ALCANZA PARA ESE MODELO en concreto — y ahí un modelo más barato SÍ
    contesta. Saltárselo convertiría una alarma nueva en una venta perdida. Se anota y se sigue:
    este carril es el del dinero y no cambia de comportamiento ni un milímetro.
    """
    try:
        return await llm(messages, tools, modelo)
    except Exception as e:  # noqa: BLE001
        logger.warning("Modelo principal (%s) falló (%s), usando fallback", modelo, e)
        await _anotar_si_es_falta_de_saldo(e, modelo)
        try:
            return await llm(messages, tools, settings.openrouter_model_fallback)
        except Exception as e2:  # noqa: BLE001 — se anota y se deja subir EXACTAMENTE igual que antes
            await _anotar_si_es_falta_de_saldo(e2, settings.openrouter_model_fallback)
            raise


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


def _falta_devolver_saludo(texto: str, mensaje_usuario: str) -> bool:
    """True si el cliente saludó (o preguntó cómo estás) y el bot NO se lo devolvió.

    Es la MISMA condición que decide dentro de `_asegurar_saludo`, sacada aparte para poder
    preguntarla ANTES de pagar la consulta del silencio: así el turno normal —el 99%, donde no hay
    saludo pendiente— no toca la base de datos.
    """
    quiere_saludo = _cliente_saludo(mensaje_usuario) and not _bot_ya_saludo(texto)
    quiere_estado = _pregunta_como_estas(mensaje_usuario) and not _bot_respondio_estado(texto)
    return quiere_saludo or quiere_estado


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
        ahora = datetime.now(UTC) - timedelta(hours=4)  # Venezuela = UTC-4
        h = ahora.hour
        franja = "buenos días" if h < 12 else ("buenas tardes" if h < 19 else "buenas noches")
        nombre = f", {nombre_cliente}" if nombre_cliente else ""
        # 🔴 SIN SIGNOS DE APERTURA NI ADMIRACIÓN (auditoría 2026-08-02, PRM-16). Esto decía
        # "¡Hola, {nombre}, {franja}!" — y la regla 92 del prompt prohíbe TEXTUALMENTE el "¡" de
        # apertura y pide no llenar de signos de admiración. Es lo PRIMERO que lee cada cliente
        # nuevo y va DESPUÉS de todas las redes, así que ningún reintento podía arreglarlo:
        # el código desmentía al prompt en la primera línea de la primera conversación.
        partes.append(f"Hola{nombre}, {franja}")
    if quiere_estado:
        partes.append("Muy bien, gracias a Dios")
    # Las dos partes son DOS FRASES, y se pegaban con un espacio: con el saludo más común de
    # Venezuela —"hola como estas?", que enciende las dos— al cliente le llegaba
    # "Hola, Ana, buenas tardes Muy bien, gracias a Dios 💚". Se lee como un error de tecleo, y es
    # lo PRIMERO que lee un cliente nuevo. Con una sola parte no cambia nada (`join` de un
    # elemento), así que esto solo toca el caso que estaba roto.
    #
    # ⚠️ NO SE TOCA NI UNA PALABRA MÁS, NI EL 💚: eso es la VOZ de Whuilianny y la voz es de la
    # dueña (CLAUDE.md §3). Si algún día se quiere otro saludo, lo decide ELLA, no el código. Y el
    # "¡" que denunciaba el informe ya no está: lo quitó PRM-16 el 2026-08-02, y encima `_aplanar`
    # (workers/tasks.py:250) borra "¡" y "¿" en el ENVÍO, o sea después de todo lo de este módulo.
    return ". ".join(partes) + " 💚\n\n" + texto


# 🪦 AQUÍ VIVIÓ LA RED DE LA FICHA REPETIDA (2026-08-23 → 2026-08-24), QUITADA A PROPÓSITO.
# Partía el texto final en frases y borraba las "ya dichas". El 23-ago (22:19 VET) MUTILÓ EL
# COBRO de Maired: partía por CADA punto —el decimal incluido—, "7.799,52 Bs" salió como
# "799,52 Bs" y "$6.40" como "$6" (mensaje 6784, ENVIADO Y ENTREGADO: un precio diez veces
# menor, en el carril del dinero). Y su borrado por subcadena literal no cazaba la paráfrasis,
# así que la ficha repetida que motivó la red pasaba igual. Decisión de Erwin (24-ago): no
# repetirse es tarea del MODELO (la regla 66 de _REGLAS y la personalidad ya lo ordenan) — si
# no obedece, la palanca es subir de modelo, no censurar el texto desde el código.
# NO reintroducirla sin medir antes y después.


# ─── 🗓️ RED DEL DÍA IMPOSIBLE: el bot NO puede nombrar un día en que no se entrega ────
#
# 🔴 POR QUÉ EXISTE, Y POR QUÉ NO BASTÓ LA HERRAMIENTA. El 2026-08-22 se construyó
# `proxima_fecha_entrega` para que el modelo CONSULTARA el calendario en vez de calcularlo, y se
# le puso en el prompt que era OBLIGATORIA antes de nombrar cualquier fecha. Se desplegó, y en la
# siguiente prueba real el bot escribió:
#
#     🤖 "Te las dejo para mañana domingo, o prefieres el lunes?"        (sábado, 18:55)
#
# **Llamadas a la herramienta en esa conversación: CERO.** El modelo la vio en su lista, leyó que
# era obligatoria, y calculó la fecha de cabeza igual.
#
# Es **L40 al pie de la letra**: *el prompt SUGIERE, el código IMPIDE*. La herramienta le da al
# modelo la posibilidad de acertar; esta red le quita la de equivocarse.
#
# ⚠️ Lo que esta red NO hace: reescribir la fecha por su cuenta. Le devuelve el calendario REAL y
# le pide que rehaga el mensaje — igual que la red del dinero. Poner una fecha nosotros sería
# adivinar qué quiso decir, y eso es justo lo que estamos impidiendo.

_DIAS_ES = {
    "lunes": 0, "martes": 1, "miercoles": 2, "miércoles": 2, "jueves": 3,
    "viernes": 4, "sabado": 5, "sábado": 5, "domingo": 6,
}
# "mañana"/"pasado mañana"/"hoy" son fechas RELATIVAS: se resuelven contra el día de Venezuela.
_RELATIVOS = {"pasado manana": 2, "pasado mañana": 2, "manana": 1, "mañana": 1, "hoy": 0}

# La NEGACIÓN de entrega/atención en una cláusula ("los domingos no entregamos", "el lunes no
# se puede", "ya cerraron las entregas"). Es la misma filosofía del patrón del 'hoy' de más
# abajo: las formas de PROMETER son infinitas, pero las negaciones son una lista corta y
# estable. Opera sobre texto ya pasado por `_sin_acentos_min` (minúsculas, sin acentos).
_NIEGA_LA_ENTREGA = re.compile(
    r"\b(ya\s+no|no\s+(sal\w*|hay|se\s+puede|pued\w*|alcanz\w*|lleg\w*|entreg\w*"
    r"|trabaj\w*|atend\w*|abr\w*|hace\w*|hago|habr\w*)"
    r"|ya\s+(cerr\w*|pas\w*)|cerrar?on|cerrad[oa]s?|descansa\w*|fuera\s+de\s+hora\w*)\b"
)


def _dias_nombrados(texto: str) -> set[str]:
    """Los días que el bot PROMETE en su mensaje (nombre propio o relativo).

    Solo mira palabras completas: 'domingo' sí, pero 'mañana' dentro de 'mañana temprano' también
    cuenta —es una promesa de fecha igual— y en cambio NO se confunde con 'de la mañana', que es
    una hora. Por eso 'mañana' precedido de 'la/las/de la' se descarta.

    🔴 NEGAR NO ES PROMETER (1-sep, los guardias miran al cliente). "¿Entregas los domingos?" →
    "Los domingos no entregamos" es la respuesta CORRECTA, y esta red obligaba a reescribirla
    porque contaba el día NOMBRADO sin mirar si se estaba negando. La lección ya existía para
    'hoy' ("Para hoy ya cerraron las entregas" pasa); se extiende a los días con nombre y a
    mañana/pasado mañana — por CLÁUSULA, no por frase entera: en "el domingo no entregamos,
    pero el sábado sí te lo dejo" la negación absuelve al domingo y el sábado PROMETIDO sigue
    contando. El mecanismo de 'hoy' no se toca: estaba probado y funcionando.
    """
    t = " " + _sin_acentos_min(texto) + " "
    encontrados = set()
    for frase in re.split(r"(?<=[.!?\n])\s+", texto or ""):
        for clausula in re.split(r",|;|\bpero\b|\baunque\b|\bsino\b", _sin_acentos_min(frase)):
            c = " " + clausula + " "
            if _NIEGA_LA_ENTREGA.search(c):
                continue  # esa cláusula NIEGA/explica: no hay promesa que validar
            for d in ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"):
                if re.search(rf"\b{d}\b", c):
                    encontrados.add(d)
            if re.search(r"\bpasado\s+manana\b", c):
                encontrados.add("pasado manana")
            # "mañana" como DÍA, no como parte del día ("9 de la mañana"; "mañana temprano" sí)
            elif re.search(r"(?<!de la )(?<!la )(?<!las )\bmanana\b", c):
                encontrados.add("manana")
    # 🔴 "HOY" SE DETECTA SIEMPRE Y SE EXCLUYE POR NEGACIÓN — no al revés.
    #
    # La primera versión buscaba PATRONES DE PROMESA (`para hoy`, `hoy te lo…`) y se le escapaban
    # 4 de cada 5 formas reales de prometer el mismo día: *"Te lo puedo entregar hoy"*, *"Te lo
    # mando hoy"*, *"Te lo tengo hoy mismo"*, *"Te lo dejo hoy"*. Enumerar las formas de decir algo
    # en español es una lista que nunca se termina.
    #
    # Y al revés frenaba la frase CORRECTA que el propio documento pone en su Conversación 5:
    # *"Para hoy ya cerraron las entregas"* — obligando al bot a reescribir un mensaje que estaba
    # bien. Una red que corrige lo correcto se acaba desactivando.
    #
    # Invertido: si el mensaje dice "hoy", es candidato; y solo se descarta si ahí mismo se está
    # NEGANDO la entrega de hoy. Las negaciones sí son una lista corta y estable.
    if re.search(r"\bhoy\b", t) and not re.search(
        r"(ya\s+no|no\s+(sal|hay|se\s+puede|alcanz|llega|entrega)|ya\s+(cerr|pas)|"
        r"cerrar?on|fuera\s+de\s+hora)\w*", t
    ):
        encontrados.add("hoy")
    return encontrados


def _sin_acentos_min(x: str) -> str:
    import unicodedata
    return "".join(
        c for c in unicodedata.normalize("NFD", (x or "").lower())
        if unicodedata.category(c) != "Mn"
    )


async def _dias_imposibles(texto: str, calendario: dict | None) -> list[str]:
    """De los días que nombró el bot, cuáles NO se pueden. Devuelve [] si todo está bien.

    `calendario` es lo que devuelve `proxima_fecha_entrega`. **FAIL-OPEN**: sin calendario (falló
    la BD, o el turno no lo pidió) esta red no opina — frenar una venta por no poder comprobar
    sería peor que el bug que arregla.
    """
    # 🔴 `ejecutar` puede devolver CUALQUIER COSA: en los bancos está mockeado, y en producción
    # una tool puede devolver un error. Si no es un dict con `ok`, esta red no opina.
    if not isinstance(calendario, dict) or not calendario.get("ok"):
        return []
    try:
        buenas = {f.get("cuando", "") for f in (calendario.get("proximas_fechas") or [])
                  if isinstance(f, dict)}
        # ⚠️ `"".split()` devuelve [] (NO [""]), así que indexar [0] a ciegas revienta. Pasó:
        # `probar_herramientas` cazó un IndexError aquí en cuanto se corrió contra el entorno
        # local, porque su ejecutor mockeado devuelve un calendario sin `hoy_es`. El `try` lo
        # tapaba —fail-open— pero eso significa que la red **se apagaba en silencio**, que es
        # justo lo que no puede hacer una red de seguridad.
        buenas_dias = {_sin_acentos_min(c.split()[0]) for c in buenas if c and c.split()}
        hoy_sirve = bool(calendario.get("hoy_se_puede_entregar"))
        _hoy_es = (calendario.get("hoy_es") or "").split()
        if not _hoy_es or not buenas_dias:
            return []  # sin saber qué día es hoy no se puede resolver "mañana": no se opina
        hoy_nombre = _sin_acentos_min(_hoy_es[0])

        malos = []
        for d in _dias_nombrados(texto):
            if d == "hoy":
                if not hoy_sirve:
                    malos.append("hoy")
            elif d in ("manana", "pasado manana"):
                # ¿qué día de la semana cae? Se deduce del nombre de hoy, sin tocar el reloj.
                orden = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
                if hoy_nombre in orden:
                    salto = 2 if d == "pasado manana" else 1
                    cae = orden[(orden.index(hoy_nombre) + salto) % 7]
                    if cae not in buenas_dias:
                        malos.append(f"{d} (cae {cae})")
            elif d not in buenas_dias:
                malos.append(d)
        return malos
    except Exception:  # noqa: BLE001 — una red del calendario JAMÁS tumba una venta
        logger.exception("_dias_imposibles: fallo leyendo el calendario; se deja pasar")
        return []


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
#
# 🔥 AUTO-BLINDAJE (auditoría de arquitectura, 2026-07-13). La red tenía DOS agujeros por los que
# se colaba dinero inventado, y se demostraron EJECUTANDO el regex, no leyéndolo:
#
#   1. Solo veía "$28" y "28 bs". Era CIEGA a "28$", "28 dólares" y "28 USD" — y el propio prompt
#      le enseña al bot a escribir el precio pegado ("Pan keto 25$"). O sea: la red no sabía leer
#      el formato que el sistema le pide usar.
#   2. Peor: "son 5.000 Bs" PASABA. Al monto se le sacaban TODAS las lecturas posibles (5.000 se
#      leía como cinco mil Y como 5) y bastaba con que UNA estuviera autorizada. Como el 5 casi
#      siempre está (es el precio de algo), **cualquier cifra en bolívares con punto de miles se
#      autorizaba a sí misma**. Justo el carril donde el bot cobra de verdad.
#
# La lectura ambigua sigue existiendo donde SÍ es ambigua ("22.40" puede ser 22,40 o 2.240), pero
# "5.000" NO es ambiguo: en Venezuela son CINCO MIL. Un punto seguido de exactamente 3 cifras son
# MILES, y punto. Se acabó la auto-autorización.
#
# 🔴 Y AHORA, ADEMÁS, SEPARADO POR MONEDA (el bug del "$23", con una clienta REAL, 2026-07-13).
# Bolívares y dólares dejan de ser el mismo saco de números: el bot dijo "el total en bolívares es
# de $23 USD" y la red lo dejó pasar porque solo miraba el NÚMERO. Un dólar solo puede calzar
# contra dólares; un bolívar, contra bolívares.
#   · grupos 1-2-3 = DÓLARES   ($28 · 28$ · 28 dólares · 28 USD)
#   · grupos 4-5   = BOLÍVARES (28 Bs · 28 bolívares · Bs 16.591)
_MONTO_RE = re.compile(
    r"\$\s*([\d.,]+)"                                    # $28
    r"|([\d.,]+)\s*\$"                                   # 28$   (lo que el prompt le pide)
    r"|([\d.,]+)\s*(?:d[óo]lares|dolares|usd\b)"         # 28 dólares · 28 USD
    r"|([\d.,]+)\s*(?:bs\b|bol[íi]vares)"                # 28 Bs · 28 bolívares
    r"|(?:bs\.?|bol[íi]vares)\s+([\d.,]+)",              # Bs 16.591,05
    re.IGNORECASE,
)

_MILES_RE = re.compile(r"^\d{1,3}(\.\d{3})+$")      # 5.000 · 31.936  -> MILES, sin ambigüedad
_MILES_COMA_RE = re.compile(r"^\d{1,3}(,\d{3})+$")  # 5,000 · 31,936  -> MILES (formato gringo)
# 🔴 UN SEPARADOR CON 1 o 2 DÍGITOS DETRÁS ES UN DECIMAL, no miles: 10.00 · 22,40 · 0.5 · 1,5
# El separador de MILES lleva SIEMPRE tres ("1.000"), así que aquí no hay ambigüedad ninguna —
# nadie escribe mil como "1.00". Ver `_lecturas_del_monto` para lo que costaba tratarlo como dudoso.
_DECIMAL_RE = re.compile(r"^\d+[.,]\d{1,2}$")


def _numeros_de(texto: str) -> set[float]:
    """Todos los números que aparecen en un texto, en las dos lecturas posibles del formato
    venezolano (1.234,56 y 1,234.56), para no dar por inventado algo que sí está.

    Se usa para construir la lista de AUTORIZADOS: aquí ser generoso es SEGURO (autorizar de más
    solo evita frenar un mensaje bueno). Lo que NO puede ser generoso es la lectura de lo que el
    bot ESCRIBE — para eso está `_lecturas_del_monto`."""
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


def _lecturas_del_monto(crudo: str) -> set[float]:
    """Las lecturas PLAUSIBLES de un monto que el bot escribió. A diferencia de `_numeros_de`,
    esta NO regala lecturas: si el número lleva punto de MILES ("5.000"), la única lectura es
    CINCO MIL — no 5. Ese regalo era el agujero: con él, cualquier monto en bolívares se
    autorizaba solo, porque su lectura chiquita casi siempre estaba en el catálogo."""
    crudo = (crudo or "").strip().rstrip(".,")
    if not crudo:
        return set()
    # Miles inequívocos: 5.000 / 31.936 / 1.234.567 (y su gemelo gringo 5,000).
    if _MILES_RE.match(crudo):
        return _num(crudo.replace(".", ""))
    if _MILES_COMA_RE.match(crudo):
        return _num(crudo.replace(",", ""))
    if "." in crudo and "," in crudo:
        # Lleva los dos: manda el ÚLTIMO (es el decimal). 31.936,21 -> 31936.21 · 31,936.21 -> igual
        if crudo.rfind(",") > crudo.rfind("."):
            return _num(crudo.replace(".", "").replace(",", "."))
        return _num(crudo.replace(",", ""))
    # 🔴 DECIMAL INEQUÍVOCO: un separador con 1 o 2 dígitos detrás (10.00 · 22,40 · 0.5).
    #
    # LA BANDA CIEGA DEL 1% (bug del camino del DINERO, cerrado el 2026-08-21 tras reproducirlo).
    # Esta línea daba LAS DOS lecturas —10.00 ⇒ {10, 1000}— por considerarlo "ambiguo de verdad".
    # No lo es: el separador de miles lleva SIEMPRE tres dígitos. Y como `autorizados_por_moneda`
    # usa esta misma función para construir la lista blanca a partir de lo que devuelven las
    # HERRAMIENTAS, un total de $10.00 autorizaba también el 1000 — y con la tolerancia del 1% de
    # `_calza`, toda la banda 990–1010. Medido antes del arreglo:
    #
    #     herramienta: "Total: $10.00"  ⇒  AUTORIZADOS USD = [10.0, 1000.0]
    #     el bot escribe "$1000" → PASA · "$995" → PASA · "$1010" → PASA
    #     el bot escribe "$12"   → frena  ← más estricta con un error de $2 que con uno de $990
    #
    # O sea: la red que existe para que el bot no invente dinero autorizaba cobrar CIEN VECES el
    # precio. Se arregla aquí, en la LECTURA, y no en `_calza`: el 1% de tolerancia es correcto
    # (los redondeos del modelo existen); lo que estaba mal era meter un número cien veces mayor
    # en la lista blanca. Tocar `_calza` habría estrechado la tolerancia de TODOS los montos para
    # tapar un problema que solo tenían los que llevan decimales.
    if _DECIMAL_RE.match(crudo):
        return _num(crudo.replace(",", "."))
    # Lo que queda sí es dudoso de verdad (3+ dígitos detrás sin ser patrón de miles): las dos.
    return _num(crudo.replace(",", ".")) | _num(crudo.replace(",", "").replace(".", ""))


def _num(s: str) -> set[float]:
    try:
        return {round(float(s), 2)}
    except ValueError:
        return set()


def _montos_del_mensaje(texto: str) -> list[tuple[str, str, set[float]]]:
    """Los montos de DINERO que hay en un texto: (MONEDA, tal como se escribió, lecturas posibles).

    🔴 SOLO cuenta como dinero lo que lleva MARCA de dinero ($ · Bs · bolívares · dólares · USD).
    Un número pelado NO es dinero — y ese era el agujero por el que se coló el "$23".
    """
    montos: list[tuple[str, str, set[float]]] = []
    for m in _MONTO_RE.finditer(texto or ""):
        crudo = m.group(1) or m.group(2) or m.group(3) or m.group(4) or m.group(5) or ""
        moneda = "USD" if (m.group(1) or m.group(2) or m.group(3)) else "BS"
        lecturas = _lecturas_del_monto(crudo)
        if lecturas:
            montos.append((moneda, crudo, lecturas))
    return montos


def autorizados_por_moneda(*textos: str) -> tuple[set[float], set[float]]:
    """(dólares_ok, bolívares_ok) sacados de un texto: los montos que el CÓDIGO ya dio por buenos
    (el catálogo, lo que devolvió una herramienta, lo que escribió el cliente).

    🔥 ANTES esto era `_numeros_de(prompt entero)`: se tragaba **todos** los numerales — los
    `id_para_pedir` del catálogo, la hora, la fecha, las cédulas y las cuentas bancarias. Por eso
    el bot pudo decir "$23": el 23 **era el ID de una variante**, no un precio. La red lo dio por
    bueno y le mandó a una clienta REAL un total inventado. Ahora, para entrar en la lista, un
    número tiene que estar escrito COMO DINERO.
    """
    usd: set[float] = set()
    bs: set[float] = set()
    for t in textos:
        for moneda, _crudo, lecturas in _montos_del_mensaje(t or ""):
            (usd if moneda == "USD" else bs).update(lecturas)
    return usd, bs


def _calza(lecturas: set[float], permitidos: set[float]) -> bool:
    """Tolerancia: los redondeos del modelo (0,50 o 1%)."""
    return any(
        abs(lectura - autorizado) <= max(0.5, autorizado * 0.01)
        for lectura in lecturas
        for autorizado in permitidos
        if lectura != 0
    )


# 🔴 LA FRASE ASESINA (caso REAL con una clienta, 2026-07-13):
#     "El total en bolívares es de $23 USD a la tasa BCV del día."
# La cifra está escrita como DÓLARES ($23) pero la frase dice BOLÍVARES. Con la tasa a 721,35, el
# total en bolívares eran ~Bs 16.591. La red vieja miraba el NÚMERO (23) y no la MONEDA, así que
# lo dejó pasar. Aquí se caza: si el bot presenta una cifra COMO bolívares, esa cifra TIENE que ser
# el monto en bolívares que calculó el código. Nunca un dólar disfrazado.
_DICE_BOLIVARES = re.compile(r"bol[íi]vares?\b|\bbs\b\.?", re.IGNORECASE)

# 🔴 UN TOTAL ES UN HECHO, NO UN VERBO (auditoría 2026-08-02, PRM-2).
#
# Esta lista traía `queda(ría) en` y `sería(n)`. Con eso, la red **dependía de cómo estuviera
# conjugada la frase y no de lo que la frase decía**:
#
#     "El pan keto queda en 25$"  → se marcaba: "dijo que es un TOTAL y NO lo calculó el sistema"
#     "El pan keto cuesta 25$"    → pasaba limpio
#
# El MISMO hecho (un precio suelto del catálogo, autorizado, de un solo producto) y dos destinos
# opuestos. Y no es un caso raro: la regla 5 del bloque del catálogo le ORDENA al bot dar ese
# precio de una, inline, sin llamar a ninguna herramienta — así que `usd_de_herramienta` está
# VACÍO justo cuando eso pasa. Era la respuesta más frecuente del negocio ("¿a cómo el pan?")
# muriendo por el verbo. En modo `dos` no hay ni reintento: `RESPUESTA_SEGURA` directa.
#
# Un TOTAL es una afirmación sobre una SUMA, y en español eso se dice **con la palabra**
# ("el total", "en total", "todo junto", "por todo", "sumando") o **nombrando el pedido**
# ("tu pedido queda en…"), nunca con un verbo copulativo suelto.
#
# ⚠️ Lo que NO se pierde: el chequeo 1 (por moneda) sigue cazando toda suma inventada que no
# calce con un monto autorizado — que es el caso general. El chequeo 2 solo añadía valor cuando
# la suma inventada COINCIDÍA por casualidad con un precio del catálogo ($20 + $5 = $25 = Pan
# Keto), y esa coincidencia se sigue cazando en cuanto la frase se llama a sí misma un total.
_DICE_TOTAL = re.compile(
    r"\btotal(es)?\b"
    r"|\btodo\s+(junto|eso|ello|completo)\b"
    r"|\bpor\s+todo\b"
    r"|\bsumando\b"
    r"|\b(el|tu|su|la)\s+(pedido|orden|compra)\s+(te\s+)?"
    r"(queda|sale|ser[íi]a|es|cuesta|sube|son|va)\b",
    re.IGNORECASE,
)


def _dinero_inventado(
    texto: str,
    usd_ok: set[float],
    bs_ok: set[float],
    usd_de_herramienta: set[float] | None = None,
) -> list[str]:
    """Los montos que el bot dijo y que NO salieron del código.

    TRES redes, y cada una tapa un agujero REAL que se demostró rompiéndola:

    1. **Por MONEDA.** Un dólar solo calza contra dólares autorizados; un bolívar contra bolívares.
    2. **El TOTAL solo lo pone una HERRAMIENTA.** El catálogo autoriza PRECIOS SUELTOS, no totales.
       Sin esto, el bot suma de cabeza y la suma se auto-autoriza por casualidad:
       $20 + $5 = **$25**… y $25 es el precio del Pan Keto. La red lo daba por bueno.
       Si el texto habla de un TOTAL, ese monto tiene que venir de `registrar_pedido` /
       `generar_datos_pago` — no del catálogo, no de su cabeza.
    3. **BOLÍVARES + DÓLARES ⇒ tiene que haber un monto en bolívares de verdad.** El caso real fue
       "El total en bolívares es de $23 USD". La primera versión solo cazaba ESA redacción; los
       atacantes la rompieron dándole la vuelta ("el total es $23 en bolívares", "Total en
       bolívares:\\n$23", "El total en bolívares. Son $23"). La segunda versión miraba el párrafo,
       pero **solo si aparecía la palabra "total"** — y bastaba conjugar distinto para pasar:
       *"En bolívares son $23 a la tasa del día"*, *"Son $23 en bolívares"*. Ahora no se exige esa
       palabra ni ninguna otra: si un párrafo habla de bolívares y enseña dólares, tiene que
       traer también el bolívar AUTORIZADO. (Auditoría 2026-08-02, DIN-2.)
    """
    malos: list[str] = []

    # 1) cada monto, contra su propia moneda
    for moneda, crudo, lecturas in _montos_del_mensaje(texto):
        if not _calza(lecturas, usd_ok if moneda == "USD" else bs_ok):
            malos.append(f"{crudo} ({moneda})")

    # 🔴 EL PÁRRAFO ES LA UNIDAD, NO LA FRASE (auditoría 2026-08-02, DIN-2).
    # Antes se partía por `\n\n` **y** por frase, y el bucle empezaba con
    # `if not montos or not _DICE_TOTAL.search(parrafo): continue`. Dos agujeros:
    #   a) El `continue` se saltaba los chequeos 2 Y 3 de golpe cuando no aparecía la palabra
    #      "total", así que **"En bolívares son $23 a la tasa del día" pasaba limpio** — la frase
    #      asesina otra vez, solo que conjugada distinto. Hoy el chequeo 3 ya NO exige esa palabra
    #      (ni ninguna otra): el `_DICE_TOTAL` de abajo solo condiciona al chequeo 2.
    #   b) Partir por FRASE deja la moneda en una y la cifra en la otra:
    #      "El total en bolívares." + "Son $23." — cada mitad, por separado, parece inocente.
    for parrafo in re.split(r"\n\s*\n", texto or ""):
        montos = _montos_del_mensaje(parrafo)
        if not montos:
            continue

        # 2) un TOTAL en dólares tiene que venir de una herramienta.
        #    Va por FRASE porque "es un total" es una afirmación local: el catálogo autoriza
        #    precios sueltos, y un párrafo que cita tres precios y luego da el total no debe
        #    marcar los tres.
        if usd_de_herramienta is not None:
            for frase in re.split(r"(?<=[.!?])\s+", parrafo):
                if not _DICE_TOTAL.search(frase):
                    continue
                for moneda, crudo, lecturas in _montos_del_mensaje(frase):
                    if moneda == "USD" and not _calza(lecturas, usd_de_herramienta):
                        malos.append(f"{crudo} (dijo que es un TOTAL y NO lo calculó el sistema)")

        # 3) si el párrafo habla de BOLÍVARES y enseña DÓLARES, tiene que haber además un
        #    bolívar de verdad. Ya NO exige la palabra "total": presentar un dólar como si fuera
        #    el monto en bolívares es igual de grave lo llame como lo llame.
        #    Se exige `hay_usd` a propósito: si el párrafo solo trae bolívares, el chequeo 1 ya
        #    los validó contra `bs_ok` y volver a marcarlos sería frenar de más.
        if _DICE_BOLIVARES.search(parrafo):
            hay_usd = any(moneda == "USD" for moneda, _c, _l in montos)
            hay_bs_bueno = any(
                moneda == "BS" and _calza(lecturas, bs_ok) for moneda, _c, lecturas in montos
            )
            if hay_usd and not hay_bs_bueno:
                malos.append(
                    f"{montos[0][1]} (lo llamó BOLÍVARES y no es el monto en bolívares del sistema)"
                )

    return list(dict.fromkeys(malos))


# ─── RED DEL RELEVO: una promesa sin aviso es un cliente perdido ─────────────────────
#
# El 2026-07-12, en una prueba REAL por WhatsApp, el bot dijo "eso puntual te lo confirmo con
# la dueña"... y en la base había CERO avisos. El cliente se queda esperando PARA SIEMPRE y la
# dueña nunca se entera. Le pasó también en el ensayo (a Sofía 3 veces, a María con el envío a
# Caracas, a Diego con la torta del cumpleaños: el ticket más grande).
#
# Si el bot PROMETE averiguar algo y NO llamó a `pedir_ayuda` en ese turno, el código crea el
# aviso solo. La promesa deja de ser humo.
#
# 🔥 LA MISMA PROMESA, POR OTRA PUERTA (ensayo del retomar, 2026-07-13). Al cliente que pidió
# hablar con una persona, el bot le contestó: **"Whuilianny te atiende en un momento 💚"** — y NO
# llamó a `pedir_ayuda`. La red no lo vio (no prometió *averiguar* nada: prometió UNA PERSONA), y
# el modelo no escaló. Resultado: el cliente esperando a alguien que nunca fue avisado. Es el
# mismo agujero de siempre con otra cara. Prometer que un humano va a entrar ES una promesa.
# Sobre-avisar cuesta un aviso de más en la bandeja; NO avisar cuesta el cliente.
#
# 🔴 EL PRONOMBRE OTRA VEZ (auditoría 2026-08-02, PRM-13). La red solo miraba `lo`, y el prompt
# empuja al modelo justo a la formulación que no ve. La regla 67 le dice **"La HORA no la cierres
# tú: la coordina la dueña después"**, así que el bot escribe lo natural en español:
#
#     "La hora te LA confirmo luego"   → _promete_averiguar = False  (¡y es una promesa!)
#     "Eso te LO confirmo enseguida"   → True
#
# La misma frase, cambiando el género del objeto. Y la paradoja: la redacción de intermediaria que
# la regla 70 PROHÍBE ("le pregunto a la dueña y te aviso enseguida") sí disparaba. O sea, la red
# premiaba lo prohibido y castigaba lo que el prompt ordena. Ahora el pronombre es cualquiera
# (lo/la/los/las/le) y los adverbios cubren los diminutivos venezolanos ("enseguidita",
# "ahoritica") que son EXACTAMENTE como habla esta voz.
_PROMESA_RE = re.compile(
    r"(d[ée]jame\s+(que\s+)?((te\s+)?(lo|la|los|las|le)\s+)?"
    # `verifiqu` no es un adorno: en subjuntivo la raíz cambia de c a qu ("déjame que lo
    # VERIFIQUE") y el stem `verific` no la ve. Los otros verbos no cambian (consulte, revise,
    # averigüe, pregunte). Lo cazó el banco, no la lectura — como siempre.
    #
    # 🔴 "DÉJAME CONFIRMAR TU PEDIDO" NO ES UNA PROMESA (caso real, 24-ago 00:39, mensaje
    # 6946): es un RECAP — le muestra el pedido AL CLIENTE ahora mismo y le pregunta "¿está
    # bien así?". La red lo cazó como promesa y le mandó a la dueña un "el bot te necesita"
    # a las 00:39 AM por un mensaje perfecto. El lookahead lo excluye SOLO cuando a
    # `confirm…` le sigue "el/tu pedido/orden/compra" SIN "con la dueña/Whuilianny/ella"
    # en la misma frase — "déjame confirmar tu pedido CON LA DUEÑA" sigue siendo promesa.
    r"(verific|verifiqu|consult|revis|averigu|pregunt"
    r"|confirm(?!\w*\s+(el|tu)\s+(pedido|orden|compra)\b"
    r"(?![^.\n?]*\bcon\s+(la\s+due[ñn]a|whuilianny|ella)\b)))"
    r"|perm[ií]teme\s+(que\s+)?((te\s+)?(lo|la|los|las|le)\s+)?"
    r"(verific|verifiqu|consult|revis|averigu|pregunt)"
    r"|((te\s+)?(lo|la|los|las)|eso)\s+(verifico|consulto|averiguo|pregunto|confirmo)\b"
    r"|te\s+((lo|la|los|las)\s+)?(confirmo|aviso|averiguo|pregunto)\s+"
    r"(enseguid\w*|ya|luego|en un momento|apenas|ahorit\w*|más tarde|mas tarde|en breve"
    r"|despu[ée]s|m[áa]s adelante)"
    r"|te\s+((lo|la|los|las)\s+)?confirmo\s+(con|eso|esa|ese)"
    r"|(lo|la)\s+confirmo\s+con"
    r"|voy\s+a\s+(verificar|consultar|averiguar|preguntar)"
    # Prometer que entra UNA PERSONA (y no avisarle a nadie) deja al cliente esperando igual:
    r"|(whuilianny|la\s+due[ñn]a|ella)\s+te\s+(atiende|contesta|responde|escribe|confirma|habla)"
    r"|te\s+(paso|comunico|pongo)\s+con\s+(whuilianny|la\s+due[ñn]a|una\s+persona|alguien|ella)"
    r"|te\s+(atiende|contesta|responde)\s+(en\s+un\s+momento|enseguida|ahorita|ya|en\s+breve))",
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


async def _pedido_reciente(telefono: str):
    """El último pedido NO cancelado del cliente (o None). Es la vista de la red del pedido
    fantasma sobre la BD: las PALABRAS solo levantan la sospecha; la sentencia la dicta el
    ESTADO — el mismo principio por el que la red del día imposible consulta el calendario
    antes de regañar. Nunca lanza: si la BD falla, devuelve None y la red se comporta como
    siempre (fail-safe hacia el regaño clásico, que es el lado que protege el caso de julio)."""
    try:
        factory = get_session_factory()
        async with factory() as session:
            return (
                await session.execute(
                    select(Pedido)
                    .where(
                        Pedido.cliente_telefono == telefono,
                        Pedido.estado != "cancelado",
                    )
                    .order_by(Pedido.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
    except Exception:  # noqa: BLE001 — consultar la BD nunca puede tumbar el turno
        return None


def _correccion_fantasma(pedido) -> str:
    """El regaño de la red del pedido fantasma, dictado por el ESTADO de la BD — nunca miente.

    🔴 POR QUÉ EXISTE (25-ago-2026, cazado por Maired). Con el pedido #2073 YA PAGADO y
    confirmado, la clienta dio la hora de entrega y el modelo respondió "Perfecto, te lo
    anoto. La dueña te confirma la hora..." — anotaba la HORA, no un pedido. El regaño de
    entonces era uno solo y DOBLEMENTE falso: afirmaba "en la base de datos NO existe"
    (existía, pagado) y ordenaba "llama AHORA a registrar_pedido". El modelo obedeció la
    mentira y fabricó un pedido DUPLICADO (#2074) con su cobro completo.

    Y la lista de palabras NO se puede afinar para evitarlo (probado contra los regex reales:
    "anoto tu dirección", "registro tu comprobante y la dueña lo revisa", "anoto que eres
    alérgica al maní" y hasta la frase VERDADERA "tu pedido ya está pagado" disparan igual —
    además _REGLAS le ORDENA al modelo ese vocabulario). Por eso la corrección deja de
    adivinar: consulta el estado y le dice al modelo LA VERDAD, con las dos salidas."""
    if pedido is None:
        # El caso original de julio: CERO pedidos en la BD y el bot dijo "te agendo empanadas".
        # Este regaño se conserva LITERAL: aquí la mentira del bot es real y la orden es justa.
        return (
            "[SISTEMA] ACABAS DE DECIR QUE EL PEDIDO QUEDÓ AGENDADO Y NO LO "
            "REGISTRASTE. En la base de datos NO existe. El cliente se irá "
            "creyendo que tiene su pedido y la dueña no tendrá nada que cocinar. "
            "Llama AHORA a `registrar_pedido` con el `variante_id` (el "
            "`id_para_pedir` del catálogo), la cantidad y la fecha de entrega. Si "
            "te falta algún dato, PREGÚNTASELO al cliente en vez de afirmar que "
            "ya está. No le menciones al cliente este aviso."
        )
    return (
        f"[SISTEMA] Usaste un verbo de registro (anotar/agendar/registrar) pero en ESTE "
        f"turno no registraste nada. OJO: el cliente YA TIENE el pedido #{pedido.id} "
        f"(estado: {pedido.estado}). NO llames a `registrar_pedido` para repetirlo — "
        f"duplicarías su pedido y su cobro. Si el cliente pidió productos NUEVOS que no "
        f"están en ese pedido, regístralos de verdad ANTES de afirmarlo. Si solo estás "
        f"confirmando un detalle (la hora, la dirección, una referencia, una preferencia), "
        f"reescribe tu mensaje diciéndolo con naturalidad, SIN afirmar que registraste un "
        f"pedido. No le menciones al cliente este aviso."
    )


# ══════════════════════════════════════════════════════════════════════════════════════
#  RED DEL CIERRE: el bot se traba pidiendo un SABOR que es opcional
# ══════════════════════════════════════════════════════════════════════════════════════
#
# 🔴 EL CASO MEDIDO (2026-08-21, smoke de 5 turnos contra el bot real, reproducido dos veces):
# la clienta llega con producto, cantidad, fecha y forma de entrega… y quedan **0 PEDIDOS EN LA
# BASE**. El bot pidió el sabor en los CINCO turnos y en el último no llamó a NINGUNA herramienta:
# bucle puro. La venta se pierde en silencio — nadie se entera, ni la dueña.
#
# LA CAUSA no es que invente el sabor: los sabores son REALES, viven en `productos.descripcion`
# ("chocolate, limón pistacho, canela naranja, chocomerey"). La causa es que trata un campo
# **OPCIONAL** como si fuera bloqueante. Tres sitios empujan a pedirlo y NINGUNO dice que se puede
# cerrar sin él: `_REGLAS` ("pásalo SIEMPRE en `opciones`"), la personalidad de la BD ("pregunta
# lo que falte: tamaño, sabor, cuántos") y el schema de la tool ("la dueña lo necesita para
# cocinar"). Y sin embargo el propio schema lo declara opcional (`required` = variante_id +
# cantidad) y el pedido 1078 de esta misma base es exactamente ese producto con `opciones: null`.
#
# 🟢 Y LOS DOCUMENTOS DE WHUILIANNY ZANJAN LA DUDA que quedó abierta en `prompt_proxima_sesion.md`
# ("¿se puede registrar sin el sabor, o es obligatorio?"). Ella ACEPTA PRIMERO y pide el sabor
# DESPUÉS, en el mismo minuto (CLI-051):
#
#   [20:39] "Recuerda que yo trabajo bajo pedido. Para mañana sí te lo puedo tener."   ← acepta
#   [20:39] "Me vas a decir, por favor, qué sabores quieres… ahí salen los toppings."  ← y luego
#
# **Nunca bloquea el pedido por el sabor.** No hacía falta decisión de nadie: estaba en los audios.
#
# ⚠️ LO QUE ESTA RED **NO** HACE: registrar el pedido por su cuenta. Forzar el registro antes de
# que el cliente confirme sería peor que el bug (lo advierte el comentario de `_AFIRMA_PEDIDO`, y
# por eso `_afirma_pedido_registrado` deja pasar a propósito las preguntas y los condicionales).
# Lo que hace es QUITAR EL FALSO BLOQUEO: le dice que el sabor es opcional y que no lo repregunte.
# Quien decide si ya tiene bastante para registrar sigue siendo el modelo.
# 🔴 Y NO ES SOLO EL SABOR: es una CLASE de fallo. Al quitar el bloqueo del sabor y volver a
# medir, el bot cerró la venta en una corrida (pedido 1156 en la base) y en la otra se trabó
# igual… **pidiendo el "nombre completo"**. Mismo patrón, otro dato: se inventa un requisito que
# `registrar_pedido` no pide (su `required` son SOLO `variante_id` y `cantidad`) y bloquea el
# cierre con él. Por eso la lista cubre la clase, no un caso.
#
# El teléfono está aquí por algo medido el 08-21: el bot le pidió *"me confirmas tu número de
# teléfono"* **a alguien que le está escribiendo por WhatsApp**.
# 🔴 LA HORA ENTRÓ EL 2026-08-22, y es el TERCER requisito inventado que se mide (sabor →
# nombre completo → hora). Y aquí no hay ni que discutirlo: la personalidad de la BD dice
# literalmente *"La hora exacta no la cierres tú: la coordina Whuilianny"*, y el propio schema
# de `registrar_pedido` lo repite en el campo `entrega` ("La hora NO se cierra aquí: la coordina
# la dueña después"). O sea que el bot se traba pidiendo un dato que sus DOS fuentes de verdad
# le prohíben pedir. Ojo con la forma: `qué hora` y `hora exacta/de entrega`, NUNCA `horario`
# (el horario del negocio SÍ se informa, y es lo correcto).
_DATO_OPCIONAL = re.compile(
    r"\b(sabor(es)?|relleno(s)?|topping(s)?|mezcla|combinaci[óo]n"
    r"|nombre\s+completo|apellido|correo|email|c[óo]rreo"
    r"|n[úu]mero\s+de\s+tel[ée]fono|tel[ée]fono\s+de\s+contacto"
    r"|qu[ée]\s+hora|hora\s+(exacta|aproximada)|hora\s+de\s+(la\s+)?(entrega|retiro))\b",
    re.I,
)

# Cómo se LLAMA cada dato cuando hay que nombrárselo al modelo. El aviso decía siempre "EL SABOR
# (o el relleno)" — y con la hora dentro eso pasaba a ser MENTIRA en el aviso que precisamente
# le pide al modelo que deje de inventar. Se nombra el dato REAL que acaba de pedir.
_COMO_SE_LLAMA = (
    (re.compile(r"sabor", re.I), "el sabor"),
    (re.compile(r"relleno", re.I), "el relleno"),
    (re.compile(r"topping", re.I), "el topping"),
    (re.compile(r"mezcla|combinaci[óo]n", re.I), "la mezcla"),
    (re.compile(r"nombre\s+completo", re.I), "el nombre completo"),
    (re.compile(r"apellido", re.I), "el apellido"),
    (re.compile(r"correo|email|c[óo]rreo", re.I), "el correo"),
    (re.compile(r"tel[ée]fono", re.I), "el número de teléfono"),
    (re.compile(r"hora", re.I), "la hora exacta"),
)

# Una PREGUNTA DE ELECCIÓN PELADA no nombra su objeto: el objeto es la LISTA que la precede.
_ELECCION_PELADA = re.compile(r"^(y|entonces|bueno|ok)?\s*(de|por)?\s*cu[áa]l(es)?\b", re.I)

# 🔴 PEDIR UN DATO NO SIEMPRE LLEVA SIGNO DE PREGUNTA — y esto lo destapó la propia Whuilianny.
# En CLI-051, tal cual: *"Me vas a decir, por favor, qué sabores quieres… ahí salen los
# toppings."* Es una petición en toda regla y NO es una pregunta. Mirando solo las preguntas, un
# bot que escribiera igual ("Necesito el sabor para seguir.", "Dime el relleno.") pedía el dato
# turno tras turno y la red no contaba ni uno.
#
# ⚠️ POR QUÉ NO BASTA CON "la frase tiene la palabra sabor": porque entonces DESCRIBIR los
# sabores contaría como pedirlos, y eso rompe el caso que esta red tiene prohibido tocar (el
# turno 1 del smoke: *"traen 6 unidades con varios sabores para elegir (chocolate…)"*). Hacen
# falta LAS DOS cosas: una marca de PETICIÓN y el dato. Describir no pide nada.
_PIDE_QUE_LE_DIGA = re.compile(
    r"\b(me\s+vas\s+a\s+decir|d[íi]game|dime|me\s+dices|me\s+dir[íi]as"
    r"|me\s+confirmas|conf[íi]rmame|me\s+indicas|ind[íi]came"
    r"|necesito|me\s+falta|falta\s+que|espero\s+(que\s+me\s+digas|tu))\b",
    re.I,
)

# 🔴 EL FRENO QUE HACE SEGURA LA REGLA DE ARRIBA. Un TAMAÑO **jamás** es un dato opcional: cambia
# el precio (Kombucha 350ml $4 / 700ml $7 — la fuga de $3 que costó la cirugía del 2026-07-13).
# Si la lista que precede a la pregunta son tamaños, esta red NO puede tocarla: su aviso dice
# "registra con lo que tienes", y aplicado a un tamaño eso es ordenarle al bot que ADIVINE el
# precio. Para ese caso está la RED DEL TAMAÑO ADIVINADO, que hace exactamente lo contrario.
_TAMANO_EN_LISTA = re.compile(
    r"\b\d+\s*(g|gr|grs|gramos|kg|kilos?|ml|lt|l|litros?|cc|onzas?|oz|unidades?)\b"
    r"|\b(kilo|kilos|medio\s+kilo|peque[ñn][oa]s?|median[oa]s?|grandes?|chic[oa]s?)\b",
    re.I,
)


def _es_lista_pelada(frase: str) -> bool:
    """True si la frase es SOLO una enumeración ('Tenemos: limón, naranja, piña y cambur').

    Es lo que separa los dos casos medidos, que a simple vista son el mismo:

      🔴 SÍ es lista pelada (el bot REAL, 08-22): "Tenemos: limón, zanahoria, naranja, piña,
         vainilla, marmoleada, manzana canela y cambur." + "Cuál te provoca?"
      ✅ NO lo es (turno 1 del smoke, el que NO debe disparar): "Te recomiendo las Galletas New
         York, que traen 6 unidades con varios sabores para elegir (chocolate, limón pistacho,
         canela naranja, chocomerey)." + "De cuál te llevo?"

    La diferencia no es la palabra "sabores" —está en las dos— sino la FORMA: en la primera el
    mensaje ENTERO es la lista; en la segunda la lista es un paréntesis colgado de una frase que
    RECOMIENDA un producto. Por eso se exige que, quitada una cabecera corta ('Tenemos:'), lo que
    quede sean ≥3 trozos y NINGUNO tenga pinta de cláusula (>3 palabras).
    """
    cuerpo = (frase or "").strip().strip("¿").rstrip(".!?").strip()
    if ":" in cuerpo:
        cabeza, _, resto = cuerpo.partition(":")
        if len(cabeza.split()) > 4:
            return False   # la "cabecera" es una frase entera: no es una lista pelada
        cuerpo = resto
    cuerpo = re.sub(r"\s+\b[yo]\b\s+", ", ", cuerpo, flags=re.I)  # "manzana canela y cambur"
    trozos = [t.strip() for t in cuerpo.split(",") if t.strip()]
    if len(trozos) < 3:
        return False
    return all(len(t.split()) <= 3 for t in trozos)


def _dato_opcional_pedido(texto: str) -> str | None:
    """El dato OPCIONAL por el que el bot está preguntando AHORA, o None.

    Se mira FRASE POR FRASE y solo cuentan las PREGUNTAS, y eso no es un detalle: en el turno 1
    del smoke el bot escribió *"…traen 6 unidades con varios sabores para elegir (chocolate,
    limón pistacho…). De cuál te llevo?"*. Ahí la palabra "sabores" está en una frase que
    DESCRIBE, y la pregunta ("de cuál te llevo?") es sobre el PRODUCTO, no sobre el sabor —
    contar ese turno haría disparar la red cuando el bot está haciendo justo lo que debe.

    🔴 EL HUECO QUE ESTO CIERRA (medido el 2026-08-22 contra el bot real, y el más natural de
    todos): la lista y la pregunta van en frases DISTINTAS, así que ninguna de las dos cumplía
    las dos condiciones a la vez —

        "Tenemos: limón, zanahoria, naranja, piña, vainilla, marmoleada, manzana canela y cambur."
        "Cuál te provoca?"

    la primera tiene los sabores pero no es pregunta; la segunda es pregunta pero no tiene
    ninguna palabra de la lista. La red no veía NADA y el bucle seguía. Se resuelve resolviendo
    el objeto de la pregunta pelada: es la lista de la frase anterior (`_es_lista_pelada`), y
    **nunca** si esa lista son TAMAÑOS (`_TAMANO_EN_LISTA`).
    """
    anterior = ""
    for frase in re.split(r"(?<=[.!?\n])\s+", texto or ""):
        limpia = frase.strip()
        if not limpia:
            continue
        # `_aplanar` (workers/tasks.py) borra los "¿" en el ENVÍO, pero aquí el texto todavía
        # los puede traer: se aceptan las dos formas.
        es_pregunta = limpia.endswith("?") or limpia.startswith("¿")
        # PEDIR es preguntar o mandar a decir. Describir, no (ver `_PIDE_QUE_LE_DIGA`).
        if es_pregunta or _PIDE_QUE_LE_DIGA.search(limpia):
            hallado = _DATO_OPCIONAL.search(limpia)
            if hallado:
                trozo = hallado.group(0)
                return next(
                    (nombre for patron, nombre in _COMO_SE_LLAMA if patron.search(trozo)),
                    "ese dato",
                )
        # La ELECCIÓN PELADA ("cuál te provoca") toma su objeto de la lista anterior, y cuenta
        # AUNQUE le falte el "?": una frase que empieza por "cuál" ya es una pregunta, y la
        # personalidad manda escribir suelto ("escribe como una persona chateando"). Exigir el
        # signo dejaba el mismo hueco que acaba de aparecer con las peticiones sin signo.
        if (
            _ELECCION_PELADA.match(limpia.lstrip("¿"))
            and _es_lista_pelada(anterior)
            and not _TAMANO_EN_LISTA.search(anterior)
        ):
            return "eso mismo"
        anterior = limpia
    return None


def _pide_opcion_del_paquete(texto: str) -> bool:
    """True si el bot PREGUNTA por un dato OPCIONAL (sabor, relleno, nombre completo, hora…)."""
    return _dato_opcional_pedido(texto) is not None


def _ya_pidio_opcion_antes(historial: list | None) -> bool:
    """True si el bot YA preguntó por el sabor/relleno en un turno ANTERIOR.

    Es la señal del BUCLE: preguntarlo una vez es correcto (Whuilianny lo pregunta). Volver a
    preguntarlo cuando el cliente no lo contestó es donde se muere la venta.

    ⚠️ Un historial VACÍO devuelve False, y eso está bien: sin turnos anteriores no hay bucle que
    romper. (Es la trampa de L20 —un historial vacío APAGA las redes que lo reciben por
    parámetro—; aquí el fail-safe cae del lado bueno: la red no dispara en el primer contacto.)
    """
    for h in reversed(historial or []):
        if h.get("role") != "assistant":
            continue
        if _pide_opcion_del_paquete(h.get("content") or ""):
            return True
    return False


# Una pregunta del CLIENTE no siempre lleva signo ("que sabores tienes"): por WhatsApp se
# escribe suelto. Basta que la frase ARRANQUE con la palabra interrogativa. Sobre texto ya
# aplanado por `_sin_acentos_min`, así que sin acentos.
_PREGUNTA_SIN_SIGNO = re.compile(
    r"^\s*¿?\s*(y\s+)?(de\s+|en\s+|a\s+)?(que|cual(es)?|cuant[oa]s?|como)\b", re.I
)


def _cliente_pidio_ese_dato(mensaje: str) -> bool:
    """¿El CLIENTE está PIDIENDO un dato opcional (la lista de sabores/rellenos…)?

    🔴 ES LA ABSOLUCIÓN DE LA RED DEL CIERRE (caso real del taller, 31-ago 21:08, verificado en
    los logs): la clienta preguntó *"De que sabor tienes?"*, el modelo escribió la lista
    perfecta con los 8 sabores recién cargados, y la red —que solo miraba el borrador y el
    historial del BOT— la censuró como si fuera el bucle. Responder lo que el cliente acaba de
    preguntar no es insistir: es contestar.

    Absuelve SOLO la PETICIÓN del dato (pregunta con o sin signo, o un "dime…"). Si el cliente
    ya lo DIO ("quiero el sabor limón") o habla de otra cosa, devuelve False y la red sigue
    vigilando — re-preguntar lo dado es exactamente el bucle que existe para cortar. Los bordes
    caen a propósito del lado de DEJAR RESPONDER: esta red no vigila mentiras ni dinero (esas
    redes van aparte y no miran esto), así que absolver de más cuesta un mensaje redundante;
    censurar de más cuesta un bot que "no sabe" lo que sí sabe.
    """
    for frase in re.split(r"(?<=[.!?\n])\s+", mensaje or ""):
        limpia = frase.strip()
        if not limpia or limpia.startswith("[SISTEMA]"):
            continue  # una orden interna (el RETOMAR) jamás es el cliente pidiendo algo
        if not _DATO_OPCIONAL.search(limpia):
            continue
        es_pregunta = (
            limpia.endswith("?")
            or limpia.startswith("¿")
            or bool(_PREGUNTA_SIN_SIGNO.match(_sin_acentos_min(limpia)))
        )
        if es_pregunta or _PIDE_QUE_LE_DIGA.search(limpia):
            return True
    return False


# ══════════════════════════════════════════════════════════════════════════════════════
#  RED DEL TAMAÑO ADIVINADO: el bot elige el tamaño (y el precio) que el cliente no pidió
# ══════════════════════════════════════════════════════════════════════════════════════
#
# 🔴 EL CASO MEDIDO (2026-08-22, contra el bot real). A un *"ok esa quiero, 1"* el bot contestó
# *"te preparo la Torta baja en carbohidratos **de 1kg**"*. La clienta NUNCA dijo el tamaño: dijo
# "1", que ahí es la CANTIDAD. El bot eligió por ella — **y eligió el más caro de los tres**
# (250g / 500g / 1kg, tres precios distintos).
#
# No hubo cobro incorrecto solo porque no llegó a registrar (eso es el P0, la red del cierre). En
# cuanto el cierre funcione, ese camino queda abierto de par en par.
#
# 🔴 Y ES EXACTAMENTE LA FUGA DE LA KOMBUCHA, otra vez: dos "Kombucha" (350ml $4 / 700ml $7), el
# buscador devolvía siempre la primera y **siempre cobraba $4**. Aquello costó una cirugía entera
# (migraciones 022/022b, el "código de barras"). Aquello lo arreglaba el CÓDIGO eligiendo bien;
# esto es el MODELO eligiendo por su cuenta, y hace falta código igual.
#
# ⚠️ SE LE PROHÍBE DOS VECES EN EL PROMPT Y LO HIZO IGUAL: el catálogo ("TIENE VARIOS TAMAÑOS…
# PREGÚNTALE cuál quiere ANTES de registrar, y NUNCA lo adivines") y el schema del `variante_id`
# ("cada tamaño tiene SU precio"). *El prompt SUGIERE, el código IMPIDE.*
#
# QUÉ CUENTA COMO QUE EL CLIENTE ELIGIÓ:
#   1. Lo dijo ÉL, en cualquier mensaje suyo de la conversación ("la de 1kg", "500", "un kilo").
#   2. O el bot le propuso UN tamaño concreto —uno solo— en su último mensaje, y el cliente
#      siguió la conversación con eso delante ("te la dejo de 1kg?" → "sí"). Un tamaño que el bot
#      se propone a SÍ MISMO dentro del turno NO cuenta: no puede autorizarse solo.
_UNIDADES = (
    (("g", "gr", "grs", "gramo", "gramos"), ("g", "gr", "grs", "gramos")),
    (("kg", "k", "kilo", "kilos"), ("kg", "k", "kilo", "kilos")),
    (("ml", "mililitro", "mililitros", "cc"), ("ml", "mililitros", "cc")),
    (("l", "lt", "litro", "litros"), ("l", "lt", "litro", "litros")),
)
_NUM_UNIDAD = re.compile(r"(\d+(?:[.,]\d+)?)\s*([a-zA-Z]+)")


def _formas_del_tamano(presentacion: str) -> list[str]:
    """Todas las maneras REALES de nombrar un tamaño ('1kg' → '1 kg', 'un kilo', 'kilo'…).

    La clienta no escribe como el catálogo. Si esta función se queda corta, la red bloquea una
    venta buena; si se pasa, deja colar un tamaño adivinado. Se prefiere pasarse (dejar pasar):
    una red del dinero que frena de más se convierte en la red que alguien apaga.
    """
    plano = _sin_acentos(presentacion).strip()
    if not plano:
        return []
    m = _NUM_UNIDAD.search(plano)
    if not m:
        return [re.escape(plano)]          # 'familiar', 'personal'… tal cual
    numero, unidad = m.group(1), m.group(2).lower()
    alias = next((a for nombres, a in _UNIDADES if unidad in nombres), (unidad,))
    formas = [rf"{re.escape(numero)}\s*{a}\b" for a in alias]
    # El número PELADO solo si es distintivo. '1' NO lo es: en "ok esa quiero, 1" el 1 es la
    # CANTIDAD — que es justo el caso que abrió esta red.
    try:
        if float(numero.replace(",", ".")) >= 100:
            formas.append(rf"\b{re.escape(numero)}\b")
    except ValueError:
        pass
    if unidad in ("kg", "k", "kilo", "kilos") and numero in ("1", "1.0", "1,0"):
        formas += [r"\bun\s+kilo\b", r"\bkilo\b", r"\bkilito\b"]
    if unidad in ("g", "gr", "grs", "gramo", "gramos") and numero == "500":
        formas.append(r"\bmedio\s+kilo\b")
    return formas


def _menciona_tamano(texto: str, presentacion: str) -> bool:
    """¿Ese texto nombra ESE tamaño, con las palabras que usa una persona normal?"""
    plano = _sin_acentos(texto)
    return any(re.search(f, plano) for f in _formas_del_tamano(presentacion))


def _tamano_propuesto_por_el_bot(texto: str, tamanos: list[str]) -> str | None:
    """El único tamaño que el bot nombró en ese mensaje, o None si nombró varios (o ninguno).

    Si nombró VARIOS está ofreciendo, no proponiendo: la elección sigue siendo del cliente y un
    "sí" no dice cuál. Solo cuando nombró UNO el cliente pudo haber estado diciéndole que sí.
    """
    nombrados = [t for t in tamanos if _menciona_tamano(texto, t)]
    return nombrados[0] if len(nombrados) == 1 else None


async def _tamano_sin_elegir(
    args: dict, mensaje_usuario: str, historial: list | None
) -> dict | None:
    """El rechazo que hay que devolverle al modelo, o None si el pedido puede pasar."""
    items = args.get("items")
    if not isinstance(items, list) or not items:
        return None
    dicho_por_el_cliente = "\n".join(
        [str(mensaje_usuario or "")]
        + [
            str(h.get("content") or "")
            for h in (historial or [])
            if isinstance(h, dict) and h.get("role") == "user"
        ]
    )
    ultimo_del_bot = next(
        (
            str(h.get("content") or "")
            for h in reversed(historial or [])
            if isinstance(h, dict) and h.get("role") == "assistant"
        ),
        "",
    )
    for it in items:
        if not isinstance(it, dict):
            continue
        info = await tamanos_hermanos(it.get("variante_id"))
        if not info:
            continue                       # un solo tamaño vendible: no hay nada que adivinar
        elegido = info["elegido"]
        if _menciona_tamano(dicho_por_el_cliente, elegido):
            continue                       # lo dijo el cliente
        if _tamano_propuesto_por_el_bot(ultimo_del_bot, info["tamanos"]) == elegido:
            continue                       # el bot propuso ESE y el cliente siguió con él delante
        return {
            "ok": False,
            "nota": (
                f"NO se registró nada. '{info['producto']}' se vende en varios tamaños "
                f"({', '.join(info['tamanos'])}) y el cliente NUNCA dijo cuál quiere: el "
                f"'{elegido}' lo elegiste tú. Cada tamaño tiene SU precio, así que adivinarlo es "
                "cobrarle mal. PREGÚNTALE cuál quiere —nómbrale los tamaños, sin inventar ni un "
                "precio— y registra el pedido COMPLETO recién cuando te lo diga. Si te dijo una "
                "cantidad ('1', '2'), eso es CUÁNTOS quiere, no el tamaño."
            ),
            "tamanos": info["tamanos"],
        }
    return None


# ══════════════════════════════════════════════════════════════════════════════════════
#  EL HILO, EXTENDIDO A TAMAÑO Y SABOR (rama C): lo ya elegido no se repregunta
# ══════════════════════════════════════════════════════════════════════════════════════
#
# 🔴 LA MISMA CLASE QUE EL #6, en las otras dos elecciones pre-registro. El #6
# (`hilo_de_la_venta`, tools.py) sigue la VERSIÓN que vive en el NOMBRE del producto (masa de
# yuca vs plátano). Aquí se extiende a las que viven en las CASILLAS de la BD: el TAMAÑO
# (`presentacion`) y el SABOR (`variantes.sabores`). El hueco es idéntico: entre que el cliente
# dice "de 250, de limón" y que la tool registra el pedido, esa elección vive SOLO como chat
# crudo, y una ficha fresca (info_producto trae los 3 tamaños y los 8 sabores) la REABRE y el
# modelo repregunta. Se destila del chat y se inyecta como ESTADO — el prompt SUGIERE, el estado
# GANA.
#
# 🔒 EL TAMAÑO NO ES UNA PALANCA DE DINERO AQUÍ, y esto importa: la línea inyectada solo EVITA la
# repregunta, no registra ni cobra nada. El precio sigue naciendo del `variante_id` al registrar,
# y la RED DEL TAMAÑO ADIVINADO sigue impidiendo que se registre un tamaño que el cliente no
# dijo. Un falso positivo del destilado, a lo sumo, hace que el bot no repregunte de más — y por
# eso el reconocimiento es de vocabulario CERRADO y con la MISMA atribución por producto del #6.


# Cuántos turnos del cliente hacia atrás mira el hilo extendido. El MISMO que el #6
# (`_TURNOS_HILO_VENTA` en tools.py): una elección de tamaño/sabor se esfuma igual de rápido, y
# cubrir una venta entera es justo el punto. Se define aquí para no importar un privado de tools
# que puede moverse — si cambia allá, este comentario recuerda mantenerlos a la par.
_TURNOS_HILO_VARIANTE = 10


def _sabores_tocados(sabores: list[str], mensaje: str) -> list[str]:
    """Qué SABORES de un producto toca un mensaje (por sus tokens distintivos). Vocabulario
    CERRADO: los sabores vienen de `variantes.sabores`. Una entrada por sabor mencionado; DOS
    o más ⇒ el cliente nombró varios y no hay elección que leer (misma regla que las versiones).

    Distintivo = los tokens que NO comparten todos los sabores del producto ('queso de cabra' y
    'queso crema' comparten 'queso'; lo que elige es 'cabra' o 'crema'). Un sabor se toca si el
    mensaje trae al menos uno de SUS distintivos. La atribución por producto (arriba, en el
    destilado) es la que evita el cruce del Kéfir de cabra con el sabor 'queso de cabra'.
    """
    tokens = [(s, set(_palabras_busqueda(_frase_comparable(s)))) for s in (sabores or [])]
    tokens = [(s, t) for s, t in tokens if t]
    if len(tokens) < 2:
        # Con un solo sabor real no hay nada que ELEGIR (ni, por tanto, que repreguntar).
        return []
    comunes = set.intersection(*(t for _, t in tokens))
    pedido = set(_palabras_busqueda(_frase_comparable(mensaje or "")))
    if not pedido:
        return []
    tocados = []
    for s, t in tokens:
        distintivos = t - comunes
        if distintivos and (pedido & distintivos):
            tocados.append(s)
    return tocados


def elecciones_de_variante_en(
    catalogo: list[dict], mensaje_usuario: str, historial: list | None,
    nombres_catalogo: list[str],
) -> list[tuple[str, str, str]]:
    """Las elecciones de TAMAÑO y SABOR vigentes en la conversación: [(producto, dimensión,
    valor)], p. ej. [("Torta baja en carbohidratos", "tamaño", "250g"),
    ("Empanadas…", "sabor", "carne mechada")]. Función PURA (el catálogo entra por parámetro).

    Mismo esqueleto y mismas reglas que `hilo_de_la_venta_en` (tools.py), aplicadas por
    dimensión: la más reciente gana; un mensaje que toca DOS valores de una dimensión la deja
    SIN elección (una duda no se resuelve por mayoría) y bloquea las anteriores; y la elección
    se ATRIBUYE a un producto solo si el mensaje lo nombra, o si es el único con algo tocado y
    ningún otro producto se nombra en ese mensaje — la guarda que evita el bug del Kéfir de
    cabra vs el sabor 'queso de cabra' (ROADMAP rama C).
    """
    if not catalogo:
        return []
    turnos = [
        str(h.get("content") or "")
        for h in (historial or [])
        if isinstance(h, dict) and h.get("role") == "user"
    ]
    turnos.append(mensaje_usuario or "")
    decididos: dict[tuple[str, str], str | None] = {}
    for contenido in reversed(turnos[-_TURNOS_HILO_VARIANTE:]):
        tocados: dict[str, dict[str, list[str]]] = {}
        for prod in catalogo:
            n = prod["nombre"]
            tam = [t for t in prod.get("tamanos", []) if _menciona_tamano(contenido, t)]
            sab = _sabores_tocados(prod.get("sabores", []), contenido)
            dims = {}
            if tam:
                dims["tamaño"] = tam
            if sab:
                dims["sabor"] = sab
            if dims:
                tocados[n] = dims
        if not tocados:
            continue
        nombrados = set(_productos_nombrados_en(contenido, nombres_catalogo))
        for n, dims in tocados.items():
            # La elección es de ESTE producto si el mensaje lo nombra, o si nadie más la reclama
            # (un solo producto tocado y ningún OTRO producto nombrado en el mensaje).
            es_suya = n in nombrados or (len(tocados) == 1 and not (nombrados - {n}))
            if not es_suya:
                continue
            for dim, vals in dims.items():
                clave = (n, dim)
                if clave in decididos:
                    continue  # la más reciente ya decidió esta (producto, dimensión)
                decididos[clave] = vals[0] if len(vals) == 1 else None
    return [(n, dim, v) for (n, dim), v in decididos.items() if v]


async def elecciones_de_variante(
    mensaje_usuario: str, historial: list | None
) -> list[tuple[str, str, str]]:
    """`elecciones_de_variante_en` con el catálogo puesto. La usa `responder()` para inyectar la
    elección de tamaño/sabor como ESTADO, junto al hilo de las versiones. Ante cualquier fallo
    devuelve [] — sin esto el bot queda exactamente como hoy; jamás tumba un turno."""
    if not (mensaje_usuario or "").strip() and not historial:
        return []
    try:
        catalogo = await catalogo_variantes_para_hilo()
        if not catalogo:
            return []
        factory = get_session_factory()
        async with factory() as session:
            nombres = (
                await session.execute(
                    select(Producto.nombre).where(Producto.disponible.is_(True))
                )
            ).scalars().all()
        return elecciones_de_variante_en(catalogo, mensaje_usuario, historial, list(nombres))
    except Exception:  # noqa: BLE001 — sin hilo se sigue como hoy
        logger.exception("elecciones_de_variante: fallo al leer el catálogo; va sin la línea")
        return []


# ══════════════════════════════════════════════════════════════════════════════════════
#  EL VIGILANTE PREGUNTA-vs-ESTADO (rama D): lo que IMPIDE, no lo que sugiere
# ══════════════════════════════════════════════════════════════════════════════════════
#
# 🔴 LA ÚLTIMA PIEZA DEL PLAN "que no repregunte" (B método · C tamaño/sabor · D el vigilante).
# Las ramas #6 y C INYECTAN lo ya elegido como ESTADO — pero eso es el prompt, y el prompt
# SUGIERE. Si el modelo repregunta u OFRECE la alternativa igual (el techo del modelo, medido el
# 24-ago), esta red lo IMPIDE antes de que el mensaje salga: regaño `[SISTEMA]` + `continue`, el
# modelo redacta de nuevo. El CÓDIGO NO reescribe el texto (la frontera del 24-ago: las redes de
# estilo se quitaron; esta no fabrica texto, solo devuelve la decisión al modelo).
#
# Precedente exacto: `_correccion_fantasma` — la sentencia la dicta el ESTADO, no una lista de
# palabras. Y nace con la regla de los guardias del 1-sep (2): mira lo que el CLIENTE acaba de
# hacer. Si el cliente PREGUNTÓ por ese dato o lo CAMBIÓ en su último mensaje, responder no es
# reabrir — no dispara.

# Preguntar el TAMAÑO de frente ("¿de qué tamaño?", "¿cuál tamaño quieres?"). El tamaño no tiene
# un detector de petición como el sabor (`_dato_opcional_pedido`), así que va este, chico y
# estable — solo la PREGUNTA por el tamaño, no una frase que lo mencione de pasada.
_PREGUNTA_TAMANO = re.compile(
    r"(¿?\s*(de|en|por)?\s*(qu[ée]|cu[áa]l|cu[áa]nt[oa]s?)\s+(tama[nñ]o|presentaci[óo]n|"
    r"porci[óo]n|de\s+cu[áa]nto))"
    r"|\b(tama[nñ]o|presentaci[óo]n)\b[^.?!]*\?",
    re.I,
)


def _reabre_eleccion_ya_hecha(
    texto: str,
    elecciones_hilo: list[tuple[str, str]],
    elecciones_var: list[tuple[str, str, str]],
    mensaje_cliente: str,
) -> str | None:
    """El nombre del dato que el BORRADOR reabre pese a estar YA elegido, o None.

    Reabrir = PREGUNTAR ese dato otra vez, u OFRECER la alternativa a lo elegido. Mencionar solo
    lo elegido es CONFIRMACIÓN, no reapertura — no cuenta. Y por la regla de los guardias, si el
    cliente acaba de tocar ese dato (lo pidió o lo cambió), el bot está RESPONDIENDO: tampoco.
    """
    # ── SABOR / RELLENO ──────────────────────────────────────────────────────────────
    # Vigente si hay una elección de sabor Y el cliente no acaba de pedir/cambiar el sabor.
    if any(dim == "sabor" for _, dim, _ in elecciones_var) and not _cliente_pidio_ese_dato(
        mensaje_cliente
    ):
        # `_dato_opcional_pedido` ya distingue PREGUNTAR el sabor/relleno de describirlo.
        pedido = _dato_opcional_pedido(texto)
        if pedido in ("el sabor", "el relleno", "el topping", "la mezcla"):
            return pedido

    # ── VERSIÓN (masa de yuca / plátano, etc.) ───────────────────────────────────────
    for prod, version in elecciones_hilo:
        distintivos = _versiones_distintivas(prod)
        if not distintivos:
            continue
        # El cliente cambió/tocó la versión en su mensaje ⇒ el bot responde, no reabre.
        if _versiones_tocadas(distintivos, mensaje_cliente):
            continue
        tocadas = _versiones_tocadas(distintivos, texto)
        elegida = set(version.split())
        otras = [t for t in tocadas if not (t & elegida)]
        # Ofrece la OTRA versión, o las nombra las dos ("puedes elegir yuca o plátano"):
        # reapertura. Nombrar SOLO la elegida (tocadas == [elegida]) es confirmación.
        if otras or len(tocadas) >= 2:
            return "la versión (masa) ya elegida"

    # ── TAMAÑO ───────────────────────────────────────────────────────────────────────
    # Solo la PREGUNTA genérica de tamaño (ofrecer un tamaño concreto equivocado ya lo frena la
    # RED DEL TAMAÑO ADIVINADO al registrar). Absuelto si el cliente tocó el tamaño.
    tamano_elegido = next((v for _, dim, v in elecciones_var if dim == "tamaño"), None)
    if tamano_elegido and not _menciona_tamano(mensaje_cliente, tamano_elegido):
        if _PREGUNTA_TAMANO.search(texto or ""):
            return "el tamaño ya elegido"

    return None


async def _ejecutar_con_guardas(
    ejecutar, nombre_tool: str, args: dict, telefono: str,
    mensaje_usuario: str, historial: list | None,
):
    """`ejecutar` con las guardas del DINERO delante. Es la ÚNICA puerta por la que el bucle
    ejecuta una herramienta.

    Va aquí y no dentro de `ejecutar_tool` por lo mismo que el guardia de las tools apagadas: a
    `ejecutar_tool` también entran las REDES por su cuenta y el worker de VISIÓN, y un candado
    allí les arrancaría el brazo. Aquí se filtra solo lo que pide el MODELO.

    Si una guarda frena, devuelve el rechazo con la MISMA forma que los rechazos propios de la
    herramienta (`{"ok": False, "nota": …}`) — que es lo que hace que el resto del bucle siga
    funcionando sin tocar una línea: `registro_ok` exige `ok` True y se queda en False, la lista
    blanca del dinero no autoriza nada (el rechazo no lleva ni una cifra con marca de dinero), y
    el `role: tool` de siempre le lleva la corrección al modelo dentro del mismo turno.
    """
    if nombre_tool == "registrar_pedido":
        rechazo = await _tamano_sin_elegir(args, mensaje_usuario, historial)
        if rechazo is not None:
            logger.error(
                "TAMAÑO ADIVINADO para %s: el modelo eligió un tamaño que el cliente nunca "
                "pidió, y cada tamaño tiene SU precio — args=%r",
                telefono, str(args)[:200],
            )
            return rechazo
    if nombre_tool == "enviar_fotos_producto" and _pide_fotos(mensaje_usuario):
        # 🔒 LA VÁLVULA EN CÓDIGO (rama fotos-con-memoria): la herramienta ahora tiene memoria y
        # no repite un producto ya mostrado — pero si el CLIENTE está pidiendo ver ("mándame la
        # foto", "muéstramela otra vez", "no me llegó el video"), reenviar es lo correcto y no
        # puede depender de que el modelo se acuerde de pasar `reenviar`: lo enciende el código
        # leyendo las palabras del cliente. Un cliente pidiendo VER nunca es spam. Las llamadas
        # de la RED DE LA FOTO no pasan por esta puerta (van por `ejecutar` directo): su empuje
        # proactivo sigue frenado por su propio filtro de `media_ya_mostrada`.
        args = {**args, "reenviar": True}
    return await ejecutar(nombre_tool, args, telefono)


# ─── RED DE LA HONESTIDAD: hay cosas que el bot NO puede decir JAMÁS ──────────────────
#
# Bajo presión (un cliente molesto), el bot dijo "acabo de revisar todo en mi banco" — TRES
# veces. No puede: no tiene acceso al banco. Y a "¿eres un bot? dime la verdad" respondió
# "Soy Whuilianny 💚 Sí, soy yo". Mentirle al cliente sobre el DINERO o sobre QUIÉN ES es lo
# más grave que puede hacer: quema la marca y, siendo Tech Provider de Meta, arriesga la
# cuenta de TODOS los clientes.
#
# 🔴 DOS GRUPOS, Y LA DIFERENCIA IMPORTA (auditoría de arquitectura, 2026-07-13):
#
#   · _PROHIBIDO_SIEMPRE = mentiras que son falsas SIEMPRE, en cualquier carril y pase lo que
#     pase: el bot NO tiene banco, NO es una persona y NO es médica. Ninguna situación del sistema
#     puede volverlas ciertas. Estas se aplican TAMBIÉN al carril del dinero (el comprobante y el
#     aviso de pago), que hasta hoy no tenía NINGUNA red.
#   · _PROHIBIDO_EN_CHARLA = frases que en una conversación normal son mentira (el bot no puede
#     saber si el pago llegó)… pero que en el carril del PAGO son justo lo que el código le ORDENA
#     decir: cuando el cliente manda el comprobante, la situación dice literalmente "dile que
#     recibiste su pago"; y cuando la dueña RECHAZA un pago, hay que poder decírselo al cliente.
#     Aplicarlas allí mataría el mensaje CORRECTO. Por eso van aparte.
#
# (Antes eran una sola lista y por eso no se podía proteger el carril del dinero sin romperlo.)
_PROHIBIDO_EN_CHARLA = [
    (re.compile(r"(ya\s+)?(me\s+)?(lleg[óo]|entr[óo]|recib[íi])\s+(tu|el)\s+pago\b", re.I),
     "afirmó que el pago ya llegó"),
    (re.compile(r"no\s+(me\s+)?(ha\s+)?(lleg\w+|aparec\w+)\s+(tu|el|ning[úu]n)\s+pago", re.I),
     "afirmó que el pago NO llegó"),
    # 🔴 APROBADO / VERIFICADO / CONFIRMADO — el hueco que dejó esta lista y que una auditoría
    # exhaustiva midió el 2026-08-22 contra el bot vivo:
    #
    #     "Perfecto, el pago fue aprobado"          → PASABA
    #     "Tu pago quedó verificado, gracias"       → PASABA
    #     "Listo, acabamos de verificar tu pago"    → PASABA  ← frase textual de la plantilla
    #     "Tu pago quedó confirmado"                → PASABA
    #
    # Solo se cazaba la forma en primera persona ("ya verifiqué tu pago"). El PARTICIPIO —que es
    # como lo diría cualquiera— entraba entero.
    #
    # ⚠️ Van en EN_CHARLA y no en SIEMPRE **a propósito**: después del clic de la dueña en «Pago
    # aprobado» estas frases son VERDAD, y son justo lo que `notificar_cliente_pago` le manda
    # decir. Meterlas en SIEMPRE mataría el mensaje correcto — que es el error que esta misma
    # separación de listas existe para evitar.
    #
    # Y desde hoy pesan más: el bot ESPERA ese clic antes de coordinar, así que en la charla
    # normal ya no hay ningún momento en que pueda saber que un pago quedó bueno.
    (re.compile(r"(pago|transferencia)\b[^.]{0,30}\b(fue|qued[óo]|est[áa]|ya)\s+"
                r"(aprobad|verificad|confirmad|validad)\w*", re.I),
     "afirmó que el pago quedó aprobado/verificado (solo la dueña puede saberlo)"),
    (re.compile(r"\b(acabamos|acabo|acaba)\s+de\s+(verificar|aprobar|confirmar|validar)\b", re.I),
     "afirmó que acaban de verificar el pago"),
    (re.compile(r"\b(aprobad|verificad|confirmad|validad)\w*\s+(tu|el|su)\s+pago\b", re.I),
     "afirmó que el pago quedó aprobado/verificado"),
]

_PROHIBIDO_SIEMPRE = [
    # Afirmar que miró el banco (el bot NO tiene banco: eso solo lo sabe la dueña, en el suyo).
    (re.compile(r"(revis|verifi|chequ|mir)\w*\s+(en\s+)?(mi|el|tu)\s+(banco|cuenta)", re.I),
     "dijo que revisó el banco"),
    # 🔴 LA MISMA MENTIRA EN PRETÉRITO, y se escapó por ahí (medido el 2026-08-22, mensaje 5601
    # del taller): **"Enova, acabo de revisar y ese pago no me aparece en la cuenta"**. El patrón
    # de arriba no la vio porque exige que el "banco/cuenta" venga PEGADO al verbo, y aquí hay
    # media frase en medio ("…revisar Y ESE PAGO no me aparece en la cuenta").
    #
    # Lo que separa la mentira de la verdad aquí es el TIEMPO VERBAL, no el sustantivo:
    #   · "acabo de revisar" / "ya revisé"  → afirma haber MIRADO una cuenta que no tiene. MENTIRA.
    #   · "lo estoy revisando" / "déjame revisarlo" → es exactamente lo que debe decir. VERDAD.
    # Por eso el patrón pide el pretérito con su marca delante y NO toca el gerundio.
    (re.compile(r"\b(acabo de|ya|reci[eé]n)\s+(lo\s+|la\s+|los\s+)?(revis|verifi|chequ|mir)\w*", re.I),
     "afirmó que YA revisó (el bot no revisa nada: la dueña lo hace en su banco)"),
    (re.compile(r"(mi|el)\s+banco\s+(ya\s+)?(me\s+)?(confirm|avis|lleg)", re.I),
     "dijo que el banco confirmó"),
    # Jurar que es humana cuando le preguntan de frente.
    (re.compile(r"(no\s+soy\s+un[a]?\s+(bot|robot|m[áa]quina|ia|inteligencia))", re.I),
     "negó ser un asistente virtual"),
    (re.compile(r"s[íi],?\s+soy\s+(yo|una\s+persona|humana|real)\b", re.I),
     "juró ser una persona"),
    # 🔥 LA MISMA MENTIRA, POR OTRA PUERTA (ensayo general 2026-07-13). Al cliente que pidió
    # "quiero hablar con una PERSONA de verdad, no con una máquina" le contestó: "Soy Whuilianny,
    # LA DUEÑA de masvidaconsciente". Los dos patrones de arriba no lo vieron: nunca dijo "soy
    # humana" ni negó ser un bot — se presentó como la dueña, que es exactamente la misma mentira
    # y encima suplanta a Maired delante de su cliente.
    #
    # ⚠️ Y el primer intento de ESTA red también falló, por lo mismo de siempre (el "te agendo" vs
    # "te agendé"): escribí `soy la dueña` y la frase REAL era `soy Whuilianny, la dueña` — con el
    # NOMBRE en medio. Lo cazó el banco de pruebas, no la lectura. Por eso el nombre va opcional.
    # Vale decir "soy Whuilianny" (es su nombre) y "yo NO soy la dueña" (es la verdad); lo que no
    # vale es presentarse COMO la dueña, la propietaria o una persona.
    (re.compile(
        r"(?<!no\s)soy\s+(\w+[\s,]+)?(la\s+|una\s+)?(due[ñn]a|propietaria|persona\s+real|humana)\b",
        re.I,
    ),
     "dijo ser la dueña / una persona (suplantó a la humana)"),
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

# En la CHARLA se aplican las dos listas (el bot ahí no sabe nada del banco).
_PROHIBIDO = _PROHIBIDO_SIEMPRE + _PROHIBIDO_EN_CHARLA


def frase_prohibida_siempre(texto: str) -> str | None:
    """Las mentiras que NINGUNA situación puede volver ciertas: el bot no tiene banco, no es una
    persona y no es médica. Se usa en el carril del DINERO (comprobante / aviso de pago), donde
    hasta hoy el modelo escribía SIN ninguna red."""
    for patron, que in _PROHIBIDO_SIEMPRE:
        if patron.search(texto or ""):
            return que
    return None


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
    r"|seg[úu]n (mi|el) (sistema|registro|base)"
    # 🔴 EL PERSONAJE ROTO (caso real, 24-ago 00:52, mensajes 6968-6973): la clienta le escribió
    # su lista de quejas y el bot le contestó con un análisis interno numerado citando sus
    # herramientas: "debo CONSULTAR `proxima_fecha_entrega`". Un cliente leyendo eso ve las
    # tripas del robot. Tres formas nuevas, todas imposibles en boca de una vendedora:
    r"|`[^`\n]{2,60}`"                                     # backticks = código/nombre de tool
    r"|(?<![\w@.])[a-záéíóúñ]+(?:_[a-záéíóúñ]+){1,4}\b(?!@)"  # nombre_con_guion_bajo (no email)
    r"|debo (consultar|usar|llamar|verificar|ser breve)"
    r"|d[ée]jame corregir (esos|los|mis) errores"
    r"|(mis|las) instrucciones|mi prompt|mis reglas)",
    re.IGNORECASE,
)


def _suena_a_sistema(texto: str) -> bool:
    return bool(_SUENA_A_SISTEMA.search(texto or ""))


# ─── RED DEL ENVÍO FANTASMA DE FOTOS: no digas que las mandaste si NO las mandaste ────
#
# 🔴 Caso REAL (2026-07-14, probado en vivo y confirmado en el LOG): el cliente pidió
# "Mándame la foto de la torta keto" y el bot contestó **"Ya te la envié hace poco 💚"** —
# con UNA sola llamada al modelo y CERO llamadas a `enviar_fotos_producto`. Y lo peor:
# la vez anterior había dicho "Ahí tienes las fotos" (también sin enviarlas), así que su
# PROPIA mentira quedó en la memoria del chat y la usó de excusa. Una mentira alimentando
# la siguiente. Es la familia del "te agendo": miente en el HECHO, no en el tono.
#
# ⚠️ La trampa que hace a esta red distinta: la frase del bot NO trae la palabra "foto"
# ("ya te LA envié" — el «la» viene del mensaje del cliente). Por eso se mira TAMBIÉN lo
# que el cliente pidió: si pidió fotos y el bot afirma un envío, tiene que haber un envío
# REAL en ese turno. Si el cliente las pide de nuevo, se REENVÍAN — jamás "ya te las mandé".
#
# 🔴 Y LA TRAMPA DE LA TRAMPA (auditoría 2026-08-02, PRM-1). El mismo pronombre que salva esta
# red la volvía contra el bot en el camino MÁS común del negocio:
#
#     cliente: "me puedes mostrar lo que tienen?"   → `_pide_fotos` = True (por «mostrar»)
#     prompt (regla 58): eso es pedir el CATÁLOGO → el bot llama a `enviar_catalogo`
#     bot: "Listo, ya te LO envié 💚"               → afirmación por pronombre, sin palabra de media
#     red: `fotos_ok` es False ⇒ ENVÍO FANTASMA ⇒ aviso FALSO a la dueña
#
# El cliente tiene el PDF abierto en la mano y recibe "Dame un momentito y te confirmo". En modo
# `dos` ni siquiera hay reintento: muere al primer golpe. La red no mentía sobre el hecho —
# **no sabía leer QUÉ había enviado**.
#
# El arreglo es del CÓDIGO, no del prompt (no se le quita la regla 58): la red aprende a
# distinguir los dos envíos. Un "ya te lo envié" pelado apunta al catálogo —y no a una foto—
# cuando la frase lo NOMBRA, o cuando el PDF salió de verdad en este turno y el cliente nunca
# pidió una foto con todas sus letras. Si el cliente escribió "foto"/"video"/"imagen", la red
# se queda tan dura como estaba: ese es el caso REAL del 2026-07-14 y no se toca.
_PIDE_FOTOS_RE = re.compile(r"\b(foto|fotos|video|videos|imagen|imagenes|verlo|verla|muestrame|mostrar)\b")
_FOTO_PALABRA_RE = re.compile(r"\b(foto|fotos|video|videos|imagen|imagenes)\b")
_CATALOGO_PALABRA_RE = re.compile(r"\b(catalogo|menu|carta|folleto)\b")
_AFIRMA_ENVIO_RE = re.compile(
    r"(ya\s+te\s+(la|las|lo|los)\s+(envie|mande|pase)"
    r"|te\s+(la|las|lo|los)\s+(envie|mande|envio|mando|paso)\b"
    # verbo ANTES del objeto: "te mando la foto ahorita" (presente = el mismo "te agendo")
    r"|te\s+(mando|envio|envie|mande|paso|pase)\b"
    r"|acabo\s+de\s+(enviar|mandar|pasar)"
    # 🔴 EL PRONOMBRE EN MEDIO (caso REAL, 2026-07-14): a "enviame foto de las keto" el bot
    # contestó "Ahí LAS tienes 💚" cuando las fotos habían FALLADO. La versión vieja pedía "ahi"
    # pegado a "tienes" y ese «las» la burlaba — el mismo tipo de hueco que el «te LA envié».
    # Ahora el pronombre (la/las/lo/los) es opcional entre medias.
    r"|ah[i]\s+((la|las|lo|los)\s+)?(tienes|van|te\s+van|te\s+deje|te\s+dejo)"
    r"|aqui\s+((la|las|lo|los)\s+)?(tienes|te\s+(la|las|lo|los)\s+dejo|te\s+dejo)"
    r"|ya\s+(salio|se\s+envio|se\s+enviaron|te\s+llego|te\s+llegaron))"
)


def _pide_fotos(texto_cliente: str) -> bool:
    """True si el CLIENTE está pidiendo ver fotos/videos (o 'verlo')."""
    return bool(_PIDE_FOTOS_RE.search(_sin_acentos(texto_cliente or "")))


def _pide_media_explicita(texto_cliente: str) -> bool:
    """True si el cliente pidió una FOTO/VIDEO **con todas sus letras**.

    Es el subconjunto DURO de `_pide_fotos`: "mándame la foto de la torta" sí; "me puedes
    mostrar lo que tienen?" no (eso, según la regla 58 del prompt, es pedir el catálogo).
    La distinción existe para no matar el turno del catálogo — ver `_afirma_envio_fotos`.
    """
    return bool(_FOTO_PALABRA_RE.search(_sin_acentos(texto_cliente or "")))


def _afirma_envio_fotos(
    texto: str,
    cliente_pidio_fotos: bool,
    *,
    pidio_media_explicita: bool = False,
    catalogo_enviado: bool = False,
) -> bool:
    """True si el bot AFIRMA que envió (o está enviando) fotos/videos.

    Cuenta si la frase trae una palabra de media ("ahí tienes las fotos") O si el cliente
    acaba de pedir fotos y el bot afirma un envío con pronombre ("ya te la envié").
    Las PREGUNTAS ("¿te mando la foto?") y los condicionales ("si quieres te la mando")
    NO cuentan: frenar de más rompe la venta.

    `catalogo_enviado` / `pidio_media_explicita` (auditoría 2026-08-02, PRM-1): con los dos en
    su valor por defecto la red se comporta EXACTAMENTE como antes — los bancos que la llaman
    con dos argumentos siguen midiendo lo mismo. Lo que añaden es poder resolver el pronombre:
    si el envío que de verdad ocurrió fue el CATÁLOGO y el cliente nunca escribió "foto", el
    "ya te lo envié" es **verdad**, y frenarlo le quitaba el PDF de las manos al cliente.
    """
    for frase in re.split(r"(?<=[.!?\n])\s+", texto or ""):
        limpia = frase.strip()
        if not limpia:
            continue
        if limpia.startswith("¿") or limpia.endswith("?"):
            continue
        t = _sin_acentos(limpia)
        # Condicional/oferta: "si quieres te mando la foto", "cuando me digas cuál, te la envío".
        if re.search(r"\b(cuando|si|apenas|en\s+cuanto)\b", t) and re.search(
            r"\b(quier|gust|dese|dig|dic|confirm|pid|elij|escog)", t
        ):
            continue
        if not _AFIRMA_ENVIO_RE.search(t):
            continue
        # (a) La frase NOMBRA la media: es una afirmación sobre fotos, pase lo que pase.
        if _FOTO_PALABRA_RE.search(t):
            return True
        # (b) Sin palabra de media, la afirmación es SOLO por pronombre ("ya te LO envié"):
        #     no dice qué envió. Si el cliente no pidió nada de ver, no es de esta red.
        if not cliente_pidio_fotos:
            continue
        # (c) …y el pronombre apunta al CATÁLOGO —no a una foto— si la frase lo nombra, o si
        #     el PDF salió de verdad en este turno y el cliente nunca escribió "foto".
        if _CATALOGO_PALABRA_RE.search(t):
            continue
        if catalogo_enviado and not pidio_media_explicita:
            continue
        return True
    return False


# ─── RED DE LOS DATOS BANCARIOS: los datos de pago SOLO salen de una herramienta ──────
#
# 🔴 Caso REAL (2026-07-13, una clienta): el bot le pegó los DATOS BANCARIOS COMPLETOS
# (cédula, cuenta, Zelle, Binance) SIN que hubiera un pedido, porque vivían escritos en el
# TEXTO de la personalidad y el modelo los copiaba de ahí cuando le parecía. La regla
# "envía SOLO los del método que el cliente elija" vivía en el prompt: humo.
#
# La pared: un dato sensible (una corrida de 6+ dígitos —cédula, teléfono, cuenta, wallet—
# o un correo) SOLO puede salir si en ESTE turno lo devolvió una herramienta
# (generar_datos_pago, info_negocio…) o si el propio cliente lo escribió (su referencia,
# su teléfono). Lo que no vino de ahí NO sale — aunque esté escrito en la personalidad.
# El mismo movimiento que la red del dinero: el prompt sugiere, el código impide.
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_FECHA_ISO_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_CORRIDA_DIGITOS_RE = re.compile(r"\d(?:[\d\s\-]*\d)?")


def _datos_sensibles(texto: str) -> set[str]:
    """Los correos y las corridas de 6+ dígitos (cédulas, teléfonos, cuentas, wallets,
    referencias) que hay en un texto. Los dígitos se juntan aunque vengan partidos por
    espacios o guiones ('0134-0188 8518…' es UNA cuenta); los PUNTOS no juntan (son el
    separador de miles del dinero: '18.033,64' no es una cuenta). Las fechas ISO
    (2026-07-18) se quitan antes para no confundirlas con una cédula."""
    t = _FECHA_ISO_RE.sub(" ", texto or "")
    encontrados = {m.group(0).lower() for m in _EMAIL_RE.finditer(t)}
    for m in _CORRIDA_DIGITOS_RE.finditer(t):
        digitos = re.sub(r"[\s\-]", "", m.group(0))
        if len(digitos) >= 6:
            encontrados.add(digitos)
    return encontrados


def _datos_sensibles_inventados(
    texto: str,
    autorizados: set[str],
    usd_ok: set[float] | None = None,
    bs_ok: set[float] | None = None,
) -> list[str]:
    """Los datos sensibles del texto que NO vinieron de una herramienta ni del cliente.

    Un número que en realidad es un MONTO autorizado (un total en Bs escrito sin separador
    de miles) no cuenta como dato sensible: de que sea el monto correcto ya se encarga la
    red del dinero. Y citar un PEDAZO de un dato autorizado ('termina en 7595') vale."""
    dinero_ok = (usd_ok or set()) | (bs_ok or set())
    malos: list[str] = []
    for dato in sorted(_datos_sensibles(texto)):
        if "@" in dato:
            if dato not in autorizados:
                malos.append(dato)
            continue
        if any("@" not in a and (dato in a or a in dato) for a in autorizados):
            continue
        if dinero_ok and _calza(_lecturas_del_monto(dato), dinero_ok):
            continue
        malos.append(dato)
    return malos


# ─── RED DE LA SALUD: no se dictamina sobre el cuerpo de alguien sin mirar la ficha ────
#
# 🔴 EL CASO REAL (simulacro con el bot real, 2026-08-06). Una clienta preguntó "¿es apta para
# diabéticos la kombucha?" y el bot respondió **"Sí, es apta"** SIN llamar a ninguna herramienta,
# razonando por su cuenta ("es fermentada y no lleva azúcar refinada").
# **La ficha dice `apto_diabeticos = 'no'`.** Le dijo que sí a una diabética sobre un producto
# marcado que no.
#
# Y la lección de cómo se llegó aquí: el primer intento fue QUITAR `apto diabéticos` del catálogo
# del prompt para forzar la consulta. Salió PEOR — sin el dato delante, el modelo no consultó:
# improvisó. Quitar información no obliga a buscarla, solo deja un hueco que el modelo rellena.
#
# Por eso esto es una RED y no una regla: en este repo "el prompt SUGIERE, el código IMPIDE". El
# dato vuelve al prompt (así el peor caso es una respuesta incompleta, nunca una FALSA) y esta red
# frena el mensaje si el bot dictamina sobre salud sin haber abierto la ficha en ESTE turno.
# Se piden DOS señales a la vez, sin exigir orden: el español las coloca en cualquiera
# ("¿es apto para diabéticos?" pero también "mi mamá es diabética, ¿puede comer eso?").
# Exigir las dos es lo que evita que salte con "¿tienen algo sin gluten?", que es una pregunta
# de catálogo y no un dictamen sobre una persona.
# ⚠️ SIN `\b` AL FINAL, y no es un descuido: estas son RAÍCES, no palabras. Con el `\b` de cierre
# `diabet` NO calza con "diabeticos" ni `celiac` con "celiacos" — que es justo como escribe la
# gente. El `\b` inicial sí se queda, para no cazar la raíz dentro de otra palabra.
_APTITUD = re.compile(
    r"\b(apt[oa]|pued[eoa]|podr[íi]a|sirve|recomend|buen[oa]|segur[oa]|conviene|"
    r"da[ñn]a|afecta|perjudic|riesgo|toler)",
    re.IGNORECASE,
)
_CONDICION = re.compile(
    r"\b(diabet|celiac|cel[íi]ac|gluten|al[eé]rgi|intoleran|embaraz|lactancia|"
    r"ni[ñn][oa]|beb[ée]|hipertens|presi[óo]n|ri[ñn][óo]n|h[íi]gado|tiroid|colon|gastritis|"
    r"az[úu]car\s+alta|mi\s+condici[óo]n|mi\s+enfermedad)",
    re.IGNORECASE,
)
# 🔴 AMPLIADO EL 2026-08-22. La versión anterior solo cazaba una de cada cuatro sentencias
# reales. Medido con el par pregunta→respuesta que más se repite ("soy celíaca, ¿me sirve?"):
#
#     "el pan de sándwich es libre de gluten, puedes comerlo tranquila"  → PASABA
#     "todas nuestras galletas son aptAS para celíacos"                  → PASABA (falta el plural)
#     "todo es libre de gluten, azúcar refinada y lácteos"               → PASABA
#     "el pan keto es apto para diabéticos"                              → sí frenaba
#
# Tres huecos: el PLURAL (`apta` sí, `aptas` no), la ventana de 40 caracteres (demasiado corta
# para "Sí, el pan de sándwich es libre de gluten, puedes…") y, sobre todo, la forma que más se
# usa de verdad: **"libre de X" / "no lleva X"**, que sentencia igual sin decir "apto".
#
# ⚠️ Y "todo es libre de gluten y lácteos" es FALSA en este catálogo: hay Kéfir de Leche de cabra
# y Yogurt Kéfirado. Dicha a quien pregunta por una alergia, es la respuesta más cara del sistema.
_DICTAMINA_APTO = re.compile(
    r"\b(s[íi]|no)\b[^.?!]{0,70}\b(apt[oa]s?|puede[sn]?|sirve|problema)\b"
    r"|\b(es|son|est[áa]n?)\s+apt[oa]s?\b|\bno\s+(es|son)\s+apt[oa]s?\b"
    r"|\bs[íi],?\s+(la|lo|el|las|los)\s+puede\b"
    r"|\b(libre|libres|exent[oa]s?)\s+de\s+\w+"
    r"|\bno\s+(lleva|contiene|tiene)\s+\w+"
    r"|\bpuedes?\s+(comer|consumir|tomar)\w*",
    re.IGNORECASE,
)


def _dictamina_salud_sin_ficha(mensaje_cliente: str, texto: str, consulto_ficha: bool) -> bool:
    """True si el cliente preguntó si algo le conviene a un cuerpo y el bot SENTENCIÓ sin abrir
    la ficha del producto en este turno.

    Solo mira el par PREGUNTA→VEREDICTO. Si el bot responde "eso te lo confirmo" no dictamina
    nada y la red no se mete: prefiere quedarse corta antes que frenar una respuesta honesta.
    """
    if consulto_ficha:
        return False
    pregunta = mensaje_cliente or ""
    if not (_APTITUD.search(pregunta) and _CONDICION.search(pregunta)):
        return False
    return bool(_DICTAMINA_APTO.search(texto or ""))


# ─── RED DE LA ASESORÍA: no se recomienda de memoria, se consulta ─────────────────────
#
# 🔴 EL CASO REAL (smoke de 7 turnos contra el bot real, 2026-08-08, corrido DOS veces con el
# mismo resultado). La clienta dio una necesidad clarísima — "es para compartir en familia el
# domingo, algo dulce" — y el bot le recitó OCHO categorías desde el catálogo del prompt y le
# preguntó "¿cuántas personas van a ser?". En 6 de 7 turnos no consultó NINGUNA herramienta:
# sin ficha no hay con qué asesorar, sin producto concreto no hay foto, y el pedido nunca
# existió. Las tres quejas ("asesoría pobre", "no manda fotos", "no cierra") eran el mismo
# fallo, y las reglas que ordenan consultar YA están en el prompt (con mayúsculas) — el modelo
# las ignora. Por eso esto es una RED: el prompt SUGIERE, el código IMPIDE.
#
# Es la hermana de `_dictamina_salud_sin_ficha` (mismo mecanismo: detectar el par
# pregunta→respuesta-sin-herramienta y re-preguntar UNA vez con un [SISTEMA]), con UNA
# diferencia deliberada: esto es VENTA, no salud. Si tras la corrección sigue sin consultar,
# el texto SALE IGUAL — aquí jamás se bloquea ni se escala: frenar la venta para exigir mejor
# asesoría sería matar justo lo que se quiere salvar.
#
# ⚠️ Regex con RAÍCES: `\b` solo DELANTE (lección del 2026-08-06: `\b(diabet)\b` no calza
# "diabeticos"). Y la lista de OCASIONES es CERRADA a propósito: "para el domingo, retiro yo"
# (turno 6 del mismo smoke) es una FECHA de entrega de alguien que YA está comprando, no una
# petición de consejo — dispararle ahí empujaría recomendaciones a quien ya eligió. Los días de
# la semana NO son ocasión.
_PIDE_ASESORIA = re.compile(
    # pedir consejo con todas sus letras: "qué me recomiendas", "recomiéndame", "sugiéreme",
    # "alguna recomendación", "qué me aconsejas"
    # "me puedes asesorar con una duda" — la primera línea REAL de CLI-034. La palabra que el
    # cliente usa más a menudo para pedir consejo no estaba en esta lista cerrada. Solo las
    # formas VERBALES: "la asesora" (el nombre del bot en la BD) no puede disparar la red.
    r"\b(recomiend|recomend|sugier|suger|aconsej)"
    r"|\basesor(ar|arme|ame|as)\b"
    # no saber qué elegir: "no sé qué llevar/pedir", "no sé cuál", "no sabría qué"
    r"|\bno\s+(se|sabria)\s+(que|cual|cuales)\b"
    # "algo <cualidad>": "algo dulce/dulcito", "algo salado", "algo rico", "algo ligero"…
    r"|\balgo\s+(dulc|salad|ric|sabros|liger|light|san[oa]\b|saludabl|especial|diferent|"
    r"distint|barat|economic)"
    # pedir el ranking: "cuál es (el) mejor", "qué es lo más rico", "el más vendido",
    # "cuál me conviene". OJO: nada de un `\bmejor` suelto — "mejor dame 2" es otra cosa.
    r"|\bcual(es)?\b[^.?!\n]{0,15}\bmejor"
    r"|\bque\s+es\s+((el|la|lo)\s+)?mejor"
    r"|\bmas\s+(ric[oa]|vendid|pedid|popular|buscad|gustad)"
    r"|\b(cual|que)\s+me\s+(conviene|sirve|queda)"
    # "para <ocasión o persona>": compartir, regalar, un cumpleaños, la merienda, los niños…
    # (con artículo/posesivo opcional; "pa'" también, que así se escribe aquí)
    r"|\b(para|pa)'?\s+((el|la|los|las|un|una|unos|unas|mi|mis|tu|tus)\s+)?"
    r"(compartir|regal|celebra|picar|merendar|merienda|desayun|cenar|cena\b|cumplean|"
    r"fiesta|reunion|parrilla|visita|invitad|familia|nin[oa]|bebe|antoj|evento|ocasion|"
    r"diabet|celiac|vegan|intoleran|alergi)"
    r"|\ben\s+familia\b",
)


def _pide_asesoria(texto_cliente: str) -> bool:
    """True si el CLIENTE está pidiendo una recomendación (o dando una necesidad para que se
    la recomienden). Nombrar un producto o tipo concreto ("pan keto", "las de plátano"), pedir
    el precio o dar un dato de entrega NO es pedir asesoría — esos turnos no son de esta red."""
    return bool(_PIDE_ASESORIA.search(_sin_acentos(texto_cliente or "")))


# ─── Detectar una ELECCIÓN del cliente ────────────────────────────────────────────────
#
# 🪦 Aquí vivió la RED DEL PITCH (2026-08-08 → 2026-08-24): re-preguntaba al modelo cuando la
# confirmación de una elección salía sin datos de ficha. Se QUITÓ a propósito (decisión de
# Erwin, 24-ago): en la prueba real de Maired descartó dos borradores buenos, metió la ficha
# repetida las 2 veces que ella reportó, y contradecía la regla 66 de `_REGLAS` ("NO
# RE-CONFIRMES EL PEDIDO EN CADA TURNO"). El pitch ahora es tarea del MODELO — ver el 🪦 en
# `responder()`.
#
# `_elige_entre_opciones` SE QUEDA: la fijan `test_memoria_respaldo.py` (es el ejemplo canónico
# de la lección L20 — un historial vacío apaga las redes que lo reciben por parámetro) y los
# tests de la elección en `test_red_de_la_asesoria.py`.
_OFRECIO_OPCIONES = re.compile(r"\bcual(es)?\b|\bprefier")


def _elige_entre_opciones(mensaje_cliente: str, historial: list | None) -> bool:
    """True si el turno del cliente es una ELECCIÓN: el último mensaje del bot le ofreció
    opciones (preguntó "¿cuál…?" / "¿prefieres…?") y él contestó corto, escogiendo.

    Una PREGUNTA del cliente no elige — pide un dato ("relleno de hay?" es el turno del dato
    puntual, no de esta red). Un número pelado ("1") contesta CUÁNTOS, no CUÁL. Y sin la
    oferta previa del bot no hay elección que detectar: mejor quedarse corto que corregir a
    un bot que no estaba ofreciendo nada.
    """
    t = (mensaje_cliente or "").strip()
    if not t or "?" in t or "¿" in t:
        return False
    if _es_charla_pura(t):
        return False
    palabras = re.findall(r"[a-z0-9]+", _sin_acentos(t))
    if not palabras or len(palabras) > 6:
        return False
    if all(p.isdigit() for p in palabras):
        return False
    ultimo = ""
    for h in reversed(historial or []):
        if isinstance(h, dict) and h.get("role") == "assistant":
            ultimo = str(h.get("content") or "")
            break
    return bool(_OFRECIO_OPCIONES.search(_sin_acentos(ultimo)))


# ─── RED DEL BUCLE: el bot preguntando lo mismo una y otra vez ────────────────────────
#
# 🔴 POR QUÉ EXISTE (simulacro con el bot real, 2026-08-06). Una clienta pidió 2 panes keto. El
# bot le preguntó si era para el viernes 8 o el 15. Ella preguntó el total → el bot repitió la
# MISMA pregunta. Ella preguntó cómo pagar → el bot la repitió OTRA VEZ. Tres turnos, dos
# preguntas distintas de ella, la misma evasiva. Ahí se pierde la venta.
#
# El candado que lo causa es CORRECTO y deliberado: sin fecha no se cotiza. Lo que faltaba era la
# salida cuando el cliente no la da. El bot no tiene forma de saber que ya lo intentó: cada turno
# nace sin memoria de sus propios intentos.
#
# Y es también la raíz del "pedido fantasma" del mismo simulacro: la clienta se fue creyendo que
# había encargado porque el bot le dijo "te dejo 2 panes keto" y después NUNCA llegó a registrar,
# atascado en este bucle. No se arregla ensanchando la regex del pedido fantasma — "te dejo 2
# panes" es un acuse conversacional legítimo mientras junta la fecha, y frenarlo mataría ventas
# buenas. Se arregla aquí, sacando al bot del bucle.
_VACIAS = {
    "que", "qué", "cual", "cuál", "cuales", "cuáles", "para", "por", "con", "sin", "los", "las",
    "del", "una", "uno", "unos", "unas", "es", "el", "la", "de", "en", "y", "o", "a", "te", "le",
    "lo", "se", "su", "tu", "mi", "al", "eso", "esa", "ese", "esta", "este", "prefieres", "quieres",
}


def _nucleo_pregunta(frase: str) -> frozenset[str]:
    """Las palabras con contenido de una pregunta, sin tildes ni relleno."""
    plano = unicodedata.normalize("NFKD", frase.lower())
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    palabras = re.findall(r"[a-z0-9]+", plano)
    return frozenset(p for p in palabras if len(p) > 2 and p not in _VACIAS)


def _preguntas_de(texto: str) -> list[frozenset[str]]:
    """El núcleo de cada pregunta del texto. Las muy cortas ('¿algo más?') NO cuentan: son
    coletillas de cierre, no intentos de obtener un dato."""
    fuera = []
    for frase in re.split(r"(?<=[.!?\n])\s+", texto or ""):
        limpia = frase.strip()
        if not (limpia.endswith("?") or limpia.startswith("¿")):
            continue
        # 🔴 EL PREÁMBULO NO ES LA PREGUNTA. El modelo escribe "Antes de darte el total,
        # confirma: ¿es para el viernes 8 o el 15?" — y como los dos puntos NO parten la frase,
        # el núcleo se llenaba de "antes/darte/total/confirma" y la comparación con la MISMA
        # pregunta hecha sin preámbulo caía de 1.0 a 0.5: el bucle real del simulacro no
        # disparaba. Si hay '¿', la pregunta empieza ahí.
        if "¿" in limpia:
            limpia = limpia[limpia.rindex("¿"):]
        nucleo = _nucleo_pregunta(limpia)
        if len(nucleo) >= 3:
            fuera.append(nucleo)
    return fuera


def _pregunta_repetida(texto: str, historial: list | None, veces: int = 2) -> bool:
    """True si el bot lleva `veces` turnos ANTERIORES haciendo la misma pregunta que ahora.

    Con el default (2), dispara en el TERCER intento: preguntar dos veces es insistir, tres es
    un bucle. Se compara por NÚCLEO de palabras y no por texto literal, porque el modelo
    reformula ('¿para el viernes 8 o el 15?' / '¿confirma: viernes 8 o viernes 15?').
    """
    ahora = _preguntas_de(texto)
    if not ahora:
        return False
    previos = [
        _preguntas_de(str(h.get("content") or ""))
        for h in (historial or [])
        if isinstance(h, dict) and h.get("role") == "assistant"
    ]
    for nucleo in ahora:
        repeticiones = 0
        for turno in previos:
            # Jaccard: dos preguntas son "la misma" si comparten la mayoría de su contenido.
            if any(
                len(nucleo & otro) / max(1, len(nucleo | otro)) >= 0.6
                for otro in turno
            ):
                repeticiones += 1
        if repeticiones >= veces:
            return True
    return False


# 🪦 AQUÍ VIVIERON `_ya_dice_las_cifras` Y `_asegurar_resumenes_exactos` (QUITADAS 24-ago).
# Insertaban delante del mensaje el recibo/cobro EXACTOS de las herramientas cuando el modelo
# los omitía o parafraseaba. Se quitaron por decisión de Erwin: presentar el recibo es tarea
# del MODELO (las reglas 107-108 de _REGLAS ya le ordenan copiar `resumen`/`resumen_cobro` tal
# cual). Además alimentaban el conflicto entre redes (L28 medido el 23-ago): el texto insertado
# quedaba en el historial y la red de la ficha repetida lo recortaba al turno siguiente,
# mutilando el cobro de Maired. El dinero FALSO lo sigue frenando `_dinero_inventado` (esa es
# otra red y se queda entera); lo que ya no garantiza nadie es que el modelo INCLUYA las cifras
# — a propósito: es la señal que mide si el modelo alcanza. NO reintroducir sin medir.


async def responder(
    telefono: str,
    mensaje_usuario: str,
    historial: list | None = None,
    nombre_cliente: str | None = None,
    *,
    pregunta_cliente: str | None = None,
    llm=_llamar_openrouter,
    voz=None,
    ejecutar=ejecutar_tool,
) -> str:
    """Devuelve el texto de respuesta para enviar al cliente.

    `pregunta_cliente`: lo que el CLIENTE preguntó de verdad, para los avisos a la dueña. Casi
    siempre es `mensaje_usuario` — pero en el RETOMAR, `mensaje_usuario` es una orden interna
    ("[SISTEMA] vuelves a atender este chat…") y no un mensaje del cliente. Sin esto, el aviso de
    la bandeja le decía a la dueña: *El cliente preguntó: "[SISTEMA] Vuelves a atender…"*. Basura
    en el único sitio donde ella mira para entender qué pasa.

    `llm`, `voz` y `ejecutar` son inyectables para poder testear el loop sin llamar a OpenRouter
    ni a la base de datos reales.

    🔴 LA FIRMA NO CAMBIA, Y ESO ES EL DISEÑO. Toda la arquitectura de DOS AGENTES (fase 5) vive
    DETRÁS de esta función, que sigue devolviendo un `str`. `tasks.py` (4 sitios) y el simulador
    del panel no se tocan. Radio de explosión: mínimo.
    """
    pregunta_cliente = pregunta_cliente or mensaje_usuario

    # 📊 EL TURNO EMPIEZA AQUÍ (telemetría, migración 032). `responder` es la puerta ÚNICA de
    # todos los turnos de conversación —el buffer de texto, el audio, el retomar y el simulador
    # del panel—, así que con esta línea las hasta 6 llamadas del bucle, las del fallback y las de
    # la Voz quedan cosidas al MISMO `turno_id` y al mismo cliente.
    # NO pisa un turno ya abierto: si venimos de una nota de voz, la transcripción y esto son el
    # mismo turno del mismo cliente (lo que se quiere medir es lo que costó ATENDERLO, no una
    # llamada suelta). Y el teléfono del simulador empieza por '__simulador__', así que el gasto
    # de las pruebas del panel se puede separar del gasto real con un LIKE.
    abrir_turno(telefono, "charla")

    # 🔒 LA BANDERA. 'uno' = el agente de siempre (lo que corre hoy). 'dos' = Operador + Voz.
    # Volver atrás es UN `UPDATE` en `configuracion`, sin redeploy: el bot lo obedece en el
    # siguiente mensaje. El camino viejo NO se borra — se envuelve (regla ADITIVA, CLAUDE.md §3).
    modo, modelo_operador, modelo_voz = await leer_config_agente()
    if modo == "dos":
        return await _responder_dos_agentes(
            telefono, mensaje_usuario, historial, nombre_cliente,
            pregunta_cliente=pregunta_cliente,
            llm=llm, voz=voz or _pedir_redaccion, ejecutar=ejecutar,
            modelo_operador=modelo_operador, modelo_voz=modelo_voz,
        )
    # QUÉ SABE HACER EL BOT HOY (fase 4). Se lee UNA vez por turno y baja a los dos sitios que la
    # necesitan: el prompt (para que no ORDENE usar una herramienta apagada) y la lista que ve el
    # LLM. `_DISPATCH` no se toca: las redes de seguridad siguen pudiendo ejecutarlo todo.
    activas = await leer_tools_activas()
    tools_llm = schemas_para(activas)
    # La parte ESTABLE del prompt se marca con cache_control: el proveedor la cachea y la
    # cobra a ¼ en los siguientes mensajes (mismo prompt → misma calidad, solo más barato).
    # La parte DINÁMICA (hora, ficha, estado) va aparte, sin cachear.
    estable, dinamico = await construir_partes_prompt(nombre_cliente, telefono, activas=activas)
    # 🧵 EL HILO DE LA VENTA (2026-08-31, el "pero ya te lo dije" de Maired): lo que el cliente
    # YA eligió no puede vivir SOLO como chat viejo — la ficha fresca de una herramienta lo
    # reabre y el modelo repregunta (pasó EN VIVO con la masa de yuca: la elección estaba a 4
    # renglones y la regla SIGUE EL HILO en el prompt, y repreguntó igual). El código destila
    # la elección (`hilo_de_la_venta`, tools.py) y la inyecta como ESTADO en la parte DINÁMICA
    # (la estable va cacheada y no se toca). Sin cifras a propósito: este texto lo lee la red
    # del dinero (`autorizados_por_moneda`, más abajo) y una cifra aquí sería autorizarla.
    elecciones_hilo = await hilo_de_la_venta(mensaje_usuario, historial)
    # 🧵 RAMA C (1-sep): el hilo se extiende a TAMAÑO y SABOR — las otras dos elecciones que
    # viven en las casillas de la BD (`presentacion`, `variantes.sabores`), no en el nombre.
    # Misma inyección, la misma línea de estado, para que la ficha fresca no las reabra.
    elecciones_var = await elecciones_de_variante(mensaje_usuario, historial)
    if elecciones_hilo or elecciones_var:
        _lineas = [
            f"- De '{_p}' el cliente YA eligió: {_e.upper()}."
            for _p, _e in elecciones_hilo[:2]
        ] + [
            f"- De '{_p}' el cliente YA eligió el {_dim}: {_v.upper()}."
            for _p, _dim, _v in elecciones_var[:4]
        ]
        dinamico += (
            "\n\nEL HILO DE LA VENTA (lo dedujo el código de los mensajes del cliente — es un "
            "HECHO del sistema, no una opinión):\n"
            + "\n".join(_lineas)
            + "\nNO le vuelvas a preguntar esas elecciones ni le reofrezcas las otras opciones; "
            "confírmalas con naturalidad si hace falta y avanza SOLO con lo que aún falte "
            "(relleno, tamaño, cantidad, fecha, cobro). Si el cliente cambia alguna en su "
            "último mensaje, vale lo nuevo que diga."
        )
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
    # Diagnóstico: qué modelo corre y qué herramientas OFRECE este bot (no cuántas trae el
    # código: cuántas le llegan al modelo — que desde la fase 4 son cosas distintas).
    puede_fotos = "enviar_fotos_producto" in activas
    logger.info(
        "responder: modelo=%s tools=%d/%d fotos=%s msg=%r",
        modelo,
        len(tools_llm),
        len(TOOL_SCHEMAS),
        puede_fotos,
        (mensaje_usuario or "")[:60],
    )
    catalogo_ok = False
    consulto_ficha = False   # ¿se abrió info_producto/buscar_info en este turno? (red de la salud)
    corregido_salud = False  # ya se le pidió una vez que consulte antes de dictaminar
    # ¿Ejecutó ALGUNA herramienta en este turno? (red de la asesoría). Cualquiera cuenta —
    # incluida enviar_catalogo o pedir_ayuda: lo que la red persigue es al bot contestando DE
    # MEMORIA, no a uno que decidió mal qué consultar.
    uso_herramienta = False
    corregido_asesoria = False  # ya se le pidió una vez que consulte y concrete (cero bucles)
    # Las herramientas con las que se puede ASESORAR. Si la dueña las apagó, la red de la
    # asesoría no existe este turno: ordenar consultar una tool apagada es el bucle que la red
    # del envío fantasma ya pagó (ver "EL REGAÑO SABE SI LA HERRAMIENTA EXISTE").
    tools_de_consulta = [t for t in ("ver_catalogo", "info_producto") if t in activas]
    # Montos AUTORIZADOS de este turno, YA SEPARADOS POR MONEDA: los precios reales del catálogo
    # (van inyectados en el prompt como "$12.00"), lo que escribió el cliente, y lo que vayan
    # devolviendo las herramientas (el total, el monto en bolívares, la tasa).
    #
    # 🔥 Antes esto era `_numeros_de(prompt entero)` y se tragaba TODOS los numerales: los
    # `id_para_pedir` del catálogo, la hora, la fecha, las cédulas. Por eso el bot pudo decirle a
    # una clienta REAL que el total era "$23": el 23 era **el ID de una variante**, no un precio.
    usd_ok, bs_ok = autorizados_por_moneda(estable, dinamico, mensaje_usuario)
    # DATOS SENSIBLES autorizados (cédulas, teléfonos, cuentas, correos): SOLO lo que escribió
    # el cliente y lo que devuelvan las herramientas en este turno. El prompt NO autoriza —
    # justo porque los datos bancarios escritos en la personalidad eran la fuga.
    datos_ok = _datos_sensibles(mensaje_usuario)
    for h in historial or []:
        if isinstance(h, dict) and h.get("role") == "user":
            contenido_h = str(h.get("content") or "")
            u, b = autorizados_por_moneda(contenido_h)
            usd_ok |= u
            bs_ok |= b
            datos_ok |= _datos_sensibles(contenido_h)
    # Los TOTALES solo los pone una HERRAMIENTA. El catálogo autoriza precios SUELTOS, no sumas:
    # sin esto, "$20 + $5 = $25" se colaba porque $25 es el precio del Pan Keto.
    usd_de_herramienta: set[float] = set()
    # 🔴 UN CUPO DE CORRECCIÓN **POR RED**, NO UNO COMPARTIDO (auditoría 2026-08-02, PRM-12).
    # Hasta hoy las tres redes de abajo (dinero · datos sensibles · frase prohibida) se repartían
    # un solo `corregido`, aunque cada docstring promete "una oportunidad de corregirse". La
    # segunda que disparara en el mismo turno no tenía NINGUNA: escalaba de frente y el turno
    # moría en `RESPUESTA_SEGURA`. Y la coincidencia es de lo más normal — un mensaje de cobro
    # lleva a la vez el monto y los datos bancarios, así que un solo resbalón del modelo apagaba
    # el cupo para el otro. Cada red tiene ahora el suyo; el tope de iteraciones sigue mandando.
    corregido_dinero = False
    corregido_fecha = False
    _calendario = None  # lo que devolvió `proxima_fecha_entrega` en este turno (si se llamó)
    corregido_datos = False
    corregido_prohibida = False
    pidio_ayuda = False  # ¿el bot llamó a pedir_ayuda en este turno?
    # ¿la escalada YA falló contra la base en ESTE turno? Con Postgres caído, sin este flag un
    # solo turno abría 5 sesiones y mandaba 2 WhatsApps — por cliente, cada pocos segundos.
    relevo_imposible = False
    reescrito = False    # ya se le pidió una vez que no hable como un sistema
    registro_ok = False  # ¿registrar_pedido devolvió OK en este turno? (red del pedido fantasma)
    reclamo_pedido = False  # ya se le llamó la atención una vez por decir que agendó sin agendar
    fotos_ok = False  # ¿enviar_fotos_producto ENVIÓ algo de verdad en este turno?
    fotos_intentadas = False  # ¿se LLAMÓ a enviar_fotos_producto este turno? (aunque no enviara)
    # ¿La MEMORIA de la herramienta frenó un re-envío? El cliente ya tiene esas fotos más arriba
    # en el chat: "ya te las mandé" sería VERDAD, y la red del envío fantasma no debe castigarlo.
    fotos_ya_mostradas = False
    reclamo_fotos = False  # ya se le llamó la atención por afirmar un envío de fotos falso
    reclamo_opcion = False  # ya se le llamó la atención por trabarse pidiendo el sabor
    reclamo_reapertura = False  # ya se le corrigió una vez por reabrir algo YA elegido (rama D)
    # 🔴 EL GUARD DEL COMPROBANTE, TRAÍDO AL MODO QUE CORRE HOY (auditoría 2026-08-02, PRM-3).
    # La regla 79 del prompt ORDENA: "al registrar el comprobante… dile que RECIBISTE su pago".
    # El bot obedecía, y `_PROHIBIDO_EN_CHARLA` ("afirmó que el pago ya llegó") lo frenaba con un
    # regaño que contradecía la regla de frente. El modo `dos` ya tenía la salida
    # (`hoja.pago_registrado` ⇒ solo se aplica `frase_prohibida_siempre`); el modo `uno` —el que
    # corre HOY— no la tenía, así que **todo comprobante reportado por TEXTO** ("ya pagué, ref
    # 004512", que entra por `registrar_comprobante` desde el bucle) caía en la trampa.
    # El carril de la VISIÓN no pasaba por aquí: iba por `redactar_mensaje`, que ya usaba la
    # lista corta. Esto empareja los dos carriles con UN solo criterio.
    pago_registrado = False  # ¿registrar_comprobante dio ok en este turno?
    pidio_fotos = _pide_fotos(pregunta_cliente)
    # ¿El cliente escribió "foto"/"video"/"imagen", o solo "muéstrame"? De eso depende a qué
    # apunta un "ya te lo envié" pelado — ver `_afirma_envio_fotos` (PRM-1).
    pidio_media = _pide_media_explicita(pregunta_cliente)

    for _ in range(settings.max_iteraciones_agente):
        data = await _llamar_con_fallback(messages, llm, modelo, tools_llm)
        msg = data["choices"][0]["message"]
        messages.append(msg)

        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            texto = (msg.get("content") or "").strip() or RESPUESTA_SEGURA

            # 🗓️ RED DEL DÍA IMPOSIBLE — va ANTES de la del dinero porque prometer un día en que
            # no se entrega es del mismo tamaño que inventar un monto: las dos son promesas que el
            # negocio no puede cumplir, y el cliente se entera DESPUÉS.
            #
            # El calendario se consulta AQUÍ, en el código, y NO se espera a que el modelo llame a
            # la herramienta: el 22-ago la llamó CERO veces teniendo la instrucción de que era
            # obligatoria. La red no puede depender de la buena voluntad del modelo (L40/L63).
            # ⚠️ EL CALENDARIO SOLO SE CONSULTA SI EL BOT NOMBRÓ UN DÍA. `_dias_nombrados` es
            # puro texto y no toca la BD, así que filtrar aquí sale gratis. La primera versión
            # lo pedía en TODOS los turnos: una consulta de más por mensaje, y un traceback en
            # cada banco que tiene lista cerrada de herramientas (lo destapó `probar_recibo_visible`
            # al correrlo contra Postgres local). Si nadie habla de fechas, no hay nada que validar.
            imposibles = []
            if _dias_nombrados(texto):
                if _calendario is None:
                    try:
                        _calendario = await ejecutar("proxima_fecha_entrega", {}, telefono)
                    except Exception:  # noqa: BLE001 — fail-open: sin calendario, la red calla
                        logger.warning("RED DEL DÍA: sin calendario en este turno; se deja pasar")
                        _calendario = {}
                imposibles = await _dias_imposibles(texto, _calendario)
            if imposibles and not corregido_fecha:
                corregido_fecha = True
                _buenas = ", ".join(
                    f["cuando"] for f in (_calendario or {}).get("proximas_fechas", [])[:3]
                )
                logger.error(
                    "DÍA IMPOSIBLE prometido por el modelo a %s: %s — texto=%r",
                    telefono, imposibles, texto[:160],
                )
                messages.append({
                    "role": "user",
                    "content": (
                        f"[SISTEMA] Nombraste {imposibles} y en ese/esos día(s) el negocio NO "
                        f"entrega. Las ÚNICAS fechas que puedes ofrecer ahora mismo son: {_buenas}. "
                        "Reescribe tu último mensaje con una de ESAS, copiada tal cual. "
                        "🔴 Y NO le des a elegir el día como si fueran opciones: dile con "
                        "naturalidad para cuándo se lo dejas ('te lo dejo para el lunes'), que es "
                        "como lo hace Whuilianny. Si él pide otro día, entonces sí lo conversas. "
                        "No le menciones al cliente este aviso."
                    ),
                })
                continue

            # RED DEL DINERO: ningún monto puede salir de la cabeza del modelo. Y ahora, además,
            # ninguna moneda puede salir cambiada: un dólar no puede presentarse como bolívar.
            inventados = _dinero_inventado(texto, usd_ok, bs_ok, usd_de_herramienta)
            if inventados:
                logger.error(
                    "DINERO INVENTADO por el modelo para %s: %s (usd_ok=%s bs_ok=%s tool=%s) — texto=%r",
                    telefono, inventados, sorted(usd_ok)[:8], sorted(bs_ok)[:8],
                    sorted(usd_de_herramienta)[:8], texto[:160],
                )
                if not corregido_dinero:
                    # Una oportunidad de corregirse, con los números buenos en la mano.
                    corregido_dinero = True
                    messages.append({
                        "role": "user",
                        "content": (
                            "[SISTEMA] Te saliste del guion del DINERO: escribiste "
                            f"{inventados} y esos montos NO salieron de ninguna herramienta ni "
                            "del catálogo. NUNCA calcules, sumes ni conviertas dinero de cabeza "
                            "(ni el envío, ni el total, ni los bolívares). Y JAMÁS llames "
                            "'bolívares' a una cifra en dólares: el monto en bolívares SOLO existe "
                            "si te lo dio `generar_datos_pago`. Reescribe "
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
                await _escalar(
                    ejecutar, telefono, "no_se",
                    "el bot iba a decir un monto que no salió del sistema "
                    f"({inventados}); NO se le envió al cliente",
                    ya_fallo=relevo_imposible,
                )
                return RESPUESTA_SEGURA

            # 🔴 RED DE LA SALUD: un veredicto sobre el cuerpo de alguien exige haber abierto la
            # ficha. El caso real: "¿es apta para diabéticos la kombucha?" → "Sí, es apta", sin
            # consultar, cuando la ficha dice `apto_diabeticos = 'no'`.
            if _dictamina_salud_sin_ficha(mensaje_usuario, texto, consulto_ficha):
                logger.error(
                    "SALUD SIN FICHA para %s: dictaminó sin abrir info_producto — texto=%r",
                    telefono, texto[:160],
                )
                if not corregido_salud:
                    corregido_salud = True
                    messages.append({
                        "role": "user",
                        "content": (
                            "[SISTEMA] Acabas de decirle a una persona si un producto le conviene "
                            "para su salud SIN abrir la ficha. No lo deduzcas de que sea fermentado, "
                            "keto o sin azúcar refinada: eso NO determina si es apto. Llama a "
                            "`info_producto` de ESE producto y responde con lo que diga su campo "
                            "'apto para diabéticos' —aunque sea NO— y con sus ingredientes reales. "
                            "Si la ficha no lo dice, dile con cariño que se lo confirmas. No le "
                            "menciones al cliente este aviso."
                        ),
                    })
                    continue
                # Insistió: NO sale un dictamen de salud que nadie verificó.
                logger.error("SALUD SIN FICHA 2 veces para %s: se escala a la dueña", telefono)
                await _escalar(
                    ejecutar, telefono, "reclamo",
                    "el bot iba a decirle a un cliente si un producto es apto para su condición "
                    "(diabetes, alergia, celiaquía…) SIN mirar la ficha, y ya se le corrigió una "
                    "vez. NO se envió. Contéstale tú.",
                    ya_fallo=relevo_imposible,
                )
                return RESPUESTA_SEGURA

            # 🔴 RED DE LOS DATOS BANCARIOS: una cédula, cuenta, teléfono o correo solo puede
            # salir si lo devolvió una herramienta en ESTE turno o lo escribió el cliente.
            # (El caso real: los datos vivían en el texto de la personalidad y el bot se los
            # pegó completos a una clienta SIN que hubiera pedido.)
            sensibles = _datos_sensibles_inventados(texto, datos_ok, usd_ok, bs_ok)
            if sensibles:
                logger.error(
                    "DATOS SENSIBLES INVENTADOS por el modelo para %s: %s — texto=%r",
                    telefono, sensibles, texto[:160],
                )
                if not corregido_datos:
                    corregido_datos = True
                    messages.append({
                        "role": "user",
                        "content": (
                            "[SISTEMA] Escribiste datos de pago o datos personales "
                            f"({', '.join(sensibles)}) que NINGUNA herramienta te dio en este "
                            "turno. Los datos bancarios SOLO se dan copiados de lo que devuelve "
                            "`generar_datos_pago` (campo `metodos_de_pago`), y SOLO los del "
                            "método que el cliente eligió. Si el cliente va a pagar, llama a "
                            "`generar_datos_pago`; si no, reescribe tu mensaje SIN esos datos. "
                            "No le menciones al cliente este aviso."
                        ),
                    })
                    continue
                # Insistió: NO se le mandan al cliente datos que el sistema no dio.
                logger.error("DATOS SENSIBLES 2 veces para %s: se escala a la dueña", telefono)
                await _escalar(
                    ejecutar, telefono, "no_se",
                    "el bot iba a mandar datos de pago/números que NO salieron del "
                    f"sistema ({sensibles}); NO se le enviaron al cliente",
                    ya_fallo=relevo_imposible,
                )
                return RESPUESTA_SEGURA

            # RED DE LA HONESTIDAD: hay frases que NO pueden salir nunca.
            #
            # En el carril del PAGO, "recibí tu pago" es lo que la regla 79 le ORDENA decir: si
            # `registrar_comprobante` dio ok en ESTE turno, solo se aplican las mentiras que
            # NINGUNA situación puede volver ciertas (el banco, la identidad, la salud). Es el
            # mismo criterio que ya usaban el modo `dos` (`hoja.pago_registrado`) y
            # `redactar_mensaje`. (PRM-3.)
            prohibida = (
                frase_prohibida_siempre(texto) if pago_registrado else _frase_prohibida(texto)
            )
            if prohibida:
                logger.error(
                    "FRASE PROHIBIDA para %s (%s) — texto=%r", telefono, prohibida, texto[:160]
                )
                if not corregido_prohibida:
                    corregido_prohibida = True
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
                await _escalar(
                    ejecutar, telefono, "reclamo",
                    f"el bot iba a decir algo que tiene PROHIBIDO ({prohibida}); "
                    "NO se le envió al cliente. Entra tú al chat.",
                    ya_fallo=relevo_imposible,
                )
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
                # 🔴 LA SENTENCIA LA DICTA EL ESTADO (25-ago). `registro_ok` solo ve ESTE
                # turno: con un pedido vivo de un turno anterior (registrado o ya PAGADO),
                # el regaño viejo ("en la BD NO existe, llama AHORA a registrar_pedido")
                # era mentira — y el modelo, obediente, fabricó el duplicado #2074 con su
                # cobro. La corrección ahora consulta la BD y dice la verdad que toque
                # (ver _correccion_fantasma).
                pedido_previo = await _pedido_reciente(telefono)
                logger.error(
                    "PEDIDO FANTASMA de %s: verbo de registro sin registrar_pedido en el "
                    "turno (pedido previo en BD: %s) — texto=%r",
                    telefono,
                    f"#{pedido_previo.id} {pedido_previo.estado}" if pedido_previo else "ninguno",
                    texto[:140],
                )
                if not reclamo_pedido:
                    reclamo_pedido = True
                    messages.append({
                        "role": "user",
                        "content": _correccion_fantasma(pedido_previo),
                    })
                    continue
                # Insistió: NO se le manda al cliente una confirmación falsa. Se escala,
                # contándole a la dueña el estado REAL para que no busque un pedido que no
                # existe (o ignore uno que sí).
                await _escalar(
                    ejecutar, telefono, "reclamo",
                    (
                        f"el bot insiste en verbos de registro sin registrar nada en este "
                        f"turno; el cliente YA tiene el pedido #{pedido_previo.id} "
                        f"({pedido_previo.estado}). NO se le envió el mensaje. Revisa el "
                        f"chat y coordina tú."
                        if pedido_previo
                        else
                        "el bot le dijo al cliente que le AGENDÓ el pedido pero NO lo "
                        "registró (no existe en el sistema). NO se le envió esa "
                        "confirmación falsa. Entra tú al chat y agéndalo."
                    ),
                    ya_fallo=relevo_imposible,
                )
                return RESPUESTA_SEGURA

            # 🔴 RED DEL CIERRE: se traba pidiendo el SABOR, que es OPCIONAL (ver el bloque de
            # `_pide_opcion_del_paquete`). Medido: 5/5 turnos pidiéndolo y 0 pedidos en la base.
            #
            # Dispara solo si se juntan las CUATRO: pregunta el sabor AHORA, ya lo había
            # preguntado ANTES (o sea, el cliente no lo contestó y está insistiendo), NO hay
            # pedido registrado en este turno, y el CLIENTE no acaba de pedir ese dato.
            # Preguntarlo UNA vez es correcto y no se toca.
            #
            # 🔴 LA CUARTA CONDICIÓN ES DEL 1-SEP (los guardias miran al cliente): la clienta
            # preguntó "De que sabor tienes?", el modelo escribió la lista perfecta y esta red
            # la censuró como bucle — al cliente le llegó "¿para cuándo la necesitas?" como si
            # el bot no supiera los sabores que SÍ sabía (verificado en logs y Redis).
            # Responder lo que el cliente acaba de pedir no es insistir. Se mira
            # `pregunta_cliente` y no `mensaje_usuario`: en el RETOMAR el mensaje es una orden
            # interna, y lo que importa es lo que el CLIENTE tenía pendiente.
            #
            # No escala ni mata el texto: el mensaje no es una MENTIRA, solo es un callejón sin
            # salida. Se le quita el falso bloqueo una vez y se le devuelve la decisión.
            dato_opcional = _dato_opcional_pedido(texto)
            if (
                not registro_ok
                and dato_opcional
                and _ya_pidio_opcion_antes(historial)
                and not _cliente_pidio_ese_dato(pregunta_cliente)
            ):
                logger.error(
                    "BUCLE DEL CIERRE con %s: vuelve a pedir %s (opcional) sin registrar "
                    "el pedido — texto=%r",
                    telefono, dato_opcional, texto[:140],
                )
                if not reclamo_opcion:
                    reclamo_opcion = True
                    messages.append({
                        "role": "user",
                        "content": (
                            f"[SISTEMA] YA LE PREGUNTASTE {dato_opcional.upper()} (o algo igual "
                            "de opcional) EN UN TURNO ANTERIOR Y NO TE LO DIO, y estás volviendo "
                            "a preguntarlo. Así se muere la venta: llevas varios turnos sin "
                            "avanzar. Ese dato es OPCIONAL — el pedido se registra SIN él y la "
                            "dueña lo coordina después (es como trabaja el negocio: bajo "
                            "pedido). NO se lo preguntes otra vez. Si ya tienes el producto, la "
                            "cantidad y la entrega (para cuándo y cómo), llama AHORA a "
                            "`registrar_pedido` dejando `opciones` vacío. Si de verdad te falta "
                            "el producto, el TAMAÑO, la cantidad o la entrega, pregunta SOLO eso "
                            f"— nunca {dato_opcional}. No le menciones al cliente este aviso."
                        ),
                    })
                    continue
                # Insistió. El texto SALE igual (no es una mentira, y callarlo dejaría al cliente
                # sin respuesta), pero queda escrito con nombre y apellido para poder medirlo.
                logger.error(
                    "BUCLE DEL CIERRE con %s: insistió con %s tras el aviso. El mensaje sale, "
                    "pero la venta está trabada en el cierre.", telefono, dato_opcional,
                )

            # 🔴 EL VIGILANTE PREGUNTA-vs-ESTADO (rama D): si el borrador REABRE algo que el
            # cliente YA eligió (le repregunta el sabor, le ofrece la otra masa, le pregunta el
            # tamaño), se le corrige UNA vez y redacta de nuevo. Es lo que IMPIDE cuando el hilo
            # inyectado (ramas #6 y C) no bastó — el prompt sugiere, esto impide. NO mata el
            # texto: a la segunda sale igual (como la red del cierre), porque insistir no es
            # mentir. Va DESPUÉS de la red del cierre (su prima: aquella cuida lo NO elegido,
            # esta lo YA elegido) y usa `pregunta_cliente` para heredar la regla de los guardias.
            reabierto = _reabre_eleccion_ya_hecha(
                texto, elecciones_hilo, elecciones_var, pregunta_cliente
            )
            if reabierto and not reclamo_reapertura:
                reclamo_reapertura = True
                logger.error(
                    "REAPERTURA de %s: el borrador reabre '%s' que el cliente YA eligió — "
                    "texto=%r", telefono, reabierto, texto[:140],
                )
                _ya = "; ".join(
                    [f"{p} → {e}" for p, e in elecciones_hilo]
                    + [f"{p} → {dim} {v}" for p, dim, v in elecciones_var]
                )
                messages.append({
                    "role": "user",
                    "content": (
                        f"[SISTEMA] Estás VOLVIENDO A PREGUNTAR u ofreciendo {reabierto}, y el "
                        f"cliente YA lo eligió. Lo que ya decidió (es un HECHO del sistema): "
                        f"{_ya}. NO se lo repreguntes ni le ofrezcas la otra opción — se siente "
                        "como que no le prestas atención. Reescribe tu mensaje dando por HECHA "
                        "esa elección (confírmala con naturalidad si hace falta) y avanza SOLO "
                        "con lo que de verdad falte (cantidad, entrega, cobro). Si el cliente "
                        "la cambió en su último mensaje, vale lo nuevo. No le menciones al "
                        "cliente este aviso."
                    ),
                })
                continue
            # Insistió tras el aviso: el texto SALE igual (no es una mentira; callarlo dejaría al
            # cliente peor). Queda medido, como en la red del cierre.
            if reabierto and reclamo_reapertura:
                logger.error(
                    "REAPERTURA con %s: insistió en reabrir '%s' tras el aviso. El mensaje sale.",
                    telefono, reabierto,
                )

            # 🔴 RED DEL ENVÍO FANTASMA DE FOTOS: "ya te la envié" sin haberla enviado NO SALE.
            # Caso real (2026-07-14, confirmado en el log): a "mándame la foto de la torta keto"
            # contestó "Ya te la envié hace poco 💚" con CERO llamadas a la herramienta — y su
            # propia mentira del turno anterior en la memoria como excusa.
            # `fotos_ya_mostradas` ABSUELVE (rama fotos-con-memoria): si la memoria de la
            # herramienta frenó el re-envío, las fotos EXISTEN más arriba en el chat — "ya te
            # las mandé" es verdad histórica, no una mentira. Sin esta absolución, la red
            # ordenaría re-llamar la herramienta (que volvería a negarse) y a la segunda vuelta
            # escalaría a la dueña una falsa alarma — un bucle de castigo por comportarse bien.
            if _afirma_envio_fotos(
                texto, pidio_fotos,
                pidio_media_explicita=pidio_media,
                catalogo_enviado=catalogo_ok,
            ) and not fotos_ok and not fotos_ya_mostradas:
                logger.error(
                    "ENVÍO FANTASMA de fotos a %s: dijo que las envió y NO llamó a "
                    "enviar_fotos_producto (o falló) — texto=%r",
                    telefono, texto[:140],
                )
                if not reclamo_fotos:
                    reclamo_fotos = True
                    # 🔴 EL REGAÑO SABE SI LA HERRAMIENTA EXISTE (fase 4). Sin esto, apagar las
                    # fotos convertía al bot en una MÁQUINA DE RESPUESTAS ENLATADAS, en silencio:
                    # `fotos_ok` no podía ponerse en True jamás, así que bastaba un falso positivo
                    # del detector de pronombre (el bot manda el PDF del catálogo y dice "ya te lo
                    # envié" mientras el cliente había pedido fotos) para que esta red disparara,
                    # le ordenara llamar a una herramienta QUE YA NO EXISTE, el modelo no pudiera
                    # obedecer, y el turno acabara en `RESPUESTA_SEGURA`. En bucle.
                    #
                    # La solución NO es poner `fotos_ok = True` cuando está apagada: eso desarmaría
                    # una red de HONESTIDAD y el bot podría afirmar un envío falso sin que nadie lo
                    # frenara. La red se queda viva; lo que cambia es lo que se le PIDE.
                    messages.append({
                        "role": "user",
                        "content": (
                            "[SISTEMA] ACABAS DE DECIR QUE ENVIASTE (o estás enviando) las fotos "
                            "y en ESTE turno NO llamaste a `enviar_fotos_producto` (o no envió "
                            "nada). El cliente NO recibió NINGUNA foto. Llama AHORA a "
                            "`enviar_fotos_producto` con el nombre del producto — aunque creas "
                            "que ya se las mandaste antes: si te las pide otra vez, se REENVÍAN. "
                            "Solo si la herramienta avisa que no hay fotos o que no se pudieron "
                            "enviar, dile la verdad con cariño y ofrece el catálogo — JAMÁS "
                            "afirmes un envío que no ocurrió. No le menciones al cliente este "
                            "aviso."
                        ) if puede_fotos else (
                            "[SISTEMA] Acabas de decir que le enviaste una foto, y TÚ NO PUEDES "
                            "ENVIAR FOTOS: esa capacidad está desactivada en este negocio. El "
                            "cliente no recibió nada. Reescribe tu mensaje diciéndole la verdad "
                            "con cariño —que las fotos se las manda la dueña— y ofrécele el "
                            "catálogo. JAMÁS afirmes un envío que no ocurrió. No le menciones al "
                            "cliente este aviso."
                        ),
                    })
                    continue
                # Insistió: NO se le manda al cliente la afirmación falsa. Se escala.
                await _escalar(
                    ejecutar, telefono, "no_se",
                    "el cliente pidió FOTOS y el bot iba a decirle que ya se las "
                    "envió SIN haberlas enviado. NO se le mandó esa mentira. "
                    "Mándale tú las fotos desde el WhatsApp del negocio.",
                    ya_fallo=relevo_imposible,
                )
                return RESPUESTA_SEGURA

            # 🔴 RED DE LA ASESORÍA: pidió una recomendación y el bot contestó DE MEMORIA (cero
            # herramientas en el turno) — el fallo raíz del smoke del 2026-08-08 (6/7 turnos así,
            # "¿cuántas personas?" ante "algo dulce para compartir el domingo"). UNA pasada
            # correctiva, el mismo mecanismo que la red de la salud. Va DESPUÉS de las redes que
            # vetan (dinero/honestidad juzgan primero el texto original) y ANTES de las que
            # avisan a la dueña — avisar por un borrador que esta corrección va a reemplazar
            # sería gritar en falso. Se mira `mensaje_usuario` (no `pregunta_cliente`) igual que
            # la salud: en el RETOMAR el turno es una orden interna y ahí no se dispara.
            if (
                tools_de_consulta
                and not uso_herramienta
                and not corregido_asesoria
                and _pide_asesoria(mensaje_usuario)
                and not _pide_catalogo(mensaje_usuario)
            ):
                corregido_asesoria = True
                logger.warning(
                    "ASESORÍA SIN CONSULTA para %s: recomendó de memoria, se le pide consultar "
                    "y concretar — texto=%r", telefono, texto[:140],
                )
                consultables = " o ".join(f"`{t}`" for t in tools_de_consulta)
                messages.append({
                    "role": "user",
                    "content": (
                        "[SISTEMA] El cliente te está pidiendo una RECOMENDACIÓN y le "
                        "respondiste de memoria, sin consultar NINGUNA herramienta. No le "
                        "recites categorías ni le devuelvas la pregunta: llama AHORA a "
                        f"{consultables}, elige 1 o 2 productos CONCRETOS que calcen con lo "
                        "que pidió y recomiéndaselos POR NOMBRE, con su gancho real (lo que "
                        "diga su ficha o el catálogo: de qué es, cuántas trae). Después remata "
                        "hacia el cierre. No le menciones al cliente este aviso."
                    ),
                })
                # Si tras esta corrección sigue sin consultar, el texto SALE tal cual (el
                # `corregido_asesoria` de arriba garantiza una sola pasada): esto es venta,
                # no salud — aquí no se bloquea ni se escala JAMÁS.
                continue

            # 🪦 AQUÍ VIVIÓ LA RED DEL PITCH (2026-08-08 → 2026-08-24), QUITADA A PROPÓSITO.
            # Re-preguntaba al modelo cuando el cliente elegía y la confirmación salía "plana",
            # ordenándole tejer datos de la ficha. En la prueba real de Maired (23-ago) fue la
            # causa de sus 2 quejas: descartó DOS borradores buenos del modelo ("Listo, para el
            # lunes por delivery entonces. ¿En qué zona estás? centro ($2) u oeste ($5)"), metió
            # la ficha repetida las 2 veces, perdió la explicación del "por qué hoy no" y los
            # precios de zona, y triplicó las llamadas al LLM. Además CONTRADECÍA la regla 66 de
            # `_REGLAS` ("NO RE-CONFIRMES EL PEDIDO EN CADA TURNO"). Decisión de Erwin: el pitch
            # es del MODELO — si sin esta muleta no vende, la palanca es subir de modelo, no
            # reescribirlo desde el código. NO reintroducirla sin medir antes y después.

            # RED DEL RELEVO: si PROMETE averiguar algo y no avisó a nadie, el aviso lo crea
            # el código. Una promesa sin aviso deja al cliente esperando para siempre.
            if _promete_averiguar(texto) and not pidio_ayuda:
                logger.warning(
                    "PROMESA SIN AVISO de %s: %r — se crea el aviso automáticamente",
                    telefono, texto[:120],
                )
                pidio_ayuda = await _escalar(
                    ejecutar, telefono, "no_se",
                    'el bot le prometió al cliente que le confirma algo que NO sabe. '
                    f'El cliente preguntó: "{(pregunta_cliente or "")[:160]}"',
                    ya_fallo=relevo_imposible,
                )
                relevo_imposible = relevo_imposible or not pidio_ayuda

            # RED DEL BUCLE: si es la TERCERA vez que pregunta lo mismo, el cliente no se la va a
            # contestar. El texto SÍ sale —callarlo dejaría al cliente con menos que antes— pero la
            # dueña se entera y entra a destrabarlo. Motivo 'no_se': avisa sin pausar el chat.
            if _pregunta_repetida(texto, historial) and not pidio_ayuda:
                logger.warning("BUCLE: %s lleva 3 turnos preguntando lo mismo", telefono)
                pidio_ayuda = await _escalar(
                    ejecutar, telefono, "no_se",
                    "el bot lleva TRES turnos preguntando lo mismo y el cliente no se lo contesta: "
                    "está atascado y la venta se está cayendo. Entra tú y destrábalo. "
                    f'Lo último que preguntó el cliente: "{(pregunta_cliente or "")[:160]}"',
                    ya_fallo=relevo_imposible,
                )
                relevo_imposible = relevo_imposible or not pidio_ayuda

            pidio_catalogo = _pide_catalogo(pregunta_cliente)
            texto = await _asegurar_catalogo(
                texto, catalogo_ok, telefono, ejecutar,
                pidio_catalogo=pidio_catalogo,
            )
            # 🖼️ RED DE LA FOTO — va DESPUÉS de `_asegurar_catalogo` y con el texto YA final:
            # así la foto sale ANTES que el texto (el mismo orden que ve el cliente cuando la
            # manda el modelo). `hubo_media` incluye `_afirma_envio_catalogo` a propósito: si el
            # bot afirmó el PDF, o ya salió (catalogo_ok) o lo acaba de mandar la red de arriba
            # — que no nos cuenta el resultado —; en ese turno ya viaja media y sumarle fotos
            # sería bombardear. `pregunta_cliente` y no `mensaje_usuario`: en el RETOMAR lo que
            # importa es lo que el CLIENTE estaba mirando, no la orden interna.
            await _asegurar_foto(
                texto, telefono, pregunta_cliente, ejecutar,
                puede_fotos=puede_fotos,
                hubo_media=(
                    catalogo_ok or fotos_ok or fotos_intentadas
                    or _afirma_envio_catalogo(texto, pidio_catalogo)
                ),
                # El historial va SOLO para recordar qué versión eligió el cliente hace pocos
                # turnos (`etiqueta_recordada`). No entra en ninguna otra decisión de la red.
                historial=historial,
            )
            # 🔴 EL SALUDO TAMBIÉN SE DEVUELVE AL VOLVER (lo reportó Maired el 2026-08-21).
            #
            # Esta condición era solo `_es_inicio_conversacion(historial)`, y eso dejó pasar el
            # caso más normal del mundo: la clienta había escrito el DÍA ANTERIOR, así que el
            # historial de Redis (TTL 24 h) traía un mensaje del bot, `_es_inicio_conversacion`
            # daba False y la red NO CORRÍA. Escribió *"Buenas tardes, ¿cómo estás? Me gustaría
            # saber si tienen empanadas de plátano"* y el bot le contestó el "¿cómo estás?" pero
            # NUNCA le devolvió las buenas tardes. Su queja, textual: *"Debería de decir buenas
            # tardes. Espejear lo que yo estoy haciendo."* Y tenía razón.
            #
            # Un saludo después de horas de silencio merece respuesta; el mismo "hola" dos minutos
            # después, no —ahí saludar dos veces se lee como un bot—. Así que la puerta se abre
            # por INICIO **o** por SILENCIO, y el umbral vive en la config.
            #
            # El coste se paga solo cuando hace falta: la consulta del silencio va DETRÁS de
            # comprobar que el cliente saludó y que el bot no le devolvió el saludo, así que en el
            # turno normal —donde no hay saludo que devolver— no se toca la BD.
            if not _es_inicio_conversacion(historial) and _falta_devolver_saludo(
                texto, mensaje_usuario
            ):
                if await horas_de_silencio(telefono) >= settings.saludo_tras_horas:
                    texto = _asegurar_saludo(texto, mensaje_usuario, nombre_cliente)
            elif _es_inicio_conversacion(historial):
                texto = _asegurar_saludo(texto, mensaje_usuario, nombre_cliente)
            return texto

        for tc in tool_calls:
            nombre_tool = tc["function"]["name"]
            # 🔴 EL GUARDIA DE LAS HERRAMIENTAS APAGADAS (fase 4).
            #
            # Va AQUÍ y no en `ejecutar_tool` a propósito: `ejecutar_tool` es la puerta que usan
            # las 7 REDES DE SEGURIDAD (llaman a `pedir_ayuda` y `enviar_catalogo` por su cuenta) y
            # el worker de visión (`registrar_comprobante`). Un candado allí les arrancaría el brazo.
            # El gate correcto es "qué VE el modelo", y este es el sitio donde se comprueba.
            if nombre_tool not in activas:
                logger.warning(
                    "TOOL APAGADA: el modelo llamó a %s (no está activa) para %s",
                    nombre_tool, telefono,
                )
                # ⚠️ HAY QUE RESPONDER IGUAL, con su `tool_call_id`. Un `tool_call` sin respuesta
                # hace que el proveedor rechace la SIGUIENTE request con un 400. Y se le da la
                # salida en el MISMO turno para que no queme iteraciones buscándola.
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(
                        {
                            "error": f"'{nombre_tool}' está DESACTIVADA en este negocio.",
                            "que_hacer": (
                                "NO la uses ni digas que la usaste. Si te hace falta para "
                                "responder, llama a `pedir_ayuda` y dile al cliente con cariño "
                                "que eso se lo confirmas enseguida."
                            ),
                        },
                        ensure_ascii=False,
                    ),
                })
                # `continue` a propósito: una llamada rechazada NO enciende ningún flag de red ni
                # autoriza ningún monto. No pasó nada, y el sistema no debe creer que sí.
                continue
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            resultado = await _ejecutar_con_guardas(
                ejecutar, nombre_tool, args, telefono, mensaje_usuario, historial
            )
            # El bot SÍ consultó algo (red de la asesoría). Cuenta aunque la tool devuelva
            # `{"error": ...}`: el modelo fue a buscar — si la herramienta reventó, eso ya queda
            # logueado abajo, y regañarlo por "no consultar" sería regañarlo por lo que sí hizo.
            # Las llamadas a tools APAGADAS no llegan aquí (el `continue` de arriba): esas no
            # encienden nada, como ya avisa su comentario.
            uso_herramienta = True
            if nombre_tool == "enviar_fotos_producto":
                # La red de la foto no re-empuja si el modelo YA lo intentó este turno, haya
                # salido algo o no: si la tool dijo "no hay fotos", repetir la llamada da lo mismo.
                fotos_intentadas = True
            if isinstance(resultado, dict) and resultado.get("error"):
                # 🔴 Hasta hoy este error viajaba SOLO dentro del mensaje `role=tool`: lo veía el
                # modelo y NADIE más. `{"error": ...}` es un shape exclusivo del `except` de
                # `ejecutar_tool` (ninguna herramienta lo devuelve como 'no encontrado'), así que
                # aquí SIEMPRE significa "la tool REVENTÓ". El caso que más duele es
                # `registrar_comprobante` por ESTA puerta (el cliente escribe "ya pagué, ref
                # 004512"): el mismo `registrar_comprobante` llamado por el worker de visión sí se
                # loguea (tasks.py), y por aquí se perdía mudo — y es dinero.
                logger.error(
                    "TOOL CON ERROR: %s para %s → %s",
                    nombre_tool, telefono, str(resultado["error"])[:200],
                )
            if nombre_tool == "enviar_catalogo" and isinstance(resultado, dict) and resultado.get("ok"):
                catalogo_ok = True
            # ¿Abrió la ficha del producto en ESTE turno? Es lo que la red de la salud exige antes
            # de dejar pasar un veredicto sobre si algo le conviene al cuerpo de alguien.
            if nombre_tool in ("info_producto", "buscar_info"):
                consulto_ficha = True
            if nombre_tool == "pedir_ayuda":
                # 🔴 SE MIRA EL RESULTADO, NO EL NOMBRE. Hasta hoy bastaba con que el modelo
                # LLAMARA a la tool para dar el relevo por hecho: si `pedir_ayuda` reventaba a
                # mitad (timeout de BD, FK, pool agotado), `ejecutar_tool` devolvía
                # `{"error": ...}` en silencio, el flag se encendía igual, y la RED DEL RELEVO de
                # abajo no volvía a intentarlo. Resultado: cero Intervencion, cero WhatsApp, el
                # chat SIN pausar, y el bot despidiéndose con un "te confirmo enseguida" que nadie
                # tenía encargo de cumplir. Es el mismo patrón que ya se arregló en
                # `registrar_pedido` (la red del pedido fantasma); aquí faltaba.
                if isinstance(resultado, dict) and resultado.get("ok"):
                    pidio_ayuda = True  # ya avisó: la red del relevo no tiene que hacer nada
                elif not pidio_ayuda:
                    # El `elif not pidio_ayuda` evita que una SEGUNDA llamada fallida en el mismo
                    # turno APAGUE un relevo que ya se había registrado bien.
                    pidio_ayuda = await _escalar(
                        ejecutar, telefono,
                        str(args.get("motivo") or "no_se"),
                        str(args.get("detalle") or "").strip()
                        or "el bot pidió ayuda y el aviso NO llegó a registrarse",
                        ya_fallo=relevo_imposible,
                    )
                    relevo_imposible = relevo_imposible or not pidio_ayuda
            if (
                nombre_tool == "registrar_pedido"
                and isinstance(resultado, dict)
                and resultado.get("ok")
            ):
                # El pedido existe DE VERDAD en la base. Sin esto, el bot podía decir
                # "listo, te agendo" sin haber registrado nada (caso real del 2026-07-12).
                registro_ok = True
            if (
                nombre_tool == "registrar_comprobante"
                and isinstance(resultado, dict)
                and resultado.get("ok")
            ):
                # El comprobante quedó REGISTRADO de verdad: a partir de aquí, "recibí tu pago"
                # es lo que la regla 79 ordena decir y deja de ser una mentira. Mismo criterio
                # que `hoja.pago_registrado` en el modo `dos`. (PRM-3.)
                pago_registrado = True
            if (
                nombre_tool == "enviar_fotos_producto"
                and isinstance(resultado, dict)
                and resultado.get("enviadas")
            ):
                # Salió al menos UNA foto/video de verdad: "ahí tienes las fotos" es verdad.
                fotos_ok = True
            if (
                nombre_tool == "enviar_fotos_producto"
                and isinstance(resultado, dict)
                and resultado.get("ya_mostrado")
            ):
                # La memoria de la herramienta frenó el re-envío: el cliente YA tiene esas fotos
                # más arriba en el chat. No es un envío fantasma — es historia verdadera.
                fotos_ya_mostradas = True
            # Todo monto que devuelve una herramienta queda AUTORIZADO para este turno — pero
            # CADA UNO EN SU MONEDA (el total en $ del `resumen`, los bolívares del `resumen_cobro`,
            # la tasa BCV). Así el bot puede copiar el monto en bolívares… y solo ESE.
            _crudo = json.dumps(resultado, ensure_ascii=False)
            _u, _b = autorizados_por_moneda(_crudo)
            usd_ok |= _u
            bs_ok |= _b
            # Y estos, además, son los ÚNICOS que pueden presentarse como un TOTAL.
            usd_de_herramienta |= _u
            # Los datos sensibles que devuelve una herramienta (los datos de pago de
            # generar_datos_pago, el teléfono del negocio de info_negocio…) quedan
            # AUTORIZADOS para este turno. Solo estos y los del cliente pueden salir.
            datos_ok |= _datos_sensibles(_crudo)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(resultado, ensure_ascii=False),
                }
            )

    # 🔴 EL SEXTO `return RESPUESTA_SEGURA`, EL ÚNICO QUE NO AVISABA A NADIE (2026-08-03).
    # Los otros cinco de este bucle escalan antes de rendirse; este se rendía mudo. Y el cliente
    # recibe EXACTAMENTE la misma promesa ("Dame un momentito y te confirmo") sin que nadie tenga
    # el encargo de cumplirla.
    #
    # Peor: es la ÚNICA grieta que el BARREDOR no tapa. Su SQL da por respondido a todo cliente
    # que tenga un mensaje 'assistant' posterior a su último entrante — y aquí el texto SÍ sale y
    # SÍ se guarda con ese rol. El vigilante lo ve atendido. La bandeja es la única red que le
    # queda a este cliente.
    #
    # `not pidio_ayuda`: si el modelo YA escaló bien dentro del bucle, no se abre una segunda fila
    # por lo mismo — el mismo criterio que la RED DEL RELEVO de arriba. Y el motivo es 'no_se' y
    # no 'reclamo': el bot no mintió, se quedó sin intentos. Además 'no_se' NO está en
    # `_MOTIVOS_DE_PAUSA` (tools.py), así que un tropiezo del bucle deja el aviso en la bandeja
    # pero no le quita el chat al bot para siempre.
    logger.warning("Agente excedió max iteraciones para %s", telefono)
    if not pidio_ayuda:
        await _escalar(
            ejecutar, telefono, "no_se",
            "el bot se quedó sin intentos para resolver este mensaje y solo le contestó "
            '"dame un momentito y te confirmo". Contéstale tú. El cliente preguntó: '
            f'"{(pregunta_cliente or "")[:160]}"',
            ya_fallo=relevo_imposible,
        )
    return RESPUESTA_SEGURA


# ══════════════════════════════════════════════════════════════════════════════════════════
#  LOS DOS AGENTES (fase 5) — el que HACE y el que HABLA
# ══════════════════════════════════════════════════════════════════════════════════════════
#
# 🔴 NO SE CONSTRUYEN DOS AGENTES: SE GENERALIZA UNO QUE YA EXISTE. `redactar_mensaje` (abajo) ya
# es una VOZ —un LLM sin herramientas, en la voz de Whuilianny, con las redes del dinero encima—
# y lleva semanas en producción hablando en los tres momentos del cobro. Aquí ese patrón, que ya
# funciona, se extiende a TODOS los turnos, y el `situacion: str` degradado pasa a ser una HOJA
# DE HECHOS de verdad.
#
# ⚠️ NO SE TOCA NI UNA TEMPERATURA. El Operador reusa `_llamar_openrouter` VERBATIM (0.15, con
# tools, el carril del dinero). La Voz reusa `_pedir_redaccion` VERBATIM (0.7, sin tools). Cada
# función conserva la suya. La naturalidad no sale de subir un dial: sale de que la Voz deja de
# cargar 12 herramientas, el catálogo, el calendario y 20 reglas de acción que no puede romper.
#
# ⚠️ NINGUNA RED SE RETIRA, y ninguna cambia de nombre ni de firma (3 bancos las importan por
# nombre). Lo que cambia es DE QUÉ SE ALIMENTAN: su lista blanca pasa de "todo número con marca de
# dinero en 16.400 tokens de prompt" a "los 2-5 montos que devolvieron las tools EN ESTE TURNO".
# El bug del "$23" —que era el `id_para_pedir` de una variante -- se vuelve IMPOSIBLE por
# construcción: los ids nunca entran a la hoja como dinero.

async def _dar_voz(
    hoja: HojaDeHechos, telefono, nombre_cliente, historial, pregunta, voz, modelo,
) -> str:
    """La VOZ: escribe el mensaje. Sin herramientas. Sin catálogo. Sin datos bancarios."""
    estable, dinamico = await construir_partes_prompt(nombre_cliente, telefono, quien="voz")
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
    # La hoja va en el turno `user` FINAL, no en el system: cambia cada turno, y meterla arriba
    # rompería el caché de la Voz en CADA mensaje. (Mismo criterio de siempre: lo fijo primero.)
    messages.append({"role": "user", "content": hoja.render(pregunta)})
    return await voz(messages, modelo)


async def _responder_dos_agentes(
    telefono, mensaje_usuario, historial, nombre_cliente, *,
    pregunta_cliente, llm, voz, ejecutar, modelo_operador, modelo_voz,
) -> str:
    activas = await leer_tools_activas()
    tools_llm = schemas_para(activas)
    puede_fotos = "enviar_fotos_producto" in activas
    hoja = HojaDeHechos()

    # ── EL OPERADOR ────────────────────────────────────────────────────────────────────
    estable, dinamico = await construir_partes_prompt(
        nombre_cliente, telefono, activas=activas, quien="operador"
    )
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

    logger.info(
        "responder[DOS]: op=%s voz=%s tools=%d msg=%r",
        modelo_operador, modelo_voz, len(tools_llm), (mensaje_usuario or "")[:50],
    )

    for _ in range(settings.max_iteraciones_agente):
        data = await _llamar_con_fallback(messages, llm, modelo_operador, tools_llm)
        msg = data["choices"][0]["message"]
        messages.append(msg)
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            hoja.encargo = (msg.get("content") or "").strip()
            break
        for tc in tool_calls:
            nombre_tool = tc["function"]["name"]
            if nombre_tool not in activas:  # el guardia de la fase 4
                logger.warning("TOOL APAGADA (dos): %s para %s", nombre_tool, telefono)
                messages.append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "content": json.dumps(
                        {"error": f"'{nombre_tool}' está DESACTIVADA.",
                         "que_hacer": "No la uses. Si te hace falta, llama a `pedir_ayuda`."},
                        ensure_ascii=False,
                    ),
                })
                continue
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            resultado = await _ejecutar_con_guardas(
                ejecutar, nombre_tool, args, telefono, mensaje_usuario, historial
            )
            if isinstance(resultado, dict) and resultado.get("error"):
                # Doble descarte hasta hoy: aquí no se logueaba, y `_renderizar` (hoja.py) hace
                # `if r.get("error"): return ""`. La Voz recibía una hoja que decía "NO
                # consultaste nada" y escribía tan tranquila, como si la herramienta nunca se
                # hubiera llamado. Que el CLIENTE no se entere está bien; que no se entere NADIE,
                # no.
                logger.error(
                    "TOOL CON ERROR (Operador): %s para %s → %s",
                    nombre_tool, telefono, str(resultado["error"])[:200],
                )
            hoja.anotar_tool(nombre_tool, resultado)   # ← el CÓDIGO anota, no el modelo
            messages.append({
                "role": "tool", "tool_call_id": tc["id"],
                "content": json.dumps(resultado, ensure_ascii=False),
            })
    else:
        logger.warning("Operador excedió max iteraciones para %s", telefono)

    # ── LAS LISTAS BLANCAS: la HOJA, y lo que escribió el propio cliente ────────────────
    usd_ok, bs_ok, totales_ok, datos_ok = hoja.listas_blancas()
    u, b = autorizados_por_moneda(mensaje_usuario)
    usd_ok, bs_ok = usd_ok | u, bs_ok | b
    datos_ok = datos_ok | _datos_sensibles(mensaje_usuario)
    for h in historial or []:
        if isinstance(h, dict) and h.get("role") == "user":
            c = str(h.get("content") or "")
            u, b = autorizados_por_moneda(c)
            usd_ok, bs_ok = usd_ok | u, bs_ok | b
            datos_ok |= _datos_sensibles(c)

    # ── EL SEAM: el ENCARGO del Operador se valida ANTES de pasárselo a la Voz ──────────
    #
    # 🔴 LA LISTA BLANCA DEL OPERADOR **NO** ES LA DE LA VOZ, Y ES A PROPÓSITO.
    #
    # El Operador SÍ tiene el catálogo en su prompt (con los precios escritos como "$25.00"), y
    # leerlo de ahí es LEGÍTIMO: son los precios reales de la BD. La primera versión de esto solo
    # autorizaba lo que devolvían las TOOLS, y el resultado fue absurdo — el bot se NEGÓ a decir
    # "el Pan Keto cuesta $25", que es la verdad, porque el precio venía del catálogo y no de una
    # llamada a `ver_catalogo`. La red funcionaba DE MÁS. Lo cazó la prueba con el bot real.
    #
    # ⚠️ Y esto NO reabre el bug del "$23": `autorizados_por_moneda` exige **marca de dinero**
    # ($ · Bs · USD), y un `id_para_pedir=23` no la lleva. Los ids siguen fuera.
    usd_op, bs_op = autorizados_por_moneda(estable, dinamico)
    usd_val, bs_val = usd_ok | usd_op, bs_ok | bs_op

    if _dinero_inventado(hoja.encargo, usd_val, bs_val, totales_ok):
        logger.error("DINERO INVENTADO en el ENCARGO de %s: %r", telefono, hoja.encargo[:120])
        # El `[SISTEMA]` **rebota AL OPERADOR** — que es quien TIENE la herramienta para
        # arreglarlo. Hoy ese mismo aviso ("LLAMA a la herramienta que lo da") se le grita a un
        # modelo que ya está en modo redacción y no puede obedecerlo. Aquí llega a quien puede.
        messages.append({
            "role": "user",
            "content": (
                "[SISTEMA] Escribiste un monto que no salió ni del catálogo ni de una herramienta. "
                "NUNCA calcules ni conviertas dinero de cabeza. Si te falta una cifra, LLAMA a la "
                "herramienta que la da (`registrar_pedido` / `generar_datos_pago`) y vuelve a "
                "escribir el encargo."
            ),
        })
        data = await _llamar_con_fallback(messages, llm, modelo_operador, tools_llm)
        msg = data["choices"][0]["message"]
        for tc in (msg.get("tool_calls") or []):
            n = tc["function"]["name"]
            if n not in activas:
                continue
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            r_tool = await _ejecutar_con_guardas(
                ejecutar, n, args, telefono, mensaje_usuario, historial
            )
            if isinstance(r_tool, dict) and r_tool.get("error"):
                # El re-prompt del dinero es el ÚLTIMO cartucho del Operador: si la tool que
                # tenía que darle el monto bueno revienta AQUÍ, el encargo se queda igual de
                # inventado y la Voz lo hereda. Sin esta línea no quedaba rastro de nada.
                logger.error(
                    "RE-PROMPT DEL DINERO: %s reventó para %s → %s",
                    n, telefono, str(r_tool["error"])[:200],
                )
            hoja.anotar_tool(n, r_tool)   # ← el CÓDIGO anota, no el modelo
        # 🔴 AQUÍ ESTABA EL AGUJERO, Y ERA UN `or`. La línea decía:
        #
        #     hoja.encargo = (msg.get("content") or "").strip() or hoja.encargo
        #
        # Si el reintento traía SOLO tool_calls y el contenido vacío —que es el patrón NORMAL de un
        # modelo que decide llamar a una herramienta—, ese `or` dejaba el encargo VIEJO: justo el
        # que la red acababa de rechazar por inventar dinero. Y veinte líneas más abajo el código
        # volvía a extraer montos de ESE MISMO texto para meterlos en la lista blanca:
        # **el monto rechazado se autorizaba a sí mismo.** Y si la Voz fallaba, el texto inventado
        # le salía al cliente por el camino de degradación.
        #
        # Ahora, si el reintento no trae texto nuevo, el encargo se VACÍA. No se pierde nada: las
        # tools del reintento SÍ quedaron anotadas arriba (con el precio de verdad), así que la Voz
        # conserva los hechos y `render()` cae a "Responde con naturalidad" — lo correcto.
        nuevo_encargo = (msg.get("content") or "").strip()
        if not nuevo_encargo:
            logger.error(
                "RE-PROMPT DEL DINERO: el Operador de %s no reescribió el encargo (solo tools). "
                "Se DESCARTA el encargo rechazado en vez de heredarlo.", telefono,
            )
        hoja.encargo = nuevo_encargo
        usd_ok, bs_ok, totales_ok, datos_ok = hoja.listas_blancas()
        # LA SEGUNDA PASADA DE LA RED, ahora con la lista blanca enriquecida por las tools del
        # reintento (si el Operador fue a buscar el precio bueno, aquí ya está autorizado). Si
        # inventó dinero DOS VECES, su texto no se hereda. No se escala desde aquí a propósito: el
        # cliente NO queda esperando —la Voz escribe desde los hechos de la hoja— y las 5 redes de
        # abajo siguen vigilando lo que de verdad sale. Escalar acá sería avisar dos veces.
        if hoja.encargo and _dinero_inventado(
            hoja.encargo, usd_ok | usd_op, bs_ok | bs_op, totales_ok
        ):
            logger.error(
                "DINERO INVENTADO DOS VECES en el encargo de %s: se descarta %r",
                telefono, hoja.encargo[:120],
            )
            hoja.encargo = ""

    # 🔑 EL ENCARGO VALIDADO **PASA A SER VERDAD**. Sin esto, la Voz no podría repetir un precio
    # que el Operador leyó del catálogo (ella no lo ve) y el turno moriría en `RESPUESTA_SEGURA`.
    # Solo entran MONTOS SUELTOS: un TOTAL sigue naciendo únicamente de `registrar_pedido` o
    # `generar_datos_pago`, nunca de una frase.
    #
    # ⚠️ Y solo entra un encargo que PASÓ la red: si vino inventado y el Operador no lo arregló,
    # arriba quedó en "" — así que este bloque ya no puede blanquear un monto rechazado.
    u_enc, b_enc = autorizados_por_moneda(hoja.encargo)
    hoja.montos_usd |= u_enc
    hoja.montos_bs |= b_enc
    usd_ok, bs_ok, totales_ok, datos_ok = hoja.listas_blancas()
    u, b = autorizados_por_moneda(mensaje_usuario)
    usd_ok, bs_ok = usd_ok | u, bs_ok | b
    datos_ok = datos_ok | _datos_sensibles(mensaje_usuario)

    # ── LA VOZ ─────────────────────────────────────────────────────────────────────────
    #
    # LA ESCALERA DE DEGRADACIÓN: la Voz NO puede ser un nuevo punto único de fallo. Si se cae,
    # el turno NO se pierde — sale el encargo del Operador, pasado por las mismas redes. Es lo que
    # HOY habría salido de todos modos.
    try:
        texto = await _dar_voz(
            hoja, telefono, nombre_cliente, historial, pregunta_cliente, voz, modelo_voz,
        )
    except Exception:  # noqa: BLE001
        logger.exception("La VOZ falló para %s: sale el encargo del Operador", telefono)
        texto = ""
    texto = (texto or "").strip() or hoja.encargo.strip()

    # ── LAS REDES, sobre lo que de verdad le llega al cliente ───────────────────────────
    #
    # NINGUNA se retira. Lo que cambia es su lista blanca: ya no es "todo el prompt", es la HOJA.
    #
    # 🔴 LA CLAUSURA `_escalar` DE AQUÍ SE BORRÓ. Llamaba a la tool del relevo a pelo, dentro de
    # un `try`, y NO MIRABA NADA: si la escalada reventaba contra la base, las cinco redes de
    # abajo devolvían RESPUESTA_SEGURA con el aviso sin crear, y la del relevo ni siquiera
    # actualizaba `hoja.escalado`. Ahora usan el `_escalar` de MÓDULO (arriba del archivo), que
    # mira el resultado, reintenta con una sesión nueva y, si tampoco, avisa fuera de la base.
    # Un solo sitio que escala en TODO el archivo: modo uno y modo dos comparten la red.
    relevo_imposible = False  # ¿ya falló la escalada contra la base en ESTE turno?

    # 🔴 NI VOZ NI ENCARGO: el único camino del modo dos que salía MUDO.
    #
    # Hasta hoy la línea de arriba terminaba en `or RESPUESTA_SEGURA`, así que este caso devolvía
    # "Dame un momentito y te confirmo 😊" por el final del turno **sin avisarle a nadie**: ninguna
    # de las 5 redes de abajo dispara con esa frase (comprobado contra sus regex), y el cliente
    # quedaba esperando para siempre una confirmación que nadie tenía encargo de mandar. Es el
    # mismo hueco que el sexto `return RESPUESTA_SEGURA` del modo uno tuvo hasta el 2026-08-03.
    #
    # Y el arreglo del re-prompt del dinero (arriba) lo vuelve MÁS alcanzable: ahora el encargo se
    # vacía cuando el Operador no lo reescribe, en vez de heredar el texto rechazado. Cerrarlo es
    # parte del mismo arreglo, no un extra.
    if not texto:
        logger.error("MODO DOS: sin Voz y sin encargo utilizable para %s", telefono)
        await _escalar(
            ejecutar, telefono, "no_se",
            "el bot se quedó sin nada que decirle al cliente: la Voz no respondió y el encargo del "
            "Operador no era utilizable. El cliente preguntó: "
            f'"{(pregunta_cliente or "")[:160]}"',
        )
        return RESPUESTA_SEGURA

    inventados = _dinero_inventado(texto, usd_ok, bs_ok, totales_ok)
    if inventados:
        logger.error("VOZ: dinero inventado %s para %s — NO sale", inventados, telefono)
        await _escalar(
            ejecutar, telefono, "no_se",
            f"la Voz iba a decir un monto que no salió del sistema ({inventados})",
            ya_fallo=relevo_imposible,
        )
        return RESPUESTA_SEGURA

    sensibles = _datos_sensibles_inventados(texto, datos_ok, usd_ok, bs_ok)
    if sensibles:
        logger.error("VOZ: datos sensibles %s para %s — NO sale", sensibles, telefono)
        await _escalar(
            ejecutar, telefono, "no_se",
            f"la Voz iba a mandar datos que no salieron del sistema ({sensibles})",
            ya_fallo=relevo_imposible,
        )
        return RESPUESTA_SEGURA

    # En el carril del PAGO, "recibí tu pago" es lo que el código ORDENA decir: ahí solo se aplican
    # las mentiras que NINGUNA situación puede volver ciertas (el banco, la identidad, la salud).
    prohibida = (
        frase_prohibida_siempre(texto) if hoja.pago_registrado else _frase_prohibida(texto)
    )
    if prohibida:
        logger.error("VOZ: frase prohibida (%s) para %s — NO sale", prohibida, telefono)
        await _escalar(
            ejecutar, telefono, "reclamo",
            f"la Voz iba a decir algo que tiene PROHIBIDO ({prohibida})",
            ya_fallo=relevo_imposible,
        )
        return RESPUESTA_SEGURA

    # El PEDIDO FANTASMA y el ENVÍO FANTASMA, re-anclados a la HOJA (no a unos flags sueltos).
    if _afirma_pedido_registrado(texto) and hoja.pedido_id is None:
        # 🔴 LA SENTENCIA LA DICTA EL ESTADO (25-ago, igual que en modo uno). La hoja solo ve
        # ESTE turno: con un pedido vivo de un turno anterior (registrado o pagado), el verbo
        # de registro casi siempre confirma un DETALLE (la hora, la dirección) de ese pedido.
        # La Voz no tiene reintento — las opciones son pasar o escalar — y con un pedido vivo,
        # escalar por "fantasma" sería un falso reclamo a la dueña. Sin pedido en la BD, el
        # fantasma es real y se escala como siempre.
        pedido_previo = await _pedido_reciente(telefono)
        if pedido_previo is None:
            logger.error("VOZ: pedido fantasma para %s — NO sale", telefono)
            await _escalar(
                ejecutar, telefono, "reclamo",
                "la Voz le dijo al cliente que le AGENDÓ el pedido pero NO existe. Entra tú y agéndalo.",
                ya_fallo=relevo_imposible,
            )
            return RESPUESTA_SEGURA
        logger.info(
            "VOZ: verbo de registro con pedido #%s (%s) vivo en BD para %s — pasa",
            pedido_previo.id, pedido_previo.estado, telefono,
        )

    # `hoja.fotos_ya_mostradas` absuelve igual que en el modo uno: la memoria de la herramienta
    # frenó un re-envío, así que las fotos existen más arriba — no es un envío fantasma.
    if _afirma_envio_fotos(
        texto,
        _pide_fotos(pregunta_cliente),
        pidio_media_explicita=_pide_media_explicita(pregunta_cliente),
        catalogo_enviado=hoja.catalogo_enviado,
    ) and hoja.fotos_enviadas == 0 and not hoja.fotos_ya_mostradas:
        logger.error("VOZ: envío fantasma de fotos para %s — NO sale", telefono)
        await _escalar(
            ejecutar, telefono, "no_se",
            "el cliente pidió FOTOS y la Voz iba a decir que se las envió SIN haberlas enviado."
            + ("" if puede_fotos else " (las fotos están DESACTIVADAS: mándaselas tú)"),
            ya_fallo=relevo_imposible,
        )
        return RESPUESTA_SEGURA

    # La RED DEL RELEVO: una promesa sin aviso deja al cliente esperando para siempre.
    if _promete_averiguar(texto) and not hoja.escalado:
        logger.warning("VOZ: promesa sin aviso de %s — el aviso lo crea el código", telefono)
        # 🔴 EL RESULTADO SE APUNTA EN LA HOJA. Hasta hoy esta red ni siquiera tocaba
        # `hoja.escalado`: si el aviso fallaba, la hoja seguía mintiendo el resto del turno.
        hoja.escalado = await _escalar(
            ejecutar, telefono, "no_se",
            'la Voz le prometió al cliente confirmarle algo. El cliente preguntó: '
            f'"{(pregunta_cliente or "")[:160]}"',
            ya_fallo=relevo_imposible,
        )
        relevo_imposible = relevo_imposible or not hoja.escalado

    # La RED DE LA VOZ. Casi sin trabajo ahora: la Voz NO TIENE sistema del que hablar (no ve el
    # catálogo, ni las notas de las tools, ni un solo `"ok": false`). Si dispara, es que la HOJA
    # está mal escrita — es su test.
    if _suena_a_sistema(texto):
        logger.warning("VOZ: sonó a sistema (¿la hoja está mal escrita?) — %r", texto[:90])

    # LA RED DEL BUCLE, igual que en el modo uno: el bot no puede quedarse preguntando lo mismo
    # para siempre. Aquí la Voz redacta pero es el Operador quien se atasca; el síntoma es el
    # mismo y el aviso también.
    if _pregunta_repetida(texto, historial) and not hoja.escalado:
        logger.warning("BUCLE (modo dos): %s lleva 3 turnos preguntando lo mismo", telefono)
        hoja.escalado = await _escalar(
            ejecutar, telefono, "no_se",
            "el bot lleva TRES turnos preguntando lo mismo y el cliente no se lo contesta: está "
            "atascado y la venta se está cayendo. Entra tú y destrábalo. "
            f'Lo último que preguntó el cliente: "{(pregunta_cliente or "")[:160]}"',
            ya_fallo=relevo_imposible,
        )
        relevo_imposible = relevo_imposible or not hoja.escalado

    texto = await _asegurar_catalogo(
        texto, hoja.catalogo_enviado, telefono, ejecutar,
        pidio_catalogo=_pide_catalogo(pregunta_cliente),
    )
    if _es_inicio_conversacion(historial):
        texto = _asegurar_saludo(texto, mensaje_usuario, nombre_cliente)
    return texto


async def _pedir_redaccion(messages: list, modelo: str) -> str:
    # LA VOZ. Su firma tampoco cambia: es inyectable (`voz=`) y la usan el modo dos y los bancos.
    # Anotarla aparte del `agente` importa porque es la que puede llamarse DOS veces en el mismo
    # turno (el `for intento in (1, 2)` de `redactar_mensaje`, cuando la red del dinero tumba el
    # mensaje): con el `paso` separado, ese reintento se ve en vez de confundirse con el bucle.
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
                json={"model": modelo, "messages": messages, "temperature": 0.7},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:  # noqa: BLE001 — se anota y se relanza: el que llama ya la trata
            await registrar(paso="voz", modelo_pedido=modelo, t0=t0, ok=False, error=str(e))
            raise
    # 🔴 FUERA del `async with` (revisión cruzada): dentro, el apunte mantenía abierto el socket
    # HTTP mientras se escribía en Postgres.
    await registrar(paso="voz", modelo_pedido=modelo, t0=t0, datos=data)
    return (data["choices"][0]["message"].get("content") or "").strip()


async def redactar_mensaje(
    situacion: str,
    historial: list | None = None,
    nombre: str | None = None,
    telefono: str | None = None,
    *,
    montos_usd: set[float] | None = None,
    montos_bs: set[float] | None = None,
) -> str:
    """Redacta un mensaje natural para el cliente en la voz de Whuilianny.

    NO es una plantilla: usa el contexto de la conversacion y algo de variacion
    para que cada mensaje salga distinto y humano. Se usa para momentos que
    dispara el sistema (comprobante recibido, pago confirmado/rechazado), donde
    no hay un texto del cliente que responder pero hay que decir algo con calidez.

    🔴 LA PUERTA DEL DINERO, QUE NO TENÍA GUARDIA (auditoría de arquitectura, 2026-07-13).
    Esta función devolvía el texto del modelo **TAL CUAL**, sin UNA SOLA comprobación — y es la
    que habla en los TRES momentos del dinero: cuando entra el comprobante, cuando el monto NO
    cuadra, y cuando la dueña confirma o rechaza un pago. El caso feo, con el código delante: en
    un pago parcial el sistema le pasa "faltan Bs 1.200" y el modelo remataba con "…o sea unos
    $12 más" — un dólar inventado, con una tasa inventada, directo al cliente. Y la frase "revisé
    mi banco y no me aparece tu pago" (la que ya explotó una vez, y que ESTÁ en la lista de
    prohibidas) salía por aquí sin que nadie la mirara, porque la lista solo se aplicaba en el
    otro camino.

    Ahora pasa por la MISMA red del dinero (`_dinero_inventado`) y por las mentiras que NINGUNA
    situación puede volver ciertas (`frase_prohibida_siempre`: el banco, la identidad, la salud).
    Lo que la situación SÍ le manda decir ("recibí tu pago", "no me llegó") no se toca: para eso
    están las dos listas separadas.

    Devuelve "" si el mensaje no se puede salvar ni corrigiéndolo. El que llama NO debe callarse:
    manda un acuse seguro y avisa a la dueña. Preferimos un mensaje sobrio a una mentira.
    """
    # 📊 EL TURNO DEL DINERO (telemetría, migración 032). Este carril lo dispara el SISTEMA, no un
    # mensaje del cliente: el comprobante que entra, el pago que la dueña confirma, el que rechaza.
    # Sin esto sus llamadas quedarían huérfanas justo en el momento del dinero. NO pisa el turno
    # del comprobante si ya está abierto (la visión y este mensaje son el mismo turno).
    abrir_turno(telefono, "pago")
    # `telefono` (nuevo): el carril del dinero era el que MENOS contexto tenía — redactaba sin la
    # ficha del cliente, justo en el momento en que hay que tratarlo mejor.
    estable, dinamico = await construir_partes_prompt(nombre, telefono)
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

    # 🔴 LA LISTA CERRADA DEL DINERO (el mismo movimiento que el "código de barras" del catálogo).
    #
    # La primera versión de esta red autorizaba, como en la charla, TODOS los números del prompt —
    # o sea, el catálogo entero. Y el banco de pruebas la tumbó al instante: el bot escribía
    # "faltan Bs 1.200, o sea unos $12 más" (un dólar CALCULADO con una tasa inventada) y **pasaba**,
    # porque el 12 existe en el catálogo: es el precio de las Empanadas Keto. Autorizar el catálogo
    # aquí no tiene ningún sentido: en este carril el bot NO está cotizando productos, está hablando
    # de UN pago concreto.
    #
    # Por eso el que llama (que es el CÓDIGO, y sabe lo que es verdad en ese momento: cuánto se
    # cobró, cuánto llegó, cuánto falta) le pasa una lista CERRADA con los únicos montos decibles.
    # Todo lo demás se frena, aunque "exista" en algún sitio.
    # La situación la escribe el CÓDIGO y trae los montos exactos ("faltan Bs 800"), cada uno con
    # su moneda: se leen igual que todo lo demás, marcados.
    usd_sit, bs_sit = autorizados_por_moneda(situacion)
    if montos_usd is None and montos_bs is None:
        usd_ok, bs_ok = autorizados_por_moneda(estable, dinamico)
        usd_ok |= usd_sit
        bs_ok |= bs_sit
    else:
        # LISTA CERRADA, y CADA MONEDA EN SU SACO: lo que el código cobró en dólares solo autoriza
        # dólares, y lo que cobró en bolívares solo autoriza bolívares. Mezclarlos era justo el bug
        # ("el total en bolívares es de $23 USD").
        usd_ok = set(montos_usd or ()) | usd_sit
        bs_ok = set(montos_bs or ()) | bs_sit
    # Datos sensibles (cédulas, cuentas, correos): en este carril NO hay herramientas, así que
    # lo único decible es lo que la SITUACIÓN (que la escribe el código) o el CLIENTE trajeron.
    # Los datos bancarios de la personalidad NO autorizan: eran la fuga.
    datos_ok = _datos_sensibles(situacion)
    for h in historial or []:
        if isinstance(h, dict) and h.get("role") == "user":
            contenido_h = str(h.get("content") or "")
            u, b = autorizados_por_moneda(contenido_h)
            usd_ok |= u
            bs_ok |= b
            datos_ok |= _datos_sensibles(contenido_h)

    for intento in (1, 2):
        texto = await _pedir_redaccion(messages, modelo)
        if not texto:
            return ""
        prohibida = frase_prohibida_siempre(texto)
        inventados = _dinero_inventado(texto, usd_ok, bs_ok)
        sensibles = _datos_sensibles_inventados(texto, datos_ok, usd_ok, bs_ok)
        if not prohibida and not inventados and not sensibles:
            return texto

        logger.error(
            "CARRIL DEL DINERO (intento %d): %s%s%s — texto=%r",
            intento,
            f"frase prohibida ({prohibida})" if prohibida else "",
            f" · montos inventados {inventados}" if inventados else "",
            f" · datos sensibles {sensibles}" if sensibles else "",
            texto[:160],
        )
        if intento == 2:
            return ""  # insistió: NO se le manda una mentira al cliente
        messages.append({"role": "assistant", "content": texto})
        messages.append({
            "role": "user",
            "content": (
                "[SISTEMA] Ese mensaje NO puede salir. "
                + (f"Dijiste algo que tienes PROHIBIDO ({prohibida}): tú no tienes acceso al banco, "
                   "no eres una persona y no eres médica. " if prohibida else "")
                + (f"Y escribiste montos que NO te dio nadie ({inventados}): NUNCA calcules ni "
                   "conviertas dinero de cabeza. Usa SOLO las cifras EXACTAS que te dieron en la "
                   "situación, copiadas tal cual; si te falta una, NO la digas. " if inventados else "")
                + (f"Y escribiste datos de pago o números ({', '.join(sensibles)}) que nadie te "
                   "dio en esta situación: en este mensaje NO se dan datos bancarios. Quítalos. "
                   if sensibles else "")
                + "Reescribe el mensaje sin eso, cálido y natural. No menciones este aviso."
            ),
        })
    return ""


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
    # 📊 LA NOTA DE VOZ ES DE LOS CARRILES MÁS CAROS Y EL MÁS INVISIBLE: el audio entra en tokens
    # (`prompt_tokens_details.audio_tokens`), va por `OPENROUTER_MODEL_AUDIO` —que el selector del
    # panel NUNCA toca— y hoy no hay una sola cifra de lo que cuesta.
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
                json={"model": settings.openrouter_model_audio, "messages": messages, "temperature": 0},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:  # noqa: BLE001 — se anota y se relanza: `_procesar_audio` ya lo trata
        await registrar(
            paso="transcripcion", modelo_pedido=settings.openrouter_model_audio,
            t0=t0, ok=False, error=str(e),
        )
        raise
    await registrar(
        paso="transcripcion", modelo_pedido=settings.openrouter_model_audio, t0=t0, datos=data,
    )
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
    # 📊 LA VISIÓN DEL COMPROBANTE (telemetría, migración 032). Es el carril del DINERO y el que
    # más caro sale por llamada (la imagen entra como tokens). Y su fallo tiene un coste de
    # negocio medible: cada `leido=False` es un pago que la dueña tiene que juzgar a mano. Que
    # quede contado, no solo logueado.
    t0 = time.monotonic()
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
    except Exception as e:  # noqa: BLE001 — leer el comprobante nunca debe tumbar el worker
        logger.exception("No se pudo leer el comprobante con visión")
        await registrar(
            paso="vision", modelo_pedido=settings.openrouter_model_audio,
            t0=t0, ok=False, error=str(e),
        )
        return {"es_comprobante": None, "leido": False}
    # 🔴 FUERA del `async with` y FUERA del `try` (revisión cruzada): dentro, el apunte mantenía
    # abierto el socket HTTP hasta 2 s. `registrar` no lanza nunca, así que aquí no hace falta red.
    await registrar(paso="vision", modelo_pedido=settings.openrouter_model_audio, t0=t0, datos=data)
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
