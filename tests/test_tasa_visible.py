"""EL RESPALDO DE LA TASA DEJA DE SER MUDO (SIL-14) — el camino del DINERO.

🔴 LO QUE SE MIDIÓ EN EL TALLER EL 2026-08-09, y es el motivo entero de este archivo:

    API en vivo ..................... 756,7083 Bs/$
    configuracion.tasa_manual ....... 567,68      (tasa_manual_activa = 0, tasa_margen_pct = 0.0)

Un **25% POR DEBAJO**. `obtener_tasa_bcv()` cae al respaldo cuando la API falla, y hasta ese día
lo hacía con UN `logger.warning` dentro del contenedor: ni sonda en `/salud`, ni telemetría, ni
marca de tiempo. O sea que con la API caída el bot cotiza los Pago Móvil un 25% más baratos EN
SILENCIO — el negocio cobra de menos en cada venta y nadie lo nota, con todo el contenedor verde.

⚠️ EL EQUILIBRIO ES TODO, igual que en el debounce del buffer y en la red de la salud. Un
detector que grita se acaba ignorando (DAT-10) — y este avisa a la DUEÑA por WhatsApp, que es el
canal más caro que hay. Por eso **la mitad de este archivo son los casos que NO deben avisar**:
la caché (el carril normal), la API sana, la recuperación, el segundo fallo dentro del candado,
el domingo sin ventas y el candado manual. Y por eso están también los casos en los que la
observabilidad se rompe y la VENTA TIENE QUE SEGUIR: Postgres caído al anotar, Meta caído al
avisar, Redis caído entero. Degradar, nunca bloquear la venta.

Sin un solo `sleep` y sin tocar nada real: Redis, Postgres, la API de la tasa y WhatsApp son
dobles, y el código de `tasa.py` y de `salud.py` corre ENTERO.
"""

from decimal import Decimal

import pytest

from app.config import Settings
from app.services import redis_client as rc
from app.services import salud, tasa

# Los dos números del incidente. Se escriben una sola vez y se comparan contra ellos.
API_BUENA = Decimal("756.7083")
RESPALDO_TALLER = Decimal("567.68")


# ══════════════════════════════════════════════════════════════════════════════════
# Andamios: Postgres, Redis, la API de la tasa y WhatsApp, de mentira
# ══════════════════════════════════════════════════════════════════════════════════

class FilaConfig:
    """Una fila de la tabla `configuracion`."""

    def __init__(self, clave: str, valor: str | None):
        self.clave, self.valor = clave, valor


class ResultadoFilas:
    """Lo que devuelve un `select(Configuracion)`: sirve `.scalar_one_or_none()` y `.scalars()`."""

    def __init__(self, filas):
        self.filas = list(filas)

    def scalar_one_or_none(self):
        return self.filas[0] if self.filas else None

    def scalars(self):
        return self

    def all(self):
        return self.filas

    def first(self):
        return self.filas[0] if self.filas else None


class ResultadoTupla:
    """Lo que devuelve la consulta del rastro que hace la sonda: UNA tupla."""

    def __init__(self, tupla):
        self.tupla = tupla

    def first(self):
        return self.tupla


class BaseDeMentira:
    """Lo justo de Postgres para el rastro de la tasa. Corre el SQL REAL del módulo.

    Que el SQL de verdad pase por aquí es el punto: si mañana alguien le quita el INSERT a
    `_anotar`, `apuntes` se queda vacío y estos tests se ponen rojos. Contra un doble que
    reimplemente el apunte, no se pondrían.
    """

    def __init__(self, *, tasa_manual="567.68", manual_activa="0", margen="0.0",
                 rastro=None, rompe=False):
        self.config = [
            FilaConfig("tasa_manual", tasa_manual),
            FilaConfig("tasa_manual_activa", manual_activa),
            FilaConfig("tasa_margen_pct", margen),
        ]
        self.rastro = rastro           # (origen, segundos_del_ultimo, segundos_del_ultimo_api)
        self.rompe = rompe
        self.apuntes: list[dict] = []  # las filas de `tasa_resoluciones`
        self.bandeja: list = []        # las `Intervencion`
        self.commits = 0

    def ejecutar(self, stmt, params):
        if self.rompe:
            raise RuntimeError("Postgres no responde")
        sql = str(stmt)
        if "INSERT INTO tasa_resoluciones" in sql:
            self.apuntes.append(dict(params or {}))
            return ResultadoFilas([])
        if "tasa_resoluciones" in sql:
            return ResultadoTupla(self.rastro)
        # `tasa_manual` a secas pide UNA fila; los ajustes piden las tres. `scalar_one_or_none`
        # se queda con la primera, que es justo la que ese camino busca.
        return ResultadoFilas(self.config)

    def __call__(self):
        return self

    def __enter__(self):
        return self


class SesionDeMentira:
    def __init__(self, base: BaseDeMentira):
        self.base = base

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def execute(self, stmt, params=None):
        return self.base.ejecutar(stmt, params)

    def add(self, obj):
        self.base.bandeja.append(obj)

    async def commit(self):
        self.base.commits += 1


def factoria(base: BaseDeMentira):
    def _factory():
        return lambda: SesionDeMentira(base)
    return _factory


class RedisDeMentira:
    """Lo justo de Redis: SET con NX/EX y GET. Corre el `get_cache`/`set_cache`/`aviso_unico`
    REALES, incluido el candado anti-spam, que es lo que de verdad hay que probar."""

    def __init__(self):
        self.cadenas: dict[str, str] = {}

    async def get(self, clave):
        return self.cadenas.get(clave)

    async def set(self, clave, valor, ex=None, nx=False):
        if nx and clave in self.cadenas:
            return None
        self.cadenas[clave] = str(valor)
        return True


class RedisMuerto:
    """Redis caído: TODO revienta. La venta tiene que salir igual."""

    async def get(self, *a, **k):
        raise ConnectionError("Redis no responde")

    async def set(self, *a, **k):
        raise ConnectionError("Redis no responde")


class Espia:
    """Cuenta los WhatsApps que se le mandarían a la dueña."""

    def __init__(self, revienta=False):
        self.enviados: list[tuple[str, str]] = []
        self.revienta = revienta

    async def __call__(self, destino, texto, **kwargs):
        if self.revienta:
            raise RuntimeError("Meta rechaza: la ventana de 24h está cerrada")
        self.enviados.append((destino, texto))
        return {"ok": True}


@pytest.fixture
def taller(monkeypatch):
    """El taller del 2026-08-09: respaldo 567,68 en la BD, Redis vivo, WhatsApp espiado.

    Devuelve (base, redis, espia). El fusible de `_anotar` se reinicia SIEMPRE: si se quedara
    encendido de un test al siguiente, el de al lado vería 0 apuntes y pasaría por el motivo
    equivocado.
    """
    tasa._APAGADA_HASTA["t"] = 0.0
    base, redis, espia = BaseDeMentira(), RedisDeMentira(), Espia()
    monkeypatch.setattr(rc, "_client", lambda: redis)
    monkeypatch.setattr(tasa, "get_session_factory", factoria(base))
    monkeypatch.setattr("app.services.db.get_session_factory", factoria(base))
    monkeypatch.setattr("app.services.meta_client.enviar_texto", espia)

    async def _duena(**kwargs):
        return "573005690062"

    monkeypatch.setattr("app.services.dueno.telefono_de_la_duena", _duena)
    return base, redis, espia


def api_que_responde(valor=API_BUENA):
    async def _api():
        return valor
    return _api


async def api_caida():
    raise TimeoutError("ve.dolarapi.com no responde")


# ══════════════════════════════════════════════════════════════════════════════════
# LO QUE SÍ TIENE QUE VERSE (hasta hoy era MUDO)
# ══════════════════════════════════════════════════════════════════════════════════

async def test_la_api_caida_deja_marcado_el_origen(taller, monkeypatch):
    """El caso del incidente: la API no contesta y se cobra con los 567,68 de la BD."""
    monkeypatch.setattr(tasa, "_tasa_desde_api", api_caida)
    res = await tasa._resolver_base()
    assert res.origen == tasa.ORIGEN_RESPALDO_BD
    assert res.valor == RESPALDO_TALLER
    assert res.es_respaldo is True


async def test_la_caida_al_respaldo_queda_escrita_en_el_rastro(taller, monkeypatch):
    """Sin esta fila no hay forma de decir DESPUÉS con qué tasa se cobró."""
    base, _, _ = taller
    monkeypatch.setattr(tasa, "_tasa_desde_api", api_caida)
    await tasa._resolver_base()
    assert len(base.apuntes) == 1
    assert base.apuntes[0]["origen"] == tasa.ORIGEN_RESPALDO_BD
    assert base.apuntes[0]["valor"] == RESPALDO_TALLER
    assert "TimeoutError" in base.apuntes[0]["error"]


async def test_la_caida_al_respaldo_avisa_a_la_duena_UNA_vez(taller, monkeypatch):
    base, _, espia = taller
    monkeypatch.setattr(tasa, "_tasa_desde_api", api_caida)
    await tasa._resolver_base()
    assert len(espia.enviados) == 1, "la dueña tiene que enterarse de que se cobra con el respaldo"
    assert len(base.bandeja) == 1, "y tiene que quedar en la bandeja, que nunca falla"
    assert base.bandeja[0].motivo == "tasa_de_respaldo"


async def test_el_whatsapp_lleva_el_numero_con_el_que_se_esta_cobrando(taller, monkeypatch):
    """Sin el número el aviso no sirve de nada: '567,68' es lo que le dice a la dueña que está
    cobrando un 25% por debajo. Este canal es PRIVADO; el que no lleva cifras es /salud."""
    _, _, espia = taller
    monkeypatch.setattr(tasa, "_tasa_desde_api", api_caida)
    await tasa._resolver_base()
    assert "567.68" in espia.enviados[0][1]


async def test_sin_api_y_sin_respaldo_se_anota_y_se_relanza(taller, monkeypatch):
    """El peor estado posible (no hay tasa ninguna) era el que menos rastro dejaba."""
    base, _, _ = taller
    base.config = [FilaConfig("tasa_manual", None)]
    monkeypatch.setattr(tasa, "_tasa_desde_api", api_caida)
    monkeypatch.setattr(tasa.settings, "tasa_manual_default", "")
    with pytest.raises(ValueError):
        await tasa._resolver_base()
    assert base.apuntes[0]["origen"] == tasa.ORIGEN_SIN_TASA
    assert base.apuntes[0]["valor"] is None


async def test_la_sonda_se_pone_en_rojo_sirviendo_del_respaldo(taller):
    base, _, _ = taller
    base.rastro = (tasa.ORIGEN_RESPALDO_BD, 120.0, 900.0)  # respaldo hace 2 min
    dato = await salud._tasa()
    assert dato["ok"] is False
    assert "RESPALDO" in dato["error"]
    assert dato["origen"] == tasa.ORIGEN_RESPALDO_BD


async def test_la_sonda_escala_el_mensaje_cuando_el_dato_bueno_pasa_del_umbral(taller):
    """Un tropiezo de diez minutos y llevar tres días cobrando con el número viejo NO son lo
    mismo, y el que lee la alarma tiene que poder distinguirlos."""
    base, _, _ = taller
    base.rastro = (tasa.ORIGEN_RESPALDO_BD, 60.0, 3 * 24 * 3600.0)  # el último bueno, hace 3 días
    dato = await salud._tasa()
    assert dato["ok"] is False
    assert "72 h" in dato["error"] and "UMBRAL" in dato["error"]
    assert dato["api_hace_minutos"] == 3 * 24 * 60


async def test_la_sonda_se_pone_en_rojo_si_la_api_no_contesto_nunca(taller):
    """Caja recién montada con la URL mal escrita: cobra desde el día uno con el respaldo."""
    base, _, _ = taller
    base.rastro = (tasa.ORIGEN_DEFAULT, 30.0, None)
    dato = await salud._tasa()
    assert dato["ok"] is False
    assert "NUNCA" in dato["error"]


async def test_la_sonda_se_pone_en_rojo_sin_tasa_ninguna(taller):
    base, _, _ = taller
    base.rastro = (tasa.ORIGEN_SIN_TASA, 30.0, None)
    dato = await salud._tasa()
    assert dato["ok"] is False
    assert "NO HAY TASA NINGUNA" in dato["error"]


async def test_un_origen_desconocido_nace_en_rojo(taller):
    """En el camino del dinero, lo que no se reconoce se presume malo: si alguien añade mañana un
    origen y se olvida de la sonda, tiene que salir ROJO, no verde."""
    base, _, _ = taller
    base.rastro = ("inventado_mañana", 30.0, 60.0)
    assert (await salud._tasa())["ok"] is False


async def test_la_sonda_entra_en_el_veredicto_de_salud(taller, monkeypatch):
    """De nada sirve la sonda si `revisar()` no la mira: tiene que salir en `fallos`."""
    base, _, _ = taller
    base.rastro = (tasa.ORIGEN_RESPALDO_BD, 60.0, 60.0)
    salud.olvidar_cache()
    for nombre in ("_postgres", "_redis", "_meta", "_saldo", "_barredor",
                   "_duena_contactable", "_modelo"):
        async def _ok(_n=nombre):
            return {"ok": True}
        monkeypatch.setattr(salud, nombre, _ok)
    cuerpo, codigo = await salud.revisar()
    salud.olvidar_cache()
    assert "tasa" in cuerpo["fallos"]
    assert cuerpo["estado"] == "degradado" and codigo == 200, "el bot sigue vendiendo: no es 503"


# ══════════════════════════════════════════════════════════════════════════════════
# LO QUE **NO** PUEDE AVISAR NI FRENAR (la mitad del archivo, y es a propósito)
# ══════════════════════════════════════════════════════════════════════════════════

async def test_la_api_sana_no_avisa_a_nadie(taller, monkeypatch):
    base, _, espia = taller
    monkeypatch.setattr(tasa, "_tasa_desde_api", api_que_responde())
    res = await tasa._resolver_base()
    assert res.origen == tasa.ORIGEN_API and res.valor == API_BUENA
    assert espia.enviados == [], "avisar con la API sana enseñaría a ignorar el aviso"
    assert base.bandeja == []


async def test_la_api_sana_si_deja_su_marca_de_tiempo(taller, monkeypatch):
    """El apunte del dato BUENO no es un aviso: es contra lo que se mide la antigüedad después."""
    base, _, _ = taller
    monkeypatch.setattr(tasa, "_tasa_desde_api", api_que_responde())
    await tasa._resolver_base()
    assert [a["origen"] for a in base.apuntes] == [tasa.ORIGEN_API]
    assert base.apuntes[0]["error"] is None


async def test_el_carril_normal_de_la_cache_no_paga_nada(taller, monkeypatch):
    """La caché sirve la inmensa mayoría de las resoluciones: ahí no puede haber ni un INSERT."""
    base, redis, espia = taller
    redis.cadenas[tasa.CACHE_KEY] = str(API_BUENA)

    async def _no_deberia():
        raise AssertionError("con caché no se llama a la API")

    monkeypatch.setattr(tasa, "_tasa_desde_api", _no_deberia)
    res = await tasa._resolver_base()
    assert res.origen == tasa.ORIGEN_CACHE and res.valor == API_BUENA
    assert base.apuntes == [] and espia.enviados == []


async def test_el_segundo_fallo_dentro_del_candado_no_repite_el_aviso(taller, monkeypatch):
    """🔴 EL CANDADO. Con la API caída, CADA cotización cae al respaldo: sin esto una tarde mala
    le manda a la dueña un WhatsApp por venta y el aviso importante se ahoga entre veinte."""
    base, _, espia = taller
    monkeypatch.setattr(tasa, "_tasa_desde_api", api_caida)
    for _ in range(5):
        await tasa._resolver_base()
    assert len(espia.enviados) == 1, "cinco cotizaciones, UN aviso"
    assert len(base.bandeja) == 1, "y UNA fila en la bandeja: la avería es una sola"
    assert len(base.apuntes) == 5, "pero el RASTRO se escribe siempre, que para eso está"


async def test_la_recuperacion_de_la_api_no_avisa(taller, monkeypatch):
    """Que vuelva a funcionar es una buena noticia, no una alarma."""
    _, redis, espia = taller
    monkeypatch.setattr(tasa, "_tasa_desde_api", api_caida)
    await tasa._resolver_base()
    redis.cadenas.pop(tasa.CACHE_KEY, None)
    monkeypatch.setattr(tasa, "_tasa_desde_api", api_que_responde())
    res = await tasa._resolver_base()
    assert res.origen == tasa.ORIGEN_API
    assert len(espia.enviados) == 1, "el único aviso es el de la caída, no uno por la vuelta"


async def test_la_sonda_esta_verde_con_la_api_fresca(taller):
    base, _, _ = taller
    base.rastro = (tasa.ORIGEN_API, 300.0, 300.0)
    dato = await salud._tasa()
    assert dato["ok"] is True and "error" not in dato
    assert dato["api_hace_minutos"] == 5


async def test_la_sonda_no_grita_en_un_despliegue_recien_hecho(taller):
    """Sin ninguna fila (nadie ha cotizado todavía) NO es una avería."""
    base, _, _ = taller
    base.rastro = (None, None, None)
    dato = await salud._tasa()
    assert dato["ok"] is True and "todavía no se ha resuelto" in dato["aviso"]


async def test_el_domingo_sin_ventas_no_pone_nada_en_rojo(taller):
    """🔴 EL FALSO POSITIVO QUE **NO** SE COMETE. La edad del último dato bueno crece SOLA cuando
    nadie cotiza: una regla de antigüedad a secas dejaría `/salud` en rojo cada lunes con todo
    perfecto, y un detector que grita en falso se acaba ignorando (DAT-10)."""
    base, _, _ = taller
    base.rastro = (tasa.ORIGEN_API, 40 * 3600.0, 40 * 3600.0)  # último evento bueno, hace 40 h
    dato = await salud._tasa()
    assert dato["ok"] is True, "sin ventas no hay avería que reportar"
    assert "40 h" in dato["aviso"], "pero el número se publica igual, para quien quiera mirarlo"


async def test_el_candado_manual_ni_toca_la_cadena(taller, monkeypatch):
    """Con la tasa fijada a mano no se consulta la API: ni apuntes, ni avisos, ni sonda en rojo."""
    base, _, espia = taller
    base.config = [
        FilaConfig("tasa_manual", "600.00"),
        FilaConfig("tasa_manual_activa", "1"),
        FilaConfig("tasa_margen_pct", "0.0"),
    ]

    async def _no_deberia():
        raise AssertionError("con el candado manual la API ni se toca")

    monkeypatch.setattr(tasa, "_tasa_desde_api", _no_deberia)
    assert await tasa.obtener_tasa_bcv() == Decimal("600.00")
    assert base.apuntes == [] and espia.enviados == []
    base.rastro = (None, None, None)
    dato = await salud._tasa()
    assert dato["ok"] is True and dato["candado_manual"] is True


async def test_postgres_caido_al_anotar_no_tumba_la_venta(taller, monkeypatch):
    """🔴 La observabilidad JAMÁS puede costar una venta: si no se puede anotar, se cobra igual."""
    base, _, _ = taller
    base.rompe = True
    monkeypatch.setattr(tasa, "_tasa_desde_api", api_que_responde())
    res = await tasa._resolver_base()
    assert res.valor == API_BUENA and res.origen == tasa.ORIGEN_API
    assert base.apuntes == []


async def test_meta_caido_al_avisar_no_tumba_la_venta(taller, monkeypatch):
    """La ventana de 24h de la dueña está cerrada y `enviar_texto` LANZA. La venta sigue, y el
    aviso no se pierde porque la bandeja se escribe ANTES de intentar el WhatsApp."""
    base, _, espia = taller
    espia.revienta = True
    monkeypatch.setattr(tasa, "_tasa_desde_api", api_caida)
    res = await tasa._resolver_base()
    assert res.valor == RESPALDO_TALLER
    assert len(base.bandeja) == 1, "el aviso sobrevive en el panel aunque WhatsApp lo rechace"


async def test_redis_caido_degrada_sin_reventar(taller, monkeypatch):
    """Sin Redis no hay caché ni candado. Se va a la API, se cobra, y nada revienta."""
    _, _, espia = taller
    monkeypatch.setattr(rc, "_client", lambda: RedisMuerto())
    monkeypatch.setattr(tasa, "_tasa_desde_api", api_que_responde())
    res = await tasa._resolver_base()
    assert res.valor == API_BUENA and res.origen == tasa.ORIGEN_API
    assert espia.enviados == []


async def test_redis_caido_con_la_api_caida_cobra_y_NO_inunda(taller, monkeypatch):
    """🔴 LAS DOS COSAS ROTAS A LA VEZ, que es el caso peligroso. Sin Redis no hay caché, así que
    CADA cotización va a la API; con la API caída, CADA una cae al respaldo. Sin candado eso sería
    un WhatsApp a la dueña POR VENTA, y el próximo aviso de verdad ya nadie lo leería. Se cobra
    igual (la venta nunca se bloquea) y el aviso se cierra: Redis caído ya sale en `/salud`."""
    base, _, espia = taller
    monkeypatch.setattr(rc, "_client", lambda: RedisMuerto())
    monkeypatch.setattr(tasa, "_tasa_desde_api", api_caida)
    for _ in range(4):
        res = await tasa._resolver_base()
    assert res.valor == RESPALDO_TALLER and res.origen == tasa.ORIGEN_RESPALDO_BD
    assert espia.enviados == [], "sin candado no se avisa: un aviso por venta se acaba ignorando"
    assert len(base.apuntes) == 4, "pero el rastro en Postgres se escribe igual"


async def test_la_sonda_no_puede_tumbar_salud_con_postgres_caido(taller):
    """Que Postgres esté muerto ya lo dice su propia sonda; esta no lo repite en rojo."""
    base, _, _ = taller
    base.rompe = True
    dato = await salud._tasa()
    assert dato["ok"] is True and "no se pudo leer el rastro" in dato["aviso"]


async def test_la_sonda_no_publica_ni_una_cifra_de_la_tasa(taller):
    """`/salud` es PÚBLICO y sin auth. Salen orígenes, edades y booleanos; el número, jamás."""
    base, _, _ = taller
    base.rastro = (tasa.ORIGEN_RESPALDO_BD, 60.0, 90 * 3600.0)
    texto = str(await salud._tasa())
    assert "567" not in texto and "756" not in texto


async def test_el_valor_que_se_cobra_no_cambia_ni_una_coma(taller, monkeypatch):
    """🔴 LA GARANTÍA DE QUE ESTO ES **SOLO** OBSERVABILIDAD. El margen se sigue aplicando igual
    sobre la tasa base, con API sana y con la API caída."""
    base, redis, _ = taller
    base.config = [
        FilaConfig("tasa_manual", "567.68"),
        FilaConfig("tasa_manual_activa", "0"),
        FilaConfig("tasa_margen_pct", "10"),
    ]
    monkeypatch.setattr(tasa, "_tasa_desde_api", api_que_responde(Decimal("100")))
    assert await tasa.obtener_tasa_bcv() == Decimal("110.0000")
    redis.cadenas.pop(tasa.CACHE_KEY, None)  # la caché de la llamada buena, fuera
    monkeypatch.setattr(tasa, "_tasa_desde_api", api_caida)
    assert await tasa.obtener_tasa_bcv() == (RESPALDO_TALLER * Decimal("1.1")).quantize(
        Decimal("0.0001")
    )


async def test_el_panel_ve_de_donde_salio_la_tasa(taller, monkeypatch):
    """Ver 567,68 no dice nada; ver 567,68 (respaldo_bd) lo dice todo."""
    monkeypatch.setattr(tasa, "_tasa_desde_api", api_caida)
    estado = await tasa.estado_tasa()
    assert estado["bcv_origen"] == tasa.ORIGEN_RESPALDO_BD
    assert estado["bcv_base"] == float(RESPALDO_TALLER)


# ══════════════════════════════════════════════════════════════════════════════════
# ENCARGO 2 — el validador del buffer (config.py)
# ══════════════════════════════════════════════════════════════════════════════════

def _ajustes(**kwargs) -> Settings:
    """Un `Settings` con los secretos que el validador de seguridad exige."""
    base = {
        "jwt_secret": "clave-de-pruebas-solo-para-pytest-no-es-un-secreto-real",
        "admin_password": "pytest-password",
    }
    return Settings(**{**base, **kwargs})


def test_el_tope_por_debajo_de_la_ventana_se_repara():
    """Con el tope por debajo, el debounce del 2026-08-09 queda ANULADO y no se nota: el bot
    contesta a trozos como antes, sin un solo error."""
    s = _ajustes(buffer_segundos=30, buffer_max_segundos=20)
    assert s.buffer_max_segundos > s.buffer_segundos
    assert s.buffer_max_segundos == 120  # la proporción de fábrica (x4), no el default fijo


def test_el_tope_igual_a_la_ventana_tambien_se_repara():
    """`<=`, no `<`: con el tope EXACTAMENTE igual, la espera que queda es cero y dispara igual."""
    assert _ajustes(buffer_segundos=15, buffer_max_segundos=15).buffer_max_segundos == 60


def test_una_config_mala_NO_impide_arrancar():
    """🔴 LA DECISIÓN. Los `raise` de este validador son de SEGURIDAD (una contraseña pública);
    esta es una perilla de AFINADO. Tumbar el arranque dejaría al negocio SIN VENDER por un
    ajuste que solo empeora el ritmo de las respuestas. Se degrada, no se bloquea la venta."""
    assert _ajustes(buffer_segundos=99, buffer_max_segundos=1).buffer_segundos == 99


def test_el_borde_del_buffer_desactivado():
    """`buffer_segundos = 0` (buffer apagado a propósito) sigue necesitando un tope por encima."""
    s = _ajustes(buffer_segundos=0, buffer_max_segundos=0)
    assert s.buffer_max_segundos == 1


def test_la_config_sana_no_se_toca():
    """Lo que ya funciona no se mueve: 15/60 son los valores que corren hoy en el taller."""
    s = _ajustes(buffer_segundos=15, buffer_max_segundos=60)
    assert (s.buffer_segundos, s.buffer_max_segundos) == (15, 60)


def test_una_ventana_grande_bien_configurada_no_se_toca():
    s = _ajustes(buffer_segundos=30, buffer_max_segundos=45)
    assert (s.buffer_segundos, s.buffer_max_segundos) == (30, 45)
