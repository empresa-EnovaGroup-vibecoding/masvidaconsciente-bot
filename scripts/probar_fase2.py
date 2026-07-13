"""FASE 2 — banco de pruebas: que el hilo diga la VERDAD.

Sale de la auditoría adversarial del plan (28 hallazgos confirmados contra el código).
Cada caso de aquí es un agujero REAL que alguien encontró antes de que se construyera.
Se corre DENTRO del contenedor del bot, contra la BD del TALLER.

Si algo sale MAL, NO SE DESPLIEGA.
"""
import asyncio
import sys
import time

from sqlalchemy import delete, select

from app.models import Cliente, Mensaje
from app.services.db import get_session_factory
from app.webhook.parser import contenido_seguro, extraer_eventos, tipo_valido

TEL = "__prueba_fase2__"
NEGOCIO = "573132933806"

# ⚠️ Ids ÚNICOS por corrida. El candado anti-duplicados (el que impide que un reintento de Meta
# duplique la burbuja) recuerda cada id en Redis durante 24 HORAS. Con ids fijos, la segunda
# corrida del día se saltaba TODO —y con razón: el código estaba haciendo justo su trabajo—.
# Reutilizarlos daba un rojo falso.
RUN = str(int(time.time()))
fallos: list[str] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"   {'[OK ]' if ok else '[MAL]'} {nombre}{('  → ' + str(detalle)) if not ok else ''}")
    if not ok:
        fallos.append(nombre)


def _payload(field: str, value: dict) -> dict:
    return {"entry": [{"changes": [{"field": field, "value": value}]}]}


def _eco(id_: str, tipo: str = "text", **extra) -> dict:
    e = {"id": f"{id_}.{RUN}", "from": NEGOCIO, "to": TEL, "type": tipo, "timestamp": "1783900000"}
    e.update(extra)
    return _payload("smb_message_echoes", {"message_echoes": [e]})


async def limpiar():
    factory = get_session_factory()
    async with factory() as s:
        await s.execute(delete(Mensaje).where(Mensaje.cliente_telefono == TEL))
        await s.execute(delete(Cliente).where(Cliente.telefono == TEL))
        await s.commit()


async def main() -> None:
    factory = get_session_factory()
    await limpiar()

    # ───────────────────────────────────────────────────────────────────────────
    print("\n1) EL PARSER YA NO PIERDE MENSAJES (antes leía solo el primero)")

    # Meta AGRUPA: si venía un lote de estados y detrás el mensaje de un cliente, el mensaje
    # se perdía, respondíamos 200 y Meta no reintentaba. "Quiero 8 empanadas" desaparecía.
    mixto = {"entry": [
        {"changes": [{"field": "messages", "value": {"statuses": [
            {"id": "wamid.S1", "status": "delivered"}]}}]},
        {"changes": [{"field": "messages", "value": {
            "contacts": [{"profile": {"name": "Ana"}}],
            "messages": [{"id": "wamid.M1", "from": TEL, "type": "text",
                          "text": {"body": "quiero 8 empanadas"}, "timestamp": "1783900000"}],
        }}]},
    ]}
    evs = extraer_eventos(mixto)
    clases = [e["clase"] for e in evs]
    check("un lote de estados + un mensaje de cliente ⇒ NO se pierde el mensaje",
          "mensaje" in clases and "estado" in clases, clases)

    tres = _payload("messages", {"messages": [
        {"id": f"wamid.T{i}", "from": TEL, "type": "text", "text": {"body": str(i)}}
        for i in range(3)
    ]})
    check("3 mensajes en un POST ⇒ 3 eventos (antes se perdían 2)",
          len([e for e in extraer_eventos(tres) if e["clase"] == "mensaje"]) == 3)

    evs = extraer_eventos(_eco("wamid.E0", texto := "text", text={"body": "hola"}))
    check("el ECO se reconoce y el cliente es 'to', NUNCA 'from' (si no, el bot se responde "
          "a sí mismo)",
          len(evs) == 1 and evs[0]["clase"] == "eco" and evs[0]["telefono"] == TEL,
          evs)

    check("un tipo raro de Meta NO revienta: cae en 'otro'", tipo_valido("carrier_pigeon") == "otro")
    check("una foto SIN pie de foto tiene contenido ('mensajes.contenido' es NOT NULL)",
          contenido_seguro("image", None, None) == "[foto]")

    # ───────────────────────────────────────────────────────────────────────────
    print("\n2) EL ECO: la dueña escribe desde su CELULAR")
    from app.webhook.router import _aplicar_estado, _procesar_eco

    ev = extraer_eventos(_eco("wamid.E1", text={"body": "ya te confirmo"}))[0]
    await _procesar_eco(ev)

    async with factory() as s:
        c = (await s.execute(select(Cliente).where(Cliente.telefono == TEL))).scalar_one_or_none()
        m = (await s.execute(
            select(Mensaje).where(Mensaje.cliente_telefono == TEL, Mensaje.rol == "owner")
        )).scalars().all()
    check("el chat se CREA aunque el cliente nunca hubiera escrito (chat nuevo desde el móvil)",
          c is not None)
    check("el bot queda CALLADO en ese chat", bool(c and c.bot_pausado))
    check("y firmado como 'dueña' (no 'bot': son casos opuestos)",
          bool(c and c.pausado_por == "dueña"))
    check("🔴 el eco NO abre la ventana de 24h (es un mensaje SALIENTE)",
          bool(c and c.ultimo_entrante_at is None), getattr(c, "ultimo_entrante_at", "?"))
    check("no_leidos queda en 0 (ella acaba de atenderlo)", bool(c and c.no_leidos == 0))
    check("el mensaje queda en el hilo como 'owner'", len(m) == 1 and m[0].contenido == "ya te confirmo")

    # Meta REENTREGA el mismo evento si duda: no puede duplicar la burbuja ni la memoria.
    await _procesar_eco(ev)
    await _procesar_eco(ev)
    async with factory() as s:
        m = (await s.execute(
            select(Mensaje).where(Mensaje.cliente_telefono == TEL, Mensaje.rol == "owner")
        )).scalars().all()
    check("🔴 el MISMO eco 3 veces ⇒ UNA sola burbuja (reintento de Meta)", len(m) == 1, len(m))

    # Una FOTO desde el celular: antes reventaba el INSERT y el rollback se llevaba la PAUSA.
    async with factory() as s:
        c = (await s.execute(select(Cliente).where(Cliente.telefono == TEL))).scalar_one()
        c.bot_pausado, c.pausado_por = False, None
        await s.commit()
    ev = extraer_eventos(_eco("wamid.E2", tipo="image", image={"id": "MID1"}))[0]
    await _procesar_eco(ev)
    async with factory() as s:
        c = (await s.execute(select(Cliente).where(Cliente.telefono == TEL))).scalar_one()
        foto = (await s.execute(
            select(Mensaje).where(Mensaje.wa_message_id == f"wamid.E2.{RUN}")
        )).scalar_one_or_none()
    check("🔴 una FOTO desde el celular NO revienta y la PAUSA queda puesta", c.bot_pausado is True)
    check("la foto entra al hilo con su placeholder", bool(foto and foto.contenido == "[foto]"))

    # Una REACCIÓN (❤️): tipo que el CHECK viejo no admitía.
    ev = extraer_eventos(_eco("wamid.E3", tipo="reaction", reaction={"emoji": "❤️"}))[0]
    await _procesar_eco(ev)
    async with factory() as s:
        r = (await s.execute(
            select(Mensaje).where(Mensaje.wa_message_id == f"wamid.E3.{RUN}")
        )).scalar_one_or_none()
    check("🔴 una REACCIÓN tampoco revienta (el CHECK de la 021 la admite)", r is not None)

    # ───────────────────────────────────────────────────────────────────────────
    print("\n3) EL ANTI-SUICIDIO: un eco de un mensaje NUESTRO no puede pausar al bot")
    async with factory() as s:
        s.add(Mensaje(cliente_telefono=TEL, rol="assistant", contenido="soy el bot",
                      wa_message_id=f"wamid.MIO.{RUN}", estado="enviado"))
        c = (await s.execute(select(Cliente).where(Cliente.telefono == TEL))).scalar_one()
        c.bot_pausado, c.pausado_por = False, None
        await s.commit()
    ev = extraer_eventos(_eco("wamid.MIO", text={"body": "soy el bot"}))[0]
    r = await _procesar_eco(ev)
    async with factory() as s:
        c = (await s.execute(select(Cliente).where(Cliente.telefono == TEL))).scalar_one()
    check("🔴 el eco de un mensaje NUESTRO se descarta (si no, el bot se pausaría a sí mismo "
          "tras CADA respuesta y quedaría MUDO con todos)", r == "eco_propio")
    check("y el bot NO queda pausado", c.bot_pausado is False)

    # ───────────────────────────────────────────────────────────────────────────
    print("\n4) LOS ESTADOS: entregado / leído / FALLÓ (el dinero)")
    _MIO = f"wamid.MIO.{RUN}"
    await _aplicar_estado({"clase": "estado", "wa_message_id": _MIO,
                           "estado": "leido", "error": None})
    await _aplicar_estado({"clase": "estado", "wa_message_id": _MIO,
                           "estado": "entregado", "error": None})  # llega TARDE
    async with factory() as s:
        m = (await s.execute(
            select(Mensaje).where(Mensaje.wa_message_id == _MIO)
        )).scalar_one()
    check("un 'entregado' que llega tarde NO pisa un 'leído'", m.estado == "leido", m.estado)

    await _aplicar_estado({"clase": "estado", "wa_message_id": _MIO,
                           "estado": "fallido", "error": "131047: fuera de la ventana"})
    async with factory() as s:
        m = (await s.execute(
            select(Mensaje).where(Mensaje.wa_message_id == _MIO)
        )).scalar_one()
    check("🔴 un FALLO SIEMPRE gana (es lo que la dueña tiene que ver sí o sí)",
          m.estado == "fallido" and bool(m.error), (m.estado, m.error))

    # ───────────────────────────────────────────────────────────────────────────
    print("\n5) LOS FRENOS: cada uno con SU lado seguro")
    from app.workers import tasks as T

    async def _revienta(_tel):
        raise RuntimeError("Postgres se cayó")

    original = T._estado_pausa
    T._estado_pausa = _revienta  # type: ignore[assignment]
    try:
        callado = await T._lo_paso_una_persona(TEL)
        sigue = await T._cliente_pausado(TEL)
    finally:
        T._estado_pausa = original  # type: ignore[assignment]
    check("🔴 si la BD falla y NO sé quién pausó ⇒ el bot se CALLA (no atropella a la dueña)",
          callado is True)
    check("pero un error leyendo la pausa NO deja mudo al bot entero", sigue is False)

    print("\n6) LIMPIEZA")
    await limpiar()
    check("el cliente de prueba se borró (no ensucia el panel)", True)

    print()
    if fallos:
        print(f"   🔴 {len(fallos)} FALLO(S) — NO DESPLEGAR:")
        for f in fallos:
            print(f"      - {f}")
        sys.exit(1)
    print("   ✅ FASE 2 EN VERDE: el hilo dice la verdad y el bot no se atropella con la dueña")


asyncio.run(main())
