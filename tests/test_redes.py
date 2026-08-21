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
    _dinero_inventado,
    _frase_prohibida,
    _lecturas_del_monto,
    _promete_averiguar,
    _suena_a_sistema,
    autorizados_por_moneda,
)
from app.agent.tools import _PARAMS_DECLARADOS
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


# ══════════════════════════════════════════════════════════════════════════════════
# 🔴 LA BANDA CIEGA DEL 1% — cerrada el 2026-08-21
#
# `_lecturas_del_monto` daba LAS DOS lecturas de "10.00" ({10, 1000}) por creerlo ambiguo. No lo
# es: el separador de miles lleva SIEMPRE tres dígitos. Y como `autorizados_por_moneda` construye
# la lista blanca con esta misma función a partir de lo que devuelven las HERRAMIENTAS, un total
# de $10.00 autorizaba el 1000 — y con el 1% de `_calza`, toda la banda 990–1010. Medido antes de
# arreglarlo: el bot podía escribir "$1000" sobre un pedido de $10 y ninguna red lo frenaba.
# ══════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(("crudo", "esperado"), [
    # DECIMAL inequívoco: 1 o 2 dígitos detrás del separador ⇒ UNA sola lectura
    ("10.00", {10.0}),
    ("22,40", {22.4}),
    ("0.50", {0.5}),
    ("1,5", {1.5}),
    ("45.50", {45.5}),
    # MILES inequívoco: tres dígitos ⇒ UNA sola lectura (no se toca)
    ("5.000", {5000.0}),
    ("5,000", {5000.0}),
    ("1.234", {1234.0}),
    # Los dos separadores: manda el último (no se toca)
    ("31.936,21", {31936.21}),
    ("16.591,05", {16591.05}),
    # Sin separador
    ("12", {12.0}),
])
def test_las_lecturas_de_un_monto_no_regalan_un_x100(crudo, esperado):
    assert _lecturas_del_monto(crudo) == esperado


def test_un_total_de_10_NO_autoriza_cobrar_1000():
    """El caso que se reprodujo contra el contenedor antes de arreglarlo."""
    usd_ok, bs_ok = autorizados_por_moneda("Total: $10.00")
    assert usd_ok == {10.0}, f"la lista blanca traía un x100: {sorted(usd_ok)}"
    for inventado in ("Son $1000", "Son $995", "Son $1010"):
        assert _dinero_inventado(inventado, usd_ok, bs_ok), f"{inventado} pasó la red del dinero"


def test_y_el_monto_BUENO_sigue_pasando():
    """El control que importa: estrechar la banda no puede matar una venta buena. Frenar de más
    es tan malo como frenar de menos (L5)."""
    usd_ok, bs_ok = autorizados_por_moneda("Total: $10.00")
    for bueno in ("Son $10", "Son $10.00", "Te quedan $10 en total"):
        assert not _dinero_inventado(bueno, usd_ok, bs_ok), f"{bueno} se frenó y NO debía"


def test_los_bolivares_con_formato_venezolano_siguen_pasando():
    """El carril que más formatos raros tiene: Bs 16.591,05 a la tasa del día."""
    usd_ok, bs_ok = autorizados_por_moneda("Total: $23.00", "Son Bs 16.591,05")
    assert not _dinero_inventado("El total es Bs 16.591,05", usd_ok, bs_ok)
    assert not _dinero_inventado("Son 16591,05 Bs", usd_ok, bs_ok)
    # y un bolívar inventado sigue cazándose
    assert _dinero_inventado("Son Bs 99.999,00", usd_ok, bs_ok)


@pytest.mark.parametrize(("crudo", "esperado"), [
    # 🔴 R26 pidió estos: el tope de DOS dígitos solo cambia algo cuando hay 4+ cifras ANTES del
    # separador (con 1-3, `_MILES_RE` ya decide antes y nunca se llega a `_DECIMAL_RE`). Aquí sí
    # hay duda de verdad —"1234.567" puede ser un Bs 1.234.567 mal escrito— y las dos lecturas
    # tienen que sobrevivir: estrechar la banda no puede llegar a DAR POR BUENO un x1000.
    ("1234.567", {1234.57, 1234567.0}),
    ("12345.678", {12345.68, 12345678.0}),
    # y con 2 dígitos detrás sigue siendo decimal aunque haya 4 cifras delante
    ("1234.56", {1234.56}),
    # 3 cifras delante + 3 detrás = patrón de miles, no decimal
    ("999.999", {999999.0}),
])
def test_la_duda_de_verdad_conserva_las_dos_lecturas(crudo, esperado):
    assert _lecturas_del_monto(crudo) == esperado


# ══════════════════════════════════════════════════════════════════════════════════
# 🔴 LOS ARGS DEL LLM SE RECORTAN AL SCHEMA (auditoría 2026-08-21)
#
# `ejecutar_tool` hacía `fn(session, telefono, **args)` con los args tal cual los manda el modelo,
# y ningún schema lleva `additionalProperties: false` (0 de 12). Comparando firma contra schema,
# solo `registrar_comprobante` aceptaba parámetros no declarados — entre ellos `monto_leido`, el
# monto que la VISIÓN leyó del comprobante: el modelo podía fabricarlo.
# ══════════════════════════════════════════════════════════════════════════════════

def test_ninguna_tool_acepta_parametros_que_su_schema_no_declara():
    """La red de verdad: recorre las 12 tools y comprueba que lo que el modelo puede pasar es
    exactamente lo que el schema enseña. Si mañana alguien añade un parámetro a una función y se
    olvida del schema, este test lo caza."""
    import inspect

    from app.agent.tools import _DISPATCH, _solo_lo_declarado

    for nombre, fn in _DISPATCH.items():
        acepta = {
            p for p, v in inspect.signature(fn).parameters.items()
            if v.kind in (v.POSITIONAL_OR_KEYWORD, v.KEYWORD_ONLY)
        } - {"session", "telefono"}
        # lo que el modelo lograría colar si mandara TODOS los parámetros de la firma
        colados = set(_solo_lo_declarado(nombre, dict.fromkeys(acepta, "x")))
        no_declarados = acepta - set(_PARAMS_DECLARADOS.get(nombre, set()))
        assert not (colados & no_declarados), (
            f"{nombre}: el modelo puede colar {sorted(colados & no_declarados)}"
        )


def test_el_modelo_NO_puede_fabricar_el_monto_leido_de_un_comprobante():
    """El caso concreto del dinero: `monto_leido` lo pone la VISIÓN, no el modelo."""
    from app.agent.tools import _solo_lo_declarado

    sucio = {"pedido_id": 7, "monto_leido": 9999.0, "avisar": False,
             "comprobante_url": "http://malo", "comprobante_media_id": "x"}
    limpio = _solo_lo_declarado("registrar_comprobante", sucio)
    assert "monto_leido" not in limpio
    assert "avisar" not in limpio
    assert "comprobante_url" not in limpio


def test_los_parametros_BUENOS_siguen_pasando():
    """El control: recortar no puede romper la llamada normal (frenar de más es tan malo como
    frenar de menos)."""
    from app.agent.tools import _solo_lo_declarado

    assert _solo_lo_declarado("registrar_pedido", {"items": [1], "entrega_fecha": "2026-09-01"}) == {
        "items": [1], "entrega_fecha": "2026-09-01"
    }
    assert _solo_lo_declarado("info_producto", {"nombre": "Quesillo"}) == {"nombre": "Quesillo"}
    # una tool sin schema conocido no se recorta (no hay contra qué)
    assert _solo_lo_declarado("__inexistente__", {"lo": "que", "sea": 1}) == {"lo": "que", "sea": 1}


@pytest.mark.asyncio
async def test_ejecutar_tool_USA_el_filtro_de_verdad():
    """🔴 R29 lo pidió: los tests de arriba prueban `_solo_lo_declarado` en AISLAMIENTO, así que
    quitar la llamada de `ejecutar_tool` dejaba la suite en verde y el agujero abierto. Aquí se
    llama a la puerta real con args sucios y se mira qué recibió la función."""
    from app.agent import tools as tl

    recibido = {}

    async def _falsa(session, telefono, **kw):   # noqa: ARG001
        recibido.update(kw)
        return {"ok": True}

    class _S:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return False

    original = tl._DISPATCH.get("registrar_comprobante")
    tl._DISPATCH["registrar_comprobante"] = _falsa
    try:
        await tl.ejecutar_tool(
            "registrar_comprobante",
            {"referencia": "0012", "monto_leido": 9999.0, "avisar": False},
            "584000",
            session_factory=lambda: _S(),
        )
    finally:
        tl._DISPATCH["registrar_comprobante"] = original

    assert "monto_leido" not in recibido, "el modelo colÓ monto_leido por la puerta real"
    assert "avisar" not in recibido
    # `referencia` es el ÚNICO parámetro que el schema de esta tool declara (lo enseñó este
    # mismo test al fallar: el filtro descartó un `pedido_id` que la firma ni tiene).
    assert recibido.get("referencia") == "0012", "y el parámetro bueno sí tiene que llegar"
