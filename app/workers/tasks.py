import asyncio
import json
import logging
import os
import re

from app.agent.agent import (
    leer_comprobante,
    redactar_mensaje,
    responder,
    transcribir_audio,
)
from app.config import get_settings
from app.services import redis_client as rc
from app.services.db import get_session_factory
from app.services.meta_client import descargar_media, enviar_texto
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)
settings = get_settings()

# Loop de asyncio persistente POR PROCESO del worker.
#
# Celery (prefork) corre tareas sincronas. Usar asyncio.run() en cada tarea
# crea y CIERRA un loop nuevo cada vez, dejando invalidas las conexiones async
# cacheadas (redis / engine de la BD) -> a partir de la 2da tarea explota con
# "RuntimeError: Event loop is closed". Reusar UN solo loop por proceso mantiene
# esas conexiones vivas entre tareas. Cada proceso de Celery tiene el suyo.
_LOOP = None


def _run(coro):
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_LOOP)
    return _LOOP.run_until_complete(coro)


async def _guardar_en_panel(
    telefono: str, nombre: str | None, texto_usuario: str, respuesta: str
) -> None:
    """Persiste la conversacion en Postgres para que aparezca en el panel.

    El historial en Redis es para el contexto del agente; el PANEL lee de Postgres
    (tablas clientes + mensajes). Sin esto, las charlas no se ven en el panel.
    No es critico: si falla, el bot ya respondio igual.
    """
    from sqlalchemy import select

    from app.models import Cliente, Mensaje, now_utc
    from app.services.db import get_session_factory

    try:
        factory = get_session_factory()
        async with factory() as session:
            cliente = (
                await session.execute(select(Cliente).where(Cliente.telefono == telefono))
            ).scalar_one_or_none()
            if cliente is None:
                session.add(Cliente(telefono=telefono, nombre=nombre, ultima_interaccion=now_utc()))
            else:
                cliente.ultima_interaccion = now_utc()
                if nombre and not cliente.nombre:
                    cliente.nombre = nombre
            session.add(Mensaje(cliente_telefono=telefono, rol="user", contenido=texto_usuario))
            session.add(Mensaje(cliente_telefono=telefono, rol="assistant", contenido=respuesta))
            await session.commit()
    except Exception:  # noqa: BLE001 — no critico: la respuesta ya se envio
        logger.exception("No se pudo guardar la conversacion en el panel de %s", telefono)


# ─── Cinturon de seguridad del DINERO (anti-alucinacion) ─────────────
# Solo la duena confirma un pago, desde el panel (eso dispara notificar_cliente_pago).
# Si el AGENTE, en una charla normal, afirma que un pago quedo confirmado, es una
# alucinacion: el bot NUNCA debe confirmar dinero por su cuenta. Lo interceptamos.
_FRASES_PAGO_CONFIRMADO = (
    "pago confirmado",
    "pago fue confirmado",
    "pago ya confirmado",
    "pago quedo confirmado",
    "pago esta confirmado",
    "confirmado tu pago",
    "confirme tu pago",
    "pago verificado",
    "verifique tu pago",
    "tu pago ya esta listo",
    "ya quedo confirmado tu pago",
)
_RESPUESTA_PAGO_SEGURA = "¡Recibido! Estoy revisando tu pago y te confirmo en un ratito 😊"


def _proteger_afirmacion_de_pago(respuesta: str) -> str:
    """Si el agente afirma que un pago quedo confirmado (cosa que SOLO la duena
    puede hacer desde el panel), lo reemplaza por un mensaje seguro de 'revisando'.
    Compara sin acentos para atrapar 'confirmo'/'confirmo', etc."""
    import unicodedata

    t = unicodedata.normalize("NFKD", respuesta.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    if any(frase in t for frase in _FRASES_PAGO_CONFIRMADO):
        logger.warning(
            "Anti-alucinacion dinero: el agente afirmo un pago confirmado en charla; reemplazado"
        )
        return _RESPUESTA_PAGO_SEGURA
    return respuesta


# ─── Envío humano: plano + varios mensajitos cortos (no un mensajote) ─

def _aplanar(texto: str) -> str:
    """Quita el formato que delata a un bot: viñetas (* - •) al inicio de línea,
    negritas/cursivas markdown (*texto*) y los decimales .00 de los precios.
    La dueña escribe PLANO; esto es una red de seguridad por si el modelo igual
    mete formato (a veces ignora la instrucción)."""
    lineas = [re.sub(r"^[ \t]*[\*\-•]+[ \t]+", "", ln) for ln in texto.split("\n")]
    t = "\n".join(lineas)
    t = t.replace("*", "")  # negritas / asteriscos sueltos
    t = t.replace(" — ", ", ").replace("—", ", ")  # raya larga (em-dash) -> coma (suena a folleto)
    t = re.sub(r"\$\s?(\d+)\.00(?!\d)", r"$\1", t)  # $18.00 -> $18
    return t


async def _enviar_en_partes(telefono: str, texto: str) -> None:
    """Envía la respuesta PLANA y como VARIOS mensajes cortos (como una persona real
    en WhatsApp), no un mensajote. El agente separa cada globo con una línea en blanco;
    aquí aplanamos el formato, partimos por las líneas en blanco y enviamos cada parte
    por separado, con una pausa breve. Tope de globos para proteger la calidad del número."""
    if not texto or not texto.strip():
        return
    texto = _aplanar(texto)
    partes = [p.strip() for p in re.split(r"\n\s*\n", texto.strip()) if p.strip()]
    if not partes:
        partes = [texto.strip()]
    if len(partes) > 6:  # tope anti-spam: junta el exceso en el último globo
        partes = partes[:5] + ["\n\n".join(partes[5:])]
    for i, parte in enumerate(partes):
        if i:
            await asyncio.sleep(1.0)  # pausa breve entre globos, como una persona
        await enviar_texto(telefono, parte)


# ─── Interruptor del bot (encender / apagar) ─────────────────────────

async def _bot_activo() -> bool:
    """Lee el interruptor del bot (config 'bot_activo'). Por defecto ENCENDIDO.
    Si falla la lectura, deja el bot encendido (no se queda mudo por un error de BD)."""
    from sqlalchemy import select

    from app.models import Configuracion
    from app.services.db import get_session_factory

    try:
        factory = get_session_factory()
        async with factory() as session:
            fila = (
                await session.execute(
                    select(Configuracion).where(Configuracion.clave == "bot_activo")
                )
            ).scalar_one_or_none()
        if fila and fila.valor is not None:
            return fila.valor.strip().lower() not in ("0", "false", "no", "off")
    except Exception:  # noqa: BLE001
        pass
    return True


async def _cliente_pausado(telefono: str) -> bool:
    """True si la dueña pausó el bot SOLO para este cliente (atiende ella ese chat).
    Ante error de lectura, devuelve False (el bot sigue respondiendo: fail-safe)."""
    from sqlalchemy import select

    from app.models import Cliente
    from app.services.db import get_session_factory

    try:
        factory = get_session_factory()
        async with factory() as session:
            cliente = (
                await session.execute(select(Cliente).where(Cliente.telefono == telefono))
            ).scalar_one_or_none()
        return bool(cliente and cliente.bot_pausado)
    except Exception:  # noqa: BLE001
        return False


async def _guardar_entrante(telefono: str, nombre: str | None, texto: str) -> None:
    """Guarda SOLO el mensaje entrante del cliente (sin respuesta), para que la
    dueña lo vea en Conversaciones cuando el bot está apagado y responda ella."""
    from sqlalchemy import select

    from app.models import Cliente, Mensaje, now_utc
    from app.services.db import get_session_factory

    try:
        factory = get_session_factory()
        async with factory() as session:
            cliente = (
                await session.execute(select(Cliente).where(Cliente.telefono == telefono))
            ).scalar_one_or_none()
            if cliente is None:
                session.add(Cliente(telefono=telefono, nombre=nombre, ultima_interaccion=now_utc()))
            else:
                cliente.ultima_interaccion = now_utc()
                if nombre and not cliente.nombre:
                    cliente.nombre = nombre
            session.add(Mensaje(cliente_telefono=telefono, rol="user", contenido=texto))
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo guardar el mensaje entrante de %s", telefono)


def _numero_permitido(telefono: str) -> bool:
    """LISTA BLANCA de pruebas: si settings.numeros_permitidos NO esta vacia, el bot
    SOLO responde a esos numeros; a los demas les guarda el mensaje pero NO responde
    (probar en produccion sin contestarle a clientes reales). Vacia = responde a todos.
    Compara por la COLA de 10 digitos, asi tolera el codigo de pais (+57, 0058...)."""
    permitidos = (settings.numeros_permitidos or "").strip()
    if not permitidos:
        return True  # sin lista blanca = responde a todos (produccion normal)

    def _cola(s: str) -> str:
        d = "".join(c for c in (s or "") if c.isdigit())
        return d[-10:] if len(d) >= 10 else d

    objetivo = _cola(telefono)
    return any(_cola(n) == objetivo for n in permitidos.split(","))


@celery_app.task(name="procesar_buffer")
def procesar_buffer(telefono: str, nombre: str | None = None):
    """Tarea Celery: procesa los mensajes acumulados de un cliente y responde."""
    _run(_procesar(telefono, nombre))


async def _procesar(telefono: str, nombre: str | None) -> None:
    # Solo un worker procesa el buffer de este cliente a la vez.
    if not await rc.adquirir_lock(telefono):
        return
    try:
        mensajes = await rc.vaciar_buffer(telefono)
        if not mensajes:
            return  # otra tarea ya lo procesó

        texto = "\n".join(mensajes)

        if not await _bot_activo() or await _cliente_pausado(telefono) or not _numero_permitido(telefono):
            # Bot apagado (global o solo en este chat): guarda lo que escribió el
            # cliente para que la dueña lo vea en Conversaciones y responda ella.
            await rc.guardar_historial(telefono, "user", texto)
            await _guardar_entrante(telefono, nombre, texto)
            return

        historial = await rc.obtener_historial(telefono)

        respuesta = await responder(telefono, texto, historial, nombre)
        respuesta = _proteger_afirmacion_de_pago(respuesta)

        await _enviar_en_partes(telefono, respuesta)
        await rc.guardar_historial(telefono, "user", texto)
        await rc.guardar_historial(telefono, "assistant", respuesta)
        await _guardar_en_panel(telefono, nombre, texto, respuesta)
    except Exception:  # noqa: BLE001
        logger.exception("Error procesando el buffer de %s", telefono)
    finally:
        await rc.liberar_lock(telefono)


# ─── Comprobantes de pago (imagenes / PDF) ───────────────────────────

_EXT_POR_MIME = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "application/pdf": "pdf",
}


def _guardar_comprobante(media_id: str, contenido: bytes, mime: str) -> str:
    """Guarda el binario del comprobante en COMPROBANTES_DIR y devuelve la ruta."""
    os.makedirs(settings.comprobantes_dir, exist_ok=True)
    base_mime = (mime or "").split(";")[0].strip().lower()
    ext = _EXT_POR_MIME.get(base_mime, "bin")
    ruta = os.path.join(settings.comprobantes_dir, f"{media_id}.{ext}")
    with open(ruta, "wb") as f:
        f.write(contenido)
    return ruta


async def _leer_comprobante_seguro(telefono, contenido, base_mime) -> dict:
    """Lee el comprobante con visión, dándole TODAS las cuentas de pago de la dueña
    (tabla metodos_pago) para reconocer SOLO pagos hacia alguna de ellas. Nunca lanza."""
    from sqlalchemy import select

    from app.models import MetodoPago

    cuentas: list[dict] = []
    try:
        factory = get_session_factory()
        async with factory() as session:
            metodos = (
                await session.execute(select(MetodoPago).where(MetodoPago.activo.is_(True)))
            ).scalars().all()
        cuentas = [
            {
                "titular": m.titular,
                "banco": m.banco,
                "telefono": m.telefono,
                "cedula": m.cedula,
                "cuenta": m.cuenta,
                "correo": m.correo,
                "wallet": m.wallet,
            }
            for m in metodos
        ]
    except Exception:  # noqa: BLE001
        pass
    try:
        return await leer_comprobante(contenido, base_mime, cuentas=cuentas)
    except Exception:  # noqa: BLE001 — defensa extra: nunca tumbar el worker
        logger.exception("Fallo leyendo el comprobante de %s", telefono)
        return {"es_comprobante": None, "leido": False}


async def _responder_situacion(telefono: str, situacion: str, nombre: str | None) -> None:
    """Whuilianny REDACTA un mensaje para el cliente según la situación (no plantilla),
    lo protege contra afirmaciones de pago, lo envía en partes y lo guarda en historial."""
    if not _numero_permitido(telefono):
        return  # lista blanca de pruebas: no responder a numeros fuera de la lista
    try:
        historial = await rc.obtener_historial(telefono)
        mensaje = await redactar_mensaje(situacion, historial, nombre)
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo redactar el mensaje al cliente %s", telefono)
        return
    mensaje = _proteger_afirmacion_de_pago(mensaje or "")
    if mensaje.strip():
        await _enviar_en_partes(telefono, mensaje)
        await rc.guardar_historial(telefono, "assistant", mensaje)


def _a_float(x):
    """Convierte un monto leído ('39.480,47' o '39480.47') a float. None si no se puede."""
    s = "".join(c for c in str(x or "") if c.isdigit() or c in ".,")
    if not s:
        return None
    if "," in s:  # formato venezolano: 39.480,47 -> 39480.47
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


async def _montos_cobrados(telefono: str):
    """Devuelve (monto_bs, monto_usd, monto_usd_divisas) que el bot le cobró (de la
    cotización en Redis). El comprobante puede venir en Bs (Pago Móvil/transferencia),
    en USD pleno, o en USD con 20% de descuento (divisas: Zelle/Binance/efectivo)."""
    try:
        guardado = await rc.get_cache(f"cobro:{telefono}")
        if guardado:
            d = json.loads(guardado)
            return (
                _a_float(d.get("monto_bs")),
                _a_float(d.get("monto_usd")),
                _a_float(d.get("monto_usd_divisas")),
            )
    except Exception:  # noqa: BLE001
        pass
    return None, None, None


@celery_app.task(name="procesar_comprobante")
def procesar_comprobante(telefono, message_id, media_id, caption=None, nombre=None, mime_type=None):
    """Tarea dedicada (fuera del buffer de texto): descarga y guarda el comprobante."""
    _run(_procesar_comprobante(telefono, message_id, media_id, caption, nombre, mime_type))


async def _procesar_comprobante(telefono, message_id, media_id, caption, nombre, mime_type) -> None:
    # Idempotencia del carril de DINERO: se marca SOLO tras un registro exitoso.
    # Si la descarga o el registro fallan, NO se marca, para que el reintento de
    # Meta tenga otra oportunidad real (la URL del media caduca a ~5 min).
    if message_id and await rc.comprobante_procesado(message_id):
        return

    try:
        contenido, mime = await descargar_media(media_id)
    except Exception:  # noqa: BLE001 — fallo transitorio: NO marcar, dejar reintentar
        logger.exception("No se pudo descargar el comprobante %s de %s", media_id, telefono)
        return
    ruta = _guardar_comprobante(media_id, contenido, mime or mime_type or "")
    logger.info("Comprobante de %s guardado en %s (%s bytes)", telefono, ruta, len(contenido))

    base_mime = (mime or mime_type or "").split(";")[0].strip().lower()
    es_imagen = base_mime.startswith("image/")

    # VISIÓN: extrae los datos y valida EN CÓDIGO que el pago sea A LA CUENTA de la
    # dueña. Solo para imágenes; un PDF no se analiza por visión.
    lectura = {}
    if es_imagen:
        lectura = await _leer_comprobante_seguro(telefono, contenido, base_mime)
    es_comprobante = lectura.get("es_comprobante")
    monto = lectura.get("monto")
    monto_ok = bool(monto) and str(monto).strip().lower() not in ("", "null", "none", "0")
    logger.info(
        "Visión comprobante de %s: imagen=%s leido=%s es_comprobante=%s pantalla=%s beneficiario=%r monto=%s",
        telefono, es_imagen, lectura.get("leido"), es_comprobante,
        lectura.get("es_pantalla_bancaria"), lectura.get("beneficiario_nombre"), monto,
    )

    # IMÁGENES = ESTRICTO: solo es un pago si la visión RECONOCIÓ un comprobante a la
    # cuenta de la dueña (es_comprobante True + monto). Si NO lo reconoció —no es
    # comprobante, es de otra cuenta, o la visión no pudo leer— se PIDE la captura y
    # NO se registra. (Antes la red de seguridad registraba ante la duda, y por eso
    # se colaban fotos cualquiera.)
    if es_imagen and not (es_comprobante is True and monto_ok):
        logger.info("Imagen de %s NO reconocida como comprobante de la dueña; no se registra", telefono)
        if message_id:
            await rc.marcar_comprobante(message_id)  # atendido: no reprocesar
        _pant = lectura.get("es_pantalla_bancaria")
        es_pantalla = _pant is True or str(_pant).strip().lower() in ("true", "si", "sí", "yes", "1")
        if es_pantalla:
            # SÍ es la pantalla de un pago/transferencia, pero NO a la cuenta de la dueña.
            situacion = (
                "El cliente te mandó una imagen de un pago o transferencia, pero ese pago NO te "
                "aparece hecho a TU cuenta / Pago Móvil (parece que fue a otra cuenta). Contéstale "
                "con calidez y con TUS PROPIAS PALABRAS, natural y DISTINTA cada vez (JAMÁS repitas "
                "la misma frase ni suenes a plantilla o robot): dile con cariño que ese pago no te "
                "aparece a tu cuenta, y pídele que verifique que lo envió a tu Pago Móvil y te "
                "reenvíe la captura. No lo acuses ni des el pago por hecho; solo pídele que confirme."
            )
        else:
            # No parece un comprobante (foto cualquiera, o no se ve el pago).
            situacion = (
                "El cliente te envió una imagen que no parece un comprobante de pago (parece otra "
                "cosa o no se alcanza a ver el pago). Contéstale con calidez y con TUS PROPIAS "
                "PALABRAS, natural y DISTINTA cada vez (JAMÁS repitas la misma frase ni suenes a "
                "plantilla): dile con cariño que ahí no ves el comprobante y pídele que te reenvíe "
                "la captura clara del pago (donde se vea el monto y la referencia)."
            )
        await _responder_situacion(telefono, situacion, nombre)
        return

    # ¿El MONTO del comprobante cuadra con lo cobrado? Comparamos contra el monto en
    # Bs (Pago Móvil/Transferencia) Y en USD (Binance/Zelle): basta que coincida con UNO.
    monto_cuadra = True
    if es_imagen:
        esperado_bs, esperado_usd, esperado_div = await _montos_cobrados(telefono)
        leido_monto = _a_float(monto)
        candidatos = [c for c in (esperado_bs, esperado_usd, esperado_div) if c is not None]
        if leido_monto is not None and candidatos:
            monto_cuadra = any(abs(leido_monto - c) <= max(1.0, c * 0.02) for c in candidatos)
        logger.info(
            "Monto comprobante de %s: leido=%s bs=%s usd=%s divisa=%s cuadra=%s",
            telefono, leido_monto, esperado_bs, esperado_usd, esperado_div, monto_cuadra,
        )

    # Aquí: la visión reconoció el comprobante (imagen), O es un PDF/otro -> red de
    # seguridad: se registra como 'reportado' para que la dueña lo revise.
    from app.agent.tools import registrar_comprobante

    # La referencia leída por visión solo se confía si reconoció un comprobante.
    referencia = lectura.get("referencia") if es_comprobante is True else None
    if not isinstance(referencia, str) or not referencia.strip():
        referencia = None

    factory = get_session_factory()
    try:
        async with factory() as session:
            resultado = await registrar_comprobante(
                session,
                telefono,
                referencia=referencia,
                comprobante_media_id=media_id,
                comprobante_url=ruta,
                # El monto que la visión leyó: sirve para saber si pagó en divisas (con el
                # 20% de descuento) o en bolívares (precio completo).
                monto_leido=_a_float(monto) if es_comprobante is True else None,
            )
    except Exception:  # noqa: BLE001 — error de BD: NO marcar, dejar reintentar a Meta
        logger.exception("No se pudo registrar el comprobante de %s", telefono)
        return
    logger.info("Comprobante de %s registrado: %s", telefono, resultado)

    # Registro exitoso: marcar para que un reintento de Meta no repita el cierre.
    if message_id:
        await rc.marcar_comprobante(message_id)

    # Cierre del CLOSER: si se registró el pago, SIGUE la venta (agradece, coordina,
    # ofrece más). No avisa a la dueña (su banco ya le avisa) ni afirma verificación.
    if resultado.get("ok") and monto_cuadra:
        situacion = (
            "el cliente acaba de mandar el comprobante de su pago y ya lo registraste. "
            "Agradécele con calidez, dile que recibiste su pago y que coordinas la "
            "entrega/envío, y déjale la puerta abierta por si quiere algo más. "
            "NO digas que verificaste el pago en el banco ni que está 'confirmado'."
        )
    elif resultado.get("ok"):
        # Registrado, pero el monto NO cuadra con lo cobrado: no afirmar que está completo.
        situacion = (
            "el cliente mandó el comprobante pero el MONTO no cuadra con el de su pedido. "
            "Con calidez dile que ya lo recibiste y lo estás revisando, y que le confirmas "
            "en un momentito. NO afirmes que el pago está completo ni confirmado."
        )
    else:
        situacion = (
            "el cliente te envió una imagen pero no hay un pedido esperando pago; "
            "pregúntale con calidez si es un comprobante y en qué lo puedes ayudar"
        )
    await _responder_situacion(telefono, situacion, nombre)


# ─── Notas de voz y otros eventos (respuesta humana) ─────────────────

async def _responder_y_enviar(telefono: str, texto: str, nombre: str | None) -> None:
    """Pasa un texto por el agente y envia la respuesta. Comparte el lock por
    cliente para no responder en paralelo con el flujo de texto."""
    if not await rc.adquirir_lock(telefono):
        return
    try:
        if not await _bot_activo() or await _cliente_pausado(telefono) or not _numero_permitido(telefono):
            await rc.guardar_historial(telefono, "user", texto)
            await _guardar_entrante(telefono, nombre, texto)
            return
        historial = await rc.obtener_historial(telefono)
        respuesta = await responder(telefono, texto, historial, nombre)
        respuesta = _proteger_afirmacion_de_pago(respuesta)
        await _enviar_en_partes(telefono, respuesta)
        await rc.guardar_historial(telefono, "user", texto)
        await rc.guardar_historial(telefono, "assistant", respuesta)
        await _guardar_en_panel(telefono, nombre, texto, respuesta)
    except Exception:  # noqa: BLE001
        logger.exception("Error respondiendo a %s", telefono)
    finally:
        await rc.liberar_lock(telefono)


@celery_app.task(name="procesar_audio")
def procesar_audio(telefono, message_id, media_id, nombre=None, mime_type=None):
    """Tarea: descarga la nota de voz, la transcribe y responde como a un texto."""
    _run(_procesar_audio(telefono, media_id, nombre, mime_type))


async def _procesar_audio(telefono, media_id, nombre, mime_type) -> None:
    transcripcion = ""
    try:
        contenido, mime = await descargar_media(media_id)
        transcripcion = await transcribir_audio(contenido, mime or mime_type or "audio/ogg")
    except Exception:  # noqa: BLE001 — escuchar el audio nunca debe tumbar al worker
        logger.exception("No se pudo escuchar la nota de voz de %s", telefono)
    if transcripcion.strip():
        await _responder_y_enviar(telefono, transcripcion, nombre)
    else:
        # No se pudo entender el audio: el agente responde con naturalidad.
        await _responder_y_enviar(
            telefono, "(el cliente envio una nota de voz que no se pudo escuchar bien)", nombre
        )


@celery_app.task(name="procesar_evento")
def procesar_evento(telefono, tipo, nombre=None):
    """Tarea: sticker/video/ubicacion/etc. El agente responde natural, sin robotismos."""
    _run(_responder_y_enviar(telefono, f"(el cliente envio un {tipo}, sin texto)", nombre))


@celery_app.task(name="notificar_cliente_pago")
def notificar_cliente_pago(telefono, situacion):
    """Tarea: avisa al cliente (pago confirmado/rechazado) con un mensaje redactado
    al momento por Whuilianny, en su voz y con contexto — no una plantilla."""
    _run(_notificar_cliente_pago(telefono, situacion))


async def _notificar_cliente_pago(telefono, situacion) -> None:
    try:
        historial = await rc.obtener_historial(telefono)
        mensaje = await redactar_mensaje(situacion, historial, None)
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo redactar el aviso de pago para %s", telefono)
        return
    if mensaje.strip():
        await _enviar_en_partes(telefono, mensaje)
        await rc.guardar_historial(telefono, "assistant", mensaje)
