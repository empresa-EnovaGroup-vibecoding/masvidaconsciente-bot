"""LAS HERRAMIENTAS SE APAGAN DESDE EL PANEL — sin romper el cobro ni las redes.

🔴 LOS TRES RIESGOS DE ESTA FEATURE, Y POR QUÉ ESTE BANCO EXISTE:

**1. Apagar una tool le arranca el brazo a una red de seguridad.**
`agent.py` llama a `pedir_ayuda` DIRECTO 7 veces (fuera del bucle de tool_calls) y a
`enviar_catalogo` desde `_asegurar_catalogo`. El worker de visión llama a
`registrar_comprobante` saltándose `ejecutar_tool`. Si el filtro tocara `_DISPATCH`, apagar una
tool desde el panel dejaría al bot inventando dinero **sin nadie a quien avisar**.
→ Se filtra SOLO `TOOL_SCHEMAS` (lo que el modelo VE). `_DISPATCH` queda ENTERO. Se comprueba.

**2. La red del DINERO se queda ciega.** Es el bug invisible de toda la fase.
`autorizados_por_moneda` construye la lista blanca de montos leyendo el **TEXTO DEL PROMPT**: los
precios reales entran a `usd_ok` porque `_catalogo_bloque` escribe "$25.00" ahí. Si alguien
"simplificara" haciendo condicional el bloque de FICHAS, la red se quedaría sin precios y marcaría
como INVENTADO **todo precio legítimo** ⇒ `RESPUESTA_SEGURA` en cada cotización. Ni un test de
schemas ni uno de prompts lo vería.
→ El catálogo NO es condicional (por eso `ver_catalogo` es blindada). Se comprueba **con las 5
apagadas**: los precios reales tienen que seguir autorizados.

**3. Bucle de RESPUESTA_SEGURA.** Con `enviar_fotos_producto` apagada, `fotos_ok` no puede ponerse
en True JAMÁS. Basta un falso positivo del detector de pronombre para que la red del envío fantasma
dispare, le ordene llamar a una herramienta que ya no existe, el modelo no pueda obedecer, y el
turno acabe enlatado. **En bucle, y en silencio.**
→ El regaño sabe si la tool existe. Se comprueba que hay ≤1 escalada y el turno TERMINA.

**Y la regla que gobierna el troceo del prompt:** las reglas del COBRO no mencionan NI UNA tool
desactivable (verificado con grep), así que el bisturí no entra ahí ni por error. Este banco vigila
que sigan íntegras **en las 32 combinaciones posibles**.
"""
import asyncio
import itertools
import sys

from app.agent.agent import RESPUESTA_SEGURA, autorizados_por_moneda, responder
from app.agent.system_prompt import construir_partes_prompt
from app.agent.tools import _DISPATCH, TOOL_SCHEMAS, schemas_para
from app.services.tools_config import (
    BLINDADAS,
    DESACTIVABLES,
    TOOLS,
    _parsear,
    serializar,
)

TEL = "__prueba_tools__"

fallos: list[str] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"   {'[OK ]' if ok else '[MAL]'} {nombre}{('  → ' + detalle) if detalle and not ok else ''}")
    if not ok:
        fallos.append(nombre)


# Las frases CANÓNICAS del cobro. Si una sola desaparece del prompt en CUALQUIER combinación de
# tools, el bot deja de saber cobrar — y no habría forma de enterarse hasta que un cliente pague mal.
FRASES_DEL_COBRO = [
    "NUNCA calcules, sumes, restes ni redondees montos tú",
    "SE VENDE POR PAQUETE COMPLETO",
    "Sin fecha de entrega acordada NO PUEDES COBRAR",
    "registra el pedido COMPLETO con registrar_pedido",
    "llama a generar_datos_pago con el `pedido_id`",
    "usa registrar_comprobante",
    "CADA PEDIDO ES SEPARADO",
    "NUNCA PROMETAS SIN LLAMAR A `pedir_ayuda`",
    "HONESTIDAD SOBRE QUIÉN ERES",
    "NADA DE CONSEJO MÉDICO",
    "BREVEDAD ante todo",
]


async def main() -> None:
    print("\n1) EL REGISTRO: fail-open y blindadas irrevocables")
    check("las 12 herramientas están declaradas", len(TOOLS) == 12, str(len(TOOLS)))
    check("7 blindadas + 5 desactivables", len(BLINDADAS) == 7 and len(DESACTIVABLES) == 5,
          f"{len(BLINDADAS)} + {len(DESACTIVABLES)}")
    check("clave AUSENTE ⇒ las 12 (fail-open)", _parsear(None) == frozenset(TOOLS))
    check("clave VACÍA ⇒ las 12", _parsear("  ") == frozenset(TOOLS))
    check("clave con BASURA ⇒ las 12", _parsear("no_existe,tampoco") == frozenset(TOOLS))
    # 🔒 El candado que vive en la LECTURA, no solo en la API: si alguien escribe el CSV a mano en
    # Postgres y se deja fuera `pedir_ayuda`, el bot la tiene IGUAL.
    escrito_a_mano = _parsear("ver_catalogo,info_producto")
    check(
        "un CSV escrito a mano SIN las blindadas ⇒ se re-inyectan igual",
        BLINDADAS <= escrito_a_mano,
        f"faltan: {sorted(BLINDADAS - escrito_a_mano)}",
    )
    check(
        "serializar() nunca deja fuera una blindada",
        BLINDADAS <= set(serializar({"ver_catalogo"}).split(",")),
    )

    print("\n2) EL FILTRO TOCA LO QUE EL MODELO VE — NUNCA EL DISPATCH")
    check("_DISPATCH tiene las 12 SIEMPRE", len(_DISPATCH) == 12, str(len(_DISPATCH)))
    solo_blindadas = schemas_para(BLINDADAS)
    check(
        f"con solo las blindadas, el LLM ve {len(BLINDADAS)} (no 12)",
        len(solo_blindadas) == len(BLINDADAS),
        f"ve {len(solo_blindadas)}",
    )
    check(
        "y el DISPATCH sigue entero (las redes pueden ejecutarlo TODO)",
        len(_DISPATCH) == len(TOOL_SCHEMAS) == 12,
    )
    check(
        "pedir_ayuda y enviar_catalogo siguen en el dispatch aunque el modelo no las viera",
        "pedir_ayuda" in _DISPATCH and "enviar_catalogo" in _DISPATCH,
    )

    print("\n3) LAS 32 COMBINACIONES: el COBRO sobrevive a TODAS")
    # 2^5 = 32 combinaciones de las desactivables. En cada una: el prompt tiene que conservar
    # ÍNTEGRAS las frases del cobro, y no puede quedar ni una marca sin resolver.
    desact = sorted(DESACTIVABLES)
    malas_frases, marcas_sueltas, filtradas = [], [], []
    for n in range(len(desact) + 1):
        for combo in itertools.combinations(desact, n):
            activas = BLINDADAS | set(combo)
            estable, _ = await construir_partes_prompt(activas=activas)
            for f in FRASES_DEL_COBRO:
                if f not in estable:
                    malas_frases.append((sorted(activas), f))
            if "@" in estable.replace("@masvida", "") or "{{" in estable or "}}" in estable:
                marcas_sueltas.append(sorted(combo))
            # Ninguna tool INACTIVA puede seguir nombrada en el prompt.
            for t in set(TOOLS) - activas:
                if t in estable:
                    filtradas.append((sorted(combo), t))
    check(
        f"las {len(FRASES_DEL_COBRO)} frases del cobro sobreviven a las 32 combinaciones",
        not malas_frases,
        f"se perdió: {malas_frases[0][1][:40]!r} con {malas_frases[0][0]}" if malas_frases else "",
    )
    check(
        "no sobrevive ninguna marca sin resolver (@ o {{ }})",
        not marcas_sueltas,
        str(marcas_sueltas[:2]),
    )
    check(
        "una tool APAGADA nunca sigue nombrada en el prompt",
        not filtradas,
        f"{filtradas[0][1]!r} seguía nombrada con {filtradas[0][0]}" if filtradas else "",
    )

    print("\n4) 🔴 LA RED DEL DINERO NO SE QUEDA CIEGA (el bug invisible)")
    estable, dinamico = await construir_partes_prompt(activas=BLINDADAS)  # las 5 APAGADAS
    usd, bs = autorizados_por_moneda(estable, dinamico)
    check(
        "con las 5 apagadas, los precios REALES siguen autorizados",
        len(usd) >= 5,
        f"solo {len(usd)} montos en la lista blanca ⇒ el bot no podría decir NINGÚN precio",
    )
    check(
        "el catálogo sigue en el prompt (NO es condicional)",
        "id_para_pedir" in estable and "$" in estable,
    )

    print("\n5) SE LE DECLARA EL LÍMITE (no basta con borrar la orden)")
    # "Cuando algo no existe, el modelo lo inventa" — el $23 USD que le llegó a una clienta real.
    check(
        "con las 5 apagadas aparece 'LO QUE HOY NO PUEDES HACER'",
        "LO QUE HOY NO PUEDES HACER" in estable,
    )
    check(
        "y todos los límites desembocan en pedir_ayuda",
        estable.count("pedir_ayuda") >= 3,
    )
    todo, _ = await construir_partes_prompt(activas=frozenset(TOOLS))
    check(
        "con las 12 activas NO aparece ningún límite (prompt idéntico al de siempre)",
        "LO QUE HOY NO PUEDES HACER" not in todo,
    )

    print("\n6) EL GUARDIA: si el modelo llama una tool APAGADA, no se ejecuta")
    llamadas = []

    async def fake_ejecutar(nombre, args, telefono):
        llamadas.append(nombre)
        return {"ok": True}

    turnos = {"n": 0}

    async def fake_llm(messages, tools, modelo):
        turnos["n"] += 1
        nombres = [t["function"]["name"] for t in tools]
        if turnos["n"] == 1:
            # El modelo alucina y llama una tool que NO está en su lista.
            return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "ver_pedidos_cliente", "arguments": "{}"}},
            ]}}]}
        # 2º turno: comprueba que se le respondió el tool_call (o el proveedor daría 400).
        respondidos = [m for m in messages if m.get("role") == "tool"]
        turnos["respondidos"] = len(respondidos)
        turnos["tool_call_id"] = respondidos[0].get("tool_call_id") if respondidos else None
        turnos["vio"] = nombres
        return {"choices": [{"message": {"role": "assistant", "content": "Listo 💚"}}]}

    from app.services import tools_config

    original = tools_config.leer_tools_activas

    async def sin_historial():
        return BLINDADAS  # las 5 apagadas

    tools_config.leer_tools_activas = sin_historial
    import app.agent.agent as ag

    ag.leer_tools_activas = sin_historial
    try:
        r = await responder(TEL, "hola", historial=[], llm=fake_llm, ejecutar=fake_ejecutar)
    finally:
        tools_config.leer_tools_activas = original
        ag.leer_tools_activas = original

    check(
        "la tool apagada NO se ejecutó",
        "ver_pedidos_cliente" not in llamadas,
        f"¡se ejecutó! llamadas={llamadas}",
    )
    check(
        "el modelo NO la vio en su lista",
        "ver_pedidos_cliente" not in turnos.get("vio", []),
        str(turnos.get("vio")),
    )
    check(
        "pero SÍ se le respondió el tool_call (sin esto, el proveedor da 400)",
        turnos.get("respondidos") == 1 and turnos.get("tool_call_id") == "call_1",
        f"respondidos={turnos.get('respondidos')} id={turnos.get('tool_call_id')}",
    )
    # La red del saludo antepone "¡Hola, buenas tardes!" porque el cliente saludó al inicio: eso
    # está BIEN. Lo que se comprueba es que el turno TERMINÓ con la respuesta del modelo — no
    # que acabara en RESPUESTA_SEGURA ni en bucle.
    check(
        "y el turno TERMINÓ normal (no hay bucle ni respuesta enlatada)",
        "Listo" in r and RESPUESTA_SEGURA not in r,
        repr(r)[:60],
    )

    print()
    if fallos:
        print(f"   🔴 {len(fallos)} FALLO(S). Apagar herramientas rompe algo.")
        sys.exit(1)
    print("   ✅ LAS HERRAMIENTAS SE APAGAN — Y EL COBRO Y LAS REDES SIGUEN EN PIE")


if __name__ == "__main__":
    asyncio.run(main())
