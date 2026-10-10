"""💵 EL PRECIO QUE DIO WHUILIANNY MANDA (10-oct, decisión de Maired; SESIONES (48)).

EL CASO: Whuilianny entra al chat y le dice a la clienta "el chocolate te lo dejo en 32, nena" (por
texto o nota de voz). En el catálogo vale 36. Cuando Alejandra sigue la venta, tiene que cobrar 32
sin preguntar nada. Alejandra YA leía eso (marca de autoría humana + transcripción 🎤), pero el código
no la dejaba seguirlo: el pedido salía a precio de catálogo y la red del dinero frenaba el "$32".

Lo que fijan estos tests (sin red ni base):
  · los montos de ella se leen con cifras y con palabras (así salen las transcripciones);
  · `registrar_pedido` acepta `precio_acordado` / `total_acordado` SOLO si ella dijo ese número;
    lo que propuso la CLIENTA no cuenta (Maired, 10-oct) → rechazo y relevo;
  · el precio de ella es final: sin el 20% de pagar en dólares (Maired, 10-oct);
  · un re-registro no pierde el precio de ella;
  · el pedido que ella ya dejó anotado se COMPLETA (zona, fecha) con su total, sin caer en el
    candado del duplicado ni rehacerse a precio de catálogo;
  · la red del dinero deja decir lo que ella dijo.
"""
from decimal import Decimal
from types import SimpleNamespace

from app.agent import agent, expediente, tools
from app.agent.contratos_atencion import ItemPropuesto, PropuestaExpediente
from app.agent.montos_duena import (
    lo_dijo_la_duena,
    montos_de_la_duena,
    montos_dichos,
    numeros_en_palabras,
    textos_de_la_duena_en_historial,
)
from app.models import ORIGEN_DUENA, Pedido, Producto, ProductoVariante, ZonaEntrega
from app.services.memoria import mensaje_owner_para_historial

TEL = "584240000000"


# ══ Dobles ══════════════════════════════════════════════════════════════════════════

class _Res:
    def __init__(self, filas=(), uno=None):
        self._filas, self._uno = list(filas), uno

    def scalars(self):
        return self

    def all(self):
        return list(self._filas)

    def first(self):
        return self._filas[0] if self._filas else None

    def scalar_one_or_none(self):
        return self._uno


class _Sesion:
    """Contesta según la TABLA de la consulta. Los pedidos de la dueña (los que filtran por
    `origen`) y los abiertos se configuran aparte; el candado del duplicado ve los de la dueña."""

    def __init__(self, mensajes_duena=(), de_la_duena=(), abiertos=(), zona=None, falla_mensajes=False):
        self.mensajes = list(mensajes_duena)
        self.de_la_duena = list(de_la_duena)
        self.abiertos = list(abiertos)
        self.zona = zona
        self.falla_mensajes = falla_mensajes
        self.agregados = []
        self.commits = 0
        self.variante = SimpleNamespace(id=21, producto_id=7, precio=Decimal("36"),
                                        presentacion="única", disponible=True)
        self.producto = SimpleNamespace(id=7, nombre="Torta de chocolate", disponible=True)

    async def execute(self, q):
        sql = str(q)
        if "FROM mensajes" in sql:
            if self.falla_mensajes:
                raise RuntimeError("la base tosió")
            return _Res(self.mensajes)
        if "FROM clientes" in sql:
            return _Res(uno=SimpleNamespace(telefono=TEL))
        if "FROM pagos" in sql:
            return _Res([])
        if "FROM pedidos" in sql:
            if "pedidos.origen =" in sql or "NOT IN" in sql:
                return _Res(self.de_la_duena)
            return _Res(self.abiertos)
        return _Res([])

    async def get(self, modelo, pk):
        if modelo is ProductoVariante:
            return self.variante if pk == self.variante.id else None
        if modelo is Producto:
            return self.producto
        if modelo is ZonaEntrega:
            return self.zona
        if modelo is Pedido:
            return next((p for p in self.de_la_duena + self.abiertos if p.id == pk), None)
        return None

    def add(self, obj):
        self.agregados.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = 9001


def _zona(costo="3", retiro=False):
    return SimpleNamespace(id=4, nombre="Cabudare", costo=Decimal(costo), disponible=True, es_retiro=retiro)


def _pedido_de_ella(total="32", total_acordado=None, estado="confirmado"):
    from app.models import now_utc

    return SimpleNamespace(
        id=3200, cliente_telefono=TEL, origen=ORIGEN_DUENA, estado=estado,
        items=[{"producto": "Torta de chocolate", "variante_id": 21, "cantidad": 1,
                "precio_unitario": 36.0, "presentacion": "única", "opciones": None}],
        total=Decimal(total), total_acordado=(Decimal(total_acordado) if total_acordado else None),
        costo_envio=Decimal("0"), zona_id=None, zona_nombre=None, entrega=None,
        entrega_fecha=None, entrega_referencia=None, created_at=now_utc(), updated_at=None,
    )


async def _sin_cobro(*_a, **_k):
    return None


# ══ 1) Lo que dijo ella: con cifras y con palabras ══════════════════════════════════

def test_los_audios_se_leen_con_palabras():
    assert 32.0 in numeros_en_palabras("🎤 no nena, el chocolate te lo dejo en treinta y dos")
    assert 36.5 in numeros_en_palabras("son treinta y seis con cincuenta")
    assert 20.5 in numeros_en_palabras("veinte y medio")
    assert 1200.0 in numeros_en_palabras("mil doscientos bolívares")
    assert 25.0 in numeros_en_palabras("veinticinco")


def test_y_con_cifras_aunque_no_lleve_signo_de_dolar():
    assert 36.0 in montos_dichos("ok nena, son 36, te lo llevo mañana")
    assert 32.0 in montos_dichos("te lo dejo en 32$")


def test_del_historial_solo_cuenta_lo_que_escribio_ella():
    historial = [
        {"role": "user", "content": "me lo dejas en 30?"},
        {"role": "assistant", "content": "Claro, cuesta $36"},
        {"role": "assistant", "content": mensaje_owner_para_historial("🎤 te lo dejo en 32, nena")},
    ]
    textos = textos_de_la_duena_en_historial(historial)
    assert len(textos) == 1 and "32" in textos[0]
    montos = montos_dichos(*textos)
    assert 32.0 in montos and 30.0 not in montos and 36.0 not in montos


def test_el_candado_compara_exacto():
    assert lo_dijo_la_duena(32, {32.0}) == Decimal("32.00")
    assert lo_dijo_la_duena("32.00", {32.0}) == Decimal("32.00")
    assert lo_dijo_la_duena(30, {32.0}) is None
    assert lo_dijo_la_duena(0, {0.0}) is None
    assert lo_dijo_la_duena("treinta", {30.0}) is None


async def test_de_la_base_salen_sus_mensajes_y_la_nota_sin_transcribir_no_cuenta():
    ses = _Sesion(mensajes_duena=["[nota de voz]", "🎤 te lo dejo en treinta y dos"])
    assert 32.0 in await montos_de_la_duena(ses, TEL)


async def test_si_la_base_falla_no_hay_acuerdo():
    assert await montos_de_la_duena(_Sesion(falla_mensajes=True), TEL) == set()


# ══ 2) registrar_pedido sigue el precio de ella ════════════════════════════════════

async def test_el_precio_que_dijo_ella_por_audio_es_el_que_se_registra(monkeypatch):
    """🔴 EL CASO: catálogo $36, ella dijo "treinta y dos" en una nota de voz."""
    ses = _Sesion(mensajes_duena=["🎤 no nena, la torta de chocolate te la dejo en treinta y dos"])
    r = await tools.registrar_pedido(
        ses, TEL, [{"variante_id": 21, "cantidad": 1, "precio_acordado": 32}]
    )
    assert r["ok"] is True
    item = r["items"][0]
    assert item["precio_unitario"] == 32.0 and item["precio_acordado"] is True
    assert item["precio_catalogo"] == 36.0
    assert r["total_usd"] == 32.0 and "$32" in r["resumen"]


async def test_con_envio_se_suma_el_envio_al_precio_de_ella():
    ses = _Sesion(mensajes_duena=["te la dejo en 32"], zona=_zona("3"))
    r = await tools.registrar_pedido(
        ses, TEL, [{"variante_id": 21, "cantidad": 2, "precio_acordado": 32}], zona_id=4
    )
    assert r["ok"] is True and r["total_usd"] == 67.0  # 2 × 32 + 3 de envío


async def test_un_precio_que_ella_no_dijo_no_se_registra():
    """Alejandra pone 30 sin que ella lo dijera: rechazo, relevo, nada en la BD."""
    ses = _Sesion(mensajes_duena=["te la dejo en 32"])
    r = await tools.registrar_pedido(
        ses, TEL, [{"variante_id": 21, "cantidad": 1, "precio_acordado": 30}]
    )
    assert r["ok"] is False and r.get("necesita_ayuda") is True
    assert "pedir_ayuda" in r["nota"] and "acuerdo_especial" in r["nota"]
    assert ses.agregados == [] and ses.commits == 0


async def test_lo_que_propuso_la_clienta_con_un_ok_nena_no_cuenta():
    """Decisión de Maired (10-oct): "¿me lo dejas en 32?" + "ok nena" NO vale; se releva."""
    ses = _Sesion(mensajes_duena=["ok nena"])  # el 32 solo está en el mensaje de la clienta
    r = await tools.registrar_pedido(
        ses, TEL, [{"variante_id": 21, "cantidad": 1, "precio_acordado": 32}]
    )
    assert r["ok"] is False and ses.agregados == []


async def test_el_total_que_dio_ella_manda_y_no_se_le_suma_el_envio():
    ses = _Sesion(mensajes_duena=["ok nena, son 36, te lo llevo mañana"], zona=_zona("3"))
    r = await tools.registrar_pedido(
        ses, TEL, [{"variante_id": 21, "cantidad": 1}], zona_id=4, total_acordado=36
    )
    assert r["ok"] is True and r["total_usd"] == 36.0
    pedido = ses.agregados[-1]
    assert pedido.total_acordado == Decimal("36.00")
    # Las líneas van sin precio: su total no es la suma del catálogo.
    assert "= $" not in r["resumen"] and "Total: $36" in r["resumen"]


async def test_un_total_que_ella_no_dijo_no_se_registra():
    ses = _Sesion(mensajes_duena=["ok nena, son 36"])
    r = await tools.registrar_pedido(ses, TEL, [{"variante_id": 21, "cantidad": 1}], total_acordado=30)
    assert r["ok"] is False and ses.agregados == []


async def test_re_registrar_sin_el_precio_no_lo_pierde():
    """Segunda vuelta (la clienta da la zona) y el modelo olvida `precio_acordado`."""
    abierto = SimpleNamespace(
        id=500, cliente_telefono=TEL, estado="pendiente", total=Decimal("32"), total_acordado=None,
        items=[{"producto": "Torta de chocolate", "variante_id": 21, "cantidad": 1,
                "precio_unitario": 32.0, "precio_acordado": True, "presentacion": "única"}],
        zona_id=None, costo_envio=Decimal("0"), zona_nombre=None, notas=None, entrega=None,
        entrega_fecha=None, entrega_referencia=None,
    )
    ses = _Sesion(abiertos=[abierto], zona=_zona("3"))
    r = await tools.registrar_pedido(ses, TEL, [{"variante_id": 21, "cantidad": 1}], zona_id=4)
    assert r["ok"] is True and r["total_usd"] == 35.0  # 32 de ella + 3 de envío, no 36 + 3


async def test_un_total_de_ella_no_vale_si_cambian_los_productos():
    abierto = SimpleNamespace(
        id=501, cliente_telefono=TEL, estado="pendiente", total=Decimal("36"),
        total_acordado=Decimal("36"),
        items=[{"producto": "Torta de chocolate", "variante_id": 21, "cantidad": 1,
                "precio_unitario": 36.0, "presentacion": "única"}],
        zona_id=None, costo_envio=Decimal("0"), zona_nombre=None, notas=None, entrega=None,
        entrega_fecha=None, entrega_referencia=None,
    )
    ses = _Sesion(abiertos=[abierto])
    r = await tools.registrar_pedido(ses, TEL, [{"variante_id": 21, "cantidad": 2}])
    assert r["ok"] is False and "acuerdo_especial" in r["nota"]
    assert abierto.total == Decimal("36") and ses.commits == 0


# ══ 3) El pedido que ella dejó anotado se completa, no se rehace ════════════════════

async def test_el_pedido_de_ella_se_completa_con_la_zona_y_conserva_su_total(monkeypatch):
    """🔴 Antes: la caja pedía la zona, y al re-registrar el candado del duplicado lo tomaba por
    una venta repetida (nace 'confirmado'). Alejandra no podía cobrar."""
    from app.services import redis_client

    monkeypatch.setattr(redis_client, "borrar_cobro", _sin_cobro)
    suyo = _pedido_de_ella(total="32", total_acordado="32")
    ses = _Sesion(de_la_duena=[suyo], zona=_zona("3"))
    r = await tools.registrar_pedido(
        ses, TEL, [{"variante_id": 21, "cantidad": 1}], zona_id=4, pedido_id=3200,
        referencia="frente a la plaza",
    )
    assert r["ok"] is True and r["pedido_id"] == 3200
    assert suyo.zona_id == 4 and suyo.total == Decimal("32")  # su total, sin envío encima
    assert suyo.estado == "confirmado" and ses.agregados == []  # no se creó otro


async def test_el_pedido_de_ella_a_precio_de_catalogo_si_suma_el_envio(monkeypatch):
    from app.services import redis_client

    monkeypatch.setattr(redis_client, "borrar_cobro", _sin_cobro)
    suyo = _pedido_de_ella(total="36")  # ella no dio un precio distinto
    ses = _Sesion(de_la_duena=[suyo], zona=_zona("3"))
    r = await tools.registrar_pedido(ses, TEL, [{"variante_id": 21, "cantidad": 1}], zona_id=4)
    assert r["ok"] is True and suyo.total == Decimal("39")


async def test_si_se_le_suman_productos_al_de_ella_no_se_mezclan():
    suyo = _pedido_de_ella()
    ses = _Sesion(de_la_duena=[suyo])
    r = await tools.registrar_pedido(ses, TEL, [{"variante_id": 21, "cantidad": 3}], pedido_id=3200)
    assert r["ok"] is False and "pedido aparte" in r["nota"] and ses.agregados == []


def test_la_caja_le_dice_que_complete_el_de_ella():
    nota = tools._completar_el_de_ella(SimpleNamespace(id=3200, origen=ORIGEN_DUENA))
    assert "pedido_id=3200" in nota and "NO crees otro" in nota
    assert tools._completar_el_de_ella(SimpleNamespace(id=1, origen="bot")) == ""


# ══ 4) Su precio es final: sin el 20% de pagar en dólares ═══════════════════════════

def test_con_el_total_de_ella_no_hay_veinte_por_ciento():
    p = SimpleNamespace(total=Decimal("36"), total_acordado=Decimal("36"), costo_envio=Decimal("3"), items=[])
    assert tools.monto_en_dolares(p) == Decimal("36.00")


def test_con_el_precio_de_ella_el_veinte_sigue_solo_para_lo_demas():
    p = SimpleNamespace(
        total=Decimal("45"), total_acordado=None, costo_envio=Decimal("3"),
        items=[{"precio_unitario": 32.0, "cantidad": 1, "precio_acordado": True},
               {"precio_unitario": 10.0, "cantidad": 1}],
    )
    # 32 tal cual + (10 + 3 de envío) × 0,80 = 32 + 10,40
    assert tools.monto_en_dolares(p) == Decimal("42.40")


def test_sin_precio_de_ella_todo_sigue_igual():
    p = SimpleNamespace(total=Decimal("20"), total_acordado=None, costo_envio=Decimal("0"), items=[])
    assert tools.monto_en_dolares(p) == tools.monto_en_efectivo(Decimal("20"), 0) == Decimal("16.00")


async def test_un_pago_en_dolares_del_precio_de_ella_cuadra_completo():
    p = SimpleNamespace(total=Decimal("32"), total_acordado=Decimal("32"), costo_envio=0, items=[],
                        cotizado_usd=None, cotizado_usd_divisas=None)
    assert await tools.pago_cuadra(p, Decimal("32"), "USD") is True
    assert await tools.pago_cuadra(p, Decimal("25.60"), "USD") is False  # 32 × 0,80 ya no vale


# ══ 5) El expediente marca el total de ella ═════════════════════════════════════════

class _SesionExpediente:
    def __init__(self):
        self.agregados = []

    async def execute(self, _q):
        return _Res([])

    def add(self, obj):
        self.agregados.append(obj)

    async def flush(self):
        pass


def _propuesta(total):
    return PropuestaExpediente(
        tipo="pedido_tomado", telefono=TEL, total=total, evidencia="son 32 nena",
        items=[ItemPropuesto(producto_id=7, variante_id=21, nombre="Torta de chocolate",
                             cantidad=1, precio_unitario=36.0)],
    )


async def test_el_pedido_de_ella_con_su_precio_queda_marcado():
    ses = _SesionExpediente()
    pedido = await expediente._crear_pedido_duena(ses, _propuesta(32.0), "prueba")
    assert pedido.total == Decimal("32.00") and pedido.total_acordado == Decimal("32.00")


async def test_el_pedido_de_ella_a_precio_de_catalogo_no_queda_marcado():
    ses = _SesionExpediente()
    pedido = await expediente._crear_pedido_duena(ses, _propuesta(36.0), "prueba")
    assert pedido.total_acordado is None


# ══ 6) La red del dinero deja decir lo que ella dijo ════════════════════════════════

def test_la_red_deja_decir_el_precio_de_ella_y_frena_lo_demas():
    historial = [{"role": "assistant", "content": mensaje_owner_para_historial("te la dejo en 32")}]
    de_ella = montos_dichos(*textos_de_la_duena_en_historial(historial))
    assert agent._dinero_inventado("Perfecto, son $32", de_ella, set(), de_ella) == []
    assert agent._dinero_inventado("Perfecto, son $30", de_ella, set(), de_ella) != []


def test_las_tres_puertas_de_la_red_leen_a_la_duena():
    import inspect

    assert "textos_de_la_duena_en_historial(historial)" in inspect.getsource(agent.responder)
    fuente = inspect.getsource(agent)
    assert fuente.count("textos_de_la_duena_en_historial(historial)") >= 3
