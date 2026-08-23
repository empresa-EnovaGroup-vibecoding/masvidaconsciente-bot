"""EL PROMPT NO SE CONTRADICE A SÍ MISMO — el refactor del 2026-08-23.

**Por qué existe este fichero.** El prompt creció por ACUMULACIÓN: cada bug de agosto se arregló
AÑADIENDO una regla, sin releer las que ya estaban. 68 reglas escritas en momentos distintos, y
**nadie las había leído nunca juntas**. El resultado, medido: dos reglas se declaraban las dos "la
MÁS importante", una ordenaba cerrar preguntando mientras el documento de Maired pide lo
contrario, y —lo más grave— `_REGLAS` seguía ordenando *"coordinas la entrega"* al recibir el
comprobante **el día después** de que el código pasara a ESPERAR el clic de la dueña.

Varias de esas reglas le ordenan confirmar y resumir. Haiku 4.5 —el modelo más pequeño de la
familia— las obedece TODAS a la vez, y eso visto desde fuera es un bot que repite. Es la queja de
Maired (*"repite y redunda"*, *"es un bot bruto"*) con causa medible. → L68.

⚠️ **Estos tests no miden el tono** (ninguna máquina puede). Miden lo que sí es determinista: que
dos capas no se ordenen cosas opuestas, y que el prompt obedezca sus propias reglas.

🔴 **Y los dos últimos tests existen por L57:** las frases que protegen el cobro y el reparto entre
agentes SOLO las vigilaban `probar_herramientas` y `probar_dos_agentes` — dos bancos que necesitan
Postgres y **corren DESPUÉS de desplegar**. Al refactorizar, dos de esas frases se reformularon sin
querer y los 645 tests siguieron verdes: la puerta que valida ANTES de desplegar no tenía nada que
decir. Aquí sí.
"""
from __future__ import annotations

import re

from app.agent.system_prompt import _REGLAS, _aplicar_marcas, _filtrar_por_agente
from app.services.tools_config import BLINDADAS, DESACTIVABLES

TOOLS = BLINDADAS | DESACTIVABLES


def _prompt(quien: str = "uno") -> str:
    return _aplicar_marcas(_filtrar_por_agente(_REGLAS, quien), frozenset(TOOLS))


UNO = _prompt("uno")
VOZ = _prompt("voz")
OPERADOR = _prompt("operador")


# ══════════════════════════════════════════════════════════════════════════════════
#  1. EL DESEMPATE: una jerarquía, no 60 imperativos del mismo peso
# ══════════════════════════════════════════════════════════════════════════════════

def test_el_prompt_trae_un_orden_de_prioridad_que_desempata():
    """🔴 LA CAUSA RAÍZ, convertida en test.

    Sin un orden, ANTIINVENCIÓN ("la regla MÁS importante") y BREVEDAD ("lo más importante de tu
    voz") le llegan las DOS al modo `uno` reclamando primacía, y el modelo no tiene con qué
    elegir. Las etiquetas se conservan porque en modo DOS cae una en cada prompt y ahí no
    compiten (lo protege `probar_dos_agentes`); lo que faltaba era el desempate para modo `uno`.
    """
    assert "TU ORDEN DE PRIORIDAD" in UNO
    for prioridad in ("1. VERDAD", "2. BREVEDAD", "3. CIERRE"):
        assert prioridad in UNO, f"falta la prioridad {prioridad}"
    assert "gana la de número más bajo" in UNO, "el orden existe pero no dice cómo desempatar"


def test_el_orden_dice_quien_manda_entre_las_reglas_y_la_personalidad():
    """La personalidad (BD) es de Whuilianny y manda en el TONO; `_REGLAS` manda en los HECHOS.
    Escribirlo evita la pelea capa-contra-capa que ya produjo la contradicción de las FOTOS."""
    assert "la personalidad manda en el TONO" in UNO
    assert "estas reglas mandan en los HECHOS" in UNO


def test_una_sola_regla_reclama_primacia_en_cada_prompt():
    """Espejo en pytest de lo que solo vigilaba `probar_dos_agentes` (que necesita Postgres)."""
    assert OPERADOR.count("MÁS importante") == 1
    assert VOZ.count("más importante de tu voz") == 1
    assert "más importante de tu voz" not in OPERADOR
    assert "MÁS importante" not in VOZ


# ══════════════════════════════════════════════════════════════════════════════════
#  2. EL PROMPT OBEDECE SUS PROPIAS REGLAS
# ══════════════════════════════════════════════════════════════════════════════════

_REGLA_DE_LOS_SIGNOS = "NADA de signos de apertura"


def test_los_ejemplos_del_prompt_no_usan_los_signos_que_el_prompt_prohibe():
    """🔴 Una contradicción que nadie había mirado: la regla de estilo prohíbe los signos de
    apertura ("¿" y "¡") y **8 de los ejemplos del propio prompt los usaban**. Y la regla de al
    lado dice que las frases entre comillas son ejemplos de cómo escribir. O sea que el prompt
    le enseñaba al modelo justo lo contrario de lo que le ordenaba — en un modelo pequeño, eso
    se paga.

    (La única línea exenta es la regla que los NOMBRA para prohibirlos.)
    """
    culpables = []
    for linea in UNO.split("\n"):
        if _REGLA_DE_LOS_SIGNOS in linea:
            continue
        for ejemplo in re.findall(r'"([^"]*)"', linea):
            if "¿" in ejemplo or "¡" in ejemplo:
                culpables.append(ejemplo[:60])
    assert not culpables, (
        f"{len(culpables)} ejemplo(s) del prompt usan los signos que el prompt prohíbe: {culpables}"
    )


def test_el_prompt_no_le_pide_mas_globitos_de_los_que_permite_la_personalidad():
    """🔴 De aquí salían los 6 globos seguidos que contó Maired en un solo turno.

    La personalidad (BD) es precisa: *"Usa 1 o 2 globitos cortos… Usa 3 únicamente cuando existan
    temas realmente diferentes"*. `_REGLAS` decía *"Manda VARIOS mensajitos cortos"* — una orden
    vaga y más permisiva, en la capa que gana. Ahora `_REGLAS` remite a la personalidad en vez de
    competir con ella: el cuánto lo decide la voz, que es de Whuilianny.
    """
    assert "VARIOS mensajitos" not in UNO, (
        "volvió la orden que empuja a partir la respuesta en más globitos que los que permite la voz"
    )
    linea = next(ln for ln in UNO.split("\n") if "Planos, sin formato" in ln)
    assert "personalidad" in linea, "el formato tiene que remitir a la voz, no fijar su propio número"


# ══════════════════════════════════════════════════════════════════════════════════
#  3. EL PROMPT Y EL CÓDIGO DICEN LO MISMO SOBRE EL «PAGO APROBADO»
# ══════════════════════════════════════════════════════════════════════════════════

def test_el_prompt_ya_no_ordena_coordinar_la_entrega_al_recibir_el_comprobante():
    """🔴🔴 LA CONTRADICCIÓN MÁS CARA QUE ENCONTRÓ EL REFACTOR.

    El 2026-08-22 el bot pasó a ESPERAR el clic de «Pago aprobado» de la dueña antes de coordinar
    la entrega (pasos 8-9 de la plantilla de Maired). Se cambió `tasks._procesar_comprobante` y se
    escribió `test_pago_espera_aprobacion.py`… que mira **solo ese carril**.

    `_REGLAS` —que le llega al modelo en CADA turno— siguió diciendo *"dile que RECIBISTE su pago
    **y que coordinas la entrega/envío**"*. O sea: la instrucción del turno le pedía esperar y la
    regla permanente le ordenaba seguir. Un día entero desplegado así, con los tests en verde.

    → Es L68 en vivo, y la razón por la que un cambio de conducta obliga a releer el prompt.
    """
    linea = next(ln for ln in UNO.split("\n") if "Al registrar el comprobante" in ln)
    assert "coordinas la entrega" not in linea, (
        "el prompt volvió a ordenar coordinar la entrega sin esperar a la dueña"
    )
    assert "revisando" in linea, "tiene que decir la verdad: que lo está revisando"
    assert "Hasta que ella lo apruebe NO coordines" in linea, (
        "falta la mitad que importa: que NO coordine hasta el clic"
    )


def test_el_resumen_final_antes_del_despacho_existe():
    """Paso 11 de la plantilla: *"Enviar un resumen final de productos, modalidad, saldo pendiente
    si existe, dirección o retiro y fecha/ventana de entrega; pedir confirmación antes del
    despacho"*. Estaba en el ROADMAP como N5, **atado a N6** (que el bot espere el clic). N6 se
    cerró el 22-ago, así que N5 quedó desbloqueado y cabe como regla."""
    linea = next((ln for ln in UNO.split("\n") if "resumen final" in ln), "")
    assert linea, "no existe la regla del resumen final (paso 11 de la plantilla)"
    assert "UNA vez" in linea, "sin el freno, el resumen final se convierte en otra repetición"
    for pieza in ("retiro", "fecha", "saldo pendiente"):
        assert pieza in linea, f"el resumen final no menciona {pieza}"


# ══════════════════════════════════════════════════════════════════════════════════
#  4. LO QUE PROTEGÍAN LOS BANCOS, AHORA TAMBIÉN EN EL CI  (L57)
# ══════════════════════════════════════════════════════════════════════════════════

def test_las_frases_del_cobro_que_protege_el_banco_siguen_LITERALES():
    """🔴 L57 aplicada. `probar_herramientas` exige 11 frases del cobro literales en el prompt,
    pero es un BANCO: necesita Postgres y corre DESPUÉS de desplegar. Al refactorizar se
    reformularon dos de ellas ("Sin fecha de entrega acordada NO PUEDES COBRAR" y "registra el
    pedido COMPLETO con registrar_pedido") y **los 645 tests siguieron verdes**. Solo el banco lo
    cazó, y para entonces ya habría estado desplegado.

    La lista se LEE del banco, no se copia: así una frase nueva allí queda vigilada aquí sola.
    """
    import ast
    import pathlib

    fuente = (pathlib.Path(__file__).parent.parent / "scripts" / "probar_herramientas.py").read_text()
    arbol = ast.parse(fuente)
    frases = next(
        [e.value for e in n.value.elts]
        for n in ast.walk(arbol)
        if isinstance(n, ast.Assign)
        and any(getattr(t, "id", None) == "FRASES_DEL_COBRO" for t in n.targets)
    )
    assert len(frases) >= 11, "el banco perdió frases protegidas"
    faltan = [f for f in frases if f not in UNO]
    assert not faltan, f"el prompt perdió frases que el banco exige literales: {faltan}"


def test_ninguna_frase_del_cobro_se_cae_al_apagar_herramientas_desde_el_panel():
    """Las 5 tools apagables se controlan desde el panel. Si una frase del cobro cuelga de un
    `{{tool|…}}`, la dueña podría apagar el cobro sin saberlo (es PRM-18, la misma familia)."""
    solo_blindadas = _aplicar_marcas(_filtrar_por_agente(_REGLAS, "uno"), frozenset(BLINDADAS))
    for frase in ("NUNCA calcules, sumes, restes ni redondees montos tú",
                  "Sin fecha de entrega acordada NO PUEDES COBRAR",
                  "NADA DE CONSEJO MÉDICO", "BREVEDAD ante todo",
                  "TU ORDEN DE PRIORIDAD"):
        assert frase in solo_blindadas, f"«{frase[:40]}» desaparece al apagar las tools del panel"
