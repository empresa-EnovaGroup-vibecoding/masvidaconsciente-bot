"""Cliente de la WhatsApp Cloud API (Meta) para enviar mensajes.

⚠️ ESTE MÓDULO ES LA ÚNICA PUERTA HACIA META, y por eso vive aquí todo lo que Meta MIDE:
el ritmo de salida por número del negocio, el `429` cuando nos pide frenar, el CÓDIGO con el
que rechaza un envío y el tope de lo que nos descargamos. Enova es **Tech Provider oficial de
Meta**: un envío mal calibrado le quema la calidad a este número y arriesga la cuenta de Meta
de TODOS sus clientes futuros. Regla dura del proyecto (CLAUDE.md §3).
"""
import asyncio
import logging
import re
import time

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

GRAPH_VERSION = "v21.0"


# ─── Lo que Meta contesta cuando NO le gusta un envío ────────────────
#
# 🔴 Estos tres códigos son la TELEMETRÍA QUE NO PUEDE QUEDAR MUDA (auditoría 2026-08-02,
# META-8). Hasta hoy el cuerpo del error se guardaba como texto en `mensajes.error` y ahí
# moría: nadie los contaba, nadie los miraba, y son justo la señal temprana de que el número
# se está degradando.
#   · 131047 — "más de 24 h desde el último mensaje del cliente": el envío iba FUERA DE VENTANA.
#              No es un fallo de red: es que NO teníamos derecho a escribir.
#   · 131049 — "no entregado para mantener un ecosistema sano": Meta está FRENÁNDONOS a propósito.
#              Es la señal MÁS directa de degradación que da la Cloud API.
#   · 130472 — el usuario quedó fuera por un experimento/política de Meta.
CODIGO_FUERA_DE_VENTANA = 131047
CODIGO_ECOSISTEMA_SANO = 131049
CODIGO_EXPERIMENTO = 130472
CODIGOS_DE_CALIDAD = frozenset(
    {CODIGO_FUERA_DE_VENTANA, CODIGO_ECOSISTEMA_SANO, CODIGO_EXPERIMENTO}
)


class MetaRechazo(RuntimeError):
    """Meta rechazó un envío Y SABEMOS CON QUÉ CÓDIGO.

    Antes esto era un `resp.raise_for_status()`: la excepción de httpx NO lleva el cuerpo de la
    respuesta, así que el código de Meta —lo único que dice si el problema es la VENTANA, la
    CALIDAD del número o un simple dedazo— se perdía entre el `logger.error` y el `str(exc)[:400]`
    que acaba en `mensajes.error`. Sin el código no se puede decidir nada.

    Hereda de RuntimeError a propósito: todos los `except Exception` que ya rodean los envíos
    (que son todos) siguen atrapándola exactamente igual. Nada que hoy funcione cambia.
    """

    def __init__(self, status: int, codigo: int | None, cuerpo: str):
        self.status = status
        self.codigo = codigo
        self.cuerpo = (cuerpo or "")[:600]
        super().__init__(
            f"Meta rechazó el envío (HTTP {status}"
            + (f", código {codigo}" if codigo else "")
            + f"): {self.cuerpo[:300]}"
        )


def _codigo_en(texto: str) -> int | None:
    """El código numérico de Meta escondido en un cuerpo de error (o en un `mensajes.error`).

    Se busca por patrón y no parseando el JSON porque el mismo código tiene que poder leerse
    en los DOS sitios donde aparece: el cuerpo de la respuesta de Graph (`{"error":{"code":131047…`)
    y la cadena que arma el parser del webhook para `statuses` ("131047: title — message").
    """
    if not texto:
        return None
    m = re.search(r'"code"\s*:\s*(\d{3,6})', texto) or re.match(r"\s*(\d{3,6})\s*:", texto)
    return int(m.group(1)) if m else None


def codigo_meta(algo: BaseException | str | None) -> int | None:
    """El código de Meta detrás de lo que sea: una `MetaRechazo`, otra excepción cualquiera, o
    la cadena que quedó guardada en `mensajes.error`. Los tres sitios preguntan lo mismo."""
    if isinstance(algo, MetaRechazo):
        return algo.codigo
    return _codigo_en(algo if isinstance(algo, str) else str(algo or ""))


# ─── EL FRENO DE MANO: cuántos mensajes por segundo salen de ESTE número ──
#
# 🔴 Por qué existe (auditoría 2026-08-02, META-10): los únicos topes del sistema eran POR
# CONVERSACIÓN (6 globos, 3 archivos, 80 mensajes/día). Ninguno por NÚMERO DEL NEGOCIO — que es
# la unidad que Meta mide para el tier de la WABA. Un backlog de Celery drenando en paralelo
# salía sin freno alguno.
#
# El freno es DELIBERADAMENTE FLOJO Y LOCAL AL PROCESO, y esa es la decisión, no un descuido:
#   · Meta admite 80 msg/s en el tier base. El tope de aquí (15/s por proceso, ×2 workers = 30/s)
#     no se roza JAMÁS en la operación normal de este negocio; solo muerde en la estampida.
#   · Un limitador DISTRIBUIDO (Redis) metería un round-trip en el camino del DINERO y, si Redis
#     pestañea, o frena de más o no frena nada. Aquí el peor caso es esperar al siguiente segundo:
#     coste acotado a 1 s, sin estado compartido que se pueda corromper.
TOPE_ENVIOS_SEGUNDO = 15
_segundo_actual = 0
_envios_del_segundo = 0


async def _freno_de_tasa() -> None:
    """Espera al siguiente segundo si este proceso ya gastó su cupo. Nunca falla, nunca bloquea."""
    global _segundo_actual, _envios_del_segundo
    ahora = int(time.monotonic())
    if ahora != _segundo_actual:
        _segundo_actual, _envios_del_segundo = ahora, 0
    _envios_del_segundo += 1
    if _envios_del_segundo > TOPE_ENVIOS_SEGUNDO:
        logger.warning(
            "FRENO DE TASA: %s envíos en el mismo segundo hacia Meta (tope %s); se espera al "
            "siguiente. Si esto sale seguido, hay un backlog drenando sin control.",
            _envios_del_segundo, TOPE_ENVIOS_SEGUNDO,
        )
        await asyncio.sleep(1.0)
        _segundo_actual, _envios_del_segundo = int(time.monotonic()), 1


def _espera_del_429(resp: httpx.Response) -> float:
    """Cuánto pide Meta que esperemos tras un 429. Acotado a 2 s: más arriba está el turno del
    cliente esperando, y un globo que tarda 30 s no lo arregla nadie."""
    try:
        pedido = float(resp.headers.get("retry-after") or 1.0)
    except (TypeError, ValueError):
        pedido = 1.0
    return max(0.5, min(pedido, 2.0))


async def _enviar(payload: dict, *, que: str, timeout: float) -> dict:
    """El POST a Meta, con el freno delante y el 429 respetado. Devuelve el JSON o LANZA.

    ⚠️ UN SOLO reintento y SOLO ante un 429 (que es Meta diciendo literalmente "frena"). El
    reintento GENERAL ante un 5xx es otra cosa (F4, pendiente y medido aparte): reintentar a ciegas
    un envío que quizá SÍ salió le manda dos mensajes al cliente, y eso también le baja la calidad
    al número. Aquí solo obedecemos cuando Meta nos lo pide.
    """
    await _freno_de_tasa()
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(_url(), headers=_headers(), json=payload)
        if resp.status_code == 429:
            espera = _espera_del_429(resp)
            logger.warning(
                "Meta pidió FRENAR (429) al enviar %s; se espera %.1fs y se reintenta UNA vez",
                que, espera,
            )
            await asyncio.sleep(espera)
            await _freno_de_tasa()
            resp = await client.post(_url(), headers=_headers(), json=payload)
        if resp.status_code >= 400:
            codigo = _codigo_en(resp.text)
            logger.error(
                "Meta rechazó %s (HTTP %s, código %s): %s",
                que, resp.status_code, codigo, resp.text[:600],
            )
            if codigo in CODIGOS_DE_CALIDAD:
                logger.error(
                    "🔴 CÓDIGO DE CALIDAD DE META (%s) al enviar %s: esto NO es un dedazo, es "
                    "Meta midiendo este número. Siendo Tech Provider, no puede quedar en un log "
                    "y ya.", codigo, que,
                )
            destino = str(payload.get("to") or "")
            if es_numero_de_la_duena(destino):
                # Aquí es donde el sistema APRENDE que la ventana de la dueña está cerrada. Se
                # aprende UNA vez y se ahorran todos los intentos de la hora siguiente.
                await _cerrar_ventana_de_la_duena(destino, codigo)
            raise MetaRechazo(resp.status_code, codigo, resp.text)
        return resp.json()


def _graph_base() -> str:
    return f"https://graph.facebook.com/{GRAPH_VERSION}"


def _url() -> str:
    return f"{_graph_base()}/{settings.meta_phone_number_id}/messages"


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.meta_access_token}"}


# ─── LA VENTANA DE 24H **DE LA DUEÑA** ───────────────────────────────
#
# 🔴 El problema (auditoría 2026-08-02, META-15): de los 16 puntos de salida del sistema, solo 3
# comprueban la ventana de 24h, y NINGUNO de los que le avisan a la DUEÑA. Todos intentan siempre
# y se tragan el error: no es fail-closed, es *fail-silent después de intentar* — el intento fuera
# de ventana SÍ se ejecuta.
#
# ⚠️ POR QUÉ ESTO **NO** ES UN FAIL-CLOSED CONTRA POSTGRES, que es lo que pedía el reporte:
# la ventana de la dueña NO se puede leer de `clientes.ultimo_entrante_at` como la del cliente.
# Su número (`dueno_telefono`) es su teléfono PERSONAL y no es un cliente: no tiene fila, y desde
# META-11 no la va a tener nunca. Con el fail-closed clásico ("¿no hay fila? ⇒ cerrada") el
# resultado sería apagar de golpe el ÚNICO canal por el que la dueña se entera de que un cliente
# quedó colgado — el silencio que costó la semana del 10-17 de julio, otra vez y por decisión
# propia. Y sin plantilla HSM aprobada (META-15b) no hay nada que ponga en su lugar.
#
# Así que la regla es la única que se puede sostener con lo que se SABE:
#   · NO se intenta cuando CONSTA que está cerrada (Meta acaba de rechazarnos con 131047).
#   · SÍ se intenta cuando no consta, igual que hasta hoy.
# Meta nos dice la verdad UNA vez por hora en vez de una vez por aviso, y el aviso siempre queda
# en la BANDEJA — que es donde ella mira y donde nunca falló. Cuando la dueña escribe al número
# del negocio, la marca se limpia sola y el canal vuelve entero.
#
# UNA sola clave, y su VALOR es el veredicto ("abierta" / "cerrada"). Va así y no con dos claves
# a propósito: manda SIEMPRE la última evidencia, sin tener que borrar nada (`redis_client` solo
# expone get/set con TTL). La marca de "cerrada" dura 1 h; la de "abierta", las 24 h de Meta.
_MARCA_VENTANA = "cache:ventana_duena"
_TTL_CERRADA = 3600   # si nos equivocamos, el error se cura solo en una hora
_TTL_ABIERTA = 86400  # exactamente la ventana de Meta


def _cola(s: str | None) -> str:
    """Los últimos 10 dígitos: 58412…, +58412… y 0412… son el MISMO número."""
    d = "".join(c for c in (s or "") if c.isdigit())
    return d[-10:] if len(d) >= 10 else d


def es_numero_de_la_duena(destino: str) -> bool:
    """¿Este envío va al teléfono de la DUEÑA? (y no a un cliente).

    Se mira `DUENO_TELEFONO` del entorno. Si en la tabla `configuracion` hubiera otro número, el
    envío no se reconoce como suyo y se comporta como hasta hoy (se intenta): degradar hacia el
    comportamiento viejo es siempre el lado seguro de esta comprobación.
    """
    mio = _cola(settings.dueno_telefono)
    return bool(mio) and _cola(destino) == mio


async def abrir_ventana_de_la_duena() -> None:
    """La dueña ESCRIBIÓ al número del negocio: su ventana de 24h está abierta. Nunca lanza."""
    try:
        from app.services import redis_client as rc

        await rc.set_cache(_MARCA_VENTANA, "abierta", _TTL_ABIERTA)
        logger.info("La dueña escribió al número del negocio: su ventana de 24h queda ABIERTA")
    except Exception:  # noqa: BLE001 — sin Redis se sigue avisando por la vía de siempre
        logger.warning("No se pudo anotar que la dueña abrió su ventana de 24h")


async def puede_escribirle_a_la_duena(destino: str) -> bool:
    """¿Tiene sentido intentar el WhatsApp a la dueña AHORA? False SOLO si CONSTA que no.

    Los teléfonos internos ("__…", simulador y bancos) pasan siempre: no hay un WhatsApp real
    del otro lado y ningún banco puede quedar a merced de una marca en Redis.
    """
    if (destino or "").startswith("__"):
        return True
    try:
        from app.services import redis_client as rc

        return (await rc.get_cache(_MARCA_VENTANA)) != "cerrada"
    except Exception:  # noqa: BLE001 — si Redis no contesta, NO lo sabemos ⇒ se intenta igual
        return True


async def _cerrar_ventana_de_la_duena(destino: str, codigo: int | None) -> None:
    """Meta rechazó un aviso a la dueña: si fue por la VENTANA, que el siguiente no lo repita.

    Solo se anota el 131047 (fuera de ventana). Un 500 de Meta o un token caducado NO son la
    ventana, y confundirlos apagaría el canal por el motivo equivocado.
    """
    if (destino or "").startswith("__") or codigo != CODIGO_FUERA_DE_VENTANA:
        return
    logger.error(
        "🔴 META 131047: la ventana de 24h de la DUEÑA está CERRADA. Sus avisos por WhatsApp NO "
        "van a llegar hasta que ella le escriba al número del negocio. Durante la próxima hora "
        "quedan SOLO en la bandeja (y ahí no se pierde ninguno).",
    )
    try:
        from app.services import redis_client as rc

        await rc.set_cache(_MARCA_VENTANA, "cerrada", _TTL_CERRADA)
    except Exception:  # noqa: BLE001
        pass


# ─── "ESTE MENSAJE ES MÍO": el cinturón anti-eco, PUESTO A TIEMPO ────
#
# 🔴 Por qué (auditoría 2026-08-02, META-1): el webhook decide si un eco es NUESTRO buscando el
# `wa_message_id` en la tabla `mensajes` — y esa fila no existe hasta ~3-4 s DESPUÉS del envío
# (5 globos × `sleep(1)` + la latencia + `_guardar_en_panel`). Es un TOCTOU: la prueba de "es mío"
# llega tarde. Hoy no muerde porque la Cloud API no ecoa sus propios envíos, pero el comentario
# de ese cinturón lo vende como la protección "por si Meta lo cambia" — y NO protegería: el bot
# se auto-pausaría, firmado como si fuera una persona, en casi toda respuesta multi-globo, y
# quedaría MUDO con todos los clientes.
#
# La marca se pone en Redis en el instante en que Meta devuelve el id, mucho antes de que la fila
# exista. El chequeo de la BD se queda donde está (es el respaldo si Redis se reinicia): dos
# cinturones, no uno reemplazando al otro.
_PREFIJO_MIO = "cache:mio:"


async def marcar_mensaje_propio(wa_id: str | None) -> None:
    """Deja constancia INMEDIATA de que este `wamid` lo enviamos NOSOTROS. Nunca lanza."""
    if not wa_id:
        return
    try:
        from app.services import redis_client as rc

        await rc.set_cache(f"{_PREFIJO_MIO}{wa_id}", "1", 86400)
    except Exception:  # noqa: BLE001 — sin Redis queda el cinturón de la BD; jamás romper el envío
        pass


async def es_mensaje_propio(wa_id: str) -> bool:
    """True si ese `wamid` es de un mensaje que mandamos nosotros (marca de Redis)."""
    if not wa_id:
        return False
    try:
        from app.services import redis_client as rc

        return await rc.get_cache(f"{_PREFIJO_MIO}{wa_id}") is not None
    except Exception:  # noqa: BLE001 — si Redis no contesta, decide el cinturón de la BD
        return False


def wa_message_id(respuesta: dict | None) -> str | None:
    """El id que Meta le pone al mensaje que acabamos de enviar ('wamid.XXX').

    Viene en TODAS las respuestas de envío (`{"messages": [{"id": "wamid…"}]}`) y hasta ahora
    se TIRABA en el camino de la media: `enviar_imagen`/`enviar_video`/`enviar_documento`
    devolvían el JSON y quien las llamaba lo descartaba. Sin ese id no hay forma de casar los
    acuses de Meta (entregado / leído / **FALLÓ**) con la foto que se mandó — así que una foto
    que Meta rechaza se pierde en silencio, sin rastro en el panel.
    """
    try:
        return ((respuesta or {}).get("messages") or [{}])[0].get("id") or None
    except (AttributeError, IndexError, TypeError):
        return None


async def enviar_texto(telefono: str, texto: str) -> dict:
    # 🔴 EL PORTÓN DE LOS AVISOS A LA DUEÑA (auditoría 2026-08-02, META-15). La comprobación vive
    # AQUÍ, en la única puerta, y no repartida por los seis carriles que le avisan a ella: esos
    # seis eran seis sitios donde olvidarla —y de hecho estaba olvidada en los seis—. Cualquier
    # carril futuro queda cubierto sin acordarse de nada.
    #
    # NO se intenta cuando CONSTA que Meta lo va a rechazar (nos lo dijo con un 131047 hace menos
    # de una hora). Y se LANZA en vez de devolver un dict vacío: todos los carriles de aviso ya
    # traen su `except`, así que ninguno se rompe, pero ninguno se cree tampoco que el mensaje
    # salió. Un envío que no ocurrió no puede parecer un éxito.
    if es_numero_de_la_duena(telefono) and not await puede_escribirle_a_la_duena(telefono):
        raise MetaRechazo(
            0, CODIGO_FUERA_DE_VENTANA,
            "no se intentó: consta que la ventana de 24h de la dueña está CERRADA (Meta rechazó "
            "un aviso hace menos de una hora). El aviso queda en la bandeja del panel.",
        )
    payload = {
        "messaging_product": "whatsapp",
        "to": telefono,
        "type": "text",
        "text": {"body": texto},
    }
    return await _enviar(payload, que="un texto", timeout=20)


async def enviar_documento(
    telefono: str, link: str, filename: str, caption: str | None = None
) -> dict:
    """Envía un documento (PDF) por WhatsApp con un link PÚBLICO que Meta descarga.
    El link debe ser HTTPS y accesible sin login."""
    documento: dict = {"link": link, "filename": filename}
    if caption:
        documento["caption"] = caption
    payload = {
        "messaging_product": "whatsapp",
        "to": telefono,
        "type": "document",
        "document": documento,
    }
    return await _enviar(payload, que="un documento", timeout=30)


async def enviar_imagen(telefono: str, link: str, caption: str | None = None) -> dict:
    """Envía una IMAGEN por WhatsApp con un link PÚBLICO (HTTPS) que Meta descarga."""
    imagen: dict = {"link": link}
    if caption:
        imagen["caption"] = caption
    payload = {
        "messaging_product": "whatsapp",
        "to": telefono,
        "type": "image",
        "image": imagen,
    }
    return await _enviar(payload, que="una imagen", timeout=30)


async def enviar_video(telefono: str, link: str, caption: str | None = None) -> dict:
    """Envía un VIDEO por WhatsApp con un link PÚBLICO (HTTPS) que Meta descarga."""
    video: dict = {"link": link}
    if caption:
        video["caption"] = caption
    payload = {
        "messaging_product": "whatsapp",
        "to": telefono,
        "type": "video",
        "video": video,
    }
    return await _enviar(payload, que="un video", timeout=30)


async def marcar_leido_y_escribiendo(message_id: str) -> None:
    """Marca el mensaje como leído (doble check azul) Y muestra "escribiendo…".

    El indicador de tipeo lo borra Meta solo cuando respondemos o a los 25s.
    Por eso SOLO se llama cuando SÍ vamos a responder (humaniza al agente).
    No es crítico: si falla, el bot responde igual.

    ⚠️ Esa promesa del párrafo de arriba era MENTIRA hasta hoy y el arreglo vive en el webhook,
    no aquí (auditoría 2026-08-02, META-9): se llamaba SIEMPRE, antes del tope, del interruptor
    y de la lista blanca. Aquí solo se suma el FRENO DE TASA: con el bot apagado o un cliente
    pasado de tope, esto era un POST a Meta por cada mensaje, indefinidamente y sin contar.
    """
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
        "typing_indicator": {"type": "text"},
    }
    await _freno_de_tasa()
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.post(_url(), headers=_headers(), json=payload)
        except httpx.HTTPError as e:  # no es crítico si falla
            logger.warning("No se pudo marcar leído / mostrar escribiendo: %s", e)


# 🔴 EL TOPE DE LO QUE NOS BAJAMOS (auditoría 2026-08-02, META-13). WhatsApp acepta documentos
# de hasta 100 MB. Cada uno se cargaba ENTERO en la RAM del worker (`archivo.content`, sin límite)
# y de ahí al disco. Dos clientes mandando PDFs grandes a la vez tumban un worker de 512 MB por
# OOM — y el worker que se cae es el del DINERO. 25 MB deja pasar de sobra lo único que este
# carril recibe de verdad (capturas de pago, PDFs de banco, notas de voz: WhatsApp corta el audio
# en 16 MB) y le pone techo a la RAM.
MAX_MEDIA_BYTES = 25 * 1024 * 1024


class MediaDemasiadoGrande(RuntimeError):
    """El archivo pasa del tope. Es un fallo PERMANENTE: reintentarlo da exactamente lo mismo."""


async def descargar_media(media_id: str) -> tuple[bytes, str]:
    """Descarga un archivo (comprobante) de WhatsApp en 2 pasos.

    1) GET /{media_id} -> JSON con una URL temporal (caduca ~5 min), el mime_type y `file_size`.
    2) GET de esa URL, con el MISMO token, -> bytes del archivo.

    Devuelve (contenido, mime_type). Lanza si Meta responde error, no da URL o el archivo pasa
    de `MAX_MEDIA_BYTES` — y en ese último caso lanza `MediaDemasiadoGrande`, que quien llama
    tiene que tratar como definitivo (reintentar 100 MB tres veces es tres veces el mismo OOM).

    El tope se comprueba DOS veces a propósito: con `file_size` ANTES de bajar un solo byte (lo
    barato) y otra vez mientras se baja, por trozos, por si Meta no lo manda o miente (lo seguro).
    """
    async with httpx.AsyncClient(timeout=30) as client:
        meta = await client.get(f"{_graph_base()}/{media_id}", headers=_headers())
        meta.raise_for_status()
        info = meta.json()
        url = info.get("url")
        mime = info.get("mime_type") or "application/octet-stream"
        if not url:
            raise ValueError(f"Meta no devolvio URL de descarga para el media {media_id}")
        try:
            anunciado = int(info.get("file_size") or 0)
        except (TypeError, ValueError):
            anunciado = 0
        if anunciado > MAX_MEDIA_BYTES:
            raise MediaDemasiadoGrande(
                f"el archivo {media_id} pesa {anunciado} bytes y el tope son "
                f"{MAX_MEDIA_BYTES} (no se descarga)"
            )

        trozos: list[bytes] = []
        total = 0
        async with client.stream("GET", url, headers=_headers()) as archivo:
            archivo.raise_for_status()
            async for trozo in archivo.aiter_bytes():
                total += len(trozo)
                if total > MAX_MEDIA_BYTES:
                    raise MediaDemasiadoGrande(
                        f"el archivo {media_id} pasó de {MAX_MEDIA_BYTES} bytes mientras se "
                        "descargaba (se corta ahí: la RAM del worker no es negociable)"
                    )
                trozos.append(trozo)
        return b"".join(trozos), mime
