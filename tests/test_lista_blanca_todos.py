"""El interruptor `todos` de la lista blanca (pedido de Maired, 29-ago-2026).

El número del taller es PRIVADO de Maired: solo lo tiene quien ella invita a probar. Mantener
la lista blanca ahí era pura fricción (cada amigo nuevo = un UPDATE), y abrirla del todo exigía
vaciar `NUMEROS_PERMITIDOS` en Coolify (token no disponible + redeploy). El centinela lo vuelve
un interruptor de BD: `numeros_permitidos_extra = 'todos'` = abierto a cualquiera;
los números de vuelta = cerrado. La conducta de siempre (listas que se suman, cola de 10
dígitos, internos `__*` pasan) queda intacta y probada aquí mismo."""
from app.workers import tasks


class _Resultado:
    def __init__(self, valor):
        self._valor = valor

    def scalars(self):
        return self

    def first(self):
        return self._valor


class _Sesion:
    def __init__(self, valor):
        self._valor = valor

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def execute(self, *_a, **_k):
        return _Resultado(self._valor)


def _con_extra(monkeypatch, valor):
    """La clave `numeros_permitidos_extra` de mentira (el import es local a la función,
    así que se parchea en la FUENTE: app.services.db)."""
    monkeypatch.setattr(
        "app.services.db.get_session_factory", lambda: (lambda: _Sesion(valor))
    )


def _con_env(monkeypatch, valor):
    monkeypatch.setattr(tasks.settings, "numeros_permitidos", valor)


async def test_todos_abre_aunque_el_entorno_tenga_lista(monkeypatch):
    """El centinela mata la lista ENTERA: con números en la variable de entorno y `todos` en la
    BD, un desconocido recibe respuesta."""
    _con_env(monkeypatch, "584264399792,573005690062")
    _con_extra(monkeypatch, "todos")
    assert await tasks._numero_permitido("584129999999") is True


async def test_todos_tolera_mayusculas_y_espacios(monkeypatch):
    """'Todos' escrito desde un panel con mayúscula o espacio no puede dejar el bot cerrado."""
    _con_env(monkeypatch, "584264399792")
    _con_extra(monkeypatch, "  TODOS ")
    assert await tasks._numero_permitido("584129999999") is True


async def test_sin_centinela_la_lista_sigue_mandando(monkeypatch):
    """La conducta de siempre, intacta: sin `todos`, el desconocido queda en silencio y el
    conocido pasa (comparado por la cola de 10 dígitos, tolerando el código de país)."""
    _con_env(monkeypatch, "584264399792")
    _con_extra(monkeypatch, "593993314532")
    assert await tasks._numero_permitido("584129999999") is False
    assert await tasks._numero_permitido("+58 424-6439-9792") is False  # parecido NO es igual
    assert await tasks._numero_permitido("584264399792") is True
    assert await tasks._numero_permitido("0058 4264399792") is True  # mismo, con prefijo
    assert await tasks._numero_permitido("593993314532") is True  # el extra también suma


async def test_un_numero_que_contiene_todos_no_abre(monkeypatch):
    """Solo el centinela EXACTO abre: una lista que casualmente traiga la palabra en medio de
    números no puede abrir el bot por accidente."""
    _con_env(monkeypatch, "")
    _con_extra(monkeypatch, "todos,584247490499")
    assert await tasks._numero_permitido("584129999999") is False


async def test_los_internos_pasan_siempre(monkeypatch):
    """El simulador del panel y los bancos (`__*`) nunca son un WhatsApp real: pasan con lista,
    sin lista y con centinela."""
    _con_env(monkeypatch, "584264399792")
    _con_extra(monkeypatch, "")
    assert await tasks._numero_permitido("__simulador__") is True
