"""EL MÉTODO DE PAGO TIENE SU CASILLA (rama B del plan "que no repregunte", migración 035).

🔴 EL PORQUÉ (confirmado EN VIVO por Maired, 31-ago-2026): "vuelve a preguntar los métodos de
pago cuando ya mandó los datos". La causa era estructural: la elección del cliente ("te pago
por Zelle") no se guardaba en NINGUNA parte —`Pago.metodo` nace recién con el comprobante— y
`generar_datos_pago` (schema: solo `pedido_id`) devolvía SIEMPRE los datos de TODOS los métodos
y un `resumen_cobro` re-pitcheando las DOS monedas, con la nota ordenando copiarlo EXACTO.
Reapertura mandada por la propia herramienta.

Lo que estos tests fijan:
  1. El matcher es VOCABULARIO CERRADO: se elige una fila de `metodos_pago`, jamás se escribe
     un dato. Ambiguo real ⇒ candidatos para preguntar; sin calce ⇒ nada (quien llama enseña
     la lista completa). Nunca se adivina.
  2. La moneda sale del TIPO del método (el vocabulario del panel), no de parsear texto.
  3. La elección viaja al prompt como HECHO (`_estado_cliente_texto`) — y si eligió dólares,
     la cifra en Bs se CALLA (recitarla sería el mismo re-pitch que esta rama mata).
  4. Los contratos de fuente: la persistencia existe, el paso sin elección entrega NOMBRES
     (no datos), y la cotización se sigue guardando COMPLETA (el validador del comprobante
     valida por MONTO y no se tocó).

El flujo con BD real (dos pasos, la casilla gana en la re-llamada, el método inválido no toca
la BD) lo cubre el banco `scripts/probar_datos_bancarios.py`, sección 5.
"""
import inspect
from decimal import Decimal
from types import SimpleNamespace

import app.agent.system_prompt as sp
from app.agent import tools
from app.agent.hoja import _renderizar
from app.agent.tools import (
    _MONEDA_POR_TIPO,
    _PARAMS_DECLARADOS,
    _matchear_metodo,
    _tipo_canonico,
)


def _metodo(id=1, tipo="pago_movil", titulo="Pago Móvil"):
    return SimpleNamespace(id=id, tipo=tipo, titulo=titulo)


_METODOS = [
    _metodo(1, "pago_movil", "Pago Móvil"),
    _metodo(2, "zelle", "Zelle"),
    _metodo(3, "binance", "Binance"),
    _metodo(4, "efectivo", "Efectivo"),
]


# ══════════════════════════════════════════════════════════════════════════════════
#  1. EL MATCHER — vocabulario cerrado, jamás adivinar
# ══════════════════════════════════════════════════════════════════════════════════

def test_titulo_exacto_gana():
    m, candidatos = _matchear_metodo("Zelle", _METODOS)
    assert m is _METODOS[1]
    assert candidatos == []


def test_acentos_y_mayusculas_no_estorban():
    """El modelo escribe 'pago movil' (sin acento, minúsculas) y la fila dice 'Pago Móvil'."""
    m, _ = _matchear_metodo("pago movil", _METODOS)
    assert m is _METODOS[0]


def test_una_palabra_del_titulo_basta_si_es_inequivoca():
    m, _ = _matchear_metodo("movil", _METODOS)
    assert m is _METODOS[0]


def test_transferencia_calza_con_el_tipo_banco():
    """'te hago transferencia' es como el cliente nombra la cuenta bancaria. Es un sinónimo
    FIJO del TIPO 'banco' (vocabulario del panel), no un NLU sobre lenguaje libre."""
    metodos = [*_METODOS, _metodo(5, "banco", "Banesco")]
    m, _ = _matchear_metodo("transferencia", metodos)
    assert m is metodos[4]


def test_ambiguo_real_devuelve_los_candidatos_para_preguntar():
    """Dos cuentas de banco: 'transferencia' no puede elegir sola — se pregunta, no se adivina
    (la doctrina de _buscar_producto, que costó el bug de las Empanadas)."""
    metodos = [*_METODOS, _metodo(5, "banco", "Banesco"), _metodo(6, "banco", "Mercantil")]
    m, candidatos = _matchear_metodo("transferencia", metodos)
    assert m is None
    assert set(candidatos) == {"Banesco", "Mercantil"}


def test_lo_que_no_calza_no_se_inventa():
    m, candidatos = _matchear_metodo("cheque", _METODOS)
    assert m is None
    assert candidatos == []


def test_dolares_pregunta_afinando_entre_las_tres_vias():
    """(Lo pidió Maired, 31-ago: 'voy a pagar en dólares' también es información.) 'dólares'
    calza con Zelle, Binance Y Efectivo a la vez: si el matcher lo resolviera solo estaría
    adivinando la vía, así que salen los TRES como candidatos — el bot pregunta '¿efectivo,
    Zelle o Binance?' en vez de recitar la lista entera. Jamás un match único."""
    m, candidatos = _matchear_metodo("dolares", _METODOS)
    assert m is None
    assert set(candidatos) == {"Zelle", "Binance", "Efectivo"}
    m, candidatos = _matchear_metodo("divisas", _METODOS)
    assert m is None
    assert set(candidatos) == {"Zelle", "Binance", "Efectivo"}


def test_bolivares_pregunta_afinando_entre_los_metodos_en_bs():
    """'voy a pagar en bolívares' con Pago Móvil Y transferencia activas: candidatos SOLO los
    de esa moneda, para afinar la pregunta."""
    metodos = [*_METODOS, _metodo(5, "banco", "Transferencia Banesco")]
    m, candidatos = _matchear_metodo("bolivares", metodos)
    assert m is None
    assert set(candidatos) == {"Pago Móvil", "Transferencia Banesco"}


def test_bolivares_con_un_solo_metodo_en_bs_calza_directo():
    """Si el negocio solo cobra Bs por Pago Móvil, 'en bolívares' YA es la elección: se guarda
    sin repreguntar (no hay nada que afinar)."""
    m, candidatos = _matchear_metodo("bolivares", _METODOS)
    assert m is _METODOS[0]
    assert candidatos == []


def test_dolares_fisicos_es_el_efectivo():
    """El sinónimo COMPLETO ('dólares físicos', como lo dice Maired) gana en Efectivo y no se
    desparrama a Zelle/Binance por contener la palabra 'dólares'."""
    m, candidatos = _matchear_metodo("dolares fisicos", _METODOS)
    assert m is _METODOS[3]
    assert candidatos == []


# ══════════════════════════════════════════════════════════════════════════════════
#  2. LA MONEDA SALE DEL TIPO (la regla del 24-ago: el descuento se ata a la MONEDA)
# ══════════════════════════════════════════════════════════════════════════════════

def test_la_moneda_de_cada_tipo():
    def moneda(tipo):
        return _MONEDA_POR_TIPO.get(_tipo_canonico(tipo))

    assert moneda("pago_movil") == "bs"
    assert moneda("banco") == "bs"
    assert moneda("zelle") == "usd"
    assert moneda("binance") == "usd"
    assert moneda("efectivo") == "usd"
    # 'otro' NO está a propósito: moneda desconocida ⇒ cobro completo, sin adivinar.
    assert moneda("otro") is None


def test_el_tipo_se_entiende_como_lo_escriba_la_tabla_real():
    """🔴 La lección de la primera corrida del banco en el VPS (31-ago): en el taller el tipo
    está cargado 'Zelle' (mayúscula), no 'zelle' como dice la migración 009. La moneda tiene
    que salir igual, se escriba como se escriba — si no, el bot re-pitchearía las dos monedas
    exactamente a quien ya eligió."""
    for crudo, canonico in [
        ("Zelle", "zelle"),            # así está en el taller, verificado por el banco
        ("zelle", "zelle"),
        ("pago_movil", "pago movil"),  # el canónico de la migración 009
        ("Pago Móvil", "pago movil"),  # por si el panel lo cargó como título
        ("PAGO-MOVIL", "pago movil"),
        ("  Efectivo ", "efectivo"),
        (None, ""),
    ]:
        assert _tipo_canonico(crudo) == canonico
    assert _MONEDA_POR_TIPO.get(_tipo_canonico("Zelle")) == "usd"
    assert _MONEDA_POR_TIPO.get(_tipo_canonico("Pago Móvil")) == "bs"


# ══════════════════════════════════════════════════════════════════════════════════
#  3. EL SCHEMA DECLARA `metodo` — sin esto, `_solo_lo_declarado` lo DESCARTARÍA
# ══════════════════════════════════════════════════════════════════════════════════

def test_metodo_esta_declarado_en_el_schema():
    """El filtro de args del LLM (auditoría 21-ago) recorta a lo que el schema declara: si
    `metodo` no estuviera declarado, el modelo lo mandaría y el código nunca lo vería."""
    assert "metodo" in _PARAMS_DECLARADOS["generar_datos_pago"]
    assert "pedido_id" in _PARAMS_DECLARADOS["generar_datos_pago"]


# ══════════════════════════════════════════════════════════════════════════════════
#  4. LA ELECCIÓN VIAJA AL PROMPT COMO HECHO (_estado_cliente_texto)
# ══════════════════════════════════════════════════════════════════════════════════

class _Resultado:
    def __init__(self, filas):
        self._filas = filas

    def scalars(self):
        return self

    def all(self):
        return self._filas


class _Sesion:
    def __init__(self, filas):
        self._filas = filas

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def execute(self, *_a, **_k):
        return _Resultado(self._filas)


def _con_pedidos(monkeypatch, pedidos):
    monkeypatch.setattr(sp, "get_session_factory", lambda: (lambda: _Sesion(pedidos)))


def _pedido(**kw):
    base = dict(
        id=1918,
        estado="esperando_pago",
        cotizado_bs=Decimal("7846.63"),
        cotizado_usd=None,
        cotizado_usd_divisas=None,
        tasa_cotizada=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


async def test_eligio_zelle_y_el_estado_lo_dice_sin_recitar_los_bolivares(monkeypatch):
    """Eligió dólares: la línea 'Ya ELIGIÓ' sale con la orden de llamar con SU metodo, y la
    cifra en Bs SE CALLA — recitarle 'por Pago Móvil son X Bs' a quien eligió Zelle es el
    re-pitch de las dos monedas que la rama B vino a matar. El tipo congelado va 'Zelle' con
    mayúscula A PROPÓSITO: es como está cargado en la tabla real del taller."""
    _con_pedidos(monkeypatch, [_pedido(metodo_elegido="Zelle", metodo_elegido_tipo="Zelle")])
    texto = await sp._estado_cliente_texto("584140000000")
    assert "Ya ELIGIÓ cómo pagar: Zelle" in texto
    assert "metodo='Zelle'" in texto
    assert "7.846,63" not in texto
    assert "YA ESTÁ COTIZADO" not in texto
    assert "CAMBIA de método, vale lo nuevo" in texto  # cambiar sigue permitido


async def test_eligio_pago_movil_y_los_bolivares_se_quedan(monkeypatch):
    """Eligió bolívares: SU cifra (cotizado_bs) sigue delante para copiarla — es su moneda,
    no una reapertura — y además el estado dice que la elección ya está hecha."""
    _con_pedidos(
        monkeypatch,
        [_pedido(metodo_elegido="Pago Móvil", metodo_elegido_tipo="pago_movil")],
    )
    texto = await sp._estado_cliente_texto("584140000000")
    assert "Ya ELIGIÓ cómo pagar: Pago Móvil" in texto
    assert "7.846,63 Bs" in texto
    assert "YA ESTÁ COTIZADO" in texto


async def test_sin_eleccion_no_hay_linea_nueva(monkeypatch):
    """Sin casilla llena, el bloque queda EXACTAMENTE como antes de la rama B (los tres tests
    de test_estado_cliente_cotizado fijan el resto)."""
    _con_pedidos(monkeypatch, [_pedido()])
    texto = await sp._estado_cliente_texto("584140000000")
    assert "Ya ELIGIÓ" not in texto
    assert "7.846,63 Bs" in texto  # la cifra de siempre sigue


# ══════════════════════════════════════════════════════════════════════════════════
#  5. LOS CONTRATOS DE FUENTE — lo que no se puede romper sin que esto lo vea
# ══════════════════════════════════════════════════════════════════════════════════

def test_la_eleccion_se_persiste_en_el_pedido():
    fuente = inspect.getsource(tools.generar_datos_pago)
    assert "pedido.metodo_elegido = elegido.titulo" in fuente
    assert "pedido.metodo_elegido_tipo = elegido.tipo" in fuente


def test_el_paso_sin_eleccion_entrega_nombres_no_datos():
    fuente = inspect.getsource(tools.generar_datos_pago)
    assert '"metodos_disponibles"' in fuente


def test_la_cotizacion_se_sigue_guardando_completa():
    """El validador del comprobante compara por MONTO contra `cotizado_*` y NO se tocó: las
    cinco casillas de la 027 se siguen escribiendo, elija el cliente lo que elija."""
    fuente = inspect.getsource(tools.generar_datos_pago)
    for linea in (
        "pedido.cotizado_bs = monto_bs",
        "pedido.cotizado_usd = monto_usd",
        "pedido.cotizado_usd_divisas = monto_usd_divisas",
        "pedido.tasa_cotizada = tasa",
        "pedido.cotizado_at = now_utc()",
    ):
        assert linea in fuente


# ══════════════════════════════════════════════════════════════════════════════════
#  6. LA HOJA (modo DOS): el paso sin elección le da NOMBRES a la Voz, no datos
# ══════════════════════════════════════════════════════════════════════════════════

def test_la_hoja_renderiza_los_nombres_del_paso_uno():
    bloque = _renderizar(
        "generar_datos_pago",
        {
            "ok": True,
            "resumen_cobro": "Por Pago Móvil o transferencia son 7.846,63 Bs…",
            "metodos_disponibles": ["Pago Móvil", "Zelle", "Efectivo"],
        },
    )
    assert "Pago Móvil · Zelle · Efectivo" in bloque
    assert "pregúntale cuál prefiere" in bloque
    # y sin la elección NO hay bloque de "datos de pago que sí le puedes dar"
    assert "cópialos TAL CUAL" not in bloque
