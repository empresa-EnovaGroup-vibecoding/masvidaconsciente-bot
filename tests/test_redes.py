"""LAS REDES DE SEGURIDAD, caso por caso, en el CI — ANTES de desplegar.

Cada una de estas redes nació de un incidente REAL con un cliente (están fechados en los
comentarios de `app/agent/agent.py`). Son la última pared entre el modelo y el dinero de la
dueña. Hasta hoy solo se comprobaban DESPUÉS de desplegar, dentro del contenedor.

⚠️ UNA SOLA FUENTE DE VERDAD: los casos NO se copian aquí — se IMPORTAN de
`scripts/probar_honestidad.py`, que sigue siendo el banco que corre post-deploy. Si alguien
añade un caso al banco, este test lo recoge solo. Duplicarlos sería garantizar que un día
divergen y que el CI diga "verde" sobre una red que ya no se prueba.

Lo que se gana con pytest sobre el banco: cada caso es un test con nombre. Cuando uno falla,
dice EXACTAMENTE qué frase y qué red — en vez de "🔴 3 CASO(S) MAL".
"""

import pytest

from app.agent.agent import (
    _afirma_envio_fotos,
    _afirma_pedido_registrado,
    _frase_prohibida,
    _promete_averiguar,
    _suena_a_sistema,
)
from scripts.probar_honestidad import (
    FOTOS_FANTASMA,
    PEDIDO_FANTASMA,
    PROHIBIDAS,
    PROMESAS,
    SISTEMA,
)


@pytest.mark.parametrize(("texto", "debe_avisar"), PROMESAS)
def test_red_del_relevo(texto: str, debe_avisar: bool):
    """Si el bot PROMETE averiguar algo, hay que avisarle a la dueña.

    Sin esto, el cliente espera para siempre una respuesta que nadie va a dar.
    """
    assert _promete_averiguar(texto) is debe_avisar


@pytest.mark.parametrize(("texto", "debe_bloquear"), PROHIBIDAS)
def test_red_de_la_honestidad(texto: str, debe_bloquear: bool):
    """Frases que NO pueden salir jamás: el bot no tiene banco, no es una persona,
    no es médica."""
    assert (_frase_prohibida(texto) is not None) is debe_bloquear


@pytest.mark.parametrize(("texto", "debe_reescribir"), SISTEMA)
def test_red_de_la_voz(texto: str, debe_reescribir: bool):
    """Una vendedora de verdad no habla de "lo que tiene cargado"."""
    assert _suena_a_sistema(texto) is debe_reescribir


@pytest.mark.parametrize(("texto", "debe_frenar"), PEDIDO_FANTASMA)
def test_red_del_pedido_fantasma(texto: str, debe_frenar: bool):
    """No digas que lo agendaste si NO lo agendaste.

    Frenar de MENOS deja al cliente creyendo que tiene pedido y a la dueña sin nada que
    cocinar. Frenar de MÁS rompe la venta. Las dos mitades se prueban aquí.
    """
    assert _afirma_pedido_registrado(texto) is debe_frenar


@pytest.mark.parametrize(("texto", "pidio_fotos", "debe_frenar"), FOTOS_FANTASMA)
def test_red_del_envio_fantasma_de_fotos(texto: str, pidio_fotos: bool, debe_frenar: bool):
    """No digas que mandaste las fotos si NO las mandaste.

    La trampa: "ya te LA envié" no trae la palabra "foto" — el «la» viene del mensaje del
    cliente. Por eso la red mira TAMBIÉN qué pidió él.
    """
    assert _afirma_envio_fotos(texto, pidio_fotos) is debe_frenar


def test_las_cinco_redes_siguen_importandose_con_su_nombre():
    """Cinco bancos importan estas funciones POR NOMBRE (`probar_carril_dinero`,
    `probar_datos_bancarios`, `probar_honestidad`, `ensayo_retomar`, `validar_agente`).

    Renombrarlas o cambiarles la firma deja esos bancos importando un fantasma. Este test
    es el candado: si alguien las renombra en un refactor, esto se pone rojo en el CI en vez
    de descubrirse en producción.
    """
    for red in (
        _promete_averiguar,
        _frase_prohibida,
        _suena_a_sistema,
        _afirma_pedido_registrado,
        _afirma_envio_fotos,
    ):
        assert callable(red)
