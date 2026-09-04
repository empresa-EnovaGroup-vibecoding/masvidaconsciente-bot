"""EL CANDADO DEL PEDIDO DUPLICADO v2 — "págame otra vez" después de pagar (2026-09-03).

EL CASO REAL (BD de pruebas): pedido #2602 pagado y aprobado → el cliente responde "6pm" →
el modelo REGISTRA el #2603 idéntico 22 minutos después y le genera el cobro. Todos los
candados del cobro funcionaron; el hueco era que nada impedía fabricar el pedido nuevo.

LA V2 nace de la CACERÍA ADVERSARIAL del mismo día (22 hallazgos confirmados) que rompió la v1:
  · C12/C18 (crítico): la v1 bloqueaba el flujo LEGÍTIMO de re-registrar los mismos items para
    AGREGAR la zona/fecha a un pedido abierto sin pago → ahora los abiertos SIN PAGO quedan
    FUERA del candado (de ellos se encarga la reutilización del pedido abierto).
  · C2/C13: las `opciones` en la firma dejaban pasar la paráfrasis ('queso de búfala' vs
    'relleno de queso de búfala') → la firma ya NO mira opciones.
  · C15: partir la línea ([2x1] vs [1x2]) burlaba la firma → ahora ACUMULA cantidades.
  · C1/C14/C22: la ventana anclada a created_at dejaba pasar el flujo normal "cotizar hoy,
    pagar mañana, coordinar en la noche" → ahora se ancla a la ÚLTIMA ACTIVIDAD DE DINERO
    (el pago vivo más nuevo, o la creación) con ventana de 24 h.
  · C16: la nota enseñaba el bypass ("dilo en opciones") → la salida ahora es `pedir_ayuda`
    (la dueña decide), no un truco de texto.
  · C4/C17: items=[] podía VACIAR un pedido abierto → rechazo explícito antes de tocar nada.
"""
from datetime import timedelta

from app.agent.tools import _firma_de_items, _pedido_igual_reciente, registrar_pedido
from app.models import now_utc

ITEMS_2602 = [{
    "variante_id": 13, "cantidad": 2, "opciones": "queso de búfala",
    "producto": "Empanadas Horneadas", "presentacion": "4 unidades", "precio_unitario": 12.0,
}]


class _PedidoFalso:
    def __init__(self, id_, estado, items, creado_hace_min):
        self.id = id_
        self.estado = estado
        self.items = items
        self.created_at = now_utc() - timedelta(minutes=creado_hace_min)


class _ResPedidos:
    def __init__(self, filas):
        self._filas = filas

    def scalars(self):
        return self

    def all(self):
        return self._filas


class _ResPagos:
    def __init__(self, filas):
        self._filas = filas

    def all(self):
        return self._filas


class _Sesion:
    """Contesta EN ORDEN: 1º la consulta de pedidos, 2º la de pagos vivos.
    `pagos` = lista de (pedido_id, hace_cuantos_minutos)."""

    def __init__(self, pedidos, pagos=()):
        self._resultados = [
            _ResPedidos(list(pedidos)),
            _ResPagos([(pid, now_utc() - timedelta(minutes=m)) for pid, m in pagos]),
        ]

    async def execute(self, _q):
        return self._resultados.pop(0)


# ══ 1) LA REVERSIÓN DEL CASO REAL — y sus variantes que rompieron la v1 ══

async def test_el_caso_2603_queda_bloqueado():
    """🔴 El literal del 3-sep: pedido pagado (pago confirmado hace 22 min), mismos items."""
    pagado = _PedidoFalso(2602, "pagado", ITEMS_2602, creado_hace_min=45)
    entrantes = [{"variante_id": 13, "cantidad": 2, "opciones": "Queso de búfala"}]
    r = await _pedido_igual_reciente(_Sesion([pagado], pagos=[(2602, 22)]), "58424", entrantes)
    assert r is not None and r.id == 2602


async def test_C13_la_parafrasis_de_opciones_ya_no_escapa():
    """La v1 caía aquí: el modelo redacta las opciones distinto al reconstruir del chat."""
    pagado = _PedidoFalso(2602, "pagado", ITEMS_2602, creado_hace_min=30)
    entrantes = [{"variante_id": 13, "cantidad": 2, "opciones": "relleno de queso de bufala, entrega 6pm"}]
    r = await _pedido_igual_reciente(_Sesion([pagado], pagos=[(2602, 10)]), "58424", entrantes)
    assert r is not None


async def test_C15_partir_la_linea_ya_no_escapa():
    """[13 x2] contra [13 x1, 13 x1]: misma compra, otra partición — la firma acumula."""
    pagado = _PedidoFalso(2602, "pagado", ITEMS_2602, creado_hace_min=30)
    entrantes = [{"variante_id": 13, "cantidad": 1}, {"variante_id": 13, "cantidad": 1}]
    r = await _pedido_igual_reciente(_Sesion([pagado], pagos=[(2602, 10)]), "58424", entrantes)
    assert r is not None


async def test_C14_el_flujo_lento_tambien_queda_cubierto():
    """Pedido CREADO hace 5 horas (fuera de la ventana v1 de 90 min) pero PAGADO hace 40 min:
    la ventana se ancla a la actividad del dinero, no a la creación."""
    pagado = _PedidoFalso(2602, "pagado", ITEMS_2602, creado_hace_min=300)
    entrantes = [{"variante_id": 13, "cantidad": 2}]
    r = await _pedido_igual_reciente(_Sesion([pagado], pagos=[(2602, 40)]), "58424", entrantes)
    assert r is not None


async def test_C12_el_flujo_legitimo_de_agregar_zona_YA_NO_se_bloquea():
    """🔴 EL CRÍTICO contra la v1: pedido 'pendiente' SIN pago, el modelo re-registra los mismos
    items para agregarle la zona (como ordenan las notas de la caja). El candado NO puede
    dispararse: de ese caso se encarga la reutilización del pedido abierto."""
    pendiente = _PedidoFalso(2610, "pendiente", ITEMS_2602, creado_hace_min=5)
    entrantes = [{"variante_id": 13, "cantidad": 2, "opciones": "queso de búfala"}]
    assert await _pedido_igual_reciente(_Sesion([pendiente], pagos=[]), "58424", entrantes) is None


async def test_un_abierto_CON_pago_reportado_si_bloquea():
    """esperando_pago con comprobante encima = dinero en curso: duplicarlo es el mismo peligro."""
    esperando = _PedidoFalso(2611, "esperando_pago", ITEMS_2602, creado_hace_min=15)
    entrantes = [{"variante_id": 13, "cantidad": 2}]
    r = await _pedido_igual_reciente(_Sesion([esperando], pagos=[(2611, 3)]), "58424", entrantes)
    assert r is not None


async def test_la_recompra_tardia_pasa():
    """Pagado AYER (pago hace 30 h): fuera de la ventana — una recompra igual mañana es legítima."""
    pagado = _PedidoFalso(2602, "pagado", ITEMS_2602, creado_hace_min=48 * 60)
    entrantes = [{"variante_id": 13, "cantidad": 2}]
    assert await _pedido_igual_reciente(
        _Sesion([pagado], pagos=[(2602, 30 * 60)]), "58424", entrantes
    ) is None


async def test_otra_cantidad_es_otro_pedido():
    pagado = _PedidoFalso(2602, "pagado", ITEMS_2602, creado_hace_min=30)
    entrantes = [{"variante_id": 13, "cantidad": 3}]
    assert await _pedido_igual_reciente(_Sesion([pagado], pagos=[(2602, 10)]), "58424", entrantes) is None


async def test_si_la_bd_tose_se_deja_vender():
    """El lado seguro de ESTE candado es VENDER: un hipo de Postgres jamás frena la venta."""
    class _Rota:
        async def execute(self, _q):
            raise RuntimeError("bd caída")

    assert await _pedido_igual_reciente(_Rota(), "58424", ITEMS_2602) is None


# ══ 2) La firma v2: cantidades por variante, nada más ══

def test_la_firma_acumula_y_no_mira_opciones_ni_derivados():
    a = [{"variante_id": 13, "cantidad": 2, "opciones": "Queso", "precio_unitario": 12.0}]
    b = [{"variante_id": 13, "cantidad": 1, "opciones": "otra cosa"},
         {"variante_id": 13, "cantidad": 1, "opciones": None}]
    assert _firma_de_items(a) == _firma_de_items(b) == ((13, 2),)
    assert _firma_de_items([{"variante_id": 13, "cantidad": 3}]) != _firma_de_items(a)


def test_la_firma_ignora_variantes_basura_y_vacios():
    assert _firma_de_items([]) == ()
    assert _firma_de_items([{"variante_id": "x", "cantidad": 2}]) == ()
    assert _firma_de_items(None) == ()


# ══ 3) items=[] ya no puede vaciar ni crear pedidos (C4/C17) ══

class _SesionClienteVacio:
    """Solo contesta la búsqueda del cliente; si algo más toca la BD, el test truena."""

    class _R:
        def scalar_one_or_none(self):
            return None

    def __init__(self):
        self.agregados = []

    async def execute(self, _q):
        return self._R()

    def add(self, obj):
        self.agregados.append(obj)


async def test_registrar_con_items_vacios_se_rechaza_sin_tocar_nada():
    r = await registrar_pedido(_SesionClienteVacio(), "58424", [])
    assert r["ok"] is False
    assert "sin productos" in r["nota"]


# ══ 4) Contratos en la fuente (patrón R50) ══

def test_la_nota_del_duplicado_manda_a_pedir_ayuda_y_no_ensena_bypass():
    """C16: la salida de la 'otra tanda igual' es la DUEÑA (pedir_ayuda), no un truco de texto
    en `opciones` — la v1 le enseñaba al modelo exactamente cómo esquivar su propio candado."""
    import inspect

    from app.agent import tools as tl

    fuente = inspect.getsource(tl.registrar_pedido)
    assert "motivo='pedido_repetido'" in fuente
    assert "dejándolo dicho en" not in fuente, "volvió la puerta de escape por opciones"
    assert fuente.index("_pedido_igual_reciente") < fuente.index("items_pedido = []")


def test_el_candado_solo_mira_dinero_comprometido():
    import inspect

    from app.agent import tools as tl

    fuente = inspect.getsource(tl._pedido_igual_reciente)
    assert '"cancelado", "entregado"' in fuente
    assert "_ESTADOS_COMPROMETIDOS" in fuente and "_PAGOS_VIVOS" in fuente
