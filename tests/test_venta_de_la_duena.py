"""🧺 LA REGLA DEFINITIVA (8-oct, SESIONES (47)): lo que vendió Whuilianny lo termina Whuilianny; el bot
vende lo nuevo. La línea la pone el RELOJ: lo que pasó antes de su último mensaje en el chat es de ella.

Lo que fijan estos tests (sin red, sin base: una sesión falsa que despacha por tabla):
  · el bot cobra SU pedido armado después de que ella habló, y no cobra uno de ella (a mano, del panel,
    o el que el bot armó ANTES de que ella interviniera);
  · el saludo automático de WhatsApp Business no cuenta como que ella habló;
  · si no se puede leer el chat, no se cobra (falla cerrado);
  · el bot ve la regla en su bloque de estado solo si ella habló en estos días.
"""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.agent import system_prompt as sp
from app.agent import tools as tl

TEL = "584240000000"
AHORA = datetime.now(UTC)
HABLO_ELLA = AHORA - timedelta(hours=3)


class _Res:
    def __init__(self, filas=()):
        self._filas = list(filas)

    def scalars(self):
        return self

    def __iter__(self):
        return iter(self._filas)

    def all(self):
        return list(self._filas)

    def first(self):
        return self._filas[0] if self._filas else None

    def scalar_one(self):
        return 0


class _Sesion:
    def __init__(self, pedidos=(), duena=(), revienta_mensajes=False):
        self.pedidos = list(pedidos)
        self.duena = list(duena)  # [(created_at, contenido)] del más nuevo al más viejo
        self.revienta_mensajes = revienta_mensajes

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, _modelo, pid):
        return next((p for p in self.pedidos if p.id == pid), None)

    async def execute(self, q):
        sql = str(q)
        if "FROM mensajes" in sql:
            if self.revienta_mensajes:
                raise RuntimeError("base caída")
            return _Res(self.duena)
        if "FROM pedidos" in sql:
            return _Res(self.pedidos)
        return _Res()  # pagos, intervenciones: vacíos


def _pedido(**kw):
    base = {"id": 40, "estado": "esperando_pago", "origen": "bot", "total": Decimal("36"), "items": [],
            "created_at": AHORA - timedelta(hours=1), "cliente_telefono": TEL, "metodo_elegido": None,
            "metodo_elegido_tipo": None, "cotizado_bs": None, "entrega": None, "entrega_fecha": None,
            "entrega_franja": None, "entrega_referencia": None, "zona_id": None, "costo_envio": Decimal("0")}
    base.update(kw)
    return SimpleNamespace(**base)


# ══════════════════════════════════════════════════════════════════════════════════
#  EL CANDADO DEL COBRO
# ══════════════════════════════════════════════════════════════════════════════════

async def test_el_pedido_del_bot_despues_de_que_ella_hablo_es_del_bot():
    ses = _Sesion(duena=[(HABLO_ELLA, "ok nena")])
    assert await tl._venta_de_la_duena(ses, TEL, _pedido(created_at=AHORA - timedelta(hours=1))) is False


async def test_lo_que_el_bot_armo_antes_de_que_ella_hablara_es_de_ella():
    ses = _Sesion(duena=[(HABLO_ELLA, "ok nena, son 36")])
    assert await tl._venta_de_la_duena(ses, TEL, _pedido(created_at=AHORA - timedelta(hours=5))) is True


async def test_un_pedido_a_mano_o_del_panel_es_de_ella_siempre():
    ses = _Sesion()
    assert await tl._venta_de_la_duena(ses, TEL, _pedido(origen="dueña")) is True
    assert await tl._venta_de_la_duena(ses, TEL, _pedido(origen="panel")) is True


async def test_sin_mensajes_de_ella_o_solo_el_saludo_automatico_es_del_bot():
    assert await tl._venta_de_la_duena(_Sesion(), TEL, _pedido()) is False
    saludo = _Sesion(duena=[(HABLO_ELLA, "Gracias por comunicarte con nosotros. ¿Cómo podemos ayudarte?")])
    assert await tl._venta_de_la_duena(saludo, TEL, _pedido(created_at=AHORA - timedelta(hours=5))) is False


async def test_sin_poder_leer_el_chat_no_se_cobra():
    ses = _Sesion(revienta_mensajes=True)
    assert await tl._venta_de_la_duena(ses, TEL, _pedido()) is True


async def test_generar_datos_pago_no_cobra_la_venta_de_ella():
    viejo = _pedido(created_at=AHORA - timedelta(hours=5))
    ses = _Sesion(pedidos=[viejo], duena=[(HABLO_ELLA, "ok nena")])
    r = await tl.generar_datos_pago(ses, TEL, pedido_id=40)
    assert r["ok"] is False and "Whuilianny" in r["nota"] and "pedir_ayuda" in r["nota"]
    # Sin pedido_id también: el candado mira el pedido que se iba a cobrar.
    r2 = await tl.generar_datos_pago(ses, TEL)
    assert r2["ok"] is False and "#40" in r2["nota"]


# ══════════════════════════════════════════════════════════════════════════════════
#  LO QUE EL BOT VE EN SU BLOQUE DE ESTADO
# ══════════════════════════════════════════════════════════════════════════════════

async def _estado(monkeypatch, ses):
    monkeypatch.setattr(sp, "get_session_factory", lambda: ses)
    return await sp._estado_cliente_texto(TEL)


async def test_si_ella_hablo_el_bot_ve_la_regla_aunque_no_haya_pedidos(monkeypatch):
    texto = await _estado(monkeypatch, _Sesion(duena=[(HABLO_ELLA, "claro que sí amiga")]))
    assert "Whuilianny atendió a este cliente" in texto and "pedir_ayuda" in texto
    assert "NUEVO" in texto  # lo nuevo sí lo vende


async def test_el_pedido_que_armo_antes_de_ella_no_se_ofrece_cobrar(monkeypatch):
    viejo = _pedido(created_at=AHORA - timedelta(hours=5))
    texto = await _estado(monkeypatch, _Sesion(pedidos=[viejo], duena=[(HABLO_ELLA, "ok nena")]))
    assert "incluido el pedido #40" in texto
    assert "ESPERANDO PAGO" not in texto and "llama directamente a generar_datos_pago" not in texto


async def test_sin_ella_en_el_chat_todo_sigue_igual(monkeypatch):
    texto = await _estado(monkeypatch, _Sesion(pedidos=[_pedido()]))
    assert "ESPERANDO PAGO" in texto and "Whuilianny atendió" not in texto
    assert await _estado(monkeypatch, _Sesion()) == ""


async def test_una_conversacion_vieja_de_ella_no_saca_la_linea(monkeypatch):
    hace_un_mes = AHORA - timedelta(days=sp.DIAS_VENTA_DE_LA_DUENA + 5)
    texto = await _estado(monkeypatch, _Sesion(pedidos=[_pedido()], duena=[(hace_un_mes, "gracias mi amor")]))
    assert "Whuilianny atendió" not in texto and "ESPERANDO PAGO" in texto
