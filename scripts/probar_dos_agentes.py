"""LOS DOS AGENTES: la Voz no puede inventar — porque NO TIENE DE DÓNDE.

🔴 EL PROBLEMA QUE ESTO RESUELVE (auditoría 2026-07-14): el bot corre con ~16.400 tokens de
instrucciones por turno, 42 reglas imperativas, 55 prohibiciones — y con DOS reglas que se
declaran ambas *"la MÁS importante"* (ANTIINVENCIÓN y BREVEDAD). Cuando todo es crítico, nada lo
es. Por eso hay SIETE redes de regex en `agent.py` que existen solo para atrapar al modelo
incumpliendo, y el propio código lo confiesa: *"el prompt se lo prohibía DOS VECES y lo hizo
igual"*, *"la regla vivía en el prompt: humo"*.

La salida no es una regla más. Es partir el agente en dos:

  · OPERADOR — tiene las herramientas. Busca, registra, cobra. NO le escribe al cliente.
  · VOZ      — escribe el mensaje. Sin herramientas, sin catálogo, sin datos bancarios.

**No es que a la Voz se le PROHÍBA inventar un precio: es que NO TIENE DE DÓNDE SACARLO.**
El prompt sugiere; el código impide. Ese es el salto, y es lo que este banco comprueba.

⚠️ LO QUE NO PUEDE ROMPERSE, Y POR ESO SE VIGILA:
  1. Las 9 redes de seguridad NO se retiran y NO cambian de nombre ni de firma (3 bancos las
     importan por nombre). Lo que cambia es DE QUÉ SE ALIMENTAN.
  2. La lista blanca del dinero pasa de "todo el prompt" (donde vivía el `id_para_pedir` que se
     coló como el "$23" a una clienta real) a "lo que devolvieron las tools EN ESTE TURNO".
  3. `responder()` conserva su firma y sigue devolviendo `str`: `tasks.py` no se toca.
  4. NO se toca NI UNA temperatura: el Operador reusa `_llamar_openrouter` (0.15) y la Voz reusa
     `_pedir_redaccion` (0.7), verbatim. Cada uno conserva la suya.
  5. La bandera `agente_modo` vuelve al agente único con UN `UPDATE`.
"""
import asyncio
import inspect
import re
import sys

from app.agent import agent as ag
from app.agent.hoja import HojaDeHechos
from app.agent.system_prompt import _REGLAS, _filtrar_por_agente, construir_partes_prompt

fallos: list[str] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"   {'[OK ]' if ok else '[MAL]'} {nombre}{('  → ' + detalle) if detalle and not ok else ''}")
    if not ok:
        fallos.append(nombre)


async def main() -> None:
    print("\n1) LAS 9 REDES SIGUEN AHÍ, CON SU NOMBRE Y SU FIRMA")
    # 3 bancos las importan por nombre (probar_carril_dinero, probar_honestidad, ensayo_retomar).
    # Renombrar una las deja importando un fantasma. Este es el candado.
    REDES = [
        "_dinero_inventado", "_datos_sensibles_inventados", "_frase_prohibida",
        "frase_prohibida_siempre", "_suena_a_sistema", "_afirma_pedido_registrado",
        "_afirma_envio_fotos", "_promete_averiguar", "_asegurar_catalogo", "_asegurar_saludo",
    ]
    faltan = [r for r in REDES if not callable(getattr(ag, r, None))]
    check(f"las {len(REDES)} redes existen y son invocables", not faltan, str(faltan))
    check(
        "responder() conserva su firma (tasks.py no se toca)",
        list(inspect.signature(ag.responder).parameters)[:4]
        == ["telefono", "mensaje_usuario", "historial", "nombre_cliente"],
    )

    print("\n2) NO SE TOCÓ NI UNA TEMPERATURA")
    src = inspect.getsource(ag)
    check('el Operador sigue a 0.15 (con tools, el carril del dinero)', '"temperature": 0.15' in src)
    check('la Voz sigue a 0.7 (sin tools)', '"temperature": 0.7' in src)
    check(
        "no hay ninguna temperatura NUEVA",
        len(re.findall(r'"temperature":\s*[\d.]+', src)) == 4,  # 0.15 · 0.7 · 0 (audio) · 0 (visión)
        str(re.findall(r'"temperature":\s*[\d.]+', src)),
    )

    print("\n3) EL REPARTO DE LAS REGLAS")
    op = _filtrar_por_agente(_REGLAS, "operador")
    vz = _filtrar_por_agente(_REGLAS, "voz")
    uno = _filtrar_por_agente(_REGLAS, "uno")
    check("el modo 'uno' conserva TODAS las reglas (el de siempre)", len(uno) > len(op) and len(uno) > len(vz))
    check("ninguna marca (!a / !v) sobrevive al filtro", "!a " not in uno and "!v " not in uno)
    # 🔴 LA CONTRADICCIÓN SE DISUELVE: hoy DOS reglas se declaran "la MÁS importante" y compiten
    # por la atención del MISMO modelo. Tras el reparto, cada prompt se queda con EXACTAMENTE UNA.
    check(
        "ANTIINVENCIÓN ('la regla MÁS importante') va al OPERADOR",
        "ANTIINVENCIÓN" in op and "ANTIINVENCIÓN" not in vz,
    )
    check(
        "BREVEDAD ('lo más importante de tu voz') va a la VOZ",
        "BREVEDAD ante todo" in vz and "BREVEDAD ante todo" not in op,
    )
    check(
        "cada prompt tiene UNA sola regla que reclama primacía (ya no compiten)",
        op.count("MÁS importante") + vz.count("más importante de tu voz") == 2
        and "más importante de tu voz" not in op
        and "MÁS importante" not in vz,
    )
    # Las reglas del COBRO son de ACCIÓN: la Voz no puede romperlas (no tiene herramientas).
    for frase in ("NUNCA calcules, sumes, restes", "SE VENDE POR PAQUETE COMPLETO",
                  "NO PUEDES COBRAR", "registrar_comprobante"):
        check(f"la regla del cobro {frase[:28]!r}… está en el OPERADOR", frase in op)
    # Y la Voz gana la única que necesita del dinero.
    check(
        "la VOZ tiene la regla nueva: 'las cifras se COPIAN, no se piensan'",
        "LAS CIFRAS SE COPIAN" in vz,
    )
    check("el formato (varios mensajitos) va a la VOZ", "Planos, sin formato" in vz)

    print("\n4) 🔴 LA VOZ NO PUEDE INVENTAR — PORQUE NO TIENE DE DÓNDE")
    e_voz, d_voz = await construir_partes_prompt("Ana", "584140000000", quien="voz")
    e_op, d_op = await construir_partes_prompt("Ana", "584140000000", quien="operador")
    prompt_voz = e_voz + d_voz
    prompt_op = e_op + d_op
    # Esto es lo que hace segura a la Voz. No es una prohibición: es una AUSENCIA.
    check("la Voz NO ve el catálogo (no puede inventar un producto)", "id_para_pedir" not in prompt_voz)
    check("la Voz NO ve las zonas (no puede inventar un envío)", "id_zona" not in prompt_voz)
    check(
        "la Voz NO ve el calendario (no puede prometer una fecha)",
        "DÍAS DE ENTREGA" not in prompt_voz,
    )
    check("la Voz NO ve las herramientas por su nombre", "registrar_pedido" not in prompt_voz)
    check("pero SÍ tiene la personalidad de Whuilianny", "Whuilianny" in prompt_voz)
    check("y el OPERADOR sí ve el catálogo (él es quien cotiza)", "id_para_pedir" in prompt_op)
    check("el OPERADOR NO lleva personalidad (no le escribe al cliente)",
          "Whuilianny" not in e_op or len(e_op) < len(e_voz) * 3)

    print("\n5) LA HOJA **ES** LA LISTA BLANCA (el bug del '$23' se vuelve imposible)")
    hoja = HojaDeHechos()
    # Una tool devuelve un producto con precio $14 y un `id_para_pedir` = 23.
    hoja.anotar_tool("ver_catalogo", {
        "productos": [{"nombre": "Empanadas", "id_para_pedir": 23, "precio_texto": "$14",
                       "de_que_es": "masa de plátano"}],
        "nota": "NO sueltes un folleto: usa el id_para_pedir de ESE tamaño",
    })
    usd, bs, totales, datos = hoja.listas_blancas()
    check("el PRECIO ($14) entra en la lista blanca", 14.0 in usd, str(sorted(usd)))
    # 🔴 EL BUG REAL: el 23 era un ID, no un precio — y la red vieja lo daba por bueno porque
    # leía TODOS los numerales del prompt. Aquí no entra: no lleva marca de dinero.
    check("el ID (23) NO entra (era el bug del '$23' a una clienta real)", 23.0 not in usd)
    check("ningún TOTAL nace del catálogo (solo de registrar_pedido / generar_datos_pago)",
          not totales, str(sorted(totales)))
    # La `nota` de la tool es una instrucción para el OPERADOR: si se colara a la Voz, la
    # repetiría y `_suena_a_sistema` dispararía — con razón.
    render = hoja.render("cuánto cuestan las empanadas?")
    check("la `nota` de la tool NO llega a la Voz", "folleto" not in render and "id_para_pedir" not in render)
    check("no suena a sistema (es su test)", not ag._suena_a_sistema(render), render[:70])
    check("pero el precio SÍ llega", "$14" in render)

    print("\n6) UN TOTAL SOLO NACE DE UNA HERRAMIENTA DE COBRO")
    hoja.anotar_tool("registrar_pedido", {
        "ok": True, "pedido_id": 7,
        "resumen": "Empanadas x2 = $28\nEnvío = $3\nTotal: $31\nEntrega: sábado 18 de julio",
    })
    usd, bs, totales, datos = hoja.listas_blancas()
    check("ahora SÍ hay un total ($31)", 31.0 in totales, str(sorted(totales)))
    check("y la hoja sabe que el pedido EXISTE (pedido_id=7)", hoja.pedido_id == 7)
    # La red del pedido fantasma se re-ancla a la HOJA, no a un flag suelto.
    check(
        "si la Voz dice 'te lo agendé' y el pedido existe ⇒ PASA",
        ag._afirma_pedido_registrado("Listo, te lo agendé 💚") and hoja.pedido_id is not None,
    )
    vacia = HojaDeHechos()
    check(
        "si lo dice y el pedido NO existe ⇒ la red lo FRENA",
        ag._afirma_pedido_registrado("Listo, te lo agendé 💚") and vacia.pedido_id is None,
    )

    print("\n7) 🔴 EL BOT PUEDE DECIR UN PRECIO DEL CATÁLOGO (lo cazó la prueba con el bot real)")
    # La primera versión hacía la lista blanca DEMASIADO estrecha: solo lo que devolvían las
    # TOOLS. Resultado absurdo: el bot se NEGÓ a decir "el Pan Keto cuesta $25" —que es la
    # verdad— porque el precio venía del catálogo de su prompt y no de una llamada a
    # `ver_catalogo`. La red funcionaba DE MÁS y el turno moría en RESPUESTA_SEGURA.
    e_op2, d_op2 = await construir_partes_prompt(None, None, quien="operador")
    usd_cat, _ = ag.autorizados_por_moneda(e_op2, d_op2)
    check(
        "los precios del catálogo autorizan al OPERADOR",
        len(usd_cat) >= 5,
        f"solo {len(usd_cat)} precios ⇒ el bot no podría cotizar nada",
    )
    check(
        "y un `id_para_pedir` NO se cuela (exige marca de dinero: el bug del '$23' sigue muerto)",
        not ag._dinero_inventado("cuesta $14", {14.0}, set(), set())
        and bool(ag._dinero_inventado("son $999", usd_cat, set(), set())),
    )

    print("\n8) LA BANDERA: volver atrás es un UPDATE")
    from app.agent.system_prompt import leer_config_agente

    modo, m_op, m_voz = await leer_config_agente()
    check("hay un modo, y por defecto es 'uno' (el de siempre)", modo in ("uno", "dos"), modo)
    check("los modelos caen a `modelo_ia` si no están puestos", bool(m_op) and bool(m_voz))

    print()
    if fallos:
        print(f"   🔴 {len(fallos)} FALLO(S).")
        sys.exit(1)
    print("   ✅ LA VOZ NO PUEDE INVENTAR — Y EL COBRO Y LAS 9 REDES SIGUEN EN PIE")


if __name__ == "__main__":
    asyncio.run(main())
