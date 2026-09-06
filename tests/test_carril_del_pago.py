"""EL CARRIL DEL PAGO, BLINDADO — los 7 huecos de la cacería del 3-sep (C3, C5-C11).

La cacería adversarial que pidió Maired ("¿me garantizas que no hay más?") confirmó, además del
duplicado ya cerrado, 7 huecos en el camino del dinero que ningún candado cubría. Este archivo
fija los arreglos:

  · C7  — el DOBLE CLIC concurrente en Confirmar/Rechazar/Verificar mandaba DOS notificaciones:
          la transición ahora se RECLAMA con un UPDATE condicionado (un ganador, el otro 409).
  · C9  — rechazar un pago residual devolvía a 'esperando_pago' un pedido YA COBRADO por otro
          pago: ahora se pregunta antes de tocar el estado del pedido.
  · C8  — /reabrir con otro 'reportado' vivo reventaba en 500 (índice 026): ahora 409 legible,
          y también 409 si el pedido ya quedó cobrado.
  · C11 — el encolado de la notificación iba DESPUÉS del commit y sin red: Redis caído era un
          500 con el pago ya confirmado y el cliente mudo para siempre. Ahora se reporta
          (`notificacion_encolada: false`) sin romper la respuesta.
  · C5/C10 — un comprobante REENVIADO (media_id nuevo) se pegaba a OTRO pedido abierto y el
          aviso decía "CUADRA… aprueba": la REFERENCIA bancaria ahora se compara y el aviso
          se degrada a "OJO, puede ser el mismo pago" (el registro jamás se frena).
  · C6  — un comprobante real sin pedido en 'esperando_pago' moría en silencio: ahora avisa
          a la dueña ("el carril del dinero nunca es silencioso" — también aquí).
  · C3  — el simulador del panel corría `responder` sin el lock por teléfono: dos clics
          rápidos duplicaban registros en la BD de pruebas.
"""
import pytest
from fastapi import HTTPException

from app.api import router as api
from app.api.router import _encolar_notificacion, _reclamar_transicion

TEL = "584240000000"


# ══ Dobles genéricos ══

class _Res:
    """Un resultado de execute() con la cara que cada consulta espera."""

    def __init__(self, escalar=None, rowcount=None, fila=None):
        self._escalar = escalar
        self.rowcount = rowcount if rowcount is not None else 1
        self._fila = fila

    def scalar_one_or_none(self):
        return self._escalar

    def first(self):
        return self._fila


class _Sesion:
    """Devuelve resultados EN ORDEN; get() sirve pago/pedido; commit cuenta."""

    def __init__(self, pago=None, pedido=None, resultados=()):
        self._pago = pago
        self._pedido = pedido
        self._resultados = list(resultados)
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, modelo, pk):
        return self._pago if modelo.__name__ == "Pago" else self._pedido

    async def execute(self, _q):
        return self._resultados.pop(0)

    async def commit(self):
        self.commits += 1


class _Pago:
    def __init__(self, id_=7, estado="reportado", pedido_id=50, monto_bs=None, monto_usd=None):
        self.id = id_
        self.estado = estado
        self.pedido_id = pedido_id
        self.monto_bs = monto_bs
        self.monto_usd = monto_usd
        self.referencia = "REF123"


class _Pedido:
    def __init__(self, id_=50, estado="esperando_pago", telefono=None):
        self.id = id_
        self.estado = estado
        self.cliente_telefono = telefono  # None ⇒ el endpoint no intenta notificar


def _con(monkeypatch, ses):
    monkeypatch.setattr(api, "get_session_factory", lambda: (lambda: ses))
    return ses


# ══ C7 — la transición se RECLAMA: doble clic = un ganador ══

def test_reclamar_transicion_409_si_no_gano():
    with pytest.raises(HTTPException) as exc:
        _reclamar_transicion(_Res(rowcount=0))
    assert exc.value.status_code == 409
    assert "Otro clic" in exc.value.detail


def test_reclamar_transicion_pasa_si_gano():
    _reclamar_transicion(_Res(rowcount=1))  # no lanza


async def test_confirmar_el_segundo_clic_recibe_409(monkeypatch):
    """🔴 EL CASO: la petición B leyó el pago 'reportado' antes del commit de A. Su UPDATE
    condicionado no reclama ninguna fila (A ya lo movió) → 409, y NUNCA se encola la segunda
    notificación al cliente."""
    ses = _con(monkeypatch, _Sesion(
        pago=_Pago(estado="reportado"), pedido=_Pedido(),
        resultados=[_Res(escalar=None), _Res(rowcount=0)],  # sin otro confirmado; update pierde
    ))
    with pytest.raises(HTTPException) as exc:
        await api.confirmar_pago(7, usuario="prueba@masvida.local")
    assert exc.value.status_code == 409
    assert ses.commits == 0, "el perdedor no comitea nada"


async def test_confirmar_el_ganador_confirma_y_paga_el_pedido(monkeypatch):
    pedido = _Pedido()
    _con(monkeypatch, _Sesion(
        pago=_Pago(estado="reportado"), pedido=pedido,
        resultados=[_Res(escalar=None), _Res(rowcount=1)],
    ))
    r = await api.confirmar_pago(7, usuario="prueba@masvida.local")
    assert r["ok"] is True and r["estado"] == "confirmado"
    assert pedido.estado == "pagado"
    assert r["notificacion_encolada"] is True  # sin teléfono no hay nada que encolar: no falló


# ══ C9 — rechazar un residual no resucita el cobro de un pedido YA pagado ══

async def test_rechazar_residual_NO_toca_un_pedido_ya_cobrado(monkeypatch):
    """El pedido está 'pagado' por OTRO pago confirmado (el #99): rechazar este residual deja
    el pedido quieto — antes lo devolvía a 'esperando_pago' y volvía a ser imán de comprobantes."""
    pedido = _Pedido(estado="pagado")
    _con(monkeypatch, _Sesion(
        pago=_Pago(estado="reportado"), pedido=pedido,
        resultados=[_Res(rowcount=1), _Res(escalar=99)],  # update gana; hay otro confirmado
    ))
    r = await api.rechazar_pago(7, datos=None, usuario="prueba@masvida.local")
    assert r["ok"] is True
    assert pedido.estado == "pagado", "el pedido cobrado JAMÁS vuelve a esperando_pago"


async def test_rechazar_normal_si_devuelve_el_pedido_a_esperando(monkeypatch):
    pedido = _Pedido(estado="pagado")
    _con(monkeypatch, _Sesion(
        pago=_Pago(estado="reportado"), pedido=pedido,
        resultados=[_Res(rowcount=1), _Res(escalar=None)],  # sin otro confirmado
    ))
    await api.rechazar_pago(7, datos=None, usuario="prueba@masvida.local")
    assert pedido.estado == "esperando_pago"


# ══ C8 — reabrir con 409 legible, nunca 500 ══

async def test_reabrir_con_otro_reportado_da_409_legible(monkeypatch):
    _con(monkeypatch, _Sesion(
        pago=_Pago(estado="rechazado"),
        resultados=[_Res(escalar=33)],  # ya hay otro 'reportado'
    ))
    with pytest.raises(HTTPException) as exc:
        await api.reabrir_pago(7, _="prueba@masvida.local")
    assert exc.value.status_code == 409
    assert "por verificar" in exc.value.detail


async def test_reabrir_sobre_pedido_ya_cobrado_da_409(monkeypatch):
    _con(monkeypatch, _Sesion(
        pago=_Pago(estado="parcial"),
        resultados=[_Res(escalar=None), _Res(escalar=88)],  # sin reportado; sí confirmado
    ))
    with pytest.raises(HTTPException) as exc:
        await api.reabrir_pago(7, _="prueba@masvida.local")
    assert exc.value.status_code == 409
    assert "cobrado" in exc.value.detail


async def test_reabrir_limpio_funciona(monkeypatch):
    pago = _Pago(estado="rechazado")
    ses = _con(monkeypatch, _Sesion(pago=pago, resultados=[_Res(escalar=None), _Res(escalar=None)]))
    r = await api.reabrir_pago(7, _="prueba@masvida.local")
    assert r == {"ok": True} and pago.estado == "reportado" and ses.commits == 1


# ══ C11 — Redis caído no convierte el éxito en 500 ══

def test_encolar_notificacion_absorbe_el_broker_caido():
    class _TareaRota:
        @staticmethod
        def apply_async(args):
            raise RuntimeError("redis caído")

    class _TareaSana:
        @staticmethod
        def apply_async(args):
            return None

    assert _encolar_notificacion(_TareaRota, (TEL, "x")) is False
    assert _encolar_notificacion(_TareaSana, (TEL, "x")) is True


# ══ C5/C10 — la referencia repetida degrada el aviso (no frena el registro) ══

async def test_referencia_repetida_encuentra_al_gemelo(monkeypatch):
    from app.workers import tasks as T

    class _SesionRef(_Sesion):
        async def get(self, modelo, pk):
            return _Pago(id_=200)  # referencia "REF123"

    ses = _SesionRef(resultados=[_Res(fila=(150, 60))])  # otro pago 150 del pedido 60
    monkeypatch.setattr(T, "get_session_factory", lambda: (lambda: ses))
    r = await T._referencia_repetida(200)
    assert r == {"pago_id": 150, "pedido_id": 60, "referencia": "REF123"}


async def test_referencia_sin_gemelo_devuelve_none(monkeypatch):
    from app.workers import tasks as T

    class _SesionRef(_Sesion):
        async def get(self, modelo, pk):
            return _Pago(id_=200)

    ses = _SesionRef(resultados=[_Res(fila=None)])
    monkeypatch.setattr(T, "get_session_factory", lambda: (lambda: ses))
    assert await T._referencia_repetida(200) is None


async def test_referencia_con_bd_rota_no_frena_nada(monkeypatch):
    from app.workers import tasks as T

    def _explota():
        raise RuntimeError("bd caída")

    monkeypatch.setattr(T, "get_session_factory", _explota)
    assert await T._referencia_repetida(200) is None


# ══ Los cableados, fijados en la fuente (patrón R50) ══

def test_el_aviso_del_cuadra_consulta_la_referencia_antes_de_decir_aprueba():
    import inspect

    import app.workers.tasks as modulo

    fuente = inspect.getsource(modulo)
    assert "monto_cuadra" in fuente
    zona = fuente[fuente.index("and monto_cuadra:"):]
    assert "_referencia_repetida" in zona[:3200], "el aviso del CUADRA ya no mira la referencia"
    assert "comprobante_sin_pedido" in fuente, "volvió el silencio del comprobante sin pedido (C6)"


def test_el_simulador_toma_y_suelta_el_lock():
    import inspect

    fuente = inspect.getsource(api.probar_bot)
    assert "adquirir_lock" in fuente and "liberar_lock" in fuente
    assert fuente.index("adquirir_lock") < fuente.index("responder(")
