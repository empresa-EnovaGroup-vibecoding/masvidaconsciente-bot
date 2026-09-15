"""Regresiones del corpus real: eventos técnicos y autoría de Whuilianny."""
from __future__ import annotations

import pytest

from app.services.memoria import mensaje_owner_para_historial
from app.webhook import router
from app.workers import tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("tipo", ["reaction", "edit", "revoke"])
async def test_evento_tecnico_se_guarda_sin_responder(monkeypatch, tipo):
    acciones: list[str] = []

    async def falso(*_a, **_k):
        return False

    async def nada(*_a, **_k):
        return None

    async def guardar(mensaje):
        acciones.append(f"guardar:{mensaje['tipo']}")

    async def no_debe_marcar(*_a, **_k):
        acciones.append("escribiendo")

    async def no_debe_encolar(*_a, **_k):
        acciones.append("agente")
        return "mal"

    monkeypatch.setattr(router, "_es_la_duena", falso)
    monkeypatch.setattr(router, "_es_contacto_privado", falso)
    monkeypatch.setattr(router, "_marcar_entrante", nada)
    monkeypatch.setattr(router, "_excede_tope", falso)
    monkeypatch.setattr(router, "_guardar_sin_responder", guardar)
    monkeypatch.setattr(router, "_marcar_leido_si_vamos_a_responder", no_debe_marcar)
    monkeypatch.setattr(router, "_encolar_evento", no_debe_encolar)

    resultado = await router._procesar_entrante({
        "clase": "mensaje",
        "message_id": f"wamid-{tipo}",
        "telefono": "584120000000",
        "nombre": "Cliente",
        "tipo": tipo,
        "texto": "❤️" if tipo == "reaction" else None,
        "media_id": None,
        "caption": None,
        "mime_type": None,
        "timestamp": "1789459200",
    })

    assert resultado == "evento_silencioso"
    assert acciones == [f"guardar:{tipo}"]


def test_mensaje_de_whuilianny_conserva_la_autoria():
    memoria = mensaje_owner_para_historial("Dios te multiplique, mi mami está mejor")
    assert memoria.startswith("[MENSAJE HUMANO DEL NEGOCIO AL CLIENTE]")
    assert "Dios te multiplique, mi mami está mejor" in memoria
    assert memoria.endswith("[FIN DEL MENSAJE HUMANO DEL NEGOCIO]")


def test_marca_de_autoria_es_idempotente():
    una = mensaje_owner_para_historial("Te lo llevo el lunes")
    assert mensaje_owner_para_historial(una) == una


@pytest.mark.asyncio
async def test_fallo_tecnico_de_audio_avisa_y_no_pide_repetir(monkeypatch):
    avisos: list[dict] = []
    respuestas: list[str] = []

    async def descarga_rota(*_a, **_k):
        raise RuntimeError("proveedor caído")

    async def un_fallo(*_a, **_k):
        return 1

    async def avisar(_telefono, **datos):
        avisos.append(datos)

    async def responder(_telefono, entrada, _nombre):
        respuestas.append(entrada)

    monkeypatch.setattr(tasks, "descargar_media", descarga_rota)
    monkeypatch.setattr(tasks.rc, "contar_audio_fallido", un_fallo)
    monkeypatch.setattr(tasks, "_avisar_a_la_duena", avisar)
    monkeypatch.setattr(tasks, "_responder_y_enviar", responder)

    await tasks._procesar_audio("584120000000", "media-1", "Cliente", "audio/ogg")

    assert len(avisos) == 1
    assert avisos[0]["motivo"] == "audio_sin_procesar"
    assert len(respuestas) == 1
    assert "problema técnico nuestro" in respuestas[0]
    assert "NO le pidas que mande otra nota" in respuestas[0]


@pytest.mark.asyncio
async def test_segundo_audio_vacio_abre_relevo(monkeypatch):
    avisos: list[dict] = []

    async def descargar(*_a, **_k):
        return b"audio", "audio/ogg"

    async def vacio(*_a, **_k):
        return ""

    async def segundo(*_a, **_k):
        return 2

    async def avisar(_telefono, **datos):
        avisos.append(datos)

    async def nada(*_a, **_k):
        return None

    monkeypatch.setattr(tasks, "descargar_media", descargar)
    monkeypatch.setattr(tasks, "transcribir_audio", vacio)
    monkeypatch.setattr(tasks.rc, "contar_audio_fallido", segundo)
    monkeypatch.setattr(tasks, "_avisar_a_la_duena", avisar)
    monkeypatch.setattr(tasks, "_responder_y_enviar", nada)

    await tasks._procesar_audio("584120000000", "media-2", "Cliente", "audio/ogg")

    assert len(avisos) == 1
    assert "dos audios seguidos" in avisos[0]["detalle"]
