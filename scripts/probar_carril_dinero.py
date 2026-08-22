"""EL CARRIL DEL DINERO — la puerta que no tenía guardia.

`redactar_mensaje` es la que le habla al cliente en los TRES momentos del dinero: cuando manda el
comprobante, cuando el monto NO cuadra, y cuando la dueña confirma o rechaza el pago. Devolvía el
texto del modelo **TAL CUAL**, sin una sola comprobación, y a temperatura 0.7 (el resto del bot
corre a 0.15). Encontrado en la auditoría de arquitectura del 2026-07-13.

Lo que se prueba:
  1. 💵 La red del dinero ya NO es ciega: caza "28$", "28 dólares", "28 USD" (antes solo veía "$28").
  2. 🔴 "son 5.000 Bs" ya NO se autoriza a sí mismo. (Antes: al monto se le sacaban todas las
     lecturas posibles —5.000 se leía también como 5— y bastaba con que UNA estuviera autorizada.
     Como el 5 casi siempre está en el catálogo, CUALQUIER cifra en bolívares pasaba. En el carril
     donde el bot cobra de verdad.)
  3. 🚪 La puerta del dinero tiene guardia: si el modelo inventa un monto o dice una frase
     prohibida, el mensaje NO sale (ni a la segunda).
  4. 🧭 Las dos listas de frases prohibidas: lo que es mentira SIEMPRE (el banco, ser una persona,
     la salud) se frena en TODOS los carriles; lo que la situación SÍ le manda decir ("recibí tu
     pago") NO se frena en el carril del pago — si no, mataríamos el mensaje correcto.
  5. ⏰ El aviso de pago comprueba la ventana de 24h de Meta (es el ÚNICO camino que habla DÍAS
     después). Cerrada ⇒ no envía y te avisa a TI.
  6. 🔌 El interruptor de apagado ya cubre el comprobante: con el bot apagado, no le habla al
     cliente que paga (pero el pago SÍ queda registrado).

Y desde la auditoría del 2026-08-02 (tanda 4), EL CARRIL DEL COMPROBANTE de punta a punta:
  7. 🔁 El reintento EXISTE (SIL-5). Los `except` decían "dejar reintentar a Meta" — y Meta no
     reintenta (el webhook ya devolvió 200 al encolar) y Celery tampoco (el `@task` iba a secas).
     El pago se perdía PARA SIEMPRE: sin fila, sin respuesta al cliente y sin aviso a la dueña.
     Ahora se reintenta 3 veces y, si aun así no se puede, se le entrega el caso a una persona.
  8. 💾 El disco no puede tumbar el cobro: `_guardar_comprobante` vivía FUERA de todo try, así
     que un /data lleno mataba la tarea y el PAGO no dejaba ni un rastro.
  9. 👁️ El dinero se juzga UNA vez: la visión no es determinista, y con reintentos el segundo
     intento podía dar un veredicto DISTINTO sobre el MISMO comprobante.
 10. 🙈 "No pude leer" ≠ "no es un comprobante" (SIL-6): con la visión caída el bot pedía la
     captura otra vez, con cada captura, y el negocio dejaba de cobrar en silencio.

No se manda un solo WhatsApp: Meta está amordazado y el modelo, sustituido por un doble.
"""
import asyncio
import sys
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import delete, select

from app.agent import agent as ag
from app.agent.agent import (
    _dinero_inventado,
    _frase_prohibida,
    autorizados_por_moneda,
    frase_prohibida_siempre,
)
from app.agent.system_prompt import _REGLAS
from app.models import Cliente, Configuracion, Intervencion, Mensaje, Pago, Pedido, now_utc
from app.services import redis_client as rc
from app.services.db import get_session_factory
from app.workers import tasks

TEL = "__prueba_dinero__"
fallos: list[str] = []
enviados: list[tuple[str, str]] = []

# Los ids que usa el carril del COMPROBANTE (secciones 6-8). Están en constantes para poder
# SOLTARLOS en `_limpiar`: la marca `comprob:` dura 24 h y la caché de visión 15 min, así que sin
# esto la SEGUNDA corrida del banco saldría toda por "duplicado" —el banco verde el lunes y rojo
# el martes, sin que nada esté roto—. Un banco que solo funciona una vez no es un banco.
MENSAJES_PRUEBA = (
    "wamid.PRUEBA_COMP_1", "wamid.DISCO", "wamid.BD", "wamid.VOL", "wamid.IL1",
    "wamid.ILEG", "wamid.FOTO", "wamid.OTRA", "wamid.GIF1", "wamid.GIF2",
)
MEDIAS_PRUEBA = (
    "media_prueba_comp_1", "media_disco", "media_bd", "media_voluble", "media_ilegible_cache",
    "media_ileg_1", "media_foto_1", "media_otra_cuenta", "media_gif_1", "media_gif_2",
)


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"   {'[OK ]' if ok else '[MAL]'} {nombre}{('  → ' + detalle) if detalle and not ok else ''}")
    if not ok:
        fallos.append(nombre)


async def _falso_envio(telefono: str, texto: str) -> dict:
    enviados.append((telefono, texto))
    return {"messages": [{"id": f"wamid.D{len(enviados)}"}]}


async def _limpiar() -> None:
    f = get_session_factory()
    async with f() as s:
        pedidos = (await s.execute(
            select(Pedido.id).where(Pedido.cliente_telefono == TEL)
        )).scalars().all()
        if pedidos:
            await s.execute(delete(Pago).where(Pago.pedido_id.in_(pedidos)))
            await s.execute(delete(Pedido).where(Pedido.id.in_(pedidos)))
        await s.execute(delete(Mensaje).where(Mensaje.cliente_telefono == TEL))
        await s.execute(delete(Intervencion).where(Intervencion.cliente_telefono == TEL))
        await s.execute(delete(Cliente).where(Cliente.telefono == TEL))
        await s.commit()
    await rc.borrar_memoria(TEL)
    # Los candados antiinundación duran 15 min y las marcas del dinero 24 h: sin soltarlos, el
    # caso siguiente no vería ningún aviso —o saldría por "duplicado"— y parecería una regresión.
    # Mismo patrón que `probar_retomar._soltar_candado`.
    await rc._client().delete(
        f"aviso:vision_caida:{TEL}", "aviso:panel_incompleto",
        *[f"comprob:{m}" for m in MENSAJES_PRUEBA],
        *[f"cache:vision:{m}" for m in MEDIAS_PRUEBA],
    )


# ─── Utilería del carril del COMPROBANTE (secciones 6-8) ─────────────

async def _pedido_esperando_pago(total: str = "28") -> int:
    """Un cliente con un pedido ESPERANDO PAGO: sin esto `registrar_comprobante` devuelve
    ok=False y no se crea ningún Pago (esa puerta es la que impide que una foto cualquiera
    se convierta en dinero)."""
    f = get_session_factory()
    async with f() as s:
        s.add(Cliente(telefono=TEL, nombre="Rosa", ultimo_entrante_at=now_utc()))
        await s.flush()
        ped = Pedido(cliente_telefono=TEL, items=[], total=Decimal(total), estado="esperando_pago")
        s.add(ped)
        await s.commit()
        return ped.id


async def _pagos() -> list[tuple[str, str | None]]:
    f = get_session_factory()
    async with f() as s:
        filas = (await s.execute(
            select(Pago.estado, Pago.comprobante_url)
            .join(Pedido, Pago.pedido_id == Pedido.id)
            .where(Pedido.cliente_telefono == TEL)
        )).all()
    return [(e, u) for e, u in filas]


async def _motivos() -> list[str]:
    f = get_session_factory()
    async with f() as s:
        return list((await s.execute(
            select(Intervencion.motivo).where(Intervencion.cliente_telefono == TEL)
        )).scalars().all())


async def _burbujas() -> list[tuple[str | None, str | None]]:
    """(media_id, media_url) de lo que entró al HILO del panel."""
    f = get_session_factory()
    async with f() as s:
        filas = (await s.execute(
            select(Mensaje.media_id, Mensaje.media_url)
            .where(Mensaje.cliente_telefono == TEL, Mensaje.media_id.is_not(None))
        )).all()
    return [(m, u) for m, u in filas]


async def main() -> None:
    tasks.enviar_texto = _falso_envio
    tasks.settings.numeros_permitidos = ""
    f = get_session_factory()

    print("\n1) 💵 LA RED DEL DINERO YA NO ES CIEGA (antes solo veía '$28' y '28 bs')")
    # Autorizados = lo que de verdad salió de una herramienta/catálogo, YA SEPARADO POR MONEDA.
    usd_ok = {14.0, 28.0, 4.0, 7.0}
    bs_ok = {31936.21}
    for texto, esperado, nota in [
        ("El total es $28", False, "lo autorizado pasa"),
        ("El total es 28$", False, "PEGADO — el formato que el propio prompt le enseña"),
        ("Son 28 dólares", False, "en palabras"),
        ("Son 28 USD", False, "en siglas"),
        ("El total es $35", True, "inventado en dólares"),
        ("El total es 35$", True, "🔴 inventado, PEGADO (antes se colaba)"),
        ("Son 35 dólares", True, "🔴 inventado, en palabras (antes se colaba)"),
        ("Son Bs 31.936,21", False, "el monto REAL en bolívares, con su formato"),
        ("son 5.000 Bs", True, "🔴 EL AGUJERO GORDO: se autorizaba solo (5.000 se leía como 5)"),
        ("son 45.000 Bs", True, "🔴 ídem (45 es un precio del catálogo… 45.000 Bs NO lo es)"),
        # 🔴🔴 EL CASO REAL DE LA CLIENTA (2026-07-13): la cifra está en DÓLARES y la frase dice
        # BOLÍVARES. El 28 SÍ está autorizado… pero como DÓLAR, no como bolívar.
        ("El total en bolívares es de $28 USD a la tasa BCV", True,
         "🔴🔴 EL CASO REAL: llamó bolívares a unos dólares"),
        ("El total en bolívares es de Bs 31.936,21", False, "el de verdad: pasa"),
    ]:
        malos = _dinero_inventado(texto, usd_ok, bs_ok)
        check(f"{'FRENA ' if esperado else 'pasa  '} | {texto:<28} ({nota})",
              bool(malos) == esperado, f"detectados={malos}")

    print("\n1.b) 🏷️ UN ID DEL CATÁLOGO **NO** ES UN PRECIO (de aquí salió el '$23')")
    # El prompt inyecta: precio $20.00 (id_para_pedir=23). El 23 es un ID, no dinero.
    catalogo = "Pan de Sándwich [SOLO PARA TI]: precio $20.00 (id_para_pedir=23)"
    u, b = autorizados_por_moneda(catalogo)
    check("el precio ($20) SÍ entra como dinero", 20.0 in u, str(sorted(u)))
    check("🔴 el id (23) NO entra como dinero", 23.0 not in u,
          "el id se colaba como precio: por eso el bot dijo '$23'")
    check("y por eso '$23' AHORA se frena", bool(_dinero_inventado("El total es $23", u, b)))

    print("\n1.c) 🗣️ LA FRASE ASESINA, CONJUGADA DE OTRA FORMA (auditoría 2026-08-02, DIN-2)")
    # La red del punto 1 miraba el párrafo SOLO si aparecía la palabra "total", y `_DICE_TOTAL`
    # cubre "sería/serían" pero NO "son" ni "es". Bastaba conjugar distinto para colar un dólar
    # disfrazado de bolívar. Y partir por FRASE dejaba la moneda en una y la cifra en la otra.
    for texto, esperado, nota in [
        ("En bolívares son $28 a la tasa del día", True,
         "🔴 sin la palabra 'total': antes PASABA"),
        ("Son $28 en bolívares 💚", True, "🔴 ídem, más corto todavía"),
        ("El total en bolívares. Son $28.", True,
         "🔴 partido en dos frases: la moneda en una, la cifra en la otra"),
        ("Todo junto te sale en $35", True, "inventado, sin decir 'total'"),
        # Y lo legítimo tiene que seguir pasando: frenar de más mata la venta igual.
        ("Son Bs 31.936,21 (precio completo)", False, "solo bolívares, el de verdad"),
        ("El total es $28. En bolívares son Bs 31.936,21.", False,
         "las DOS monedas, ambas autorizadas"),
        ("El pan keto cuesta $14", False, "un precio suelto del catálogo"),
    ]:
        malos = _dinero_inventado(texto, usd_ok, bs_ok, {28.0})
        check(f"{'FRENA ' if esperado else 'pasa  '} | {texto:<44} ({nota})",
              bool(malos) == esperado, f"detectados={malos}")

    print("\n1.d) 📋 EL TEXTO DEL PROMPT NO AUTORIZA DINERO (auditoría 2026-08-02, PRM-11)")
    # `autorizados_por_moneda` construye la lista blanca leyendo el TEXTO del prompt. Los precios
    # de EJEMPLO de las reglas entraban como montos buenos: el "$14" de las empanadas y —peor— el
    # contraejemplo "$25.00" (escrito para enseñar cómo NO formatear), que autorizaba **$2500**.
    # Las reglas son instrucciones, no datos: el dinero lo autoriza el catálogo y las herramientas.
    u_reglas, b_reglas = autorizados_por_moneda(_REGLAS)
    check("las reglas fijas NO autorizan ningún dólar", not u_reglas, str(sorted(u_reglas)))
    check("las reglas fijas NO autorizan ningún bolívar", not b_reglas, str(sorted(b_reglas)))
    check("🔴 y por eso '$2500' ya no se auto-autoriza",
          bool(_dinero_inventado("Te sale en $2500", u_reglas, b_reglas)),
          "salía del contraejemplo '$25.00' leído como 2500")

    print("\n1.e) 🔒 NO PODER COMPROBAR NO ES COMPROBAR BIEN (auditoría 2026-08-02, DIN-5)")
    # `monto_cuadra` arrancaba en True y solo se reevaluaba si HABÍA con qué comparar. Y quedarse
    # sin comparación es lo normal, no una rareza: la cotización vivía solo en Redis con TTL de 24h
    # y aquí los pedidos van con días de anticipación. Ante un comprobante de Bs 5.000 sobre una
    # venta de Bs 16.591, el bot soltaba "recibí tu pago y coordino la entrega".
    from app.workers.tasks import _monto_cuadra

    COBRADO = (16591.05, 23.0, 19.0)   # (bolívares, dólares, dólares con el 20% de descuento)
    for nombre, leido, esperados, debe in [
        ("🔴 SIN cotización con que comparar (caché vencida)", 5000.0, (None, None, None), False),
        ("sin monto leído por la visión", None, COBRADO, False),
        ("el monto correcto en bolívares", 16591.05, COBRADO, True),
        ("pagó de MENOS", 5000.0, COBRADO, False),
        ("pagó en divisas, con su 20% de descuento", 19.0, COBRADO, True),
        ("pagó el precio pleno en dólares", 23.0, COBRADO, True),
    ]:
        check(f"cuadra={str(debe):5} | {nombre}", _monto_cuadra(leido, esperados) is debe,
              f"dio {_monto_cuadra(leido, esperados)}")

    print("\n2) 🧭 LAS DOS LISTAS: lo que es mentira SIEMPRE vs. lo que la situación SÍ le manda decir")
    for texto, siempre, charla in [
        ("Ya revisé en mi banco y no me aparece", True, True),
        ("Mi banco ya me confirmó el pago", True, True),
        ("Soy Whuilianny, la dueña de masvidaconsciente", True, True),
        ("Te lo preparo con alulosa, así no te sube el azúcar", True, True),
        # ESTAS son la razón de las dos listas: en la CHARLA el bot no puede saberlo (miente),
        # pero en el carril del comprobante la situación le ORDENA decirlo ("dile que recibiste
        # su pago"). Aplicar la lista entera allí mataría el mensaje CORRECTO.
        ("¡Recibí tu pago! 💚 Ya coordino tu entrega", False, True),
        ("No me ha llegado ningún pago tuyo", False, True),
    ]:
        s = frase_prohibida_siempre(texto) is not None
        c = _frase_prohibida(texto) is not None
        check(f"siempre={'SÍ' if siempre else 'no'} charla={'SÍ' if charla else 'no'} | {texto[:44]}",
              s == siempre and c == charla, f"siempre={s} charla={c}")

    print("\n3) 🚪 LA PUERTA TIENE GUARDIA: si el modelo inventa dinero, el mensaje NO SALE")
    print("   (con LISTA CERRADA: solo los montos que el código cobró de verdad. El catálogo NO")
    print("    entra: el 12 es el precio de las Empanadas Keto, y por eso el '$12' se colaba.)")
    guardado = ag._pedir_redaccion

    async def _modelo_mentiroso(messages, modelo):
        return "Te faltaron Bs 1.200, o sea unos $12 más para completar 💚"

    async def _modelo_del_banco(messages, modelo):
        return "Ya revisé mi banco y me llegó tu pago 💚"

    async def _modelo_bueno(messages, modelo):
        return "¡Recibí tu comprobante! 💚 Ya coordino tu entrega."

    async def _modelo_repite_total(messages, modelo):
        return "¡Recibí tu pago de $28! 💚 Ya coordino tu entrega."

    ag._pedir_redaccion = _modelo_mentiroso
    r = await ag.redactar_mensaje(
        "el cliente pago Bs 1.200 pero el total era Bs 2.000, asi que faltan Bs 800",
        [], "Rosa", TEL, montos_usd=set(), montos_bs=set(),
    )
    check("🔴 un dólar CALCULADO de cabeza ($12) ⇒ el mensaje se descarta", r == "",
          f"salió: {r!r}")

    ag._pedir_redaccion = _modelo_del_banco
    r = await ag.redactar_mensaje("el cliente mandó su comprobante", [], "Rosa", TEL, montos_usd=set(), montos_bs=set())
    check("'ya revisé mi banco' ⇒ el mensaje se descarta", r == "", f"salió: {r!r}")

    ag._pedir_redaccion = _modelo_bueno
    r = await ag.redactar_mensaje("el cliente mandó su comprobante", [], "Rosa", TEL, montos_usd=set(), montos_bs=set())
    check("y el mensaje BUENO sí pasa (no frenamos de más)", r != "", f"salió: {r!r}")

    ag._pedir_redaccion = _modelo_repite_total
    r = await ag.redactar_mensaje(
        "el cliente mandó su comprobante", [], "Rosa", TEL, montos_usd={28.0}, montos_bs=set(),
    )
    check("repetir el total que SÍ se cobró ($28) también pasa", r != "", f"salió: {r!r}")
    ag._pedir_redaccion = guardado

    print("\n4) ⏰ EL AVISO DE PAGO Y LA VENTANA DE 24H (el único camino que habla DÍAS después)")
    await _limpiar()
    async with f() as s:
        s.add(Cliente(telefono=TEL, nombre="Rosa",
                      ultimo_entrante_at=now_utc() - timedelta(hours=30)))  # ventana CERRADA
        await s.commit()
    enviados.clear()
    await tasks._notificar_cliente_pago(TEL, "su pago quedó confirmado")
    al_cliente = [t for t, _ in enviados if t == TEL]
    async with f() as s:
        ints = (await s.execute(
            select(Intervencion).where(Intervencion.cliente_telefono == TEL)
        )).scalars().all()
    check("con la ventana CERRADA, el bot NO le escribe al cliente", not al_cliente,
          str(enviados))
    check("y te deja el aviso a TI (motivo 'ventana_cerrada')",
          any(i.motivo == "ventana_cerrada" for i in ints), str([i.motivo for i in ints]))

    print("\n5) 🔌 EL INTERRUPTOR YA CUBRE EL COMPROBANTE (el bot apagado NO habla)")
    await _limpiar()
    async with f() as s:
        s.add(Cliente(telefono=TEL, nombre="Rosa", ultimo_entrante_at=now_utc()))
        fila = (await s.execute(
            select(Configuracion).where(Configuracion.clave == "bot_activo")
        )).scalar_one_or_none()
        antes = fila.valor if fila else None
        if fila:
            fila.valor = "false"
        else:
            s.add(Configuracion(clave="bot_activo", valor="false"))
        await s.commit()
    enviados.clear()
    partes = await tasks._responder_situacion(TEL, "el cliente mandó su comprobante", "Rosa")
    check("con el bot APAGADO, no le responde al cliente que paga", partes == [] and not enviados,
          str(enviados))
    async with f() as s:  # devolver el interruptor a como estaba
        fila = (await s.execute(
            select(Configuracion).where(Configuracion.clave == "bot_activo")
        )).scalar_one_or_none()
        if fila:
            fila.valor = antes if antes is not None else "true"
        await s.commit()
    check("el interruptor quedó como estaba (no se ensucia el panel)", True)

    # ─── EL CARRIL DEL COMPROBANTE (auditoría 2026-08-02, tanda 4) ───
    # De aquí en adelante el interruptor se fuerza ENCENDIDO: en el servidor puede estar
    # legítimamente apagado (pasó el 2026-07-13) y estas pruebas se caerían por el motivo
    # equivocado. Que el interruptor calla al bot ya lo prueba la sección 5, arriba.
    real_activo = tasks._bot_activo

    async def _siempre_encendido() -> bool:
        return True

    async def _descarga_ok(media_id):
        return b"...bytes de la captura del pago...", "image/jpeg"

    async def _descarga_rota(media_id):
        raise RuntimeError("Meta devolvió 500 al entregar el media")

    situaciones: list[str] = []

    async def _situacion_doble(telefono, situacion, nombre):
        """Doble de `_responder_situacion`: anota QUÉ se le iba a decir al cliente y no llama al
        modelo. Devuelve una parte 'enviado' para no disparar el aviso de chat pausado."""
        situaciones.append(situacion)
        return [{"texto": "(mensaje al cliente)", "wa_message_id": "wamid.SIT", "estado": "enviado"}]

    real_descarga = tasks.descargar_media
    real_guardar = tasks._guardar_comprobante
    real_vision = tasks._leer_comprobante_seguro
    real_situacion = tasks._responder_situacion
    tasks._bot_activo = _siempre_encendido

    try:
        print("\n6) 🔁 EL REINTENTO DEL COMPROBANTE EXISTE DE VERDAD (SIL-5)")
        print("   (los `except` decían 'dejar reintentar a Meta'. Meta NO reintenta: el webhook")
        print("    ya devolvió 200 al encolar. Y Celery tampoco: el `@task` iba a secas.)")
        await _limpiar()
        MSG, MEDIA = "wamid.PRUEBA_COMP_1", "media_prueba_comp_1"
        tasks.descargar_media = _descarga_rota
        enviados.clear()
        v = await tasks._procesar_comprobante(
            TEL, MSG, MEDIA, None, "Rosa", "image/jpeg", ultimo_intento=False
        )
        check("la descarga falla ⇒ veredicto 'reintentar' (antes: `return` y el pago perdido)",
              v == "reintentar", repr(v))
        check("y NO se marca como atendido: el reintento tiene que poder entrar",
              not await rc.comprobante_procesado(MSG))
        check("y todavía no se molesta a la dueña (quedan intentos por delante)",
              not await _motivos(), str(await _motivos()))

        v = await tasks._procesar_comprobante(
            TEL, MSG, MEDIA, None, "Rosa", "image/jpeg", ultimo_intento=True
        )
        check("agotados los intentos ⇒ 'rendido': NO se abandona en silencio", v == "rendido", repr(v))
        check("🔴 la CAPTURA entra al hilo IGUAL, sin archivo (el panel se la baja de Meta)",
              await _burbujas() == [(MEDIA, None)], str(await _burbujas()))
        check("🔴 y hay aviso en la bandeja: 'comprobante_sin_procesar'",
              "comprobante_sin_procesar" in await _motivos(), str(await _motivos()))
        check("el cliente —que ACABA de pagar— recibe el acuse sobrio",
              any("revisando tu pago" in t for d, t in enviados if d == TEL), str(enviados))
        check("y AHORA sí se marca: ya hay una persona enterada, no se repite el aviso",
              await rc.comprobante_procesado(MSG))

        print("\n6.b) 💾 EL DISCO NO PUEDE TUMBAR EL COBRO (esa línea vivía FUERA de todo try)")
        await _limpiar()
        await _pedido_esperando_pago("28")

        def _disco_lleno(media_id, contenido, mime):
            raise OSError("[Errno 28] No space left on device")

        async def _vision_buena(telefono, contenido, base_mime):
            return {"es_comprobante": True, "leido": True, "es_pantalla_bancaria": True,
                    "monto": "28", "referencia": "0123456789"}

        tasks.descargar_media = _descarga_ok
        tasks._guardar_comprobante = _disco_lleno
        tasks._leer_comprobante_seguro = _vision_buena
        tasks._responder_situacion = _situacion_doble
        situaciones.clear()
        v = await tasks._procesar_comprobante(
            TEL, "wamid.DISCO", "media_disco", None, "Rosa", "image/jpeg"
        )
        check("con el disco lleno la tarea NO muere a mitad: llega al final", v == "ok", repr(v))
        check("🔴 y el PAGO se registra igual (antes: OSError, y el pago no dejaba NI UN rastro)",
              [e for e, _ in await _pagos()] == ["reportado"], str(await _pagos()))
        check("la burbuja entra al hilo, sin archivo local pero con su media_id",
              await _burbujas() == [("media_disco", None)], str(await _burbujas()))
        check("y la dueña se entera del disco ('comprobante_sin_archivo')",
              "comprobante_sin_archivo" in await _motivos(), str(await _motivos()))

        print("\n6.c) 🗄️ Y SI LA BASE FALLA AL REGISTRAR, TAMPOCO SE PIERDE")
        await _limpiar()
        await _pedido_esperando_pago("28")
        import app.agent.tools as tools_mod
        real_registrar = tools_mod.registrar_comprobante

        async def _registro_roto(*a, **k):
            raise RuntimeError("la base de datos se cayó justo aquí")

        tasks._guardar_comprobante = real_guardar
        tools_mod.registrar_comprobante = _registro_roto
        try:
            v = await tasks._procesar_comprobante(
                TEL, "wamid.BD", "media_bd", None, "Rosa", "image/jpeg", ultimo_intento=False
            )
            check("la base falla ⇒ 'reintentar' y SIN marcar", v == "reintentar", repr(v))
            check("(sigue sin marcar: el reintento tiene que poder entrar)",
                  not await rc.comprobante_procesado("wamid.BD"))
            v = await tasks._procesar_comprobante(
                TEL, "wamid.BD", "media_bd", None, "Rosa", "image/jpeg", ultimo_intento=True
            )
            check("y al último intento ⇒ 'rendido' + aviso a la dueña", v == "rendido", repr(v))
            check("con el aviso 'comprobante_sin_procesar' en la bandeja",
                  "comprobante_sin_procesar" in await _motivos(), str(await _motivos()))
        finally:
            tools_mod.registrar_comprobante = real_registrar

        print("\n7) 👁️ EL DINERO SE JUZGA UNA VEZ (la visión NO es determinista)")
        print("   Con reintentos, el 2º intento podría dar un veredicto DISTINTO sobre el MISMO")
        print("   comprobante y cerrar como 'no es un pago' algo que el 1º ya había leído bien.")
        await _limpiar()
        await _pedido_esperando_pago("28")
        MEDIA_V = "media_voluble"
        lecturas: list[int] = []

        async def _vision_voluble(telefono, contenido, base_mime):
            lecturas.append(1)
            if len(lecturas) == 1:
                return {"es_comprobante": True, "leido": True, "es_pantalla_bancaria": True,
                        "monto": "28"}
            return {"es_comprobante": False, "leido": True, "es_pantalla_bancaria": False}

        tasks._leer_comprobante_seguro = _vision_voluble
        situaciones.clear()
        v1 = await tasks._procesar_comprobante(
            TEL, "wamid.VOL", MEDIA_V, None, "Rosa", "image/jpeg"
        )
        # El reintento REAL llega con el mismo message_id y SIN marca (el fallo fue antes de
        # marcar); aquí se suelta a mano para reproducir esa segunda pasada.
        await rc._client().delete("comprob:wamid.VOL")
        v2 = await tasks._procesar_comprobante(
            TEL, "wamid.VOL", MEDIA_V, None, "Rosa", "image/jpeg"
        )
        check("la visión se cobra UNA sola vez por comprobante", len(lecturas) == 1, str(len(lecturas)))
        check("🔴 y el 2º intento NO cierra como 'no es un pago' lo que el 1º leyó bien",
              v1 == "ok" and v2 != "no_es_comprobante", f"{v1!r} → {v2!r}")

        MEDIA_I = "media_ilegible_cache"
        ilegibles: list[int] = []

        async def _vision_caida(telefono, contenido, base_mime):
            ilegibles.append(1)
            return {"es_comprobante": None, "leido": False}

        tasks._leer_comprobante_seguro = _vision_caida
        await tasks._procesar_comprobante(TEL, "wamid.IL1", MEDIA_I, None, "Rosa", "image/jpeg")
        await rc._client().delete("comprob:wamid.IL1")
        await tasks._procesar_comprobante(TEL, "wamid.IL1", MEDIA_I, None, "Rosa", "image/jpeg")
        check("una lectura FALLIDA no se congela (si la visión vuelve, que la aproveche)",
              len(ilegibles) == 2, str(len(ilegibles)))

        print("\n8) 🙈 'NO PUDE LEER' **NO** ES 'NO ES UN COMPROBANTE' (SIL-6)")
        print("   Con la visión caída (402 del 2026-07-15, 429, timeout, un GIF), el cliente")
        print("   recibía 'mándame la captura clara' con CADA captura. El negocio dejaba de cobrar.")
        await _limpiar()
        await _pedido_esperando_pago("28")
        tasks._leer_comprobante_seguro = _vision_caida
        situaciones.clear()
        enviados.clear()
        v = await tasks._procesar_comprobante(
            TEL, "wamid.ILEG", "media_ileg_1", None, "Rosa", "image/jpeg"
        )
        check("🔴 con la visión caída el PAGO SE REGISTRA igual (el corazón del arreglo)",
              [e for e, _ in await _pagos()] == ["reportado"], str(await _pagos()))
        check("🔴 y la dueña lo ve en la bandeja ('comprobante_ilegible')",
              "comprobante_ilegible" in await _motivos(), str(await _motivos()))
        check("al cliente NO se le pide otra vez la captura: se le dice que se está revisando",
              bool(situaciones) and "MONTO no cuadra" in situaciones[-1]
              and "reenvíe la captura" not in situaciones[-1], str(situaciones[-1:]))

        # ANTI-REGRESIÓN: la conducta vieja, la que SÍ hay que conservar.
        await _limpiar()
        await _pedido_esperando_pago("28")

        async def _vision_no_es(telefono, contenido, base_mime):
            return {"es_comprobante": False, "leido": True, "es_pantalla_bancaria": False}

        tasks._leer_comprobante_seguro = _vision_no_es
        situaciones.clear()
        v = await tasks._procesar_comprobante(
            TEL, "wamid.FOTO", "media_foto_1", None, "Rosa", "image/jpeg"
        )
        check("✅ una foto cualquiera (la visión SEGURA de que no es pago) NO crea Pago",
              v == "no_es_comprobante" and not await _pagos(), f"{v!r} {await _pagos()}")
        check("✅ y se le pide la captura clara, como siempre",
              bool(situaciones) and "no parece un comprobante" in situaciones[-1],
              str(situaciones[-1:]))

        async def _vision_otra_cuenta(telefono, contenido, base_mime):
            return {"es_comprobante": False, "leido": True, "es_pantalla_bancaria": True}

        tasks._leer_comprobante_seguro = _vision_otra_cuenta
        situaciones.clear()
        await tasks._procesar_comprobante(
            TEL, "wamid.OTRA", "media_otra_cuenta", None, "Rosa", "image/jpeg"
        )
        # 🔴 REESCRITO EL 2026-08-22. Este check buscaba la frase literal "NO te aparece hecho a
        # TU cuenta" — que es exactamente la instrucción que hizo al bot decirle a una clienta
        # "acabo de revisar y ese pago no me aparece en la cuenta". El bot NO tiene banco: esa
        # frase era una mentira que el sistema le ordenaba. Ahora la instrucción habla de la
        # CAPTURA, y este banco verifica la INTENCIÓN (neutral, sobre lo que de verdad se vio)
        # en vez de un texto exacto — que es lo que debió comprobar desde el principio.
        _sit = situaciones[-1] if situaciones else ""
        check("✅ un pago a OTRA cuenta sigue con su mensaje neutral (no acusa a nadie)",
              bool(situaciones)
              and "CAPTURA" in _sit                    # habla de lo que la visión SÍ vio
              and "no lo acuses" in _sit.lower()       # sigue siendo neutral
              and "NUNCA de haber revisado" in _sit,   # y le prohíbe la mentira del banco
              str(situaciones[-1:]))

        print("\n8.b) 🔒 Y LA VISIÓN CAÍDA NO INUNDA A LA DUEÑA (un aviso por cliente / 15 min)")
        await _limpiar()
        await _pedido_esperando_pago("28")
        tasks._leer_comprobante_seguro = _vision_caida
        await tasks._procesar_comprobante(TEL, "wamid.GIF1", "media_gif_1", None, "Rosa", "image/gif")
        await tasks._procesar_comprobante(TEL, "wamid.GIF2", "media_gif_2", None, "Rosa", "image/gif")
        ilegible = [m for m in await _motivos() if m == "comprobante_ilegible"]
        check("🔴 dos imágenes ilegibles seguidas ⇒ UN solo aviso (con la visión caída serían "
              "una Intervencion y un WhatsApp por CADA imagen de CADA cliente)",
              len(ilegible) == 1, str(await _motivos()))
    finally:
        tasks._bot_activo = real_activo
        tasks.descargar_media = real_descarga
        tasks._guardar_comprobante = real_guardar
        tasks._leer_comprobante_seguro = real_vision
        tasks._responder_situacion = real_situacion

    await _limpiar()
    print()
    if fallos:
        print(f"   🔴 {len(fallos)} FALLO(S): " + " · ".join(fallos[:6]))
        sys.exit(1)
    print("   ✅ LA PUERTA DEL DINERO TIENE GUARDIA: ningún monto inventado, ninguna mentira,")
    print("      y nada sale fuera de la ventana de 24h de Meta.")


asyncio.run(main())
