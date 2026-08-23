"""LOS CUATRO HUECOS QUE ENCONTRÓ LA AUDITORÍA EXHAUSTIVA (2026-08-22).

Se releyó la plantilla de negocio entera y se extrajeron **200 requisitos** (una revisión previa
a mano había sacado 51 — ahí estaba el hueco), cada uno verificado ejecutando código dentro del
contenedor. Tres de los cuatro fallos que siguen aquí eran **de arreglos hechos ese mismo día**:
código nuevo, con tests en verde, que no hacía lo que decía hacer.

Es la lección del proyecto un nivel más arriba: *el código impide… si el código mira lo correcto*.
"""

import pytest

from app.agent.agent import _dias_imposibles, _frase_prohibida, _sin_ficha_repetida
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
#  2 · LA FICHA REPETIDA — el arreglo del día que no arreglaba su propio caso
# ══════════════════════════════════════════════════════════════════════════════════

FICHA = "Son versión mini de las galletas New York, duran 2 semanas y son aptas para diabéticos"


def test_el_caso_LITERAL_de_la_base_de_datos():
    """🔴 Este test es la razón del fichero. La primera versión de la red comparaba frases
    COMPLETAS, y la misma ficha venía con distinto prefijo cada vez — así que no quitaba nada.
    Se desplegó con el mensaje "ahora en CÓDIGO" y la queja de Maired habría seguido igual.
    Estos son los textos exactos de los mensajes 5858 y 5864."""
    historial = [{"role": "assistant", "content":
                  "Listo, 1 paquete de Mini New York, son versión mini de las galletas New York, "
                  "duran 2 semanas y son aptas para diabéticos."}]
    nuevo = f"Perfecto, te las dejo para el lunes. {FICHA}."
    salida = _sin_ficha_repetida(nuevo, historial)
    assert "duran 2 semanas" not in salida, "la ficha se repitió por segunda vez"
    assert "te las dejo para el lunes" in salida, "se llevó lo que sí era nuevo"


def test_el_recibo_NO_se_mutila():
    """🔴 El otro defecto del mismo arreglo: al recibo repetido le quitaba la línea
    `Entrega: lunes 24 de agosto`. Un recibo con las cifras sueltas y sin la fecha es peor que
    un recibo repetido."""
    recibo = ("Mini New York x1 = $14\nEnvío a Barquisimeto centro = $3\n"
              "Total: $17\nEntrega: lunes 24 de agosto")
    salida = _sin_ficha_repetida(recibo + "\nMe confirmas?", [{"role": "assistant", "content": recibo}])
    for imprescindible in ("Total:", "Entrega: lunes 24", "$14", "$3"):
        assert imprescindible in salida, f"mutiló el recibo: falta {imprescindible!r}"


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
