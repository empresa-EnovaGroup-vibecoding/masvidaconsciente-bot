"""💰 LA PREGUNTA DEL PAGO POR WHATSAPP (PR6b) — decisión de Maired 24-sep, SESIONES (41).

Whuilianny NO entra al panel. Cuando el extractor lee que ella confirmó un pago (propuesta
`pago_confirmado`, SIEMPRE propuesta), el bot le PREGUNTA a su celular personal y su SÍ/NO aplica o
descarta la propuesta. Sin panel. El pago lo sigue confirmando una persona (ella), por su palabra a
una pregunta directa. El cliente NUNCA recibe un mensaje por este carril.

Sin IA, sin red, sin base real: sesiones falsas y Meta simulada.
"""
import inspect
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


class _Sesion:
    """intervenciones → la lista de propuestas; clientes → el nombre; `get` por id."""

    def __init__(self, inters=(), nombre="Ana"):
        self.inters = list(inters)
        self.nombre = nombre
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
        propuesta=_propuesta(), resuelta_at=None, aplicada_por=None, aplicada_at=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def meta(monkeypatch):
    """Meta simulada + la dueña resuelta + el panel mudo. Devuelve el doble de `enviar_texto`."""
    enviado = AsyncMock(return_value=RESP_META)
    monkeypatch.setattr(tasks, "enviar_texto", enviado)
    monkeypatch.setattr("app.services.dueno.telefono_de_la_duena", AsyncMock(return_value=DUENA))
    monkeypatch.setattr(tasks.rc, "notificar_conversacion", AsyncMock())
    return enviado


def _montar(monkeypatch, *inters, nombre="Ana"):
    ses = _Sesion(inters, nombre)
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
    assert texto == "💰 ¿Te llegó el pago de $14 por Zelle de Ana? Responde SÍ o NO. (#7)"
    assert inter.propuesta["pregunta_wamid"] == WAMID_PREG and inter.propuesta["preguntada_at"]
    assert inter.estado == "pendiente" and ses.commits == 1  # sigue pendiente: la resuelve su SÍ/NO


async def test_sin_nombre_usa_la_cola_del_telefono(meta, monkeypatch):
    _montar(monkeypatch, _inter(), nombre=None)
    await tasks._preguntar_pagos_a_la_duena(TEL)
    assert "de …0000?" in meta.await_args.args[1]


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


async def test_con_telefono_filtra_ese_cliente_y_sin_telefono_las_recorre_todas(meta, monkeypatch):
    ses = _montar(monkeypatch, _inter())
    await tasks._preguntar_pagos_a_la_duena(TEL)
    assert "intervenciones.cliente_telefono = :" in ses.sql[0]  # el WHERE, no la lista de columnas
    ses2 = _montar(monkeypatch, _inter(propuesta=_propuesta(pregunta_wamid="x")))
    await tasks._preguntar_pagos_a_la_duena(None)
    assert "intervenciones.cliente_telefono = :" not in ses2.sql[0]


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


def _preguntada(id=7, wamid=WAMID_PREG, **kw):
    return _inter(id, propuesta=_propuesta(pregunta_wamid=wamid, preguntada_at="2026-09-24T19:40:00", **kw))


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


async def test_varias_sin_pista_pide_ayuda_y_no_toca_nada(meta, aplicado, monkeypatch):
    a, b = _preguntada(7, "wamid.A"), _preguntada(8, "wamid.B")
    ses = _montar(monkeypatch, a, b)
    assert await tasks._responder_pago_duena("sí", None, "wamid.MSG") == "varias"
    aplicado.assert_not_awaited()
    assert a.estado == b.estado == "pendiente" and ses.commits == 0
    destino, texto = meta.await_args.args
    assert destino == DUENA and "varios pagos" in texto and "número" in texto


async def test_no_descarta_sin_escribir_nada(meta, aplicado, monkeypatch):
    inter = _preguntada()
    ses = _montar(monkeypatch, inter)
    assert await tasks._responder_pago_duena("no", WAMID_PREG, "wamid.MSG") == "no"
    aplicado.assert_not_awaited()
    assert inter.estado == "resuelta" and inter.propuesta["resultado"] == "descartada"
    assert inter.aplicada_por == "whuilianny (WhatsApp)" and ses.commits == 1
    meta.assert_awaited_once_with(DUENA, "👍 Anotado: ese pago NO se registra.")


async def test_si_aplicar_falla_se_lo_dice_y_la_propuesta_sigue_pendiente(meta, aplicado, monkeypatch):
    aplicado.side_effect = ValueError("el pedido #30 ya tiene un pago confirmado (#4)")
    inter = _preguntada()
    ses = _montar(monkeypatch, inter)
    assert await tasks._responder_pago_duena("sí", WAMID_PREG, "wamid.MSG") == "error"
    assert inter.estado == "pendiente" and ses.rollbacks == 1 and ses.commits == 0
    meta.assert_awaited_once_with(DUENA, "No pude registrar ese pago: el pedido #30 ya tiene un pago confirmado (#4)")


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
