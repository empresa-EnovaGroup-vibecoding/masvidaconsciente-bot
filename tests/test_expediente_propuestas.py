"""🗂️ LA PROPUESTA CON UN TOQUE (PR3) — SESIONES (37).

"Sí, es correcto" aplica la propuesta por la única puerta de escritura y la cierra firmada; "No" la
cierra sin escribir nada. Ninguna de las dos despausa, retoma ni avisa a nadie: una propuesta nunca
fue un chat tomado. Y los dos disparadores del extractor (eco del celular y mensaje desde el panel)
quedan fijados en la fuente.
"""
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.agent import expediente
from app.api import router as api
from app.services import redis_client as rc
from app.webhook import router as webhook
from app.workers import tasks

TEL = "584240000000"


class _Sesion:
    def __init__(self, inter):
        self.inter = inter
        self.commits = 0
        self.rollbacks = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, modelo, pk):
        return self.inter if pk == 7 else None

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def _inter(**kw):
    base = dict(
        id=7, motivo="propuesta_expediente", estado="pendiente", cliente_telefono=TEL,
        propuesta={"tipo": "pago_confirmado", "telefono": TEL, "pedido_id": 30, "monto": 14.0},
        resuelta_at=None, aplicada_por=None, aplicada_at=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def panel(monkeypatch):
    # 💰 PR6c: al resolver, el panel reencola la siguiente pregunta de pago. Se captura `apply_async`
    # (sin esto Celery iría al broker real y cada test tardaría 108 s). `encolados` lo deja verificar.
    encolados = []
    monkeypatch.setattr(tasks.preguntar_pagos_pendientes, "apply_async", lambda *a, **kw: encolados.append(a))

    def _montar(inter):
        ses = _Sesion(inter)
        monkeypatch.setattr(api, "get_session_factory", lambda: (lambda: ses))
        monkeypatch.setattr(rc, "notificar_conversacion", AsyncMock())
        monkeypatch.setattr(
            api, "_disparar_retomar",
            lambda *a: (_ for _ in ()).throw(AssertionError("una propuesta no retoma nada")),
        )
        ses.encolados = encolados
        return ses
    return _montar


async def test_si_es_correcto_aplica_y_cierra_firmado_sin_tocar_la_pausa(panel, monkeypatch):
    inter = _inter()
    ses = panel(inter)
    aplicado = AsyncMock(return_value={"tipo": "pago_confirmado", "pedido_id": 30, "pago_id": 5})
    monkeypatch.setattr(expediente, "aplicar_propuesta", aplicado)

    r = await api.aplicar_propuesta_expediente(7, usuario="maired@enova")

    assert r == {"ok": True, "bot_reactivado": False, "tipo": "pago_confirmado", "pedido_id": 30, "pago_id": 5}
    aplicado.assert_awaited_once_with(ses, {"tipo": "pago_confirmado", "telefono": TEL, "pedido_id": 30, "monto": 14.0}, usuario="maired@enova")
    assert inter.estado == "resuelta" and inter.aplicada_por == "maired@enova" and inter.aplicada_at is not None
    assert inter.propuesta["resultado"] == "aplicada" and ses.commits == 1
    rc.notificar_conversacion.assert_awaited_once_with(TEL, "actualizada")


async def test_si_aplicar_falla_devuelve_409_legible_y_la_propuesta_sigue_pendiente(panel, monkeypatch):
    inter = _inter()
    ses = panel(inter)
    monkeypatch.setattr(expediente, "aplicar_propuesta", AsyncMock(side_effect=ValueError("el pedido #30 está cancelado")))
    with pytest.raises(HTTPException) as exc:
        await api.aplicar_propuesta_expediente(7, usuario="u")
    assert exc.value.status_code == 409 and "cancelado" in exc.value.detail
    assert inter.estado == "pendiente" and ses.rollbacks == 1 and ses.commits == 0


async def test_aplicar_rechaza_lo_que_no_es_propuesta_o_ya_se_atendio(panel):
    panel(_inter(motivo="chat_tomado", propuesta=None))
    with pytest.raises(HTTPException) as exc:
        await api.aplicar_propuesta_expediente(7, usuario="u")
    assert exc.value.status_code == 409
    panel(_inter(estado="resuelta"))
    with pytest.raises(HTTPException) as exc:
        await api.aplicar_propuesta_expediente(7, usuario="u")
    assert exc.value.status_code == 409
    panel(_inter())
    with pytest.raises(HTTPException) as exc:
        await api.aplicar_propuesta_expediente(99, usuario="u")
    assert exc.value.status_code == 404


async def test_no_cierra_sin_escribir_nada_y_deja_quien_la_descarto(panel, monkeypatch):
    inter = _inter()
    ses = panel(inter)
    monkeypatch.setattr(expediente, "aplicar_propuesta", AsyncMock(side_effect=AssertionError("no debe escribir")))
    r = await api.descartar_propuesta_expediente(7, usuario="maired@enova")
    assert r == {"ok": True, "bot_reactivado": False, "resultado": "descartada"}
    assert inter.estado == "resuelta" and inter.aplicada_por == "maired@enova"
    assert inter.propuesta["resultado"] == "descartada" and inter.propuesta["pedido_id"] == 30
    assert ses.commits == 1


async def test_descartar_tambien_exige_una_propuesta_pendiente(panel):
    panel(_inter(estado="resuelta"))
    with pytest.raises(HTTPException) as exc:
        await api.descartar_propuesta_expediente(7, usuario="u")
    assert exc.value.status_code == 409


async def test_el_panel_libera_la_siguiente_pregunta_al_resolver_un_pago(panel, monkeypatch):
    """💰 PR6c: resolver una propuesta de PAGO desde el panel deja salir la siguiente pregunta de la
    cola. Una propuesta que NO es pago no toca la cola."""
    ses = panel(_inter())  # tipo pago_confirmado
    monkeypatch.setattr(expediente, "aplicar_propuesta",
                        AsyncMock(return_value={"tipo": "pago_confirmado", "pedido_id": 30, "pago_id": 5}))
    await api.aplicar_propuesta_expediente(7, usuario="maired@enova")
    assert len(ses.encolados) == 1

    ses2 = panel(_inter())
    await api.descartar_propuesta_expediente(7, usuario="maired@enova")
    assert len(ses2.encolados) == 2  # misma lista compartida: +1 al descartar

    ses3 = panel(_inter(propuesta={"tipo": "entrega_acordada", "telefono": TEL, "pedido_id": 30, "fecha": "2026-09-25"}))
    await api.descartar_propuesta_expediente(7, usuario="maired@enova")
    assert len(ses3.encolados) == 2  # una entrega NO libera la cola de pagos


# ══ Cableados fijados en la fuente (patrón R50) ══

def test_la_bandeja_entrega_la_propuesta_al_panel():
    assert '"propuesta": i.propuesta' in inspect.getsource(api.listar_intervenciones)


def test_las_palancas_del_expediente_son_de_la_proveedora():
    for clave in ("modelo_extractor", "expediente_escritura"):
        assert clave in api.CLAVES_CONFIG and clave in api.CLAVES_PROVEEDORA


def test_los_dos_disparadores_encolan_el_extractor():
    """El eco del celular (texto o audio) y el mensaje desde el panel: los dos caminos por los que
    habla la dueña, los dos leen 90 s después."""
    eco = inspect.getsource(webhook._procesar_eco)
    panel_ = inspect.getsource(api.responder_como_dueña)
    assert "extraer_expediente.apply_async" in eco and "countdown=90" in eco
    assert "extraer_expediente.apply_async" in panel_ and "countdown=90" in panel_


def test_el_default_del_expediente_es_auto_y_el_modelo_es_el_barato():
    """24-sep noche (SESIONES (41)): lo claro se anota solo; pagos y lo dudoso siguen siendo propuesta
    (eso lo fija `validar`, no este interruptor). `propuestas` y `off` siguen existiendo como palancas."""
    from app.config import get_settings

    assert expediente.ESCRITURA_DEFAULT == "auto"
    assert set(expediente.ESCRITURAS) == {"off", "propuestas", "auto"}
    assert "flash-lite" in get_settings().openrouter_model_extractor
