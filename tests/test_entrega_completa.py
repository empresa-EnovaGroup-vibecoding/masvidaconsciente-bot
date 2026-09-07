"""LA ENTREGA COMPLETA — franja y referencia con casilla (6-sep, regla de negocio de Maired).

EL CASO REAL (pruebas, 6-sep, DOS modelos con el mismo guion): el cliente dijo "a las 8 am", el
bot dijo "anotado" y no anotó en NINGUNA parte (`pedidos.notas` y `clientes.notas` vacíos). Y los
dos registraron un delivery a "Barquisimeto centro" sin pedir jamás la dirección: pedido pagado,
repartidor sin destino. Encima, `services/mensajes.py` ordenaba al cierre del pago "pregúntale a
qué hora le queda bien" — el propio código empujaba a prometer una hora que la dueña no controla.

LA REGLA (Maired, 6-sep): el cliente NO elige una hora, elige una FRANJA de la lista CERRADA de la
dueña (`franjas_entrega`; sin configurar, las de fábrica). La HORA EXACTA la confirma Whuilianny
según su ruta. Y un DELIVERY sin referencia NO se cobra (`generar_datos_pago` lo exige).

  · `anotar_entrega`: la casilla. Franja de vocabulario cerrado (una hora suelta NO calza a
    propósito), referencia con las palabras del cliente. Acepta pedidos PAGADOS: coordinar la
    entrega ocurre casi siempre después del pago.
  · `_falta_referencia`: solo True cuando se SABE que es delivery y no hay dirección (fail-open).
  · `_frase_entrega`: pregunta la franja, nunca la hora — y la pared del dinero no se mueve.
  · El aviso del guardia de promesas ya no sale desfasado un turno (`mensaje_cliente`).
"""
import inspect
import re
from pathlib import Path

from app.agent import agent, tools
from app.agent.agent import _datos_sensibles, autorizados_por_moneda
from app.agent.system_prompt import _lineas_entrega_pendiente
from app.api.router import CLAVES_CONFIG
from app.models import Pedido, ZonaEntrega
from app.services import tools_config
from app.services.mensajes import MENSAJES_DEFAULT, _frase_entrega

FRANJAS = list(tools._FRANJAS_DEFAULT)  # los momentos de fábrica, tal como se le dicen al cliente


# ══ Dobles ══

class _Res:
    def __init__(self, v):
        self._v = v

    def scalars(self):
        return self

    def first(self):
        return self._v


class _Sesion:
    """`get` distingue por modelo (Pedido / ZonaEntrega); `execute` responde la cola en orden y,
    agotada, el valor de configuración de las franjas (None ⇒ las de fábrica)."""

    def __init__(self, pedido=None, zona=None, cfg_franjas=None, cola=()):
        self.pedido, self.zona, self.cfg = pedido, zona, cfg_franjas
        self._cola = list(cola)
        self.commits = 0

    async def get(self, modelo, pk):
        if modelo is Pedido:
            return self.pedido if self.pedido is not None and self.pedido.id == pk else None
        if modelo is ZonaEntrega:
            return self.zona
        return None

    async def execute(self, _q):
        return _Res(self._cola.pop(0) if self._cola else self.cfg)

    async def commit(self):
        self.commits += 1


class _Pedido:
    def __init__(self, id_=2705, telefono="58424", estado="pagado", zona_id=8, referencia=None,
                 franja=None, costo_envio=3.0, entrega="delivery en Barquisimeto centro"):
        self.id = id_
        self.cliente_telefono = telefono
        self.estado = estado
        self.zona_id = zona_id
        self.entrega_referencia = referencia
        self.entrega_franja = franja
        self.costo_envio = costo_envio
        self.entrega = entrega
        self.updated_at = None
        # Lo que `_resumen_del_pedido` lee al armar la respuesta del registro.
        self.items = []
        self.total = 17
        self.zona_nombre = None
        self.entrega_fecha = None


class _Zona:
    def __init__(self, es_retiro):
        self.es_retiro = es_retiro


DELIVERY = _Zona(es_retiro=False)
RETIRO = _Zona(es_retiro=True)


# ══ 1) Las franjas: lista cerrada, fail-open ══

def test_sin_configurar_salen_las_de_fabrica():
    assert tools._parsear_franjas(None) == tools._FRANJAS_DEFAULT
    assert tools._parsear_franjas("   ") == tools._FRANJAS_DEFAULT


def test_la_duena_las_escribe_una_por_linea_o_con_comas():
    assert tools._parsear_franjas("mañana (9 a 12)\ntarde (3 a 6)") == ["mañana (9 a 12)", "tarde (3 a 6)"]
    assert tools._parsear_franjas("mañana, tarde, tarde") == ["mañana", "tarde"]  # sin repetidos


def test_la_franja_se_elige_de_la_lista_y_una_hora_suelta_no_calza():
    """🔴 El caso: "8 am" NO es una franja. La hora exacta la pone la dueña."""
    assert tools._matchear_franja("en la mañana (10 a 12)", FRANJAS) == FRANJAS[0]
    assert tools._matchear_franja("mañana", FRANJAS) == FRANJAS[0]
    assert tools._matchear_franja("en la tarde", FRANJAS) == FRANJAS[1]
    assert tools._matchear_franja("8 am", FRANJAS) is None
    assert tools._matchear_franja("a las 8", FRANJAS) is None
    assert tools._matchear_franja("", FRANJAS) is None


# ══ 2) `anotar_entrega`: la casilla que faltaba ══

async def test_guarda_franja_y_referencia_en_un_pedido_PAGADO():
    """La reversión del caso: después del pago, "8 am" → franja de la lista + referencia."""
    p = _Pedido(estado="pagado")
    ses = _Sesion(pedido=p, zona=DELIVERY)
    r = await tools.anotar_entrega(
        ses, "58424", franja="en la mañana", referencia="calle 25 frente a la farmacia",
        pedido_id=2705,
    )
    assert r["ok"] is True and ses.commits == 1
    assert p.entrega_franja == FRANJAS[0]
    assert p.entrega_referencia == "calle 25 frente a la farmacia"
    assert r["falta"] == [] and r["referencia_guardada"] is True
    # La referencia (texto libre del cliente) NO viaja de vuelta al modelo.
    assert "farmacia" not in str(r)


async def test_una_hora_suelta_no_se_guarda_y_devuelve_las_franjas():
    p = _Pedido()
    ses = _Sesion(pedido=p, zona=DELIVERY)
    r = await tools.anotar_entrega(ses, "58424", franja="8 am", pedido_id=2705)
    assert r["ok"] is False and ses.commits == 0
    assert p.entrega_franja is None
    assert r["franjas_de_entrega"] == tools._FRANJAS_DEFAULT
    assert "hora exacta" in r["nota"]


async def test_delivery_sin_referencia_avisa_lo_que_falta():
    p = _Pedido(referencia=None)
    ses = _Sesion(pedido=p, zona=DELIVERY)
    r = await tools.anotar_entrega(ses, "58424", franja="tarde", pedido_id=2705)
    assert r["ok"] is True and p.entrega_franja == FRANJAS[1]
    assert any("referencia" in f for f in r["falta"])


async def test_sin_nada_que_anotar_no_toca_la_base():
    ses = _Sesion(pedido=_Pedido(), zona=DELIVERY)
    r = await tools.anotar_entrega(ses, "58424", pedido_id=2705)
    assert r["ok"] is False and ses.commits == 0


async def test_el_pedido_de_otro_cliente_no_se_toca():
    ses = _Sesion(pedido=_Pedido(telefono="58999"), zona=DELIVERY)
    r = await tools.anotar_entrega(ses, "58424", franja="mañana", pedido_id=2705)
    assert r["ok"] is False and ses.commits == 0


async def test_sin_pedido_id_usa_el_ultimo_del_cliente_aunque_este_pagado():
    p = _Pedido(estado="pagado")
    ses = _Sesion(pedido=p, zona=DELIVERY, cola=[p, None])  # 1º el pedido, 2º la config
    r = await tools.anotar_entrega(ses, "58424", referencia="al lado del liceo")
    assert r["ok"] is True and p.entrega_referencia == "al lado del liceo"


async def test_la_referencia_se_recorta_y_se_limpia():
    p = _Pedido()
    ses = _Sesion(pedido=p, zona=DELIVERY)
    await tools.anotar_entrega(ses, "58424", referencia="  casa   azul\n\nportón negro " + "x" * 500, pedido_id=2705)
    assert p.entrega_referencia.startswith("casa azul portón negro")
    assert len(p.entrega_referencia) <= tools._REFERENCIA_MAX


# ══ 3) `_falta_referencia`: el candado de la caja, fail-open ══

async def test_falta_referencia_solo_cuando_se_sabe_que_es_delivery_sin_direccion():
    assert await tools._falta_referencia(_Sesion(zona=DELIVERY), _Pedido(referencia=None)) is True
    assert await tools._falta_referencia(_Sesion(zona=DELIVERY), _Pedido(referencia="frente al liceo")) is False
    assert await tools._falta_referencia(_Sesion(zona=RETIRO), _Pedido(referencia=None)) is False
    assert await tools._falta_referencia(_Sesion(zona=None), _Pedido(referencia=None)) is False  # zona borrada
    assert await tools._falta_referencia(_Sesion(zona=DELIVERY), _Pedido(zona_id=None)) is False


async def test_si_la_lectura_de_la_zona_revienta_no_traba_el_cobro():
    class _Rota(_Sesion):
        async def get(self, modelo, pk):
            raise RuntimeError("postgres caído")

    assert await tools._falta_referencia(_Rota(), _Pedido(referencia=None)) is False


def test_la_caja_exige_la_direccion_ANTES_de_dar_los_datos_de_pago():
    """Contrato de fuente: el candado vive en `generar_datos_pago`, entre el de la zona y la
    casilla del método (misma doctrina que el envío: en la CAJA, no solo en el registro)."""
    src = inspect.getsource(tools.generar_datos_pago)
    i_zona = src.index("CANDADO DEL ENVÍO")
    i_ref = src.index("_falta_referencia(session, pedido)")
    i_metodo = src.index("LA CASILLA DEL MÉTODO")
    assert i_zona < i_ref < i_metodo
    assert "NO le des los datos de pago sin eso" in src


def test_registrar_pedido_pide_la_direccion_en_el_turno_natural():
    r = tools._respuesta_registro(_Pedido(), nuevo=True, falta_referencia=True)
    assert r["falta_referencia"] is True and "anotar_entrega" in r["nota"]
    r2 = tools._respuesta_registro(_Pedido(), nuevo=True)
    assert r2["falta_referencia"] is False and "anotar_entrega" not in r2["nota"]
    assert "_falta_referencia(session" in inspect.getsource(tools.registrar_pedido)


def test_proxima_fecha_entrega_ya_no_habla_de_hora_sino_de_franjas():
    src = inspect.getsource(tools.proxima_fecha_entrega)
    assert "franjas_de_entrega" in src
    assert "no la cierras tú" not in src


# ══ 4) El cierre del pago pregunta la FRANJA, nunca la hora — y la pared no se mueve ══

def test_el_cierre_ofrece_las_franjas_y_no_la_hora():
    texto = _frase_entrega("Barquisimeto centro", False, None, franjas=FRANJAS)
    assert "a qué hora" not in texto
    assert FRANJAS[0] in texto and FRANJAS[1] in texto
    assert "anotar_entrega" in texto


def test_con_la_franja_ya_elegida_no_se_repregunta():
    texto = _frase_entrega("Barquisimeto centro", False, None, franjas=FRANJAS, franja_elegida=FRANJAS[1])
    assert FRANJAS[1] in texto and FRANJAS[0] not in texto
    assert "NO prometas una hora" in texto


def test_delivery_sin_direccion_la_pide_en_el_mismo_cierre():
    texto = _frase_entrega("Barquisimeto centro", False, None, franjas=FRANJAS, falta_referencia=True)
    assert "referencia" in texto


def test_los_llamadores_viejos_siguen_igual():
    """ADITIVO: sin franjas, la redacción de siempre (los tests de test_contexto_entrega mandan)."""
    assert "a qué hora" in _frase_entrega("Retiro en La Mendera", True, None)


def test_la_pared_del_dinero_no_se_mueve_con_franjas_ni_referencia():
    """🔴 EL QUE IMPORTA: las franjas traen dígitos ("10 a 12") y NADA de eso puede autorizar un
    monto ni un dato sensible para el turno del pago."""
    texto = _frase_entrega(
        "Barquisimeto centro", False, None, franjas=FRANJAS, franja_elegida=FRANJAS[0],
        falta_referencia=True,
    )
    assert autorizados_por_moneda(texto) == (set(), set())
    assert _datos_sensibles(texto) == set()


def test_la_guia_por_defecto_ya_no_pide_la_hora():
    assert "a qué hora" not in MENSAJES_DEFAULT["msg_guia_confirmado"]
    assert "FRANJA" in MENSAJES_DEFAULT["msg_guia_confirmado"]


# ══ 5) ESTADO DEL CLIENTE: lo guardado se muestra, lo que falta se pide ══

def test_estado_muestra_lo_que_falta_de_un_delivery():
    lineas = " ".join(_lineas_entrega_pendiente(_Pedido(costo_envio=3.0)))
    assert "SIN ELEGIR" in lineas and "FALTA la dirección" in lineas


def test_estado_muestra_lo_ya_guardado_y_no_repregunta():
    lineas = " ".join(_lineas_entrega_pendiente(_Pedido(franja=FRANJAS[0], referencia="frente al liceo")))
    assert "YA ELEGIDA" in lineas and "YA GUARDADA" in lineas and "frente al liceo" in lineas


def test_un_retiro_no_pide_direccion():
    lineas = " ".join(_lineas_entrega_pendiente(_Pedido(costo_envio=0, entrega="lo retiro en La Mendera")))
    assert "dirección" not in lineas


def test_una_fila_vieja_sin_los_campos_no_revienta():
    class _Vieja:
        id = 1

    assert isinstance(_lineas_entrega_pendiente(_Vieja()), list)


# ══ 6) El cableado ══

def test_la_tool_existe_para_el_modelo_y_para_el_codigo_y_esta_blindada():
    assert "anotar_entrega" in tools._DISPATCH
    assert any(t["function"]["name"] == "anotar_entrega" for t in tools.TOOL_SCHEMAS)
    assert tools._PARAMS_DECLARADOS["anotar_entrega"] == {"franja", "referencia", "pedido_id"}
    # Y `registrar_pedido` acepta la dirección de una, para ahorrarle una vuelta al modelo.
    assert "referencia" in tools._PARAMS_DECLARADOS["registrar_pedido"]
    assert "referencia" in inspect.signature(tools.registrar_pedido).parameters
    assert "anotar_entrega" in tools_config.BLINDADAS
    # Aunque la proveedora la deje fuera del CSV, el bot la tiene igual.
    assert "anotar_entrega" in tools_config._parsear("ver_catalogo,info_producto")


def test_franjas_entrega_es_una_clave_editable_del_panel():
    assert "franjas_entrega" in CLAVES_CONFIG


def test_la_migracion_038_es_aditiva_e_idempotente():
    sql = Path(__file__).resolve().parents[1].joinpath("migrations", "038_entrega_completa.sql").read_text(encoding="utf-8")
    assert sql.count("ADD COLUMN IF NOT EXISTS") == 2
    assert "entrega_franja" in sql and "entrega_referencia" in sql
    assert "DO $" not in sql and "DROP" not in sql.upper()


# ══ 7) El aviso del guardia ya no sale desfasado un turno ══

def test_el_mensaje_en_vuelo_viaja_hasta_pedir_ayuda():
    """El turno del cliente se escribe en `mensajes` al FINAL: `pedir_ayuda` leía el anterior
    ("(comprobante)" en vez de "8 am"). Ahora las redes le pasan el mensaje en vuelo."""
    assert "mensaje_cliente" in inspect.signature(tools.pedir_ayuda).parameters
    assert "mensaje_cliente" in tools._PARAMS_DECLARADOS["pedir_ayuda"]  # o `_solo_lo_declarado` lo tiraría
    assert "mensaje_cliente" in inspect.signature(agent._escalar).parameters
    src = inspect.getsource(agent)
    assert len(re.findall(r"mensaje_cliente=pregunta_cliente", src)) >= 2
