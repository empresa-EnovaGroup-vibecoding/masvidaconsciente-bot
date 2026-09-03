"""EL CANDADO DEL PEDIDO DUPLICADO — "págame otra vez" después de pagar (2026-09-03).

EL CASO REAL, verificado en la BD de pruebas: pedido #2602 pagado y aprobado por la dueña →
el cliente responde "6pm" → el modelo REGISTRA el #2603 (idéntico, 22 minutos después) y le
genera el cobro: re-envía los datos de Binance a quien ACABA de pagar. Todos los candados del
cobro funcionaron — el hueco era que nada impedía fabricar un pedido nuevo idéntico, y un
pedido nuevo es legítimamente cobrable.

LA CURA: `registrar_pedido` se niega si el MISMO cliente tiene un pedido VIVO (ni cancelado ni
entregado) con EXACTAMENTE los mismos items, creado hace menos de 90 minutos. La salida
legítima ("quiero otra tanda igual", dicho con todas sus letras) es registrarla con `opciones`
distintas — la firma cambia y pasa.
"""
from datetime import timedelta

from app.agent.tools import _firma_de_items, _pedido_igual_reciente
from app.models import now_utc

ITEMS_2602 = [{
    "variante_id": 13, "cantidad": 2, "opciones": "queso de búfala",
    "producto": "Empanadas Horneadas", "presentacion": "4 unidades", "precio_unitario": 12.0,
}]
# Lo que el modelo mandaría al re-registrar (sin los campos derivados):
ITEMS_ENTRANTES = [{"variante_id": 13, "cantidad": 2, "opciones": "Queso de búfala"}]


class _PedidoFalso:
    def __init__(self, id_, estado, items, hace_min):
        self.id = id_
        self.estado = estado
        self.items = items
        self.created_at = now_utc() - timedelta(minutes=hace_min)


class _Res:
    def __init__(self, filas):
        self._filas = filas

    def scalars(self):
        return self

    def all(self):
        return self._filas


class _Sesion:
    """El doble devuelve lo que la consulta YA filtrada traería de la BD."""

    def __init__(self, filas):
        self._filas = filas

    async def execute(self, _q):
        return _Res(self._filas)


# ── 1) LA REVERSIÓN DEL CASO REAL: el #2603 no habría nacido ──
async def test_el_caso_2603_queda_bloqueado():
    """🔴 El caso literal del 3-sep: pedido pagado hace 22 min, mismos items (aunque el modelo
    escriba las opciones con otra mayúscula) → el candado lo encuentra y registrar se negará."""
    pagado = _PedidoFalso(2602, "pagado", ITEMS_2602, hace_min=22)
    repetido = await _pedido_igual_reciente(_Sesion([pagado]), "584247047595", ITEMS_ENTRANTES)
    assert repetido is not None and repetido.id == 2602


async def test_un_pedido_distinto_pasa_limpio():
    """Otra cantidad = otro pedido de verdad: el candado no puede frenar la venta que crece."""
    pagado = _PedidoFalso(2602, "pagado", ITEMS_2602, hace_min=22)
    otros = [{"variante_id": 13, "cantidad": 3, "opciones": "queso de búfala"}]
    assert await _pedido_igual_reciente(_Sesion([pagado]), "58424", otros) is None


async def test_la_salida_legitima_son_opciones_distintas():
    """'Quiero OTRA tanda igual' (con todas sus letras) → el modelo lo deja dicho en opciones,
    la firma cambia y el registro pasa. El candado frena olvidos, no ventas."""
    pagado = _PedidoFalso(2602, "pagado", ITEMS_2602, hace_min=10)
    segunda = [{"variante_id": 13, "cantidad": 2,
                "opciones": "queso de búfala — segunda tanda, pedida por el cliente"}]
    assert await _pedido_igual_reciente(_Sesion([pagado]), "58424", segunda) is None


async def test_items_ilegibles_no_activan_el_candado():
    """Con variante_id basura no hay firma que comparar: de eso se encargan las validaciones
    del código de barras, y este candado no puede taparlas devolviendo otro rechazo."""
    assert await _pedido_igual_reciente(
        _Sesion([]), "58424", [{"variante_id": "x", "cantidad": 2}]
    ) is None


async def test_si_la_bd_tose_se_deja_vender():
    """El lado seguro de ESTE candado es VENDER: un hipo de Postgres jamás frena la venta."""
    class _Rota:
        async def execute(self, _q):
            raise RuntimeError("bd caída")

    assert await _pedido_igual_reciente(_Rota(), "58424", ITEMS_ENTRANTES) is None


# ── 2) La firma: identidad sin orden ni mayúsculas, y sin campos derivados ──
def test_la_firma_ignora_orden_y_mayusculas_pero_no_cantidad():
    a = [{"variante_id": 1, "cantidad": 2, "opciones": "Pollo"},
         {"variante_id": 5, "cantidad": 1, "opciones": None}]
    b = [{"variante_id": 5, "cantidad": 1, "opciones": ""},
         {"variante_id": 1, "cantidad": 2, "opciones": "pollo "}]
    assert _firma_de_items(a) == _firma_de_items(b)
    c = [{"variante_id": 1, "cantidad": 3, "opciones": "pollo"},
         {"variante_id": 5, "cantidad": 1, "opciones": ""}]
    assert _firma_de_items(a) != _firma_de_items(c)


def test_la_firma_no_mira_los_campos_derivados():
    """`presentacion` y `precio_unitario` salen del variante_id: no son identidad."""
    assert _firma_de_items(ITEMS_2602) == _firma_de_items(ITEMS_ENTRANTES)


# ── 3) El cableado y la consulta (contrato en la fuente, patrón R50) ──
def test_registrar_pedido_consulta_el_candado_ANTES_de_construir_items():
    import inspect

    from app.agent import tools as tl

    fuente = inspect.getsource(tl.registrar_pedido)
    assert "_pedido_igual_reciente" in fuente, "el candado quedó sin cablear"
    assert fuente.index("_pedido_igual_reciente") < fuente.index("items_pedido = []"), (
        "el candado tiene que correr ANTES de construir el pedido"
    )


def test_la_consulta_excluye_cancelados_y_entregados_y_tiene_ventana():
    """Un cancelado se puede volver a pedir y un entregado se puede repetir mañana; y el
    candado solo mira lo RECIENTE (la ventana). Vive en la consulta: se fija en la fuente."""
    import inspect

    from app.agent import tools as tl

    fuente = inspect.getsource(tl._pedido_igual_reciente)
    assert '"cancelado", "entregado"' in fuente
    assert "_VENTANA_DUPLICADO_MIN" in fuente
