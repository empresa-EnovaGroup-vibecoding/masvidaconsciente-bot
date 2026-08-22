"""LAS FECHAS SE CONSULTAN, NO SE CALCULAN — el domingo que el bot se inventó.

**EL CASO MEDIDO** (2026-08-22, sábado, 12:44 del mediodía, conversación real con Maired):

    👩 "para hoy"
    🤖 "Te las dejo para mañana domingo, entonces?"      ← el negocio NO entrega domingos
    👩 "no trabajan los domingos"
    🤖 "hoy es sábado y ya pasaron las 6 de la tarde"    ← eran las 12:44, y lo tenía inyectado

Dos inventos encadenados: primero la fecha, después **una excusa falsa para sostenerla**.

🔴 LO QUE HACE ESTE CASO DISTINTO: la maquinaria para no equivocarse YA EXISTÍA ENTERA
(`_validar_entrega`: días de entrega, feriados, anticipación por producto, hora de corte). Pero
solo se dispara **al REGISTRAR el pedido**. Mientras el bot CONVERSA sobre para cuándo lo quiere
—que es justo cuando el cliente decide si compra— no había nada, y el modelo sumaba días de
cabeza. Por eso la conversación se contradecía sola: el invento chocaba después contra la verdad
del código, delante de la clienta.

La tool `proxima_fecha_entrega` abre esa misma maquinaria al modelo. Es la doctrina del dinero
aplicada al calendario: *las cifras se copian, no se piensan* — y una fecha es una cifra.

⚠️ Estos tests NO tocan la BD: ejercitan la función con una sesión falsa que devuelve el
calendario real del taller (lun–sáb, sin domingo, verificado el 2026-08-22).
"""

from datetime import date

import pytest

from app.agent import tools as t

SABADO = date(2026, 8, 22)   # el día del caso real
DOMINGO = date(2026, 8, 23)


class _SesionFalsa:
    """Devuelve el calendario real del taller sin tocar Postgres."""

    def __init__(self, dias="lunes,martes,miercoles,jueves,viernes,sabado", feriados=(),
                 anticipacion=0):
        self.dias, self.feriados, self.anticipacion = dias, list(feriados), anticipacion

    async def execute(self, consulta):
        texto = str(consulta)
        if "feriados" in texto.lower():
            return _Resultado(self.feriados, escalares=False)
        if "dias_anticipacion" in texto.lower():
            return _Resultado([self.anticipacion])
        return _Resultado([self.dias])


class _Resultado:
    def __init__(self, filas, escalares=True):
        self._filas, self._escalares = filas, escalares

    def scalars(self):
        return self

    def first(self):
        return self._filas[0] if self._filas else None

    def all(self):
        return self._filas


@pytest.fixture
def sabado(monkeypatch):
    """Congela el reloj en el sábado del caso, a las 12:44 — antes de la hora de corte."""
    monkeypatch.setattr(t, "hoy_venezuela", lambda: SABADO)

    async def _no_paso_el_corte(session):
        return False

    async def _corte(session, clave, default):
        return "18:00"

    monkeypatch.setattr(t, "_paso_la_hora_de_corte", _no_paso_el_corte)
    monkeypatch.setattr(t, "_config_hora", _corte)


# ══════════════════════════════════════════════════════════════════════════════════
#  EL CASO REAL
# ══════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_el_domingo_no_aparece_entre_las_fechas_ofrecibles(sabado):
    """Lo que falló: ofreció "mañana domingo". La herramienta NO puede devolverlo."""
    r = await t.proxima_fecha_entrega(_SesionFalsa(), "tel")
    fechas = [f["fecha"] for f in r["proximas_fechas"]]
    assert DOMINGO.isoformat() not in fechas, "ofreció el domingo, que es el bug exacto"
    assert all("domingo" not in f["cuando"] for f in r["proximas_fechas"])


@pytest.mark.asyncio
async def test_despues_del_sabado_viene_el_LUNES_nunca_manana(sabado):
    """El sábado a las 12:44 hoy todavía sirve, así que la primera fecha ES el sábado. Lo que
    NO puede pasar es que la siguiente sea "mañana": mañana es domingo y salta al lunes.

    ⚠️ La primera versión de este test esperaba el lunes como primera fecha y salió roja. El
    código tenía razón: hoy todavía se podía. El instrumento traía la expectativa equivocada."""
    r = await t.proxima_fecha_entrega(_SesionFalsa(), "tel")
    assert r["primera_fecha"]["fecha"] == SABADO.isoformat()
    fechas = [f["fecha"] for f in r["proximas_fechas"]]
    assert fechas[0] == SABADO.isoformat()
    assert fechas[1] == date(2026, 8, 24).isoformat(), "tras el sábado va el LUNES, no el domingo"


@pytest.mark.asyncio
async def test_a_las_1244_del_sabado_HOY_todavia_se_puede(sabado):
    """La segunda mentira: dijo que ya habían pasado las 6. Eran las 12:44."""
    r = await t.proxima_fecha_entrega(_SesionFalsa(), "tel")
    assert r["hoy_se_puede_entregar"] is True
    assert r["ya_paso_la_hora_de_corte"] is False
    assert "sábado" in r["hoy_es"]


@pytest.mark.asyncio
async def test_pasada_la_hora_de_corte_hoy_ya_no_cuenta(monkeypatch):
    """Y cuando SÍ pasó la hora, hoy desaparece de verdad — no por invención, por cálculo."""
    monkeypatch.setattr(t, "hoy_venezuela", lambda: SABADO)

    async def _si_paso(session):
        return True

    async def _corte(session, clave, default):
        return "18:00"

    monkeypatch.setattr(t, "_paso_la_hora_de_corte", _si_paso)
    monkeypatch.setattr(t, "_config_hora", _corte)
    r = await t.proxima_fecha_entrega(_SesionFalsa(), "tel")
    assert r["hoy_se_puede_entregar"] is False
    assert r["primera_fecha"]["fecha"] != SABADO.isoformat()
    assert r["primera_fecha"]["fecha"] == date(2026, 8, 24).isoformat()  # lunes, no domingo


# ══════════════════════════════════════════════════════════════════════════════════
#  LA ANTICIPACIÓN POR PRODUCTO (hoy en 0 en los 32 — el dato es de Whuilianny)
# ══════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_un_producto_lento_corre_la_fecha(sabado):
    """Con 2 días de anticipación, el sábado ya no alcanza para el lunes: se va al martes."""
    r = await t.proxima_fecha_entrega(_SesionFalsa(anticipacion=2), "tel", productos=["Tortas keto"])
    assert r["dias_anticipacion_del_pedido"] == 2
    assert r["primera_fecha"]["fecha"] == date(2026, 8, 24).isoformat()
    assert r["hoy_se_puede_entregar"] is False


@pytest.mark.asyncio
async def test_sin_productos_no_revienta(sabado):
    """El bot la llama antes de que el cliente elija: tiene que responder igual."""
    r = await t.proxima_fecha_entrega(_SesionFalsa(), "tel", productos=None)
    assert r["ok"] is True and r["dias_anticipacion_del_pedido"] == 0


@pytest.mark.asyncio
async def test_los_feriados_tambien_se_saltan(sabado):
    """El lunes cerrado por feriado ⇒ la primera buena es el martes."""
    r = await t.proxima_fecha_entrega(
        _SesionFalsa(feriados=[(date(2026, 8, 24), "vacaciones")]), "tel"
    )
    fechas = [f["fecha"] for f in r["proximas_fechas"]]
    assert date(2026, 8, 24).isoformat() not in fechas, "ofreció un día cerrado por feriado"
    assert date(2026, 8, 25).isoformat() in fechas


# ══════════════════════════════════════════════════════════════════════════════════
#  QUE EL MODELO LA VEA Y NO SE PUEDA APAGAR
# ══════════════════════════════════════════════════════════════════════════════════

def test_la_tool_esta_declarada_y_en_el_dispatch():
    assert "proxima_fecha_entrega" in t._DISPATCH
    nombres = [x["function"]["name"] for x in t.TOOL_SCHEMAS]
    assert "proxima_fecha_entrega" in nombres


def test_no_se_puede_apagar_desde_el_panel():
    """Va al NÚCLEO por el mismo motivo que el catálogo: es la fuente cerrada de verdad sobre el
    calendario. Apagarla no le quita una capacidad al bot — le devuelve la de inventar fechas."""
    from app.services.tools_config import BLINDADAS, DESACTIVABLES, _parsear

    assert "proxima_fecha_entrega" in BLINDADAS
    assert "proxima_fecha_entrega" not in DESACTIVABLES
    # Ni escribiendo el CSV a mano en Postgres dejándola fuera
    assert "proxima_fecha_entrega" in _parsear("ver_catalogo,info_producto")


def test_la_regla_del_prompt_prohibe_calcular_fechas():
    from app.agent.system_prompt import _REGLAS

    assert "@proxima_fecha_entrega" in _REGLAS
    assert "LAS FECHAS SE CONSULTAN, NO SE CALCULAN" in _REGLAS
