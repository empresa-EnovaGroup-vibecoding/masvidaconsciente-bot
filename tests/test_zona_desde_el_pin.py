"""📍 LA ZONA DEL ENVÍO SALE DEL PIN (16-oct, decisión de Maired; SESIONES (53)).

La clienta manda su ubicación de WhatsApp y Alejandra sabe sola la zona ("tu ubicación queda en Barquisimeto
Este") y cobra el envío de esa zona. Cada zona lista sus sectores en el panel; el sistema los ubica UNA vez en
OpenStreetMap y el pin cae en la zona del sector más cercano. Coordenadas REALES de OpenStreetMap (16-oct):
Nueva Segovia 10.0653,-69.2947 · Santa Elena 10.0701,-69.2779 · Cerritos Blancos 10.0390,-69.4023.
"""
from types import SimpleNamespace

import pytest

from app.agent import system_prompt as sp
from app.agent import tools
from app.services import zonas_geo as zg

NUEVA_SEGOVIA = {"sector": "Nueva Segovia", "lat": 10.0652628, "lon": -69.2947422}
SANTA_ELENA = {"sector": "Santa Elena", "lat": 10.0700680, "lon": -69.2779356}
CERRITOS = {"sector": "Cerritos Blancos", "lat": 10.0390281, "lon": -69.4023085}


def _zona(id_, nombre, puntos=(), retiro=False, disponible=True):
    return SimpleNamespace(id=id_, nombre=nombre, puntos=list(puntos), es_retiro=retiro, disponible=disponible,
                           referencias=None)


ZONAS = [
    _zona(7, "Retiro en La Mendera", retiro=True),
    _zona(532, "Barquisimeto Este", [NUEVA_SEGOVIA, SANTA_ELENA]),
    _zona(9, "Barquisimeto oeste", [CERRITOS]),
]


# ══ 1) Lo que se lee del pin y del panel ═════════════════════════════════════════════

def test_las_coordenadas_salen_del_enlace_del_pin_y_de_los_enlaces_pegados():
    assert zg.coordenadas_de("Mi casa — https://maps.google.com/?q=10.0651,-69.2950") == (10.0651, -69.2950)
    assert zg.coordenadas_de("https://www.google.com/maps/@10.07,-69.28,17z") == (10.07, -69.28)
    assert zg.coordenadas_de("frente a la plaza") is None


def test_los_sectores_se_escriben_uno_por_linea_o_con_comas():
    assert zg.sectores_de("Nueva Segovia, Santa Elena\nEl Parral; santa elena") == [
        "Nueva Segovia", "Santa Elena", "El Parral",
    ]
    assert zg.sectores_de(None) == []


def test_la_distancia_es_real():
    km = zg.distancia_km((NUEVA_SEGOVIA["lat"], NUEVA_SEGOVIA["lon"]), (SANTA_ELENA["lat"], SANTA_ELENA["lon"]))
    assert 1.5 < km < 2.2  # Nueva Segovia y Santa Elena están a ~1,9 km


# ══ 2) La zona más cercana ═══════════════════════════════════════════════════════════

def test_un_pin_en_nueva_segovia_cae_en_el_este():
    r = zg.zona_mas_cercana((10.0660, -69.2940), ZONAS, 2.0)
    assert r["zona_id"] == 532 and r["zona"] == "Barquisimeto Este" and r["sector"] == "Nueva Segovia"


def test_un_pin_en_cerritos_blancos_cae_en_el_oeste():
    assert zg.zona_mas_cercana((10.0385, -69.4030), ZONAS, 2.0)["zona_id"] == 9


def test_un_pin_lejos_de_todo_queda_fuera():
    assert zg.zona_mas_cercana((10.4806, -66.9036), ZONAS, 2.0) is None  # Caracas


def test_el_retiro_y_las_zonas_apagadas_no_cuentan():
    zonas = [_zona(1, "Apagada", [NUEVA_SEGOVIA], disponible=False), _zona(2, "Retiro", [NUEVA_SEGOVIA], retiro=True)]
    assert zg.zona_mas_cercana((10.0653, -69.2947), zonas, 2.0) is None


# ══ 3) Ubicar los sectores en el mapa (OpenStreetMap, simulado) ═══════════════════════

class _Respuesta:
    def __init__(self, datos):
        self._datos = datos

    def raise_for_status(self):
        pass

    def json(self):
        return self._datos


class _ClienteOSM:
    def __init__(self, conocidos):
        self.conocidos, self.preguntados = conocidos, []

    async def get(self, _url, params=None, headers=None):
        sector = params["q"].split(",")[0]
        self.preguntados.append(sector)
        assert "User-Agent" in headers and params["bounded"] == 1  # política de Nominatim + ciudad acotada
        lugar = self.conocidos.get(sector)
        return _Respuesta([{"lat": str(lugar[0]), "lon": str(lugar[1])}] if lugar else [])


async def test_se_ubican_los_sectores_y_se_marcan_los_que_no_aparecen():
    zona = SimpleNamespace(es_retiro=False, puntos=None, referencias="Nueva Segovia, Sector Inventado")
    osm = _ClienteOSM({"Nueva Segovia": (10.0652628, -69.2947422)})
    puntos, sin_ubicar = await zg.ubicar_sectores(zona, cliente=osm, contexto="Barquisimeto, Lara",
                                                  viewbox="-69.5,10.2,-69.1,9.9", pausa_s=0)
    assert puntos == [{"sector": "Nueva Segovia", "lat": 10.0652628, "lon": -69.2947422}]
    assert sin_ubicar == ["Sector Inventado"]


async def test_los_sectores_ya_ubicados_no_se_vuelven_a_buscar():
    zona = SimpleNamespace(es_retiro=False, puntos=[NUEVA_SEGOVIA], referencias="Nueva Segovia\nSanta Elena")
    osm = _ClienteOSM({"Santa Elena": (10.0700680, -69.2779356)})
    puntos, _ = await zg.ubicar_sectores(zona, cliente=osm, contexto="x", viewbox="x", pausa_s=0)
    assert osm.preguntados == ["Santa Elena"] and len(puntos) == 2


async def test_la_zona_de_retiro_no_se_ubica():
    zona = SimpleNamespace(es_retiro=True, puntos=None, referencias="La Mendera")
    assert await zg.ubicar_sectores(zona, cliente=_ClienteOSM({}), contexto="x", viewbox="x") == ([], [])


# ══ 4) Lo que ve Alejandra y el candado del pedido ══════════════════════════════════

def test_la_ficha_le_dice_la_zona_del_pin_sin_cifra():
    (linea,) = sp._lineas_del_pin({"dentro": True, "zona_id": 532, "zona": "Barquisimeto Este",
                                   "sector": "Nueva Segovia", "km": 0.1})
    assert "Barquisimeto Este" in linea and "zona_id=532" in linea and "NO le preguntes la zona" in linea
    assert "$" not in linea
    (fuera,) = sp._lineas_del_pin({"dentro": False})
    assert "FUERA" in fuera and "pedir_ayuda" in fuera
    assert sp._lineas_del_pin(None) == []


@pytest.fixture
def pin_en_el_este(monkeypatch):
    async def _pin(_session, _telefono):
        return {"dentro": True, "zona_id": 532, "zona": "Barquisimeto Este", "sector": "Nueva Segovia", "km": 0.1}

    monkeypatch.setattr(zg, "zona_del_pin", _pin)


async def test_el_candado_no_deja_anotar_otra_zona_que_la_del_pin(pin_en_el_este):
    r = await tools._zona_contra_el_pin(None, "58424", _zona(9, "Barquisimeto oeste"))
    assert r["ok"] is False and "zona_id=532" in r["nota"]
    assert await tools._zona_contra_el_pin(None, "58424", _zona(532, "Barquisimeto Este")) is None
    # Retirar siempre se puede.
    assert await tools._zona_contra_el_pin(None, "58424", _zona(7, "Retiro", retiro=True)) is None


async def test_un_pin_fuera_de_las_zonas_releva(monkeypatch):
    async def _fuera(_session, _telefono):
        return {"dentro": False}

    monkeypatch.setattr(zg, "zona_del_pin", _fuera)
    r = await tools._zona_contra_el_pin(None, "58424", _zona(532, "Barquisimeto Este"))
    assert r["ok"] is False and r["necesita_ayuda"] is True and "pedir_ayuda" in r["nota"]


async def test_sin_pin_o_sin_mapa_todo_sigue_como_antes(monkeypatch):
    async def _nada(_session, _telefono):
        return None

    monkeypatch.setattr(zg, "zona_del_pin", _nada)
    assert await tools._zona_contra_el_pin(None, "58424", _zona(9, "Barquisimeto oeste")) is None


def test_la_regla_pide_el_pin():
    assert "pásame tu ubicación, porfa" in sp._REGLAS and "esa manda, no la preguntes" in sp._REGLAS
