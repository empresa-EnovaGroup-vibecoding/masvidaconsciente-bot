"""Candados de la mejora del panel de conversaciones (8-sep-2026).

El caso real que la motivó: Amanda estaba en la lista blanca, pero su chat seguía firmado como
`pausado_por='dueña'`. El panel solo decía "Tú" y parecía que la lista blanca no funcionaba.
"""
import pytest

from app.api.router import (
    _cola_numero,
    _estado_numero_lista,
    _normalizar_numero_lista,
    router,
)
from app.models import Cliente
from app.services import redis_client as rc


def _cliente(*, pausado=False, por=None, privado=False):
    return Cliente(
        telefono="584125198777",
        nombre="Amanda",
        bot_pausado=pausado,
        pausado_por=por,
        privado=privado,
    )


def test_lista_blanca_acepta_formato_humano_y_compara_como_el_worker():
    assert _normalizar_numero_lista("+58 412-519-8777") == "584125198777"
    assert _cola_numero("+58 412-519-8777") == "4125198777"


@pytest.mark.parametrize("valor", ["", "Amanda", "0412"])
def test_lista_blanca_rechaza_valores_que_no_son_un_whatsapp_completo(valor):
    with pytest.raises(ValueError):
        _normalizar_numero_lista(valor)


@pytest.mark.parametrize(
    ("cliente", "esperado"),
    [
        (None, "esperando_primer_mensaje"),
        (_cliente(), "bot_listo"),
        (_cliente(pausado=True, por="dueña"), "atendido_por_ti"),
        (_cliente(pausado=True, por="bot"), "bot_pide_ayuda"),
        (_cliente(privado=True), "privado"),
    ],
)
def test_estado_de_lista_blanca_explica_quien_atiende(cliente, esperado):
    assert _estado_numero_lista(cliente) == esperado


def test_ruta_sse_no_es_confundida_con_un_numero_de_telefono():
    rutas = [r.path for r in router.routes]
    assert rutas.index("/api/conversaciones-eventos") < rutas.index(
        "/api/conversaciones/{telefono}"
    )


@pytest.mark.asyncio
async def test_aviso_visual_no_tumba_el_flujo_si_redis_falla(monkeypatch):
    class RedisCaido:
        async def publish(self, *_args, **_kwargs):
            raise ConnectionError("redis reiniciando")

    monkeypatch.setattr(rc, "_client", lambda: RedisCaido())
    await rc.notificar_conversacion("584125198777", "mensaje")
