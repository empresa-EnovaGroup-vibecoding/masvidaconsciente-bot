"""EL CATÁLOGO EN PDF NO PUEDE MORIR EN SILENCIO (autopsia 2026-09-02, SESIONES (15)).

EL BUG: `config.py` traía HARDCODEADA la URL pública del TALLER
(`https://api-masvida.enovagroup.tech`). Cuando el taller murió (1-sep) el link del catálogo
apuntó a un dominio muerto en TODOS los entornos; Meta no podía descargar el PDF (error 131053)
y el envío moría sin dejar rastro útil — hizo falta una autopsia a la BD para verlo. El texto
"ahí te dejo el catálogo" SÍ llegaba, así que el bot "juraba" que lo había mandado.

LA CURA tiene tres mitades. Las DOS puras corren aquí, en el CI, ANTES de desplegar:
  1. `config.py` ya NO trae la URL de UN entorno: el default es vacío y cada entorno pone la
     suya (Coolify). Vacío o no-HTTPS ⇒ el arranque AVISA fuerte, pero NO bloquea (el bot sigue
     vendiendo; solo el catálogo se degrada — la doctrina del buffer de `config.py`).
  2. `enviar_catalogo` DEGRADA al catálogo de TEXTO en vez de mandarle a Meta un link condenado.
La TERCERA mitad —avisarle a la dueña cuando Meta reporta el `fallido` de un link que SÍ era
`https` pero apuntaba a un host caído— vive en el webhook (`_avisar_media_no_entregada`) y se
prueba en el banco `scripts/probar_meta.py`, porque toca BD y Redis.
"""
import pytest

from app import config
from app.agent import tools as tl


# ── 1) el default dejó de ser la URL de UN entorno (la reversión-roja de la enfermedad) ──
def test_el_default_ya_no_es_una_url_hardcodeada():
    """🔴 Si alguien vuelve a poner una URL como default, esto grita: un default con la URL de
    UN entorno es EXACTAMENTE la enfermedad que mató el catálogo cuando ese entorno se cayó."""
    campo = config.Settings.model_fields["public_base_url"]
    assert campo.default == "", (
        "public_base_url volvió a traer un default hardcodeado. Cada entorno define la suya en "
        "Coolify (PUBLIC_BASE_URL); un default con la URL de un entorno se rompe cuando ese "
        "entorno muere — como pasó el 1-sep."
    )


# ── 2) el filtro de la URL pública: solo pasa un https:// ──
@pytest.mark.parametrize("url,ok", [
    ("https://api.masvidaconsciente.store", True),
    ("https://jthc51nxqitd9opc8ywioocr.152.53.194.89.sslip.io", True),
    ("  https://con.espacios/  ", True),
    ("", False),
    ("   ", False),
    ("http://api.masvidaconsciente.store", False),   # Meta EXIGE https
    ("api-masvida.enovagroup.tech", False),          # host pelado, sin esquema
])
def test_url_publica_utilizable(url, ok):
    assert config.url_publica_utilizable(url) is ok


# ── 3) una URL vacía DEGRADA, NO revienta el arranque (la doctrina: no se bloquea la venta) ──
def test_config_con_url_vacia_no_bloquea_el_arranque():
    """El validador AVISA (log) pero NO lanza. Apagar webhook + worker + bancos por el catálogo
    sería bloquear la venta por una pieza que se degrada sola — y reventaría hasta este test."""
    s = config.Settings(public_base_url="")   # no debe lanzar
    assert s.public_base_url == ""


# ── 4) enviar_catalogo degrada al TEXTO cuando la URL no sirve, y NO toca a Meta ──
class _FilaPDF:
    contenido = b"%PDF-1.4 catalogo de mentira"


class _SesionFalsa:
    async def get(self, modelo, pk):
        return _FilaPDF()


async def test_enviar_catalogo_sin_url_publica_usa_el_texto(monkeypatch):
    """🔴 EL CORAZÓN: con la URL mal puesta, el bot NO le manda a Meta un PDF condenado —
    devuelve el catálogo de TEXTO. El cliente igual recibe el catálogo; el motivo queda en el log.
    """
    class _Settings:
        public_base_url = ""
    monkeypatch.setattr(tl, "get_settings", lambda: _Settings())

    # Sentinela: si el código intentara guardar/mandar la media, esto lo delataría en el acto.
    async def _explota(*a, **k):
        raise AssertionError("¡intentó mandar el catálogo con una URL muerta!")
    monkeypatch.setattr(tl, "_guardar_media_saliente", _explota)

    res = await tl.enviar_catalogo(_SesionFalsa(), "584121112233")  # teléfono REAL, no simulador
    assert res["ok"] is False
    assert "texto" in res["nota"], res


async def test_enviar_catalogo_con_url_valida_SI_encola_el_pdf(monkeypatch):
    """El contraste: con una URL https buena, el catálogo NO se degrada — se encola tras el texto
    (si esto se pusiera rojo, el guardia estaría disparando de más y ROMPERÍA el catálogo bueno)."""
    from app.services import cola_media

    class _Settings:
        public_base_url = "https://api.masvidaconsciente.store"
    monkeypatch.setattr(tl, "get_settings", lambda: _Settings())

    async def _no_tomo(*a, **k):
        return False
    monkeypatch.setattr(tl, "_la_duena_tomo_el_chat", _no_tomo)

    cola_media.abrir()
    try:
        res = await tl.enviar_catalogo(_SesionFalsa(), "584121112233")
        assert res["ok"] is True
        assert cola_media.cuantos() == 1, "el PDF debía quedar encolado detrás del texto"
    finally:
        cola_media.cerrar()
