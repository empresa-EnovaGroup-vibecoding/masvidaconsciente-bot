"""🛒 ALEJANDRA SIGUE LA VENTA (8-oct, decisión de Maired; SESIONES (47)).

Whuilianny entra de vez en cuando a guiar y Alejandra sigue atendiendo. Cuando Alejandra no sabe algo,
lo dice con sus palabras, sin nombrar a nadie del negocio y sin repetirlo, y le avisa a ella por detrás.
Lo que fijan estos tests (sin red ni base):
  · un producto que no está en el catálogo NO se niega ("no lo tengo"): se releva con pedir_ayuda;
  · la regla de las promesas: sin frase fija, sin nombrar a nadie, sin repetir;
  · con un aviso en camino, el bot ve "ya avisaste: no lo repitas";
  · el WhatsApp a Whuilianny dice la verdad (si el bot se calló o sigue) y pide subir lo que falta;
  · lo que ella vende fuera del catálogo se le recuerda UNA sola vez por producto.
"""
import inspect
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.agent import expediente as ex
from app.agent import system_prompt as sp
from app.agent import tools as tl
from app.agent.contratos_atencion import Contexto, EventoDuena, ItemDuena
from app.workers import tasks

TEL = "584240000000"


# ══════════════════════════════════════════════════════════════════════════════════
#  LO QUE ALEJANDRA LEE
# ══════════════════════════════════════════════════════════════════════════════════

def test_lo_que_no_esta_en_el_catalogo_no_se_niega_se_releva():
    reglas = sp._REGLAS
    assert 'Mejor mil veces "no lo tengo"' not in reglas
    assert "NO digas que no lo hay" in reglas and "no está en el catálogo" in reglas
    nota = inspect.getsource(tl.ver_catalogo)
    assert "NO le digas que no lo tienes" in nota and "pedir_ayuda" in nota


def test_la_regla_de_las_promesas_es_natural_y_no_nombra_a_nadie():
    reglas = sp._REGLAS
    assert "no hay frase fija" in reglas
    assert "JAMÁS nombres a nadie del negocio" in reglas
    assert "NO lo repitas" in reglas


class _Res:
    def __init__(self, filas=(), escalar=0):
        self._filas, self._escalar = list(filas), escalar

    def scalars(self):
        return self

    def __iter__(self):
        return iter(self._filas)

    def all(self):
        return list(self._filas)

    def first(self):
        return self._filas[0] if self._filas else None

    def scalar_one(self):
        return self._escalar


class _Sesion:
    """Distingue los dos conteos de `intervenciones`: el de propuestas (motivo = …) y el de avisos
    vivos (motivo NOT IN …)."""

    def __init__(self, pedidos=(), propuestas=0, avisos=0):
        self.pedidos, self.propuestas, self.avisos = list(pedidos), propuestas, avisos

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, q):
        sql = str(q.compile(compile_kwargs={"literal_binds": True}))
        if "FROM pedidos" in sql:
            return _Res(self.pedidos)
        if "FROM intervenciones" in sql:
            return _Res(escalar=self.avisos if "NOT IN" in sql else self.propuestas)
        return _Res()


async def test_con_un_aviso_en_camino_no_repite_la_promesa(monkeypatch):
    monkeypatch.setattr(sp, "get_session_factory", lambda: _Sesion(avisos=1))
    texto = await sp._estado_cliente_texto(TEL)
    assert sp._LINEA_YA_AVISASTE in texto
    monkeypatch.setattr(sp, "get_session_factory", lambda: _Sesion())
    assert await sp._estado_cliente_texto(TEL) == ""


async def test_sigue_la_venta_de_ella_cobrando_su_pedido(monkeypatch):
    pedido = SimpleNamespace(
        id=3130, estado="confirmado", origen="dueña", items=[], total=Decimal("36"), entrega=None,
        entrega_fecha=None, entrega_franja=None, entrega_referencia=None, zona_id=None,
        metodo_elegido=None, cotizado_bs=None, created_at=None, costo_envio=Decimal("0"),
    )
    monkeypatch.setattr(sp, "get_session_factory", lambda: _Sesion(pedidos=[pedido]))
    texto = await sp._estado_cliente_texto(TEL)
    assert "SIGUES tú" in texto and "generar_datos_pago con pedido_id=3130" in texto
    assert "NO registres ni cobres" not in texto


# ══════════════════════════════════════════════════════════════════════════════════
#  LO QUE LE LLEGA A WHUILIANNY
# ══════════════════════════════════════════════════════════════════════════════════

def test_el_aviso_dice_si_el_bot_se_callo_o_sigue():
    assert "se quedó callado" in tl._cierre_del_aviso("pide_persona", "")
    sigue = tl._cierre_del_aviso("no_se", "Harina de yuca no está en el catálogo")
    assert "sigue atendiendo" in sigue and "se quedó callado" not in sigue
    assert "súbelo al catálogo" in sigue
    assert "súbelo al catálogo" in tl._cierre_del_aviso("no_se", "harina de yuca no esta en el catalogo")
    assert "súbelo al catálogo" not in tl._cierre_del_aviso("no_se", "envíos a Caracas")


def _ctx():
    return Contexto(hoy=date(2026, 9, 22), productos={1: {"id": 1, "nombre": "Quesillo", "variantes": {
        11: {"id": 11, "presentacion": "200g", "precio": Decimal("8")}}}})


def test_el_lector_marca_lo_que_ella_vendio_fuera_del_catalogo():
    ev = EventoDuena(tipo="pedido_tomado", items=[ItemDuena(nombre_literal="granola", cantidad_literal="2")],
                     evidencia="ok amiga")
    v = ex.validar(ev, "ok amiga", _ctx(), telefono=TEL, hoy=date(2026, 9, 22), franjas=[], pedido=None,
                   evidencia_id=1, texto_cliente="me vendes 2 granola")
    assert v.accion == "propuesta" and v.fuera_de_catalogo == "granola"


def test_los_apodos_de_ella_llevan_al_producto_del_catalogo():
    from app.agent.fuentes_atencion import parsear_apodos

    apodos = parsear_apodos("# comentario\nGalletas pequeñas choco: Quesillo\nsin dos puntos\nflan: Torta que no existe")
    assert apodos == {"galletas pequenas choco": "Quesillo", "flan": "Torta que no existe"}
    ctx = _ctx()
    ctx.apodos = apodos
    prod, ambiguo = ex.producto_por_nombre(ctx, "galletas pequeñas choco")
    assert prod["nombre"] == "Quesillo" and ambiguo is False
    assert ex.producto_por_nombre(ctx, "flan") == (None, False)  # un apodo a un nombre que no existe no vale


def test_los_otros_nombres_viven_en_la_ficha_y_el_ambiguo_no_se_usa():
    from app.agent.fuentes_atencion import apodos_de_productos, separar_apodos

    assert separar_apodos("yogur, yoghurt;\nyogurt de kefir") == ["yogur", "yoghurt", "yogurt de kefir"]
    mapa = apodos_de_productos([("yogur, barras", "Yogurt Kéfirado"), ("barras", "Barra de maca-proteica"), (None, "Pan")])
    assert mapa == {"yogur": "Yogurt Kéfirado"}  # "barras" lo reclaman dos: el bot pregunta, no adivina


def test_el_buscador_encuentra_por_los_otros_nombres():
    prod = SimpleNamespace(nombre="Yogurt Kéfirado", descripcion="", apodos="yogur, yoghurt")
    assert tl._coincide_texto(prod, ["yogur"]) and "yoghurt" in tl._tokens_producto(prod)


def test_un_panel_viejo_no_borra_los_otros_nombres():
    from app.api.router import ProductoIn

    assert "apodos" not in ProductoIn(nombre="Quesillo").model_fields_set
    assert "apodos" in ProductoIn(nombre="Quesillo", apodos="").model_fields_set


async def test_lo_de_fuera_del_catalogo_se_le_recuerda_una_vez_por_producto(monkeypatch):
    enviados, vistos = [], set()

    async def _una_vez(clave, _ttl):
        if clave in vistos:
            return False
        vistos.add(clave)
        return True

    async def _enviar(destino, texto):
        enviados.append((destino, texto))

    async def _duena():
        return "584125260318"

    class _S(_Sesion):
        async def execute(self, q):
            return SimpleNamespace(scalar_one_or_none=lambda: "Ana")

    monkeypatch.setattr(tasks.rc, "aviso_unico", _una_vez)
    monkeypatch.setattr(tasks, "enviar_texto", _enviar)
    monkeypatch.setattr("app.services.dueno.telefono_de_la_duena", _duena)
    monkeypatch.setattr(tasks, "get_session_factory", lambda: _S())

    assert await tasks._avisar_fuera_de_catalogo(TEL, ["granola", "Granola", "sal"]) == 2
    assert len(enviados) == 1
    destino, texto = enviados[0]
    assert destino == "584125260318" and "granola y sal a Ana" in texto and "no están en el catálogo" in texto
    assert "Alejandra también lo podrá vender" in texto
    # Otra venta de granola: no se repite (una sola vez por producto).
    assert await tasks._avisar_fuera_de_catalogo(TEL, ["granola"]) == 0
    assert len(enviados) == 1
