"""LOS CUATRO HUECOS QUE ENCONTRÓ LA AUDITORÍA EXHAUSTIVA (2026-08-22).

Se releyó la plantilla de negocio entera y se extrajeron **200 requisitos** (una revisión previa
a mano había sacado 51 — ahí estaba el hueco), cada uno verificado ejecutando código dentro del
contenedor. Tres de los cuatro fallos que siguen aquí eran **de arreglos hechos ese mismo día**:
código nuevo, con tests en verde, que no hacía lo que decía hacer.

Es la lección del proyecto un nivel más arriba: *el código impide… si el código mira lo correcto*.
"""

import pytest

from app.agent.agent import _dias_imposibles, _frase_prohibida
from app.agent.tools import _es_restriccion_alimentaria

CAL = {
    "ok": True, "hoy_es": "sábado 22 de agosto", "hoy_se_puede_entregar": False,
    "proximas_fechas": [{"fecha": "2026-08-24", "cuando": "lunes 24 de agosto"}],
}


# ══════════════════════════════════════════════════════════════════════════════════
#  1 · SEGURIDAD ALIMENTARIA — el más grave de los cuatro
# ══════════════════════════════════════════════════════════════════════════════════
#
# MEDIDO en el worker desplegado: `ver_catalogo(busqueda='vegano')` devolvía **Pan de Sándwich**
# (lleva manteca de cochino y huevo) y **Wafles Dulces** (huevos e hígado deshidratado), con una
# nota que le ORDENA nombrárselos. Y `'celiaco'` devolvía los 31 productos como si todos fueran
# aptos.
#
# La causa está escrita en el propio código: el escalón difuso dice *"en la asesoría encontrar de
# más es gratis"*. Es verdad para un typo. **Es falso para una restricción alimentaria** — ahí
# encontrar de más es ofrecerle hígado a un vegano. Y la Conversación 3 de la plantilla es
# exactamente una clienta vegana comprando para su hijo.

@pytest.mark.parametrize("consulta", [
    "vegano", "algo vegano", "soy vegana", "vegetariano",
    "celiaco", "soy celíaca", "sin gluten", "sin trigo",
    "sin lactosa", "sin lácteos", "alérgico al maní", "lleva almendra?",
    "tiene huevo", "sin soya",
])
def test_una_restriccion_alimentaria_se_reconoce(consulta):
    assert _es_restriccion_alimentaria(consulta) is not None, f"no la detectó: {consulta!r}"


@pytest.mark.parametrize("consulta", [
    "galletas", "pan keto", "algo dulce", "kombucha", "torta de chocolate",
    "para diabéticos",  # esta SÍ tiene campo en la BD y la resuelve el escalón 4
])
def test_una_busqueda_normal_NO_se_confunde_con_una_restriccion(consulta):
    assert _es_restriccion_alimentaria(consulta) is None, (
        f"frenó una búsqueda normal: {consulta!r} — la red no puede cortar ventas buenas"
    )


def test_el_freno_NO_aplica_si_la_consulta_nombra_un_PRODUCTO():
    """🔴 "pan sin gluten" es una búsqueda legítima —todos sus panes lo son— y frenarla deja al
    bot sin nada que ofrecer. La primera versión del freno la cortaba: lo cazó \,
    que tenía ese caso escrito desde antes. El freno solo vale para la restricción A SECAS."""
    # La detección de la palabra sigue dando positivo…
    assert _es_restriccion_alimentaria("pan sin gluten") is not None
    # …pero el freno de  la descarta al ver que nombra un producto real.
    # (El comportamiento completo lo cubre scripts/probar_buscador.py contra Postgres.)


# ══════════════════════════════════════════════════════════════════════════════════
#  2 · 🪦 LA FICHA REPETIDA — red QUITADA el 2026-08-24 (decisión de Erwin)
# ══════════════════════════════════════════════════════════════════════════════════
#
# Aquí vivían los tests de `_sin_ficha_repetida`. La red mutiló el cobro de Maired el 23-ago
# (partía los números por el punto decimal: "7.799,52" → "799,52", "$6.40" → "$6", mensaje
# 6784, entregado) y su comparación por subcadena no cazaba la paráfrasis. No repetirse es
# ahora tarea del MODELO (regla 66 de _REGLAS + personalidad). Ver SESIONES.md 2026-08-24.

# ══════════════════════════════════════════════════════════════════════════════════
#  3 · LA RED DEL DÍA — 4 de 5 promesas de "hoy" se escapaban
# ══════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@pytest.mark.parametrize("texto", [
    "Te lo puedo entregar hoy",
    "Te lo mando hoy",
    "Te lo tengo hoy mismo",
    "hoy te lo llevo",
    "Te lo dejo hoy",
    "Para hoy te lo preparo",
])
async def test_todas_las_formas_de_prometer_HOY(texto):
    """Enumerar las formas de decir algo en español es una lista que nunca se acaba: la primera
    versión buscaba patrones de PROMESA y solo cazaba 1 de estas 6."""
    assert await _dias_imposibles(texto, CAL) != [], f"se escapó: {texto!r}"


@pytest.mark.asyncio
@pytest.mark.parametrize("texto", [
    "Para hoy ya cerraron las entregas",          # ← frase de la Conversación 5 del documento
    "Hoy ya no salen entregas, te lo dejo el lunes",
    "Hoy no alcanzamos, pero el lunes sí",
    "Te lo dejo para el lunes",
])
async def test_NO_frena_cuando_el_bot_esta_NEGANDO_el_mismo_dia(texto):
    """🔴 La red frenaba la frase CORRECTA que el propio documento pone como ejemplo, obligando
    a reescribir un mensaje que estaba bien. Una red que corrige lo correcto se acaba apagando."""
    assert await _dias_imposibles(texto, CAL) == [], f"falso positivo: {texto!r}"


# ══════════════════════════════════════════════════════════════════════════════════
#  4 · "APROBADO" Y "VERIFICADO" PASABAN LA RED DEL PAGO
# ══════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("texto", [
    "Tu pago quedo confirmado",
    "Perfecto, el pago fue aprobado",
    "Tu pago quedo verificado, gracias",
    "Listo, acabamos de verificar tu pago",     # ← frase textual de la Conversación 2
    "Tu pago ya fue aprobado por el banco",
    "Ya verifique tu pago",
])
def test_no_puede_afirmar_que_el_pago_quedo_bueno(texto):
    """Solo se cazaba la forma en primera persona; el PARTICIPIO —como lo diría cualquiera—
    entraba entero. Y desde que el bot ESPERA el clic de la dueña, en la charla normal ya no hay
    ningún momento en que pueda saberlo."""
    assert _frase_prohibida(texto) is not None, f"se escapó: {texto!r}"


def test_pero_tras_el_clic_de_la_dueña_SI_puede_confirmar():
    """⚠️ Por esto van en EN_CHARLA y no en SIEMPRE: `notificar_cliente_pago` le ORDENA decirlo
    después de que ella aprueba. Meterlas en SIEMPRE mataría el mensaje correcto."""
    from app.agent.agent import frase_prohibida_siempre

    for legitimo in ("Tu pago quedo confirmado", "Perfecto, el pago fue aprobado"):
        assert frase_prohibida_siempre(legitimo) is None, (
            f"se frenaría el mensaje legítimo tras la aprobación: {legitimo!r}"
        )


# ══════════════════════════════════════════════════════════════════════════════════
#  5 · LA RED DE LA SALUD NO CAZABA A LOS CELÍACOS
# ══════════════════════════════════════════════════════════════════════════════════
#
# La red impide que el bot sentencie sobre el cuerpo de alguien sin abrir la ficha del producto.
# Funcionaba para "apto para diabéticos" (que sí es un campo de la BD) y **se le escapaban tres
# de cada cuatro formas reales** — justo las del celíaco, que es la mitad del público que declara
# el documento en su primera línea.
#
# ⚠️ Y una de las que pasaba es objetivamente FALSA en este catálogo: *"todo es libre de gluten,
# azúcar refinada y lácteos"* — hay Kéfir de Leche de cabra y Yogurt Kéfirado. Dicha a quien
# pregunta por una alergia, es la respuesta más cara que puede dar el sistema.

@pytest.mark.parametrize("respuesta", [
    "Si, el pan de sandwich es libre de gluten, puedes comerlo tranquila",
    "Claro, todas nuestras galletas son aptas para celiacos",   # el PLURAL no se cazaba
    "Todo es libre de gluten, azucar refinada y lacteos",        # y además es falso
    "Si eres celiaca puedes comer cualquiera de nuestros panes",
    "El pan keto es apto para diabeticos, comelo tranquila",
    "No lleva gluten, tranquila",
])
def test_no_puede_sentenciar_sobre_salud_sin_abrir_la_ficha(respuesta):
    from app.agent.agent import _dictamina_salud_sin_ficha

    assert _dictamina_salud_sin_ficha("soy celiaca, me sirve?", respuesta, False), (
        f"se escapó una sentencia sobre el cuerpo de alguien: {respuesta!r}"
    )


@pytest.mark.parametrize("respuesta", [
    "Eso te lo confirmo con seguridad antes de que compres",
    "Dejame verificar la ficha y te digo",
    "Eso lo tienes que ver con tu medico, yo no soy nutricionista",
])
def test_la_respuesta_HONESTA_no_se_frena(respuesta):
    """La red prefiere quedarse corta antes que frenar a un bot que está diciendo la verdad."""
    from app.agent.agent import _dictamina_salud_sin_ficha

    assert not _dictamina_salud_sin_ficha("soy celiaca, me sirve?", respuesta, False)


def test_con_la_ficha_consultada_la_red_no_se_mete():
    """Si el bot SÍ abrió la ficha, puede responder: el dato es real y viene de la BD."""
    from app.agent.agent import _dictamina_salud_sin_ficha

    assert not _dictamina_salud_sin_ficha(
        "soy celiaca, me sirve?", "Si, es libre de gluten", True
    )


def test_ningun_banco_se_queda_huerfano():
    """🔴 `probar_testigo` EXISTÍA y el vigilante no lo corría: la lista se escribe a mano y se
    quedó fuera. Pasaba en verde cuando alguien lo lanzaba suelto, pero nadie lo lanzaba.
    Es el mismo patrón del panel que estuvo 13 días sin desplegar: algo que se da por vigilado
    y que no mira nadie. Este test hace que el hueco no pueda repetirse en silencio."""
    import ast
    import pathlib

    raiz = pathlib.Path(__file__).resolve().parent.parent
    arbol = ast.parse((raiz / "scripts" / "correr_bancos.py").read_text(encoding="utf-8"))
    lista = []
    for n in ast.walk(arbol):
        if isinstance(n, ast.Assign) and any(getattr(x, "id", "") == "BANCOS" for x in n.targets):
            lista = [e.value for e in n.value.elts if isinstance(e, ast.Constant)]
    en_disco = {f.stem for f in (raiz / "scripts").glob("probar_*.py")}
    huerfanos = sorted(en_disco - set(lista))
    assert not huerfanos, (
        f"estos bancos existen pero el vigilante NO los corre: {huerfanos}. "
        "Un banco que nadie ejecuta no vigila nada."
    )


def test_el_banco_meta_limpia_su_telefono_interno_antes_de_reutilizar_el_wa_id():
    """🔴 Post-deploy 3-sep: `probar_meta` inserta `wamid.MEDIA2` dos veces dentro de la
    misma corrida, con `_limpiar()` entre ambas. `TEL_INTERNO` no estaba en `TODOS`, así que la
    primera fila sobrevivía y la segunda chocaba con `ux_mensajes_wa_id`. El banco era rojo por
    su propia basura aunque el producto estuviera sano.
    """
    import ast
    import pathlib

    raiz = pathlib.Path(__file__).resolve().parent.parent
    arbol = ast.parse(
        (raiz / "scripts" / "probar_meta.py").read_text(encoding="utf-8")
    )
    todos = next(
        n.value for n in ast.walk(arbol)
        if isinstance(n, ast.Assign)
        and any(getattr(t, "id", "") == "TODOS" for t in n.targets)
    )
    nombres = {e.id for e in todos.elts if isinstance(e, ast.Name)}
    assert "TEL_INTERNO" in nombres, (
        "_limpiar() vuelve a dejar wamid.MEDIA2 vivo entre los dos casos del banco Meta"
    )


# ══════════════════════════════════════════════════════════════════════════════════
#  6 · 🪦 EL RECIBO DUPLICADO — la red de la INSERCIÓN se quitó el 2026-08-24
# ══════════════════════════════════════════════════════════════════════════════════
#
# Aquí vivían los tests de `_asegurar_resumenes_exactos` / `_ya_dice_las_cifras`, quitadas por
# decisión de Erwin: presentar el recibo es tarea del MODELO (reglas 107-108 de _REGLAS). El
# dinero FALSO lo sigue frenando `_dinero_inventado` con sus propios tests; que las cifras
# APAREZCAN ya no lo garantiza nadie — a propósito: es la señal que mide si el modelo alcanza.
# Ver SESIONES.md 2026-08-24.
