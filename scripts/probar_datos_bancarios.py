"""EL CANDADO DE LOS DATOS BANCARIOS — los datos de pago SOLO salen de una herramienta.

🔴 Caso REAL (2026-07-13): el bot le pegó a una clienta los DATOS BANCARIOS COMPLETOS
(cédula, cuenta, Zelle, Binance) SIN que hubiera pedido, porque vivían escritos en el TEXTO
de la personalidad y el modelo los copiaba de ahí cuando le parecía. La regla "envía SOLO
los del método que el cliente elija" vivía en el prompt: humo.

Lo que se prueba:
  1. 🕵️ El detector: caza cédulas, teléfonos, cuentas (aunque vengan partidas con espacios
     o guiones) y correos. Y NO confunde: el dinero ("Bs 18.033,64"), las fechas ISO
     (2026-07-18) y los números CORTOS (ids, horas, cantidades) no son datos sensibles.
  2. 🔑 La autorización: lo que devuelve una herramienta en ESE turno y lo que escribió el
     propio cliente (su referencia) SÍ se puede decir; citar un PEDAZO ("termina en 7595")
     también. Todo lo demás se frena — aunque esté escrito en la personalidad.
  3. 🚪 La puerta en `responder`: si el modelo suelta datos bancarios SIN haber llamado a
     `generar_datos_pago`, se le corrige UNA vez; si insiste, el mensaje NO SALE y se
     escala a la dueña. Con la herramienta llamada, los datos REALES sí salen.
  4. 💰 La puerta en el carril del dinero (`redactar_mensaje`): ahí NUNCA hay datos
     bancarios legítimos que dar — se frenan siempre.
  5. 🏦 `generar_datos_pago` en DOS PASOS (rama B, 31-ago — el bug que Maired confirmó en
     vivo: "vuelve a preguntar los métodos de pago cuando ya mandó los datos"): sin `metodo`
     entrega SOLO los NOMBRES (nada de datos); con `metodo` entrega SOLO los datos de ESE
     (de la tabla `metodos_pago`, la MISMA que valida los comprobantes) y la elección queda
     GUARDADA en el pedido — la re-llamada sin `metodo` responde por el elegido en vez de
     reabrir, un método inventado se rechaza con la lista real, y la cotización se sigue
     guardando COMPLETA (el validador del comprobante valida por MONTO y no se tocó).

No se manda un solo WhatsApp: el modelo se sustituye por un doble.
"""
import asyncio
import json
import sys

from sqlalchemy import delete, select

from app.agent import agent as ag
from app.agent.agent import (
    RESPUESTA_SEGURA,
    _datos_sensibles,
    _datos_sensibles_inventados,
)
from app.agent.tools import _grupo_bolivares, _tipo_canonico, generar_datos_pago
from app.models import Cliente, MetodoPago, Pedido, ZonaEntrega, hoy_venezuela
from app.services.db import get_session_factory

TEL = "__prueba_datos_bancarios__"
ZONA_PRUEBA = "__zona_prueba_datos__"
fallos: list[str] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"   {'[OK ]' if ok else '[MAL]'} {nombre}{('  → ' + detalle) if detalle and not ok else ''}")
    if not ok:
        fallos.append(nombre)


async def _limpiar() -> None:
    f = get_session_factory()
    async with f() as s:
        await s.execute(delete(Pedido).where(Pedido.cliente_telefono == TEL))
        await s.execute(delete(ZonaEntrega).where(ZonaEntrega.nombre == ZONA_PRUEBA))
        await s.execute(delete(Cliente).where(Cliente.telefono == TEL))
        await s.commit()


async def main() -> None:
    print("\n1) 🕵️ EL DETECTOR: qué es un dato sensible y qué no")
    for texto, esperado, nota in [
        ("Mi cédula es 21367558", True, "una cédula (8 dígitos)"),
        ("Teléfono 0424-7047595", True, "un teléfono partido con guion"),
        ("Cuenta 0134 0188 8518 8102 8171", True, "una cuenta partida con espacios"),
        ("Escríbeme a familiapenazabala@gmail.com", True, "un correo (Zelle)"),
        ("El ID es 326103739", True, "una wallet/ID Binance"),
        ("El total es Bs 18.033,64", False, "DINERO con separador de miles: NO es una cuenta"),
        ("Te lo tengo para el 2026-07-18", False, "una fecha ISO: NO es una cédula"),
        ("Son 3 paquetes, pedido #285, a las 14:35", False, "números cortos: ids, horas"),
        ("El paquete trae 8 unidades por $14", False, "precios y cantidades del catálogo"),
    ]:
        detectado = bool(_datos_sensibles_inventados(texto, set()))
        check(f"{'CAZA ' if esperado else 'deja '} | {texto[:44]} ({nota})",
              detectado == esperado, f"detectó={_datos_sensibles(texto)}")

    print("\n2) 🔑 LA AUTORIZACIÓN: la herramienta y el cliente SÍ; la personalidad NO")
    # Lo que devolvería generar_datos_pago (un JSON de herramienta) autoriza SUS datos.
    tool_json = json.dumps({
        "metodos_de_pago": [
            {"metodo": "Pago Móvil", "telefono": "04247047595", "cedula": "V-21367558"},
            {"metodo": "Zelle", "correo": "familiapenazabala@gmail.com"},
        ]
    })
    autorizados = _datos_sensibles(tool_json)
    for texto, esperado, nota in [
        ("Pago Móvil: 0424-7047595, cédula 21367558", False, "los datos de la herramienta pasan"),
        ("El Zelle es familiapenazabala@gmail.com", False, "el correo de la herramienta pasa"),
        ("termina en 047595", False, "citar un PEDAZO de un dato autorizado vale"),
        ("Mi Pago Móvil es 04141234567", True, "🔴 un número que la herramienta NO dio"),
        ("Escríbeme a otra@cuenta.com", True, "🔴 un correo que la herramienta NO dio"),
    ]:
        malos = _datos_sensibles_inventados(texto, autorizados)
        check(f"{'FRENA' if esperado else 'pasa '} | {texto[:44]} ({nota})",
              bool(malos) == esperado, f"detectados={malos}")

    # La referencia que escribió el PROPIO cliente se puede repetir.
    del_cliente = _datos_sensibles("mi referencia es 004512398765")
    malos = _datos_sensibles_inventados("¡Listo! Anoté tu referencia 004512398765 💚", del_cliente)
    check("la referencia que dio el CLIENTE se puede repetir", not malos, f"detectados={malos}")

    # Un monto grande en Bs escrito sin separador NO se confunde con una cuenta
    # (de si es el monto correcto ya se encarga la red del dinero).
    malos = _datos_sensibles_inventados("son 1234567 Bs", set(), set(), {1234567.0})
    check("un MONTO autorizado escrito sin puntos no se confunde con cuenta", not malos,
          f"detectados={malos}")

    print("\n3) 🚪 LA PUERTA EN `responder`: sin herramienta NO hay datos bancarios")
    llamadas_ayuda: list[dict] = []

    async def _ejecutar_falso(nombre, args, telefono):
        if nombre == "pedir_ayuda":
            llamadas_ayuda.append(args)
            return {"ok": True, "nota": "listo"}
        if nombre == "generar_datos_pago":
            return {
                "ok": True, "pedido_id": 1, "monto_usd": 20.0, "monto_bs": 14427.0,
                "resumen_cobro": "Por Pago Móvil son 14.427,00 Bs (precio completo).",
                "metodos_de_pago": [
                    {"metodo": "Pago Móvil", "telefono": "04247047595", "cedula": "V-21367558"},
                ],
                "nota": "da SOLO los datos del método que el cliente elija",
            }
        return {"ok": True}

    # a) El modelo suelta los datos SIN llamar a la herramienta — e INSISTE.
    async def _modelo_pega_datos(messages, tools, model):
        return {"choices": [{"message": {
            "content": "Claro 💚 Pago Móvil: Banesco, cédula 21367558, teléfono 04247047595"
        }}]}

    llamadas_ayuda.clear()
    r = await ag.responder(
        TEL, "dame tus datos de pago", [{"role": "user", "content": "hola"},
                                        {"role": "assistant", "content": "¡Hola! 💚"}],
        "Rosa", llm=_modelo_pega_datos, ejecutar=_ejecutar_falso,
    )
    check("🔴 datos pegados de la personalidad (sin tool) ⇒ NO salen", "21367558" not in r
          and "04247047595" not in r, f"salió: {r!r}")
    check("   ...el cliente recibe el acuse seguro", r == RESPUESTA_SEGURA, f"salió: {r!r}")
    check("   ...y la dueña recibe el aviso (pedir_ayuda)", len(llamadas_ayuda) == 1,
          f"avisos={llamadas_ayuda}")

    # b) El modelo hace las cosas BIEN: llama a la herramienta y copia lo que devolvió.
    turnos = {"n": 0}

    async def _modelo_bueno(messages, tools, model):
        turnos["n"] += 1
        if turnos["n"] == 1:
            return {"choices": [{"message": {
                "content": None,
                "tool_calls": [{"id": "t1", "function": {
                    "name": "generar_datos_pago", "arguments": "{}"}}],
            }}]}
        return {"choices": [{"message": {
            "content": "Por Pago Móvil son 14.427,00 Bs (precio completo).\n\n"
                       "Pago Móvil: teléfono 04247047595, cédula 21367558 💚"
        }}]}

    llamadas_ayuda.clear()
    r = await ag.responder(
        TEL, "listo, ¿a dónde te pago?", [{"role": "user", "content": "hola"},
                                          {"role": "assistant", "content": "¡Hola! 💚"}],
        "Rosa", llm=_modelo_bueno, ejecutar=_ejecutar_falso,
    )
    check("con la herramienta llamada, los datos REALES sí salen", "04247047595" in r, f"salió: {r!r}")
    check("   ...sin avisos de más (no frenamos el camino bueno)", not llamadas_ayuda,
          f"avisos={llamadas_ayuda}")

    # c) Llamó a la herramienta… pero escribió OTRO número (uno que la tool no dio).
    turnos["n"] = 0

    async def _modelo_cambia_numero(messages, tools, model):
        turnos["n"] += 1
        if turnos["n"] == 1:
            return {"choices": [{"message": {
                "content": None,
                "tool_calls": [{"id": "t1", "function": {
                    "name": "generar_datos_pago", "arguments": "{}"}}],
            }}]}
        return {"choices": [{"message": {
            "content": "Pago Móvil: teléfono 04149999999, cédula 21367558 💚"
        }}]}

    llamadas_ayuda.clear()
    r = await ag.responder(
        TEL, "¿a dónde te pago?", [{"role": "user", "content": "hola"},
                                   {"role": "assistant", "content": "¡Hola! 💚"}],
        "Rosa", llm=_modelo_cambia_numero, ejecutar=_ejecutar_falso,
    )
    check("🔴 un TELÉFONO CAMBIADO (la tool dio otro) ⇒ NO sale", "04149999999" not in r,
          f"salió: {r!r}")

    print("\n4) 💰 LA PUERTA EN EL CARRIL DEL DINERO: ahí JAMÁS se dan datos bancarios")
    guardado = ag._pedir_redaccion

    async def _redactor_pega_datos(messages, modelo):
        return "¡Recibí tu pago! 💚 Cualquier cosa, mi cuenta es 01340188851881028171"

    ag._pedir_redaccion = _redactor_pega_datos
    r = await ag.redactar_mensaje(
        "el cliente mandó su comprobante", [], "Rosa", None, montos_usd=set(), montos_bs=set()
    )
    check("🔴 una CUENTA en el aviso de pago ⇒ el mensaje se descarta", r == "", f"salió: {r!r}")
    ag._pedir_redaccion = guardado

    print("\n5) 🏦 `generar_datos_pago` EN DOS PASOS — la elección tiene su casilla (rama B)")
    f = get_session_factory()
    async with f() as s:
        activos = (await s.execute(
            select(MetodoPago).where(MetodoPago.activo.is_(True))
        )).scalars().all()
    check("hay métodos de pago activos en la tabla", bool(activos), "tabla metodos_pago vacía")
    check("🔴 ZELLE existe en la tabla (antes NO, y la visión rechazaba pagos Zelle legítimos)",
          any("zelle" in f"{m.tipo} {m.titulo}".lower() for m in activos),
          f"métodos: {[(m.tipo, m.titulo) for m in activos]}")
    # Los métodos en BOLÍVARES de la tabla real (pago móvil, transferencia) — los usa el
    # escenario "en bolívares" de abajo. Se calculan AQUÍ, de la misma consulta.
    metodos_activos_bs = _grupo_bolivares(activos)

    await _limpiar()
    try:
        async with f() as s:
            zona = ZonaEntrega(nombre=ZONA_PRUEBA, costo=0, es_retiro=True, disponible=True)
            s.add(zona)
            s.add(Cliente(telefono=TEL, nombre="Rosa"))
            await s.flush()
            s.add(Pedido(
                cliente_telefono=TEL,
                items=[{"producto": "prueba", "cantidad": 1, "precio_unitario": 20.0}],
                total=20, estado="pendiente", entrega_fecha=hoy_venezuela(),
                zona_id=zona.id, zona_nombre=zona.nombre, costo_envio=0,
            ))
            await s.commit()

        # PASO 1 — sin `metodo`: nombres SÍ, datos NO.
        async with f() as s:
            r = await generar_datos_pago(s, TEL)
        check("genera el cobro", bool(r.get("ok")), str(r))
        check("🔴 SIN elegir método NO viajan datos de cuentas",
              not r.get("metodos_de_pago") and not r.get("telefono_pago")
              and not r.get("cedula") and not r.get("banco"), str(r)[:250])
        nombres = r.get("metodos_disponibles") or []
        check("   ...pero SÍ los NOMBRES para preguntar (`metodos_disponibles`)",
              bool(nombres), str(r)[:250])
        check("   ...y el cobro completo de las dos monedas (aún no eligió)",
              r.get("monto_bs") is not None and r.get("monto_usd_divisas") is not None,
              str(r)[:200])

        # PASO 2 — el cliente elige Zelle: SOLO sus datos, UNA sola moneda, y queda GUARDADO.
        async with f() as s:
            r2 = await generar_datos_pago(s, TEL, metodo="Zelle")
        check("con metodo='Zelle' genera el cobro", bool(r2.get("ok")), str(r2)[:250])
        metodos = r2.get("metodos_de_pago") or []
        check("🔴 devuelve UN solo método y es el Zelle (con su correo)",
              len(metodos) == 1 and "zelle" in str(metodos[0].get("metodo", "")).lower()
              and metodos[0].get("correo"), str(metodos))
        check("   ...y el resumen es de UNA sola moneda (sin bolívares)",
              "bs" not in (r2.get("resumen_cobro") or "").lower()
              and r2.get("monto_bs") is None, str(r2.get("resumen_cobro")))
        async with f() as s:
            ped = (await s.execute(
                select(Pedido).where(Pedido.cliente_telefono == TEL)
            )).scalars().first()
            # ⚠️ El tipo se compara NORMALIZADO (_tipo_canonico): la casilla congela el tipo
            # TAL CUAL está en la tabla real, y en el taller está cargado 'Zelle' (mayúscula).
            # La primera corrida de este banco (31-ago) salió roja por comparar contra 'zelle'
            # literal — el flujo entero estaba bien; el estricto era el check.
            check("🔴 la elección quedó en su CASILLA (pedidos.metodo_elegido)",
                  ped.metodo_elegido and "zelle" in ped.metodo_elegido.lower()
                  and _tipo_canonico(ped.metodo_elegido_tipo) == "zelle",
                  f"metodo_elegido={ped.metodo_elegido!r} tipo={ped.metodo_elegido_tipo!r}")
            check("   ...y la cotización se guardó COMPLETA igual (el validador no cambió)",
                  ped.cotizado_bs is not None and ped.cotizado_usd is not None
                  and ped.cotizado_usd_divisas is not None and ped.tasa_cotizada is not None,
                  f"cotizado_bs={ped.cotizado_bs} usd={ped.cotizado_usd}")

        # LA CASILLA GANA: la re-llamada SIN `metodo` (el "dame los datos otra vez" típico)
        # responde por el método elegido en vez de volcar todos y reabrir la elección.
        async with f() as s:
            r3 = await generar_datos_pago(s, TEL)
        m3 = r3.get("metodos_de_pago") or []
        check("🔴 re-llamada SIN metodo ⇒ responde por el ELEGIDO (no reabre)",
              r3.get("ok") and len(m3) == 1
              and "zelle" in str(m3[0].get("metodo", "")).lower(), str(r3)[:250])

        # Un método INVENTADO se rechaza con la lista real y sin tocar la BD.
        async with f() as s:
            r4 = await generar_datos_pago(s, TEL, metodo="cheque")
        check("🚫 un método inventado ('cheque') ⇒ rechazado con la lista real",
              r4.get("ok") is False and bool(r4.get("metodos_disponibles"))
              and not r4.get("metodos_de_pago"), str(r4)[:250])

        # Y el Pago Móvil elegido trae teléfono, cédula y las llaves viejas (compatibilidad).
        async with f() as s:
            r5 = await generar_datos_pago(s, TEL, metodo="Pago Móvil")
        m5 = r5.get("metodos_de_pago") or []
        check("con metodo='Pago Móvil': teléfono y cédula del método",
              len(m5) == 1 and m5[0].get("telefono") and m5[0].get("cedula"), str(m5))
        check("   ...las llaves viejas siguen (compatibilidad)",
              bool(r5.get("telefono_pago") and r5.get("cedula")), str(r5)[:250])

        # "VOY A PAGAR EN BOLÍVARES" = elección de GRUPO (Maired, 1-sep): pago móvil y
        # transferencia son la misma plata — se dan JUNTOS, sin repreguntar cuál vía, y sin
        # una sola cuenta en dólares en el resultado.
        async with f() as s:
            r6 = await generar_datos_pago(s, TEL, metodo="en bolívares")
        m6 = r6.get("metodos_de_pago") or []
        en_bs = metodos_activos_bs
        check("🔴 'en bolívares' entrega TODOS los métodos en Bs JUNTOS (sin repreguntar)",
              r6.get("ok") and len(m6) == len(en_bs) >= 1, f"{len(m6)} vs {len(en_bs)} · {str(m6)[:200]}")
        check("   ...y NINGUNO es de dólares (ni Zelle ni Binance ni efectivo)",
              not any("zelle" in str(m.get("metodo", "")).lower()
                      or "binance" in str(m.get("metodo", "")).lower() for m in m6), str(m6)[:200])
        check("   ...el resumen es SOLO bolívares (sin $ ni descuento)",
              "$" not in (r6.get("resumen_cobro") or "") and r6.get("monto_usd_divisas") is None,
              str(r6.get("resumen_cobro")))
        async with f() as s:
            ped = (await s.execute(
                select(Pedido).where(Pedido.cliente_telefono == TEL)
            )).scalars().first()
            esperado = ("bolivares" if len(en_bs) > 1
                        else _tipo_canonico(en_bs[0].tipo))
            check("   ...y la casilla guarda la elección (la moneda, si hay varias vías)",
                  _tipo_canonico(ped.metodo_elegido_tipo) == esperado,
                  f"metodo_elegido={ped.metodo_elegido!r} tipo={ped.metodo_elegido_tipo!r}")
    finally:
        await _limpiar()

    print()
    if fallos:
        print(f"❌ {len(fallos)} PRUEBA(S) FALLARON:")
        for x in fallos:
            print(f"   - {x}")
        sys.exit(1)
    print("✅ TODAS LAS PRUEBAS DEL CANDADO DE DATOS BANCARIOS PASARON")


if __name__ == "__main__":
    asyncio.run(main())
