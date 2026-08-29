"""La red del pedido fantasma dicta su regaño según el ESTADO de la BD — nunca miente.

🔴 EL CASO (25-ago-2026, cazado por Maired probando en vivo): con el pedido #2073 YA PAGADO y
confirmado, la clienta dio la hora de entrega ("Gracias. A las 10 am") y el modelo respondió
"Perfecto, te lo anoto. La dueña te confirma la hora..." — anotaba la HORA. El regaño viejo era
uno solo y DOBLEMENTE falso ("en la base de datos NO existe" + "llama AHORA a registrar_pedido"):
el modelo obedeció la mentira y fabricó el pedido DUPLICADO #2074 con su cobro completo.

La detección por palabras NO cambia (sus tests viven en test_redes.py y siguen igual): las
palabras levantan la SOSPECHA. Lo que cambia es la SENTENCIA: la corrección consulta la BD y
dice la verdad que toque — sin pedido, la orden de julio ("registra de verdad"); con pedido
vivo, la prohibición de duplicar + las dos salidas honestas."""
from types import SimpleNamespace

import app.agent.agent as ag
from app.agent.agent import _afirma_pedido_registrado, _correccion_fantasma


def _pedido(id=2073, estado="pagado"):
    return SimpleNamespace(id=id, estado=estado)


# ══════════════════════════════════════════════════════════════════════════════════
#  LA CORRECCIÓN DICE LA VERDAD EN LOS DOS MUNDOS
# ══════════════════════════════════════════════════════════════════════════════════

def test_sin_pedido_la_orden_de_julio_queda_intacta():
    """CERO pedidos en BD (el caso real de julio: 'te agendo empanadas' y nada registrado):
    el regaño clásico se conserva LITERAL — ahí la mentira del bot es real y la orden es justa."""
    texto = _correccion_fantasma(None)
    assert "Llama AHORA a `registrar_pedido`" in texto
    assert "NO existe" in texto


def test_con_pedido_prohibe_duplicar_y_nunca_ordena_registrar():
    """Pedido vivo en BD: el regaño nombra el pedido y su estado, PROHÍBE re-registrarlo, y
    deja las dos salidas honestas. Jamás la orden ciega que fabricó el #2074."""
    texto = _correccion_fantasma(_pedido(id=2073, estado="pagado"))
    assert "#2073" in texto
    assert "pagado" in texto
    assert "NO llames a `registrar_pedido`" in texto
    assert "duplicar" in texto
    assert "Llama AHORA" not in texto  # la orden ciega no existe en este mundo
    # Las dos salidas: registrar solo lo NUEVO, o reformular el detalle sin verbo de registro.
    assert "NUEVOS" in texto
    assert "SIN afirmar" in texto


def test_el_caso_literal_de_maired():
    """El mensaje EXACTO del 25-ago sigue levantando la sospecha (la detección no cambió)…
    pero con el pedido pagado delante, la corrección ya no puede fabricar un duplicado."""
    mensaje = "Perfecto, te lo anoto. La dueña te confirma la hora cuando tenga todo listo."
    assert _afirma_pedido_registrado(mensaje) is True  # la palabra sigue sonando la alarma
    correccion = _correccion_fantasma(_pedido(id=2073, estado="pagado"))
    assert "NO llames a `registrar_pedido`" in correccion
    assert "la hora" in correccion  # la salida del detalle está nombrada explícitamente


def test_la_frase_verdadera_tampoco_produce_duplicado():
    """'Tranquila, tu pedido ya está pagado' es VERDAD y aun así dispara la detección (probado
    contra los regex reales). Con el estado delante, la corrección la trata con verdad."""
    mensaje = "Tranquila, tu pedido ya está pagado y confirmado para mañana."
    assert _afirma_pedido_registrado(mensaje) is True
    correccion = _correccion_fantasma(_pedido(id=2073, estado="pagado"))
    assert "YA TIENE el pedido #2073" in correccion


# ══════════════════════════════════════════════════════════════════════════════════
#  LA CONSULTA A LA BD: ÚTIL CUANDO PUEDE, INOFENSIVA CUANDO NO
# ══════════════════════════════════════════════════════════════════════════════════

class _Resultado:
    def __init__(self, filas):
        self._filas = filas

    def scalar_one_or_none(self):
        return self._filas[0] if self._filas else None


class _Sesion:
    def __init__(self, filas, *, explota=False):
        self._filas = filas
        self._explota = explota

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def execute(self, *_a, **_k):
        if self._explota:
            raise RuntimeError("base de datos caída")
        return _Resultado(self._filas)


def _con_bd(monkeypatch, filas, *, explota=False):
    monkeypatch.setattr(
        ag, "get_session_factory", lambda: (lambda: _Sesion(filas, explota=explota))
    )


async def test_pedido_reciente_devuelve_el_vivo(monkeypatch):
    _con_bd(monkeypatch, [_pedido(id=2073, estado="pagado")])
    p = await ag._pedido_reciente("584264399792")
    assert p is not None and p.id == 2073


async def test_pedido_reciente_sin_filas_devuelve_none(monkeypatch):
    _con_bd(monkeypatch, [])
    assert await ag._pedido_reciente("584264399792") is None


async def test_bd_caida_no_tumba_el_turno(monkeypatch):
    """Si la BD falla, la red vuelve al comportamiento clásico (fail-safe hacia el regaño de
    julio) — jamás una excepción matando el turno."""
    _con_bd(monkeypatch, [], explota=True)
    assert await ag._pedido_reciente("584264399792") is None
