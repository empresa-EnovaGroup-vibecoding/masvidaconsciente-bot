"""🔓 EL BOT RETOMA SOLO (PR5, SESIONES (39)) — decisión de Maired 24-sep: "se calla solo y vuelve solo".

Cuando la dueña pausa un chat al escribir (`pausado_por='dueña'`, que no expira), el bot vuelve por su
cuenta SI: la palanca `retomar_auto_horas` > 0, pasaron esas horas desde el último mensaje de ella en
ese chat, el cliente vuelve a escribir (por eso el gancho corre dentro de su turno) y NO hay propuestas
del expediente sin confirmar. Al volver: despausa, cierra el aviso `chat_tomado` con una nota y avisa al
panel. Todo lo demás (escalada del bot, contacto privado, pago) queda intacto. Apagado por defecto.

Sin IA, sin red, sin base real: la sesión se falsea y se observa la decisión.
"""
import inspect
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.workers import tasks

TEL = "584240000000"
AHORA = datetime(2026, 9, 24, 18, 0, tzinfo=UTC)


class _Res:
    def __init__(self, filas=(), escalar="__nada__"):
        self._filas = list(filas)
        self._escalar = escalar

    def scalars(self):
        return self

    def all(self):
        return list(self._filas)

    def scalar_one_or_none(self):
        if self._escalar != "__nada__":
            return self._escalar
        return self._filas[0] if self._filas else None


class _Sesion:
    """Despacha por tabla: clientes → el Cliente falso; mensajes → último owner; intervenciones →
    propuestas o chat_tomado según el filtro del SQL compilado."""

    def __init__(self, cliente, ultimo_owner, propuestas=(), tomados=()):
        self.cliente = cliente
        self.ultimo_owner = ultimo_owner
        self.propuestas = list(propuestas)
        self.tomados = list(tomados)
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, q):
        sql = str(q).lower()
        if "pg_advisory_xact_lock" in sql:  # bloquear_cliente hace el lock y luego el select
            return _Res()
        if "from clientes" in sql:
            return _Res(escalar=self.cliente)
        if "from mensajes" in sql:
            return _Res(escalar=self.ultimo_owner)
        if "from intervenciones" in sql:
            return _Res(self.propuestas if "propuesta_expediente" in sql or "in (" in sql else self.tomados)
        return _Res()

    async def commit(self):
        self.commits += 1


def _cliente(**kw):
    base = {"telefono": TEL, "bot_pausado": True, "pausado_por": "dueña", "privado": False}
    base.update(kw)
    return SimpleNamespace(**base)


def _tomado(**kw):
    base = {"id": 1, "motivo": "chat_tomado", "estado": "pendiente", "detalle": "Le respondiste tú.", "resuelta_at": None}
    base.update(kw)
    return SimpleNamespace(**base)


def _montar(monkeypatch, *, horas, cliente, ultimo_owner, propuestas=(), tomados=()):
    ses = _Sesion(cliente, ultimo_owner, propuestas, tomados)
    monkeypatch.setattr(tasks, "_horas_retorno_auto", AsyncMock(return_value=horas))
    monkeypatch.setattr("app.services.db.get_session_factory", lambda: (lambda: ses))
    # bloquear_cliente hace su lock y devuelve el mismo cliente falso (via el select de la sesión)
    monkeypatch.setattr(
        "app.agent.fuentes_atencion.bloquear_cliente", AsyncMock(return_value=cliente)
    )
    monkeypatch.setattr(tasks, "now_utc", lambda: AHORA, raising=False)
    monkeypatch.setattr(tasks.rc, "notificar_conversacion", AsyncMock())
    # now_utc se importa DENTRO de la función desde app.models; parchear ahí:
    monkeypatch.setattr("app.models.now_utc", lambda: AHORA)
    return ses


# ── APAGADO / FUERA DE ALCANCE: no toca nada ──

async def test_flag_apagado_no_retoma(monkeypatch):
    ses = _montar(monkeypatch, horas=0, cliente=_cliente(), ultimo_owner=AHORA - timedelta(hours=5))
    assert await tasks._retorno_automatico(TEL) is False
    assert ses.commits == 0


async def test_pausado_por_el_bot_no_retoma(monkeypatch):
    """El bot pidió ayuda: eso lo resuelve una persona, no el reloj."""
    c = _cliente(pausado_por="bot")
    ses = _montar(monkeypatch, horas=2, cliente=c, ultimo_owner=AHORA - timedelta(hours=5))
    assert await tasks._retorno_automatico(TEL) is False
    assert c.bot_pausado is True and ses.commits == 0


async def test_contacto_privado_no_retoma(monkeypatch):
    c = _cliente(privado=True)
    _montar(monkeypatch, horas=2, cliente=c, ultimo_owner=AHORA - timedelta(hours=5))
    assert await tasks._retorno_automatico(TEL) is False


async def test_no_pausado_no_retoma(monkeypatch):
    c = _cliente(bot_pausado=False, pausado_por=None)
    _montar(monkeypatch, horas=2, cliente=c, ultimo_owner=AHORA - timedelta(hours=5))
    assert await tasks._retorno_automatico(TEL) is False


# ── EL RELOJ ──

async def test_aun_no_pasan_las_horas_no_retoma(monkeypatch):
    c = _cliente()
    ses = _montar(monkeypatch, horas=2, cliente=c, ultimo_owner=AHORA - timedelta(minutes=30))
    assert await tasks._retorno_automatico(TEL) is False
    assert c.bot_pausado is True and ses.commits == 0


async def test_sin_mensaje_de_la_duena_no_retoma(monkeypatch):
    """Pausa puesta a mano sin escribir (botón 'Yo atiendo' sin mensaje): deliberada, no caduca."""
    c = _cliente()
    _montar(monkeypatch, horas=2, cliente=c, ultimo_owner=None)
    assert await tasks._retorno_automatico(TEL) is False
    assert c.bot_pausado is True


async def test_una_propuesta_pendiente_no_frena_el_retorno(monkeypatch):
    """Decisión de Maired (24-sep noche, SESIONES (41)): una tarjeta sin tocar en la Bandeja no puede dejar
    al cliente sin bot. El bot vuelve igual; PR4 le dice al modelo que no la dé por hecha."""
    c = _cliente()
    prop = SimpleNamespace(id=9, motivo="propuesta_expediente", estado="pendiente")
    ses = _montar(monkeypatch, horas=2, cliente=c, ultimo_owner=AHORA - timedelta(hours=5), propuestas=[prop])
    assert await tasks._retorno_automatico(TEL) is True
    assert c.bot_pausado is False and c.pausado_por is None and ses.commits == 1


# ── EL CAMINO FELIZ ──

async def test_pasan_las_horas_y_el_bot_retoma_solo(monkeypatch):
    c = _cliente()
    aviso = _tomado()
    ses = _Sesion(c, AHORA - timedelta(hours=3), propuestas=[], tomados=[aviso])
    monkeypatch.setattr(tasks, "_horas_retorno_auto", AsyncMock(return_value=2.0))
    monkeypatch.setattr("app.services.db.get_session_factory", lambda: (lambda: ses))
    monkeypatch.setattr("app.agent.fuentes_atencion.bloquear_cliente", AsyncMock(return_value=c))
    monkeypatch.setattr("app.models.now_utc", lambda: AHORA)
    notificar = AsyncMock()
    monkeypatch.setattr(tasks.rc, "notificar_conversacion", notificar)

    assert await tasks._retorno_automatico(TEL) is True
    assert c.bot_pausado is False and c.pausado_por is None
    assert aviso.estado == "resuelta" and aviso.resuelta_at == AHORA
    assert "El bot retomó solo" in aviso.detalle and "2 h" in aviso.detalle
    assert ses.commits == 1
    notificar.assert_awaited_once_with(TEL, "actualizada")


# ── FALLOS: lado seguro ──

async def test_si_la_base_revienta_no_retoma(monkeypatch):
    monkeypatch.setattr(tasks, "_horas_retorno_auto", AsyncMock(return_value=2.0))

    def _revienta():
        raise RuntimeError("sin base")

    monkeypatch.setattr("app.services.db.get_session_factory", _revienta)
    assert await tasks._retorno_automatico(TEL) is False


# ── EL LECTOR DE LA CONFIG ──

@pytest.mark.parametrize("valor,esperado", [
    ("2", 2.0), ("0.05", 0.05), ("1,5", 1.5), ("", 0.0), ("abc", 0.0), ("0", 0.0), ("-3", 0.0), (None, 0.0),
])
async def test_lector_de_horas(monkeypatch, valor, esperado):
    class _S:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, q):
            fila = SimpleNamespace(valor=valor) if valor is not None else None
            return _Res(escalar=fila)

    monkeypatch.setattr("app.services.db.get_session_factory", lambda: (lambda: _S()))
    assert await tasks._horas_retorno_auto() == esperado


async def test_lector_error_devuelve_cero(monkeypatch):
    def _revienta():
        raise RuntimeError("sin base")

    monkeypatch.setattr("app.services.db.get_session_factory", _revienta)
    assert await tasks._horas_retorno_auto() == 0.0


# ── CABLEADO FIJADO EN LA FUENTE (patrón R50) ──

def test_el_gancho_corre_antes_de_cliente_pausado_en_los_dos_carriles():
    for fn in (tasks._procesar, tasks._responder_y_enviar):
        fuente = inspect.getsource(fn)
        i_ret = fuente.find("_retorno_automatico(")
        i_pausa = fuente.find("_cliente_pausado(")
        assert i_ret != -1, f"{fn.__name__} no llama a _retorno_automatico"
        assert i_ret < i_pausa, f"{fn.__name__}: el retorno debe evaluarse ANTES del freno de pausa"


def test_la_clave_es_de_la_proveedora():
    from app.api.router import CLAVES_CONFIG, CLAVES_PROVEEDORA

    assert "retomar_auto_horas" in CLAVES_CONFIG and "retomar_auto_horas" in CLAVES_PROVEEDORA


def test_el_barredor_sigue_sin_despausar():
    from app.services import barredor

    fuente = inspect.getsource(barredor).lower()
    assert "update clientes" not in fuente, "el barredor no puede despausar a nadie"
