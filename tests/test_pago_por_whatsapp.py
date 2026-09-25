"""💰 LA PREGUNTA DEL PAGO POR WHATSAPP (PR6b + PR6c) — decisiones de Maired 24-sep, SESIONES (41)/(42).

Whuilianny NO entra al panel. Cuando el extractor lee que ella confirmó un pago (propuesta
`pago_confirmado`, SIEMPRE propuesta), el bot le PREGUNTA a su celular personal y su SÍ/NO aplica o
descarta la propuesta. Sin panel. El pago lo sigue confirmando una persona (ella), por su palabra a
una pregunta directa. El cliente NUNCA recibe un mensaje por este carril.

PR6c: UNA pregunta a la vez (la siguiente espera hasta que se resuelva la abierta), la pregunta NOMBRA
a la clienta y el pedido (sin códigos), y la dueña nunca es cliente del expediente.

Sin IA, sin red, sin base real: sesiones falsas y Meta simulada.
"""
import inspect
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent import expediente
from app.agent.contratos_atencion import PropuestaExpediente
from app.api import router as api
from app.services.meta_client import CODIGO_FUERA_DE_VENTANA, MetaRechazo
from app.webhook import parser
from app.webhook import router as webhook
from app.workers import tasks

TEL = "584240000000"
DUENA = "573005690062"
WAMID_PREG = "wamid.PREGUNTA.7"
RESP_META = {"messages": [{"id": WAMID_PREG}]}
AHORA_ISO = datetime.now(UTC).isoformat()               # preguntada hace nada → vigente
VIEJA_ISO = (datetime.now(UTC) - timedelta(hours=30)).isoformat()  # > 24 h → ya no bloquea


# ══ dobles ══

class _Res:
    def __init__(self, filas=(), escalar=None):
        self._filas = list(filas)
        self._escalar = escalar

    def scalars(self):
        return self

    def all(self):
        return list(self._filas)

    def first(self):
        if self._escalar is not None:
            return self._escalar
        return self._filas[0] if self._filas else None


def _pedido(id=30, total=14.0, items=None):
    return SimpleNamespace(
        id=id, total=total,
        items=items if items is not None else [{"producto": "Quesillo 200g", "cantidad": 2}],
    )


class _Sesion:
    """intervenciones → la lista de propuestas; clientes → el nombre; `get` por id (Intervencion o Pedido)."""

    def __init__(self, inters=(), nombre="Ana", pedido=None):
        self.inters = list(inters)
        self.nombre = nombre
        self.pedido = pedido if pedido is not None else _pedido()
        self.commits = 0
        self.rollbacks = 0
        self.sql = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, q):
        sql = str(q).lower()
        self.sql.append(sql)
        if "from intervenciones" in sql:
            return _Res(self.inters)
        if "from clientes" in sql:
            return _Res(escalar=self.nombre)
        return _Res()

    async def get(self, modelo, pk):
        if getattr(modelo, "__name__", "") == "Pedido":
            return self.pedido if (self.pedido and self.pedido.id == pk) else None
        return next((i for i in self.inters if i.id == pk), None)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def _propuesta(**kw):
    base = {"tipo": "pago_confirmado", "telefono": TEL, "pedido_id": 30, "monto": 14.0, "moneda": "$", "metodo": "Zelle"}
    base.update(kw)
    return base


def _inter(id=7, **kw):
    base = dict(
        id=id, motivo="propuesta_expediente", estado="pendiente", cliente_telefono=TEL,
        detalle="Parece que Whuilianny confirmó el pago", propuesta=_propuesta(),
        resuelta_at=None, aplicada_por=None, aplicada_at=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def meta(monkeypatch):
    """Meta simulada + la dueña resuelta + el panel mudo. `apply_async` de la cola se captura (no toca
    el broker: sin esto cada SÍ/NO tardaría 108 s). Devuelve el doble de `enviar_texto`, con
    `.siguiente` = lista de reencolados de `preguntar_pagos_pendientes`."""
    enviado = AsyncMock(return_value=RESP_META)
    monkeypatch.setattr(tasks, "enviar_texto", enviado)
    monkeypatch.setattr("app.services.dueno.telefono_de_la_duena", AsyncMock(return_value=DUENA))
    monkeypatch.setattr(tasks.rc, "notificar_conversacion", AsyncMock())
    enviado.siguiente = []
    monkeypatch.setattr(tasks.preguntar_pagos_pendientes, "apply_async",
                        lambda *a, **kw: enviado.siguiente.append(a))
    return enviado


def _montar(monkeypatch, *inters, nombre="Ana", pedido=None):
    ses = _Sesion(inters, nombre, pedido)
    monkeypatch.setattr(tasks, "get_session_factory", lambda: (lambda: ses))
    return ses


# ══ 1) el contrato acepta los dos campos nuevos (extra="forbid" los exigía) ══

def test_el_contrato_declara_pregunta_wamid_y_preguntada_at():
    p = PropuestaExpediente.model_validate(_propuesta(pregunta_wamid=WAMID_PREG, preguntada_at="2026-09-24T19:40:00"))
    assert p.pregunta_wamid == WAMID_PREG and p.preguntada_at == "2026-09-24T19:40:00"
    assert PropuestaExpediente.model_validate(_propuesta()).pregunta_wamid is None


# ══ 2) el parser captura la cita (context.id) ══

def test_parser_captura_context_id_cuando_la_duena_cita_la_pregunta():
    msg = {"id": "wamid.MSG", "from": DUENA, "type": "text", "text": {"body": "sí"}, "context": {"id": WAMID_PREG}}
    ev = parser._mensaje(msg, None)
    assert ev["context_id"] == WAMID_PREG and ev["texto"] == "sí"


def test_parser_context_id_es_none_sin_cita():
    ev = parser._mensaje({"id": "wamid.MSG", "from": TEL, "type": "text", "text": {"body": "hola"}}, "Ana")
    assert ev["context_id"] is None


# ══ 3) interpretar_respuesta_duena: SÍ/NO claros; lo ambiguo NO aplica un pago ══

@pytest.mark.parametrize("texto, esperado", [
    ("sí", ("si", None)), ("SÍ", ("si", None)), ("si", ("si", None)), ("Yes", ("si", None)),
    ("Si 3464", ("si", 3464)), ("sí #3464", ("si", 3464)), ("✅", ("si", None)), ("👍 7", ("si", 7)),
    ("no", ("no", None)), ("NO 3464", ("no", 3464)), ("❌", ("no", None)), ("👎", ("no", None)),
    ("no todavía", ("no", None)), ("sí, ya me llegó", ("si", None)),
])
def test_interpreta_si_y_no_claros(texto, esperado):
    assert expediente.interpretar_respuesta_duena(texto) == esperado


@pytest.mark.parametrize("texto", ["ok", "ya voy", "no sé", "No se si me llegó", "", None, "sí y no", "listo", "dale"])
def test_lo_ambiguo_devuelve_none(texto):
    assert expediente.interpretar_respuesta_duena(texto) is None


# ══ 4) cerrar_propuesta: el cierre es EL MISMO en el panel y por WhatsApp ══

def test_cerrar_propuesta_firma_quien_y_cuando():
    inter = _inter()
    expediente.cerrar_propuesta(inter, usuario="whuilianny (WhatsApp)", resultado="aplicada")
    assert inter.estado == "resuelta" and inter.resuelta_at is not None
    assert inter.aplicada_por == "whuilianny (WhatsApp)" and inter.aplicada_at == inter.resuelta_at
    assert inter.propuesta["resultado"] == "aplicada" and inter.propuesta["pedido_id"] == 30


def test_los_dos_endpoints_del_panel_usan_cerrar_propuesta():
    assert "cerrar_propuesta(inter, usuario=usuario, resultado=\"aplicada\")" in inspect.getsource(api.aplicar_propuesta_expediente)
    assert "cerrar_propuesta(inter, usuario=usuario, resultado=\"descartada\")" in inspect.getsource(api.descartar_propuesta_expediente)


# ══ 5) _preguntar_pagos_a_la_duena: manda una vez por propuesta y guarda el wamid ══

async def test_pregunta_una_vez_y_guarda_el_wamid(meta, monkeypatch):
    inter = _inter()
    ses = _montar(monkeypatch, inter)

    assert await tasks._preguntar_pagos_a_la_duena(TEL) == 1

    meta.assert_awaited_once()
    destino, texto = meta.await_args.args
    assert destino == DUENA
    # PR6c: nombra a la clienta y el pedido, sin código de propuesta.
    assert texto == "💰 ¿Te llegó el pago de $14 de Ana por el pedido #30 (2× Quesillo 200g)? Responde SÍ o NO."
    assert inter.propuesta["pregunta_wamid"] == WAMID_PREG and inter.propuesta["preguntada_at"]
    assert inter.estado == "pendiente" and ses.commits == 1  # sigue pendiente: la resuelve su SÍ/NO


async def test_sin_nombre_usa_la_cola_del_telefono(meta, monkeypatch):
    _montar(monkeypatch, _inter(), nombre=None)
    await tasks._preguntar_pagos_a_la_duena(TEL)
    assert "de …0000 por el pedido" in meta.await_args.args[1]


async def test_no_repregunta_la_que_ya_se_pregunto(meta, monkeypatch):
    _montar(monkeypatch, _inter(propuesta=_propuesta(pregunta_wamid="wamid.VIEJO")))
    assert await tasks._preguntar_pagos_a_la_duena(TEL) == 0
    meta.assert_not_awaited()


async def test_solo_pregunta_los_pagos_no_las_entregas(meta, monkeypatch):
    _montar(monkeypatch, _inter(propuesta={"tipo": "entrega_acordada", "telefono": TEL, "pedido_id": 30, "fecha": "2026-09-25"}))
    assert await tasks._preguntar_pagos_a_la_duena(TEL) == 0
    meta.assert_not_awaited()


async def test_ventana_cerrada_no_revienta_ni_marca(meta, monkeypatch):
    """Meta rechaza (131047): la propuesta queda en la Bandeja SIN wamid, para reintentar cuando ella abra."""
    meta.side_effect = MetaRechazo(0, CODIGO_FUERA_DE_VENTANA, "ventana cerrada")
    inter = _inter()
    ses = _montar(monkeypatch, inter)
    assert await tasks._preguntar_pagos_a_la_duena(TEL) == 0
    assert "pregunta_wamid" not in inter.propuesta and ses.commits == 0


async def test_sin_dueno_telefono_no_manda_nada(meta, monkeypatch):
    monkeypatch.setattr("app.services.dueno.telefono_de_la_duena", AsyncMock(return_value=""))
    _montar(monkeypatch, _inter())
    assert await tasks._preguntar_pagos_a_la_duena(TEL) == 0
    meta.assert_not_awaited()


async def test_la_cola_es_global_con_o_sin_telefono(meta, monkeypatch):
    """PR6c: la cola es global — sale la propuesta de pago MÁS VIEJA (menor id) aunque `telefono`
    apunte a otra clienta; ninguna preguntada todavía."""
    otra, mia = _inter(7, cliente_telefono="584249999999"), _inter(8)
    _montar(monkeypatch, otra, mia)
    assert await tasks._preguntar_pagos_a_la_duena(TEL) == 1  # se pasa TEL (la de #8) pero sale la #7
    assert otra.propuesta.get("pregunta_wamid") == WAMID_PREG
    assert "pregunta_wamid" not in mia.propuesta


async def test_solo_una_pregunta_de_pago_abierta_a_la_vez(meta, monkeypatch):
    """Con una ya preguntada y vigente, la siguiente NO sale: espera en la Bandeja."""
    abierta = _inter(7, propuesta=_propuesta(pregunta_wamid="wamid.A", preguntada_at=AHORA_ISO))
    _montar(monkeypatch, abierta, _inter(8))
    assert await tasks._preguntar_pagos_a_la_duena() == 0
    meta.assert_not_awaited()


async def test_una_abierta_de_mas_de_24h_no_bloquea_la_siguiente(meta, monkeypatch):
    """Una pregunta preguntada hace > 24 h deja de bloquear: sale la siguiente sin preguntar."""
    vieja = _inter(7, propuesta=_propuesta(pregunta_wamid="wamid.A", preguntada_at=VIEJA_ISO))
    nueva = _inter(8)
    _montar(monkeypatch, vieja, nueva)
    assert await tasks._preguntar_pagos_a_la_duena() == 1
    assert nueva.propuesta.get("pregunta_wamid") == WAMID_PREG


async def test_no_pregunta_por_un_pago_sobre_el_numero_de_la_duena(meta, monkeypatch):
    """(C) Una propuesta cuyo 'cliente' es el propio número de la dueña se ignora — ni se pregunta ni
    bloquea la cola; sale la de la clienta real."""
    de_la_duena = _inter(7, cliente_telefono=DUENA,
                         propuesta=_propuesta(telefono=DUENA, pregunta_wamid="wamid.A", preguntada_at=AHORA_ISO))
    real = _inter(8)
    _montar(monkeypatch, de_la_duena, real)
    assert await tasks._preguntar_pagos_a_la_duena() == 1
    assert real.propuesta.get("pregunta_wamid") == WAMID_PREG


async def test_la_pregunta_avisa_si_el_monto_no_cuadra_con_el_pedido(meta, monkeypatch):
    _montar(monkeypatch, _inter(propuesta=_propuesta(monto=14.0)), pedido=_pedido(total=20.0))
    await tasks._preguntar_pagos_a_la_duena()
    assert "(el pedido es de $20)" in meta.await_args.args[1]


async def test_sin_pedido_la_pregunta_no_lo_nombra(meta, monkeypatch):
    _montar(monkeypatch, _inter(propuesta=_propuesta(pedido_id=None)))
    await tasks._preguntar_pagos_a_la_duena()
    texto = meta.await_args.args[1]
    assert "por el pedido" not in texto and texto == "💰 ¿Te llegó el pago de $14 de Ana? Responde SÍ o NO."


def test_la_pregunta_no_lleva_codigo_de_propuesta(meta, monkeypatch):
    # El #30 del PEDIDO sí puede ir; lo que no va es "(#7)" (el id de la propuesta).
    texto = tasks._texto_pregunta_pago(_propuesta(), "Ana", TEL, _pedido())
    assert "(#7)" not in texto and "Responde SÍ o NO." in texto


async def test_una_base_caida_no_tumba_al_worker(meta, monkeypatch):
    def _revienta():
        raise RuntimeError("postgres caído")
    monkeypatch.setattr(tasks, "get_session_factory", _revienta)
    assert await tasks._preguntar_pagos_a_la_duena(TEL) == 0
    meta.assert_not_awaited()


def test_el_extractor_pregunta_los_pagos_al_terminar():
    assert "await _preguntar_pagos_a_la_duena(telefono)" in inspect.getsource(tasks._extraer_expediente)


# ══ 6) _responder_pago_duena: su SÍ/NO aplica o descarta, y le confirma a ELLA ══

@pytest.fixture
def aplicado(monkeypatch):
    doble = AsyncMock(return_value={"tipo": "pago_confirmado", "pedido_id": 30, "pago_id": 5})
    monkeypatch.setattr(expediente, "aplicar_propuesta", doble)
    return doble


def _preguntada(id=7, wamid=WAMID_PREG, preguntada_at=AHORA_ISO, **kw):
    return _inter(id, propuesta=_propuesta(pregunta_wamid=wamid, preguntada_at=preguntada_at, **kw))


async def test_si_citando_la_pregunta_aplica_cierra_firmado_y_le_confirma(meta, aplicado, monkeypatch):
    inter = _preguntada()
    ses = _montar(monkeypatch, inter)

    assert await tasks._responder_pago_duena("sí", WAMID_PREG, "wamid.MSG") == "si"

    aplicado.assert_awaited_once_with(ses, inter.propuesta if False else aplicado.await_args.args[1], usuario="whuilianny (WhatsApp)")
    assert aplicado.await_args.args[1]["pedido_id"] == 30 and aplicado.await_args.args[1]["pregunta_wamid"] == WAMID_PREG
    assert inter.estado == "resuelta" and inter.aplicada_por == "whuilianny (WhatsApp)"
    assert inter.propuesta["resultado"] == "aplicada" and ses.commits == 1
    meta.assert_awaited_once_with(DUENA, "✅ Listo: el pedido #30 quedó pagado.")
    tasks.rc.notificar_conversacion.assert_awaited_once_with(TEL, "actualizada")


async def test_si_con_el_numero_elige_esa_entre_varias(meta, aplicado, monkeypatch):
    a, b = _preguntada(7, "wamid.A"), _preguntada(8, "wamid.B", pedido_id=31)
    _montar(monkeypatch, a, b)
    assert await tasks._responder_pago_duena("SÍ 8", None, "wamid.MSG") == "si"
    assert aplicado.await_args.args[1]["pedido_id"] == 31
    assert b.estado == "resuelta" and a.estado == "pendiente"


async def test_si_a_secas_con_una_sola_pendiente_la_aplica(meta, aplicado, monkeypatch):
    inter = _preguntada()
    _montar(monkeypatch, inter)
    assert await tasks._responder_pago_duena("Sí", None, "wamid.MSG") == "si"
    assert inter.estado == "resuelta" and aplicado.await_count == 1


async def test_un_numero_que_no_casa_no_manda_a_otra_parte(meta, aplicado, monkeypatch):
    """'sí 16' (los $16) con una sola pendiente #7: el 16 no es un id → cae a la única pendiente."""
    inter = _preguntada()
    _montar(monkeypatch, inter)
    assert await tasks._responder_pago_duena("sí me llegaron 16", None, "wamid.MSG") == "si"
    assert inter.estado == "resuelta"


async def test_varias_abiertas_sin_pista_pide_citar_y_no_toca_nada(meta, aplicado, monkeypatch):
    a, b = _preguntada(7, "wamid.A"), _preguntada(8, "wamid.B")
    ses = _montar(monkeypatch, a, b)
    assert await tasks._responder_pago_duena("sí", None, "wamid.MSG") == "varias"
    aplicado.assert_not_awaited()
    assert a.estado == b.estado == "pendiente" and ses.commits == 0
    destino, texto = meta.await_args.args
    assert destino == DUENA and "más de un pago" in texto and "citando" in texto
    assert meta.siguiente == []  # no se resolvió nada: no se libera la cola


async def test_no_descarta_sin_escribir_nada(meta, aplicado, monkeypatch):
    inter = _preguntada()
    ses = _montar(monkeypatch, inter)
    assert await tasks._responder_pago_duena("no", WAMID_PREG, "wamid.MSG") == "no"
    aplicado.assert_not_awaited()
    assert inter.estado == "resuelta" and inter.propuesta["resultado"] == "descartada"
    assert inter.aplicada_por == "whuilianny (WhatsApp)" and ses.commits == 1
    meta.assert_awaited_once_with(DUENA, "👍 Anotado: ese pago NO se registra.")


async def test_si_aplicar_falla_se_descarta_con_motivo_y_no_traba_la_cola(meta, aplicado, monkeypatch):
    """PR6c: un ValueError (ya pagado, cancelado…) NO deja la pregunta abierta bloqueando la cola: se
    cierra descartada con el motivo, se le avisa a ella, y se libera la siguiente."""
    aplicado.side_effect = ValueError("el pedido #30 ya tiene un pago confirmado (#4)")
    inter = _preguntada()
    ses = _montar(monkeypatch, inter)
    assert await tasks._responder_pago_duena("sí", WAMID_PREG, "wamid.MSG") == "error"
    assert inter.estado == "resuelta" and inter.propuesta["resultado"] == "descartada"
    assert "No se pudo aplicar por WhatsApp" in inter.detalle
    assert ses.rollbacks == 1 and ses.commits == 1
    meta.assert_awaited_once_with(DUENA, "No pude registrar ese pago: el pedido #30 ya tiene un pago confirmado (#4)")
    assert len(meta.siguiente) == 1  # la cola se libera igual


async def test_lo_que_no_es_respuesta_no_hace_nada(meta, aplicado, monkeypatch):
    ses = _montar(monkeypatch, _preguntada())
    assert await tasks._responder_pago_duena("ok", None, "wamid.MSG") == "no_es_respuesta"
    aplicado.assert_not_awaited()
    meta.assert_not_awaited()
    assert ses.commits == 0


async def test_sin_pendiente_preguntada_no_hace_nada(meta, aplicado, monkeypatch):
    """Una propuesta pendiente pero SIN preguntar no se aplica por un 'sí' suelto: ella no vio la pregunta."""
    ses = _montar(monkeypatch, _inter())
    assert await tasks._responder_pago_duena("sí", None, "wamid.MSG") == "sin_candidata"
    aplicado.assert_not_awaited()
    assert ses.commits == 0


async def test_jamas_escribe_al_cliente(meta, aplicado, monkeypatch):
    a, b = _preguntada(7, "wamid.A"), _preguntada(8, "wamid.B")
    _montar(monkeypatch, a, b)
    await tasks._responder_pago_duena("sí", None, "wamid.MSG")   # ayuda
    await tasks._responder_pago_duena("no 7", None, "wamid.MSG")  # descarta
    await tasks._responder_pago_duena("sí 8", None, "wamid.MSG")  # aplica
    assert meta.await_count == 3
    assert all(call.args[0] == DUENA for call in meta.await_args_list)


async def test_una_base_caida_no_tumba_la_respuesta(meta, aplicado, monkeypatch):
    def _revienta():
        raise RuntimeError("postgres caído")
    monkeypatch.setattr(tasks, "get_session_factory", _revienta)
    assert await tasks._responder_pago_duena("sí", WAMID_PREG, "wamid.MSG") == "error"
    aplicado.assert_not_awaited()


async def test_al_resolver_por_whatsapp_se_encola_la_siguiente(meta, aplicado, monkeypatch):
    """PR6c: tras aplicar (o descartar) la pregunta abierta, sale la siguiente de la cola."""
    _montar(monkeypatch, _preguntada())
    await tasks._responder_pago_duena("sí", WAMID_PREG, "wamid.MSG")
    assert len(meta.siguiente) == 1
    meta.siguiente.clear()
    _montar(monkeypatch, _preguntada())
    await tasks._responder_pago_duena("no", WAMID_PREG, "wamid.MSG")
    assert len(meta.siguiente) == 1


async def test_una_respuesta_suelta_no_va_a_una_pregunta_vieja(meta, aplicado, monkeypatch):
    """Un 'sí' a secas NO resuelve una pregunta de > 24 h (ya no está abierta); una CITA sí."""
    vieja = _preguntada(preguntada_at=VIEJA_ISO)
    _montar(monkeypatch, vieja)
    assert await tasks._responder_pago_duena("sí", None, "wamid.MSG") == "sin_candidata"
    aplicado.assert_not_awaited()
    # pero citándola (context_id) sí se resuelve, aunque sea vieja
    _montar(monkeypatch, _preguntada(preguntada_at=VIEJA_ISO))
    assert await tasks._responder_pago_duena("sí", WAMID_PREG, "wamid.MSG") == "si"


async def test_el_extractor_no_abre_expediente_sobre_el_numero_de_la_duena(meta, monkeypatch):
    """(C) Si la dueña se escribe a sí misma, el extractor no le abre expediente."""
    assert await tasks._extraer_expediente(DUENA) == "duena"


# ══ 7) el webhook: la dueña escribe → reintento siempre; su SÍ/NO se encola, idempotente ══

@pytest.fixture
def cola(monkeypatch):
    reintentos, respuestas = [], []
    monkeypatch.setattr(tasks.preguntar_pagos_pendientes, "apply_async", lambda *a, **kw: reintentos.append(a))
    monkeypatch.setattr(tasks.responder_pago_duena, "apply_async", lambda args, **kw: respuestas.append(tuple(args)))
    monkeypatch.setattr("app.services.redis_client.ya_procesado", AsyncMock(return_value=False))
    return reintentos, respuestas


def _msg(texto="sí", tipo="text", context_id=WAMID_PREG, message_id="wamid.MSG"):
    return {"clase": "mensaje", "message_id": message_id, "telefono": DUENA, "nombre": None, "tipo": tipo,
            "texto": texto, "media_id": None, "caption": None, "mime_type": None, "timestamp": None,
            "context_id": context_id}


async def test_su_si_encola_la_respuesta_con_la_cita_y_el_reintento(cola):
    reintentos, respuestas = cola
    await webhook._atender_a_la_duena(_msg("sí"))
    assert len(reintentos) == 1
    assert respuestas == [("sí", WAMID_PREG, "wamid.MSG")]


async def test_un_hola_solo_encola_el_reintento(cola):
    reintentos, respuestas = cola
    await webhook._atender_a_la_duena(_msg("hola", context_id=None))
    assert len(reintentos) == 1 and respuestas == []


async def test_una_nota_de_voz_no_se_lee_como_respuesta(cola):
    reintentos, respuestas = cola
    await webhook._atender_a_la_duena(_msg(None, tipo="audio"))
    assert len(reintentos) == 1 and respuestas == []


async def test_idempotente_por_message_id(cola, monkeypatch):
    _, respuestas = cola
    monkeypatch.setattr("app.services.redis_client.ya_procesado", AsyncMock(return_value=True))
    await webhook._atender_a_la_duena(_msg("sí"))
    assert respuestas == []


async def test_una_cola_caida_no_tumba_el_webhook(cola, monkeypatch):
    def _revienta(*a, **kw):
        raise RuntimeError("redis caído")
    monkeypatch.setattr(tasks.preguntar_pagos_pendientes, "apply_async", _revienta)
    monkeypatch.setattr(tasks.responder_pago_duena, "apply_async", _revienta)
    await webhook._atender_a_la_duena(_msg("sí"))  # no lanza


def test_procesar_entrante_atiende_a_la_duena_antes_de_devolver_duena():
    src = inspect.getsource(webhook._procesar_entrante)
    assert src.index("await _atender_a_la_duena(mensaje)") < src.index('return "duena"')
