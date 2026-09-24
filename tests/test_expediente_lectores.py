"""🗂️ LOS LECTORES DEL EXPEDIENTE (PR4) — SESIONES (38): el bot sabe DÓNDE ESTÁ ENTRANDO.

Hasta PR3 el expediente se llenaba (transcripción → extractor → propuesta → "Sí" en la Bandeja) pero
NADIE lo leía: un pedido `confirmado` tomado por Whuilianny era INVISIBLE para el modo `uno` (el bloque
ESTADO DEL CLIENTE no tenía rama para él y decía "no tiene un pedido abierto"), el cerebro `confirmado`
cargaba UN solo pedido sin procedencia y relevaba toda venta nueva, el comprobante de un pedido tomado a
mano se PERDÍA (`comprobante_sin_pedido`) y "Devolver al bot" solo tenía un texto blando para no re-cobrar.

Lo que estos tests fijan (frases y datos SINTÉTICOS; sin nada real del negocio):
  · modo `uno`: el bloque de estado describe el pedido acordado (quién lo tomó, qué lleva, entrega, pago),
    no cobra ni registra sobre él, y solo abre venta nueva si el pago está confirmado; ni "dueña" ni "$";
  · `ver_pedidos_cliente` trae procedencia, entrega, pago y el total como dinero de herramienta;
  · `cargar_contexto` carga hasta 3 pedidos con `origen`, `franja`, `pago_pendiente` y cuenta propuestas;
  · cerebro `confirmado`: sobre una venta CERRADA con OTROS productos se vende lo nuevo (y se cobra el pedido
    NUEVO); los mismos productos o una venta a medias → relevo; propuestas pendientes → relevo en todo lo
    que toca la venta; `estado_pedido` y `entrega` responden con datos del expediente;
  · el comprobante se pega al pedido tomado a mano y se compara contra su total pactado;
  · el toque humano no fabrica un pedido gemelo (`_pedido_igual_reciente`);
  · "ya te lo entregué" → tipo `entregado`: propuesta, y al confirmarla el pedido queda entregado;
  · `_retomar` lee el expediente: con la venta cerrada a mano y solo acuses se calla; si habla, lleva el [HECHO].
"""
import copy
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent import atencion, fuentes_atencion, resolver_atencion
from app.agent import expediente as ex
from app.agent import system_prompt as sp
from app.agent import tools as tl
from app.agent.atencion import atender
from app.agent.contratos_atencion import Contexto, EventoDuena, Hecho
from app.agent.resolver_atencion import consultar
from app.workers import tasks

TEL = "584240000000"
HOY = date(2026, 9, 22)
CREADO = datetime(2026, 9, 22, 18, 30, tzinfo=UTC)  # 14:30 en Venezuela → "22/09"


# ══════════════════════════════════════════════════════════════════════════════════
#  UNA SESIÓN FALSA QUE DESPACHA POR TABLA (sirve para el prompt, la herramienta y el contexto)
# ══════════════════════════════════════════════════════════════════════════════════

class _Res:
    def __init__(self, filas=(), escalar=None):
        self._filas = list(filas)
        self._escalar = escalar

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

    def scalar_one_or_none(self):
        return self._escalar


class _Sesion:
    """`execute` mira de QUÉ tabla es la consulta (en su SQL compilado) y devuelve lo preparado."""

    def __init__(self, pedidos=(), pagos=(), propuestas=0):
        self.pedidos, self.pagos, self.propuestas = list(pedidos), list(pagos), propuestas
        self.consultas: list[str] = []

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, q):
        try:  # con los valores dentro del SQL (para poder afirmar sobre 'esperando_pago', 'origen'…)
            sql = str(q.compile(compile_kwargs={"literal_binds": True}))
        except Exception:  # noqa: BLE001
            sql = str(q)
        self.consultas.append(sql)
        if "FROM pedidos" in sql:
            return _Res(self.pedidos)
        if "FROM pagos" in sql:
            return _Res(self.pagos)
        if "FROM intervenciones" in sql:
            return _Res(escalar=self.propuestas)
        return _Res()  # clientes, configuracion, productos, zonas, conocimiento, metodos: vacíos


def _item(**kw):
    base = {"producto": "Quesillo", "variante_id": 11, "cantidad": 2, "precio_unitario": 8.0,
            "presentacion": "500 g", "opciones": None}
    base.update(kw)
    return base


def _pedido(**kw):
    base = {
        "id": 3130, "estado": "confirmado", "origen": "dueña", "items": [_item()], "total": Decimal("16"),
        "entrega": None, "entrega_fecha": None, "entrega_franja": None, "entrega_referencia": None,
        "zona_nombre": None, "zona_id": None, "metodo_elegido": None, "cotizado_bs": None,
        "created_at": CREADO, "costo_envio": Decimal("0"),
    }
    base.update(kw)
    return SimpleNamespace(**base)


# ══════════════════════════════════════════════════════════════════════════════════
#  1. MODO `uno`: EL BLOQUE DE ESTADO YA VE EL PEDIDO TOMADO A MANO
# ══════════════════════════════════════════════════════════════════════════════════

async def _estado(monkeypatch, pedidos, pagos=(), propuestas=0):
    monkeypatch.setattr(sp, "get_session_factory", lambda: _Sesion(pedidos, pagos, propuestas))
    return await sp._estado_cliente_texto(TEL)


async def test_el_pedido_tomado_a_mano_deja_de_ser_invisible(monkeypatch):
    """Antes: 'No tiene un pedido abierto ahora' → el bot re-anotaba y re-cobraba lo que ella vendió."""
    texto = await _estado(monkeypatch, [_pedido()])
    assert "No tiene un pedido abierto" not in texto
    assert "Pedido #3130 YA ACORDADO" in texto
    assert "una persona del negocio a mano el 22/09" in texto
    assert "2× Quesillo (500 g)" in texto
    assert "NO llames a registrar_pedido ni a generar_datos_pago" in texto
    assert "ver_pedidos_cliente" in texto, "el total se pide a la herramienta, no se imprime aquí"


async def test_sin_dinero_ni_duena_en_el_bloque(monkeypatch):
    texto = await _estado(monkeypatch, [_pedido()], pagos=[(3130, "confirmado")], propuestas=2)
    assert "$" not in texto and "16" not in texto.replace("#3130", ""), "ni una cifra de dinero"
    assert "dueñ" not in texto.lower() and "duen" not in texto.lower()


async def test_pago_sin_registrar_nunca_se_afirma_y_no_se_vende_encima(monkeypatch):
    texto = await _estado(monkeypatch, [_pedido()])
    assert "NO hay pago registrado" in texto and "NUNCA afirmes que llegó" in texto
    assert "pedir_ayuda" in texto
    # Decisión de Maired (24-sep): con la venta a medias no se abre otra venta encima.
    assert "NO registres un pedido nuevo ni lo cobres" in texto
    assert "pedido NUEVO y aparte" not in texto


async def test_pago_confirmado_si_permite_vender_lo_nuevo(monkeypatch):
    texto = await _estado(monkeypatch, [_pedido()], pagos=[(3130, "reportado"), (3130, "confirmado")])
    assert "Pago del #3130: CONFIRMADO" in texto
    assert "pedido NUEVO y aparte" in texto
    assert "NO registres un pedido nuevo" not in texto


async def test_comprobante_recibido_no_se_pide_otra_vez(monkeypatch):
    texto = await _estado(monkeypatch, [_pedido()], pagos=[(3130, "reportado")])
    assert "comprobante RECIBIDO, en revisión" in texto and "NO digas que ya está confirmado" in texto


async def test_entrega_acordada_se_muestra_y_sin_acordar_se_escala(monkeypatch):
    con = await _estado(monkeypatch, [_pedido(entrega_fecha=date(2026, 9, 23), entrega_franja="en la tarde (2 a 5)",
                                              entrega_referencia="frente a la plaza")])
    assert "Entrega del #3130 YA ACORDADA: el 2026-09-23 en la tarde (2 a 5) en frente a la plaza" in con
    sin = await _estado(monkeypatch, [_pedido()])
    assert "aún SIN acordar por el negocio" in sin and "NO inventes ni le ofrezcas franjas" in sin
    assert "Franja de entrega SIN ELEGIR" not in sin, "las franjas del bot no se ofrecen sobre un pedido de ella"


async def test_varios_pedidos_acordados_salen_todos_y_el_del_bot_primero(monkeypatch):
    abierto = _pedido(id=3131, estado="esperando_pago", origen="bot", items=[_item(producto="Pan Keto", variante_id=5, cantidad=1)])
    texto = await _estado(monkeypatch, [abierto, _pedido(id=3130), _pedido(id=3129, estado="preparando", origen="panel")])
    assert texto.index("ESPERANDO PAGO") < texto.index("Pedido #3130 YA ACORDADO") < texto.index("Pedido #3129 YA ACORDADO")
    assert "lo confirmó el negocio" in texto  # el de origen panel no dice "a mano"


async def test_solo_propuestas_pendientes_tambien_hablan(monkeypatch):
    """Ella ya le vendió y nadie tocó 'Sí' todavía: el bot no sabe QUÉ, pero sabe que no debe dar nada por hecho."""
    texto = await _estado(monkeypatch, [], propuestas=1)
    assert "ESTADO DEL CLIENTE" in texto and "aún NO están confirmados" in texto and "pedir_ayuda" in texto
    assert await _estado(monkeypatch, [], propuestas=0) == ""


async def test_las_ramas_viejas_siguen_iguales(monkeypatch):
    pagado = await _estado(monkeypatch, [_pedido(estado="pagado")])
    assert "YA PAGADO" in pagado and "YA ACORDADO" not in pagado
    cerrado = await _estado(monkeypatch, [_pedido(estado="entregado")])
    assert "ya se CERRÓ" in cerrado
    abierto = await _estado(monkeypatch, [_pedido(estado="pendiente", origen="bot")])
    assert "ARMADO pero SIN cobro" in abierto and "No tiene un pedido abierto" not in abierto


# ══════════════════════════════════════════════════════════════════════════════════
#  2. `ver_pedidos_cliente`: procedencia, entrega, pago y el total como dinero de herramienta
# ══════════════════════════════════════════════════════════════════════════════════

async def test_ver_pedidos_cliente_trae_el_expediente():
    ses = _Sesion([_pedido(entrega_fecha=date(2026, 9, 23), entrega_franja="en la tarde (2 a 5)")], pagos=[(3130, "reportado")])
    r = await tl.ver_pedidos_cliente(ses, TEL)
    (p,) = r["pedidos"]
    assert p["tomado_por"].startswith("una persona del negocio, a mano")
    assert p["total"] == "$16" and p["total_usd"] == 16.0, "el total va marcado como dinero: la red del TOTAL lo autoriza"
    assert p["entrega"] == {"fecha": "2026-09-23", "momento": "en la tarde (2 a 5)"}
    assert p["pago"].startswith("comprobante recibido")
    assert "no lo registres ni lo cobres otra vez" in r["nota"] and "dueñ" not in json.dumps(r).lower()


async def test_ver_pedidos_cliente_del_bot_sin_pago_ni_entrega():
    r = await tl.ver_pedidos_cliente(_Sesion([_pedido(origen="bot", estado="pendiente")]), TEL)
    (p,) = r["pedidos"]
    assert p["tomado_por"].startswith("el bot") and p["pago"] == "sin pago registrado" and p["entrega"] == "aún sin acordar"
    assert r["nota"] == ""


def test_la_descripcion_de_la_herramienta_invita_a_usarla_para_el_total():
    desc = next(t["function"]["description"] for t in tl.TOOL_SCHEMAS if t["function"]["name"] == "ver_pedidos_cliente")
    assert "a mano" in desc and "cuánto debe" in desc and "dueñ" not in desc.lower()


# ══════════════════════════════════════════════════════════════════════════════════
#  3. `cargar_contexto`: hasta 3 pedidos con procedencia; las propuestas pendientes cuentan
# ══════════════════════════════════════════════════════════════════════════════════

async def test_cargar_contexto_trae_los_pedidos_con_origen_franja_y_pago(monkeypatch):
    ses = _Sesion(
        [_pedido(id=40, estado="pagado", origen="bot", entrega_franja="en la mañana (10 a 12)"), _pedido(id=30)],
        pagos=[(30, "reportado"), (40, "reportado"), (40, "confirmado")], propuestas=0,
    )
    monkeypatch.setattr(fuentes_atencion, "get_session_factory", lambda: ses)
    ctx = await fuentes_atencion.cargar_contexto(TEL)
    assert [p["id"] for p in ctx.pedidos] == [40, 30] and ctx.pedido["id"] == 40
    assert ctx.pedidos[0]["pago_pendiente"] == "confirmado" and ctx.pedidos[0]["franja"] == "en la mañana (10 a 12)"
    assert ctx.pedidos[1]["origen"] == "dueña" and ctx.pedidos[1]["pago_pendiente"] == "reportado"
    assert ctx.propuestas_pendientes == 0 and ctx.humano_sin_acuerdo is False
    assert any("LIMIT" in q and "FROM pedidos" in q for q in ses.consultas)


async def test_propuestas_pendientes_marcan_humano_sin_acuerdo(monkeypatch):
    monkeypatch.setattr(fuentes_atencion, "get_session_factory", lambda: _Sesion([], propuestas=2))
    ctx = await fuentes_atencion.cargar_contexto(TEL)
    assert ctx.pedidos == [] and ctx.pedido is None
    assert ctx.propuestas_pendientes == 2 and ctx.humano_sin_acuerdo is True


def test_valor_actual_mira_todos_los_pedidos():
    ctx = Contexto(pedidos=[{"id": 40, "estado": "pagado"}, {"id": 30, "estado": "confirmado"}])
    ctx.pedido = ctx.pedidos[0]
    assert fuentes_atencion.valor_actual(ctx, Hecho("pedido", 30, "estado", "confirmado", "x")) == "confirmado"
    assert fuentes_atencion.valor_actual(ctx, Hecho("pedido", 99, "estado", "confirmado", "x")) is None


# ══════════════════════════════════════════════════════════════════════════════════
#  4. EL CEREBRO `confirmado`: seguir vendiendo, relevar lo dudoso, responder con datos
# ══════════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def contexto():
    return Contexto(
        hoy=HOY,
        productos={
            1: {"id": 1, "nombre": "Quesillo", "categoria": "postres", "duracion": None, "descripcion": "Quesillo",
                "se_congela": None, "apto_diabeticos": None, "disponibilidad": True,
                "variantes": {11: {"id": 11, "presentacion": "500 g", "precio": Decimal("8.00"), "sabores": None, "disponibilidad": True}}},
            2: {"id": 2, "nombre": "Empanadas", "categoria": "salado", "duracion": None, "descripcion": "Empanadas",
                "se_congela": None, "apto_diabeticos": None, "disponibilidad": True,
                "variantes": {21: {"id": 21, "presentacion": "x4", "precio": Decimal("7.00"), "sabores": None, "disponibilidad": True}}},
        },
        zonas={2: {"id": 2, "nombre": "Cabudare", "referencias": "La Mendera", "es_retiro": False, "costo": Decimal("2")}},
        metodos={"Zelle": {"id": 1, "tipo": "zelle"}},
    )


def _pedido_ctx(**kw):
    base = {"id": 30, "estado": "confirmado", "origen": "dueña", "items": [_item()], "fecha": None, "franja": "",
            "zona_id": 2, "referencia": None, "metodo": None, "total": Decimal("16"), "pago_pendiente": None}
    base.update(kw)
    return base


def _borrador(variante=21, producto=2, cantidad=1):
    return {"items": [{"producto_id": producto, "variante_id": variante, "cantidad": cantidad, "opciones": ""}],
            "fecha": "2026-09-23", "zona_id": 2, "referencia": "frente a la plaza", "metodo": "Zelle"}


class Entorno:
    def __init__(self, contexto, solicitud):
        self.ctx, self.solicitud = contexto, solicitud
        self.acciones, self.avisos = [], []
        self.registros = self.cobros = 0

    async def cargar(self, telefono):
        return copy.deepcopy(self.ctx)

    async def guardar(self, telefono, b):
        self.ctx.borrador = b
        return True

    async def verificar(self, telefono, hs):
        return True

    async def llm(self, messages, schemas, modelo):
        return {"choices": [{"message": {"tool_calls": [{"function": {
            "name": "proponer_turno", "arguments": json.dumps(self.solicitud)}}]}}]}

    async def ejecutar(self, nombre, args, tel):
        self.acciones.append((nombre, args))
        if nombre == "pedir_ayuda":
            self.ctx.pausado = True
            self.avisos.append(args)
            return {"ok": True, "pausado": True}
        if nombre == "registrar_pedido":
            self.registros += 1
            return {"ok": True, "pedido_id": 31, "resumen": "Empanadas: $7\nDelivery: $2\nTotal: $9"}
        if nombre == "generar_datos_pago":
            self.cobros += 1
            return {"ok": True, "resumen_cobro": "Por Zelle son $7.20.", "metodos_de_pago": [{"metodo": "Zelle", "correo": "negocio@example.test"}]}
        raise AssertionError(nombre)

    async def turno(self, mensaje, historial=None):
        return await atender("__prueba__", mensaje, historial, llm=self.llm, modelo="simulado", ejecutar=self.ejecutar,
                             cargar=self.cargar, guardar=self.guardar, verificar=self.verificar)


async def test_sobre_una_venta_pagada_los_otros_productos_son_venta_nueva(contexto):
    contexto.pedido = _pedido_ctx(estado="pagado", pago_pendiente="confirmado")
    contexto.pedidos = [contexto.pedido]
    contexto.borrador = _borrador()  # empanadas: OTRO producto
    e = Entorno(contexto, {"intencion": "registrar", "evidencia_accion": "anótalo"})
    r = await e.turno("anótalo")
    assert not r.relevo and e.registros == 1 and "Total: $9" in r
    assert e.acciones[0][1]["items"] == [{"variante_id": 21, "cantidad": 1, "opciones": ""}]


async def test_el_cobro_de_la_venta_nueva_va_al_pedido_nuevo_no_al_pagado(contexto):
    contexto.pedido = _pedido_ctx(estado="pagado", pago_pendiente="confirmado")
    contexto.borrador = _borrador()
    e = Entorno(contexto, {"intencion": "cobrar", "evidencia_accion": "dame los datos"})
    r = await e.turno("dame los datos")
    assert not r.relevo and e.registros == 1 and e.cobros == 1
    cobro = next(a for n, a in e.acciones if n == "generar_datos_pago")
    assert cobro["pedido_id"] == 31, "jamás el #30 que ya está pagado"


async def test_los_mismos_productos_sobre_lo_pagado_son_el_mismo_pedido(contexto):
    contexto.pedido = _pedido_ctx(estado="pagado", pago_pendiente="confirmado")
    contexto.borrador = _borrador(variante=11, producto=1, cantidad=2)  # 2× quesillo: lo mismo
    e = Entorno(contexto, {"intencion": "registrar", "evidencia_accion": "anótalo"})
    assert (await e.turno("anótalo")).relevo and e.registros == 0
    assert "no reconstruir ni cobrar" in e.avisos[0]["detalle"]


async def test_con_la_venta_a_medias_no_se_vende_encima(contexto):
    """Decisión de Maired (24-sep): acordado sin pago confirmado ⇒ agregar algo lo decide una persona."""
    contexto.pedido = _pedido_ctx()  # confirmado, tomado a mano, pago sin registrar
    contexto.borrador = _borrador()
    e = Entorno(contexto, {"intencion": "registrar", "evidencia_accion": "anótalo"})
    assert (await e.turno("anótalo")).relevo and e.registros == 0
    assert e.avisos[0]["motivo"] == "acuerdo_especial" and "Pedido #30" in e.avisos[0]["detalle"]


async def test_acordado_con_pago_confirmado_si_vende_lo_nuevo(contexto):
    contexto.pedido = _pedido_ctx(pago_pendiente="confirmado")
    contexto.borrador = _borrador()
    e = Entorno(contexto, {"intencion": "registrar", "evidencia_accion": "anótalo"})
    assert not (await e.turno("anótalo")).relevo and e.registros == 1


async def test_propuestas_pendientes_relevan_todo_lo_que_toca_la_venta(contexto):
    contexto.humano_sin_acuerdo = True
    contexto.propuestas_pendientes = 1
    contexto.pedido = _pedido_ctx(estado="pagado", pago_pendiente="confirmado")
    contexto.borrador = _borrador()
    for solicitud in (
        {"intencion": "registrar", "evidencia_accion": "anótalo"},
        {"intencion": "comprobante"},
        {"intencion": "consultar", "consultas": [{"tema": "estado_pedido"}]},
    ):
        e = Entorno(copy.deepcopy(contexto), solicitud)
        r = await e.turno("anótalo, ya pagué, cómo va mi pedido?")
        assert r.relevo and e.registros == e.cobros == 0
        assert "sin confirmar" in e.avisos[0]["detalle"]


async def test_ya_pague_sobre_un_pedido_tomado_a_mano(contexto):
    sin_pago = Entorno(copy.deepcopy(contexto), {"intencion": "comprobante"})
    sin_pago.ctx.pedido = _pedido_ctx()
    r = await sin_pago.turno("ya pagué")
    assert r.relevo and sin_pago.avisos[0]["motivo"] == "acuerdo_especial", "nadie afirma que llegó"
    con_captura = Entorno(copy.deepcopy(contexto), {"intencion": "comprobante"})
    con_captura.ctx.pedido = _pedido_ctx(pago_pendiente="reportado")
    assert "Ya tengo tu comprobante" in await con_captura.turno("ya pagué")


def test_estado_pedido_responde_con_los_datos_del_expediente(contexto):
    contexto.pedido = _pedido_ctx(fecha="2026-09-23", franja="en la tarde (2 a 5)", referencia="frente a la plaza")
    d = consultar(contexto, SimpleNamespace(tema="estado_pedido"), "cómo va mi pedido")
    assert d.tipo == "responder"
    assert d.texto == ("Tu pedido está confirmado. Lleva: 2× Quesillo (500 g). "
                       "Entrega: el miércoles 23 de septiembre en la tarde (2 a 5) en frente a la plaza.")
    assert {h.campo for h in d.hechos} == {"estado", "items", "fecha", "franja", "referencia"}
    assert "$" not in d.texto and "16" not in d.texto


def test_entrega_responde_o_releva_si_no_esta_acordada(contexto):
    contexto.pedido = _pedido_ctx(fecha="2026-09-23", franja="en la tarde (2 a 5)")
    d = consultar(contexto, SimpleNamespace(tema="entrega"), "a qué hora me llega?")
    assert d.tipo == "responder" and d.texto == "Tu entrega quedó el miércoles 23 de septiembre en la tarde (2 a 5)."
    contexto.pedido = _pedido_ctx()
    d = consultar(contexto, SimpleNamespace(tema="entrega"), "a qué hora me llega?")
    assert d.tipo == "relevo" and d.motivo == "acuerdo_especial" and "aún no acordada" in d.pendiente
    contexto.pedido = None
    assert consultar(contexto, SimpleNamespace(tema="entrega"), "?").tipo == "relevo"


def test_el_tema_entrega_existe_en_el_contrato():
    from typing import get_args

    from app.agent.contratos_atencion import Tema, TipoEventoDuena

    assert "entrega" in get_args(Tema) and "entregado" in get_args(TipoEventoDuena)


@pytest.mark.parametrize("estado,pago,items_viejos,esperado", [
    ("pagado", "confirmado", [_item()], True),                 # cerrada, otros productos
    ("entregado", None, [_item()], True),                      # cerrada
    ("pagado", "confirmado", [_item(variante_id=21, cantidad=1)], False),  # mismos productos
    ("confirmado", None, [_item()], False),                    # a medias
    ("confirmado", "confirmado", [_item()], True),             # acordado y pagado
    ("pagado", "confirmado", [], False),                       # sin items legibles no hay comparación
])
def test_es_venta_nueva(estado, pago, items_viejos, esperado):
    pedido = _pedido_ctx(estado=estado, pago_pendiente=pago, items=items_viejos)
    assert atencion._es_venta_nueva(_borrador(), pedido) is esperado
    assert atencion._es_venta_nueva({}, pedido) is False


def test_fecha_en_palabras():
    assert resolver_atencion.fecha_en_palabras("2026-09-23") == "el miércoles 23 de septiembre"
    assert resolver_atencion.fecha_en_palabras("mañana") == "mañana" and resolver_atencion.fecha_en_palabras(None) == ""


# ══════════════════════════════════════════════════════════════════════════════════
#  5. EL COMPROBANTE SE PEGA AL PEDIDO TOMADO A MANO (y se compara contra su total pactado)
# ══════════════════════════════════════════════════════════════════════════════════

async def test_get_pedido_esperando_pago_tambien_busca_el_tomado_a_mano():
    ses = _Sesion([_pedido()])
    assert (await tl.get_pedido_esperando_pago(ses, TEL)).id == 3130
    (sql,) = ses.consultas
    assert "esperando_pago" in sql and "origen" in sql and "CASE WHEN" in sql, "el esperando_pago del bot va primero"


async def test_el_monto_cobrado_de_un_pedido_a_mano_es_su_total_pactado(monkeypatch):
    monkeypatch.setattr(tasks.rc, "get_cache", AsyncMock(return_value=None))
    monkeypatch.setattr(tasks, "get_session_factory", lambda: _Sesion())
    monkeypatch.setattr(tl, "get_pedido_esperando_pago", AsyncMock(return_value=_pedido(cotizado_at=None)))
    assert await tasks._montos_cobrados(TEL) == (None, 16.0, None)


async def test_con_cotizacion_del_bot_el_respaldo_sigue_igual(monkeypatch):
    monkeypatch.setattr(tasks.rc, "get_cache", AsyncMock(return_value=None))
    cotizado = _pedido(id=40, origen="bot", estado="esperando_pago", cotizado_at=CREADO, cotizado_bs=Decimal("2000"),
                       cotizado_usd=Decimal("16"), cotizado_usd_divisas=Decimal("12.8"))
    monkeypatch.setattr(tasks, "get_session_factory", lambda: _Sesion([cotizado]))
    monkeypatch.setattr(tl, "get_pedido_esperando_pago", AsyncMock(return_value=cotizado))
    assert await tasks._montos_cobrados(TEL) == (2000.0, 16.0, 12.8)


# ══════════════════════════════════════════════════════════════════════════════════
#  6. EL TOQUE HUMANO NO FABRICA UN PEDIDO GEMELO
# ══════════════════════════════════════════════════════════════════════════════════

class _SesionEscritura:
    def __init__(self):
        self.added = []

    async def execute(self, _q):
        return _Res()

    def add(self, o):
        self.added.append(o)

    async def flush(self):
        for n, o in enumerate(self.added, start=1):
            if getattr(o, "id", None) is None:
                o.id = 900 + n

    async def get(self, modelo, pk):
        return None


def _prop(**kw):
    base = {"tipo": "pedido_tomado", "telefono": TEL, "evidencia": "te anoté 2 quesillos", "evidencia_mensaje_id": 3,
            "confianza": 1.0, "items": [{"producto_id": 1, "variante_id": 11, "nombre": "Quesillo", "presentacion": "500 g",
                                         "cantidad": 2, "precio_unitario": 8.0}], "total": 16.0}
    base.update(kw)
    return base


async def test_un_segundo_si_sobre_el_mismo_pedido_no_lo_duplica(monkeypatch):
    monkeypatch.setattr(ex, "_pedido_igual_reciente", AsyncMock(return_value=SimpleNamespace(id=77)))
    ses = _SesionEscritura()
    with pytest.raises(ValueError, match="ya existe el pedido #77"):
        await ex.aplicar_propuesta(ses, _prop(), usuario="maired@enova")
    assert ses.added == []
    ex._pedido_igual_reciente.assert_awaited_once()
    assert ex._pedido_igual_reciente.await_args.args[2] == [{"variante_id": 11, "cantidad": 2}]


async def test_sin_gemelo_el_pedido_se_crea(monkeypatch):
    monkeypatch.setattr(ex, "_pedido_igual_reciente", AsyncMock(return_value=None))
    ses = _SesionEscritura()
    r = await ex.aplicar_propuesta(ses, _prop(), usuario="u")
    assert r["tipo"] == "pedido_tomado" and ses.added[0].estado == "confirmado" and ses.added[0].origen == "dueña"


# ══════════════════════════════════════════════════════════════════════════════════
#  7. "YA TE LO ENTREGUÉ" → tipo `entregado`, como propuesta
# ══════════════════════════════════════════════════════════════════════════════════

def _validar(ev, texto, ctx, pedido=None):
    return ex.validar(ev, texto, ctx, telefono=TEL, hoy=HOY, franjas=["en la tarde (2 a 5)"], pedido=pedido, evidencia_id=7)


def test_entregado_es_propuesta_con_pedido_y_se_descarta_sin_el(contexto):
    ev = EventoDuena(tipo="entregado", evidencia="ya te lo entregué")
    v = _validar(ev, "🎤 listo mi reina ya te lo entregué", contexto, pedido={"id": 30, "estado": "confirmado"})
    assert v.accion == "propuesta" and v.propuesta.tipo == "entregado" and v.propuesta.pedido_id == 30
    assert _validar(ev, "ya te lo entregué", contexto, pedido=None).accion == "descarta"
    assert _validar(ev, "ya te lo entregué", contexto, pedido={"id": 30, "estado": "entregado"}).accion == "descarta"
    assert "ya entregó el pedido #30" in ex.resumen_humano(v.propuesta, tipo_mensaje="audio", hora="24-Sep 10:00")
    assert "entregado" in ex.INSTRUCCION


async def test_aplicar_entregado_cierra_el_pedido_y_respeta_cancelado():
    class _S(_SesionEscritura):
        def __init__(self, pedido):
            super().__init__()
            self.pedido = pedido

        async def get(self, modelo, pk):
            return self.pedido if pk == 30 else None

    abierto = SimpleNamespace(id=30, cliente_telefono=TEL, estado="confirmado", updated_at=None)
    r = await ex.aplicar_propuesta(_S(abierto), _prop(tipo="entregado", items=[], total=None, pedido_id=30), usuario="u")
    assert r == {"tipo": "entregado", "pedido_id": 30} and abierto.estado == "entregado" and abierto.updated_at is not None
    cancelado = SimpleNamespace(id=30, cliente_telefono=TEL, estado="cancelado", updated_at=None)
    with pytest.raises(ValueError, match="cancelado"):
        await ex.aplicar_propuesta(_S(cancelado), _prop(tipo="entregado", items=[], total=None, pedido_id=30), usuario="u")


# ══════════════════════════════════════════════════════════════════════════════════
#  8. RETOMAR LEE EL EXPEDIENTE
# ══════════════════════════════════════════════════════════════════════════════════

async def test_leer_expediente_resume_sin_dinero(monkeypatch):
    import app.services.db as db

    ses = _Sesion(
        [_pedido(entrega_fecha=date(2026, 9, 23), entrega_franja="en la tarde (2 a 5)")],
        pagos=[], propuestas=0,
    )
    ses.pedidos_ids = [SimpleNamespace(id=1)]

    async def _execute(q):
        sql = str(q)
        ses.consultas.append(sql)
        if "FROM intervenciones" in sql:
            return _Res([1])  # una propuesta pendiente (ids)
        if "FROM pagos" in sql:
            return _Res([])
        return _Res(ses.pedidos)

    ses.execute = _execute
    monkeypatch.setattr(db, "get_session_factory", lambda: ses)
    r = await ex.leer_expediente(TEL)
    assert r["venta_cerrada_a_mano"] is True and r["propuestas_pendientes"] == 1
    assert r["pedidos_a_mano"] == [{"id": 3130, "estado": "confirmado", "pago": None}]
    assert "pedido #3130 (confirmado): 2× Quesillo; entrega el 2026-09-23 en la tarde (2 a 5); pago SIN registrar." in r["hechos"]
    assert "1 dato(s)" in r["hechos"] and "$" not in r["hechos"] and "16" not in r["hechos"]


async def test_leer_expediente_sin_base_devuelve_vacio(monkeypatch):
    import app.services.db as db

    def _revienta():
        raise RuntimeError("sin base")

    monkeypatch.setattr(db, "get_session_factory", _revienta)
    r = await ex.leer_expediente(TEL)
    assert r == {"pedidos_a_mano": [], "propuestas_pendientes": 0, "venta_cerrada_a_mano": False, "hechos": ""}


def test_con_la_venta_cerrada_a_mano_el_acuse_cierra_incluso_su_pregunta():
    hist = [{"role": "assistant", "content": "[MENSAJE HUMANO DEL NEGOCIO AL CLIENTE] ¿te llegó bien mi reina?"},
            {"role": "user", "content": "Sí, gracias 🙏"}]
    assert tasks._hay_pendiente(hist, None) is True, "sin expediente, la casa terminó preguntando"
    assert tasks._hay_pendiente(hist, None, venta_cerrada_a_mano=True) is False
    hist[-1] = {"role": "user", "content": "y las empanadas cuánto valen?"}
    assert tasks._hay_pendiente(hist, None, venta_cerrada_a_mano=True) is True, "una pregunta real sigue viva"


async def test_retomar_lleva_el_hecho_del_expediente(monkeypatch):
    llamadas = []

    async def _true(*a, **k):
        return True

    async def _false(*a, **k):
        return False

    async def _nada(*a, **k):
        return None

    async def _pensar(telefono, instruccion, hist, nombre, **kw):
        llamadas.append(instruccion)
        return [{"ok": True, "texto": "r"}], "r"

    hist = [{"role": "assistant", "content": "[MENSAJE HUMANO DEL NEGOCIO AL CLIENTE] te lo llevo mañana"},
            {"role": "user", "content": "¿y cuánto sería en bolívares?"}]
    for f in ("adquirir_lock", "candado_retomar"):
        monkeypatch.setattr(tasks.rc, f, _true)
    monkeypatch.setattr(tasks.rc, "liberar_lock", _nada)
    monkeypatch.setattr(tasks.rc, "guardar_historial", _nada)
    monkeypatch.setattr(tasks.rc, "obtener_historial", AsyncMock(return_value=hist))
    monkeypatch.setattr(tasks, "_bot_activo", _true)
    monkeypatch.setattr(tasks, "_numero_permitido", _true)
    monkeypatch.setattr(tasks, "_cliente_pausado", _false)
    monkeypatch.setattr(tasks, "_ventana_abierta", _true)
    monkeypatch.setattr(tasks, "_guardar_en_panel", _nada)
    monkeypatch.setattr(tasks, "_pensar_y_enviar", _pensar)
    monkeypatch.setattr(tasks, "leer_expediente", AsyncMock(return_value={
        "pedidos_a_mano": [{"id": 30, "estado": "confirmado", "pago": None}], "propuestas_pendientes": 0,
        "venta_cerrada_a_mano": True, "hechos": "El negocio ya le tomó a mano el pedido #30 (confirmado): 2× Quesillo; entrega sin acordar; pago SIN registrar.",
    }))
    await tasks._retomar(TEL, "A", None)
    assert len(llamadas) == 1
    assert llamadas[0].startswith(tasks._INSTRUCCION_RETOMAR)
    assert "[HECHO] El negocio ya le tomó a mano el pedido #30" in llamadas[0]
