"""EL PEDIDO COMPLETO COMO ESTADO: lo registrado se muestra cada turno — no se repregunta.

🔴 POR QUÉ EXISTE (la clase "pero ya te lo dije", mapeada el 31-ago tras el caso real de
Maired): el bloque ESTADO DEL CLIENTE decía el id del pedido y el monto en Bs, pero NO el
contenido. Con el historial rodado (aquí cotizar hoy y pagar mañana es lo NORMAL, los pedidos
van con días de anticipación), el bot podía repreguntar "¿cuántos eran?", "¿de qué relleno?"
o "¿para cuándo?" teniendo la respuesta firme en la BD. La tapa más barata de toda la clase:
copiar la fila del pedido al estado — cero adivinanza, cero matching de lenguaje.

⚠️ REGLA DE ORO DE ESTE BLOQUE: ni una cifra de DINERO nueva. Este texto entra a la parte
dinámica del prompt que lee la red del dinero (`autorizados_por_moneda`), y un USD inyectado
sin herramienta en el turno chocaría con la red del TOTAL — el mismo motivo por el que la
cotización se imprime SOLO en bolívares (decisión del 24-ago, PR #1).

También quedan aquí los contratos de las 8 GUARDIAS DE HILO añadidas a las notas de las
herramientas que "tentaban" (volcaban opciones frescas sin excepción para lo ya elegido):
la mecánica exacta del bug de la masa era la ficha fresca reabriendo la elección.

⚠️ La mitad de este archivo son NO-disparos: el pedido cerrado sigue diciendo IGNORA (la
lección del duplicado #2074), y sin entrega acordada no se inventa ninguna línea.
"""
import datetime
import inspect
import re
from types import SimpleNamespace

from app.agent import system_prompt as sp
from app.agent import tools as tl

TEL = "584240000000"


# ══════════════════════════════════════════════════════════════════════════════════
# LA PIEZA: `_items_sin_dinero` (pura)
# ══════════════════════════════════════════════════════════════════════════════════

def _item(**kw):
    base = {
        "producto": "Empanadas de masa de yuca o de masa de plátano",
        "variante_id": 5,
        "cantidad": 2,
        "precio_unitario": 47.5,
        "presentacion": "8 unidades",
        "opciones": "carne mechada, masa de yuca",
    }
    base.update(kw)
    return base


def test_el_renglon_lleva_lo_repreguntable():
    texto = sp._items_sin_dinero([_item()])
    assert "2× Empanadas de masa de yuca o de masa de plátano" in texto
    assert "(8 unidades)" in texto
    assert "carne mechada, masa de yuca" in texto


def test_jamas_una_cifra_de_dinero():
    """La regla de oro: el precio del item NO viaja (47.5 es distintivo a propósito)."""
    texto = sp._items_sin_dinero([_item()])
    assert "$" not in texto
    assert "47" not in texto


def test_la_presentacion_unica_no_ensucia():
    assert "(única)" not in sp._items_sin_dinero([_item(presentacion="única")])


def test_sin_opciones_no_hay_guion_colgando():
    texto = sp._items_sin_dinero([_item(opciones=None)])
    assert not texto.endswith("—") and " — " not in texto


def test_la_basura_no_tumba_el_renglon():
    """NO-disparo: items rotos se saltan, lista vacía o None devuelve ''."""
    assert sp._items_sin_dinero(None) == ""
    assert sp._items_sin_dinero([]) == ""
    assert sp._items_sin_dinero(["basura", {"cantidad": 3}, _item(producto="")]) == ""


def test_varios_items_van_separados():
    texto = sp._items_sin_dinero([_item(), _item(producto="Pan Keto", cantidad=1, opciones=None)])
    assert " · " in texto and "1× Pan Keto" in texto


# ══════════════════════════════════════════════════════════════════════════════════
# EL CABLEADO: `_estado_cliente_texto` con la BD falseada
# ══════════════════════════════════════════════════════════════════════════════════

def _pedido(**kw):
    base = {
        "id": 2075,
        "estado": "esperando_pago",
        "items": [_item()],
        "entrega": "delivery en Cabudare",
        "entrega_fecha": datetime.date(2026, 9, 5),
        "zona_nombre": "Cabudare",
        "cotizado_bs": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


class _Resultado:
    def __init__(self, filas):
        self._filas = filas

    def scalars(self):
        return self

    def all(self):
        return list(self._filas)


class _Fabrica:
    def __init__(self, pedidos):
        self.pedidos = pedidos

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, consulta):
        return _Resultado(self.pedidos)


async def _estado(monkeypatch, pedidos):
    monkeypatch.setattr(sp, "get_session_factory", lambda: _Fabrica(pedidos))
    return await sp._estado_cliente_texto(TEL)


async def test_el_pedido_esperando_muestra_lo_que_lleva(monkeypatch):
    """El cierre del hueco: contenido + entrega acordada, delante del modelo cada turno."""
    texto = await _estado(monkeypatch, [_pedido()])
    assert "Lo que LLEVA el pedido #2075" in texto
    assert "2× Empanadas de masa de yuca o de masa de plátano" in texto
    assert "carne mechada, masa de yuca" in texto
    assert "NO se lo repreguntes" in texto
    assert "Entrega YA ACORDADA: delivery en Cabudare — para el 2026-09-05" in texto
    assert "$" not in texto, "ni una cifra de dinero nueva en el estado"


async def test_el_pedido_pendiente_tambien(monkeypatch):
    texto = await _estado(monkeypatch, [_pedido(estado="pendiente")])
    assert "ARMADO pero SIN cobro" in texto
    assert "Lo que LLEVA el pedido #2075" in texto


async def test_la_cifra_en_bs_de_siempre_no_se_rompe(monkeypatch):
    """NO-regresión: la línea del PR #1 (cotizado en Bs) sigue intacta junto a lo nuevo."""
    texto = await _estado(monkeypatch, [_pedido(cotizado_bs=14131.25)])
    assert "YA ESTÁ COTIZADO" in texto and "Bs" in texto
    assert "Lo que LLEVA el pedido #2075" in texto


async def test_el_pedido_cerrado_sigue_diciendo_IGNORA(monkeypatch):
    """NO-disparo (la lección del duplicado #2074): un pedido pagado NO vuelca su contenido —
    revivir esos productos es exactamente lo que fabricó el clon."""
    texto = await _estado(monkeypatch, [_pedido(estado="pagado")])
    assert "ya se CERRÓ" in texto and "IGNORA esos productos" in texto
    assert "Lo que LLEVA" not in texto


async def test_sin_entrega_acordada_no_se_inventa(monkeypatch):
    """NO-disparo: si aún no hay entrega ni fecha, preguntarlas por PRIMERA vez es correcto."""
    texto = await _estado(monkeypatch, [_pedido(entrega=None, entrega_fecha=None, zona_nombre=None)])
    assert "Entrega YA ACORDADA" not in texto
    assert "Lo que LLEVA el pedido #2075" in texto


async def test_solo_zona_sin_palabras_del_cliente(monkeypatch):
    texto = await _estado(monkeypatch, [_pedido(entrega="  ", entrega_fecha=None)])
    assert "Entrega YA ACORDADA: entrega en Cabudare" in texto


async def test_sin_pedidos_no_hay_bloque(monkeypatch):
    assert await _estado(monkeypatch, []) == ""


# ══════════════════════════════════════════════════════════════════════════════════
# CONTRATOS: las 8 guardias de hilo viven en las notas (la ficha fresca ya no tienta sola)
# ══════════════════════════════════════════════════════════════════════════════════

def _fuente_plana(fn) -> str:
    """El código fuente con comillas y saltos colapsados: las notas largas van partidas en
    varias líneas de string, y un literal que cruce el corte no calzaría contra la fuente
    cruda de `inspect.getsource`."""
    return re.sub(r'[\s"]+', " ", inspect.getsource(fn))


def test_ver_catalogo_multi_ya_no_ordena_repreguntar_a_ciegas():
    fuente = _fuente_plana(tl.ver_catalogo)
    assert "NO le repreguntes cuál" in fuente
    assert "Salvo que YA te lo haya dicho" in fuente  # el apéndice de tamaños


def test_info_producto_no_reofrece_el_catalogo_ni_el_tamano():
    fuente = _fuente_plana(tl.info_producto)
    assert "NO le reofrezcas el catálogo entero" in fuente
    assert "si ya te dijo cuál, usa ese" in fuente  # el string embebido en precio_usd


def test_buscar_info_lleva_guardia_de_hilo():
    assert "SIN reofrecerle las otras opciones" in _fuente_plana(tl.buscar_info)


def test_registrar_pedido_no_convierte_el_error_en_catalogo():
    assert "SOLO para corregir ese id" in _fuente_plana(tl.registrar_pedido)


def test_proxima_fecha_conserva_la_acordada():
    assert "dala por FIJA" in _fuente_plana(tl.proxima_fecha_entrega)


async def test_info_negocio_gana_su_primera_nota():
    """Era la ÚNICA herramienta sin nota: volcaba `pago` a mitad de cobro sin guardia."""

    class _Ses:
        async def execute(self, consulta):
            return _Resultado([SimpleNamespace(clave="negocio_pago", valor="Pago Móvil, Zelle")])

    r = await tl.info_negocio(_Ses(), TEL)
    assert "NO le reofrezcas formas de pago" in r["nota"]
    assert r["pago"] == "Pago Móvil, Zelle"
