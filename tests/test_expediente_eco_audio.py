"""🎤 LA NOTA DE VOZ DE LA DUEÑA SE TRANSCRIBE EN VIVO (expediente, PR2) — SESIONES (36).

En el corpus real la dueña mandó 877 notas de voz y el bot vio "[nota de voz]" en TODAS: en ellas
coordina las entregas, y por eso el bot contradecía cosas que ella ya había dicho. Desde este PR el
eco de audio encola una transcripción (mismo `transcribir_audio` del cliente) que reemplaza el
placeholder EN SU SITIO en `mensajes` y en la memoria Redis. Lo que estos tests fijan:

  · el eco de audio ENCOLA la transcripción, solo si la burbuja es nueva; el de texto no; y si
    encolar falla, el eco igual termina (la pausa y la burbuja ya están);
  · el worker pregunta si el contacto es PRIVADO y falla CERRADO: sin respuesta de la base, no se
    transcribe; privado, no se transcribe;
  · un audio que Meta ya borró deja el placeholder y NO avisa a nadie;
  · la transcripción se guarda solo si el placeholder sigue ahí (idempotente) y en Redis se
    reemplaza la entrada exacta, conservando el orden;
  · el respaldo de Postgres devuelve la nota de voz transcrita de la dueña, no el placeholder.
"""
import inspect
import json
from unittest.mock import AsyncMock

import pytest

from app.services import memoria
from app.services import redis_client as rc
from app.webhook import parser
from app.webhook import router as webhook
from app.workers import tasks

TEL = "584240000000"
WA = "wamid.eco.audio.1"
TRANSCRITO = "🎤 te lo llevo mañana en la tarde"


class _Res:
    def __init__(self, escalar=None, rowcount=1):
        self._escalar = escalar
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return self._escalar

    def scalars(self):
        return self

    def all(self):
        return []


class _Sesion:
    """Sirve resultados EN ORDEN de una lista compartida (varios `factory()` la comparten)."""

    def __init__(self, resultados, registro):
        self._resultados = resultados
        self.registro = registro

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, q):
        self.registro.append(q)
        return self._resultados.pop(0) if self._resultados else _Res()

    def add(self, obj):
        self.registro.append(obj)

    async def commit(self):
        return None


def _eco(tipo="audio", media_id="MEDIA1"):
    return {
        "clase": "eco", "message_id": WA, "telefono": TEL, "tipo": tipo, "texto": None,
        "media_id": media_id, "caption": None, "mime_type": "audio/ogg",
        "timestamp": "1758565200",
    }


# ══════════════════════════════════════════════════════════════════════════════════
#  EL WEBHOOK: el eco de audio ENCOLA (y nada más)
# ══════════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def eco_montado(monkeypatch):
    """`_procesar_eco` con Redis, Meta y Postgres de mentira. Devuelve (encolado, resultados)."""
    import app.services.db as db
    import app.services.meta_client as meta

    encolado: list = []
    resultados: list = []
    registro: list = []
    monkeypatch.setattr(rc, "get_cache", AsyncMock(return_value=None))
    monkeypatch.setattr(rc, "set_cache", AsyncMock())
    monkeypatch.setattr(rc, "guardar_historial", AsyncMock())
    monkeypatch.setattr(rc, "notificar_conversacion", AsyncMock())
    monkeypatch.setattr(meta, "es_mensaje_propio", AsyncMock(return_value=False))
    monkeypatch.setattr(
        db, "get_session_factory", lambda: (lambda: _Sesion(resultados, registro))
    )
    monkeypatch.setattr(
        tasks.transcribir_eco, "apply_async", lambda args, **kw: encolado.append(tuple(args))
    )
    # 🗂️ El extractor del expediente (PR3) también se encola desde el eco. Sin este doble, Celery
    # intenta hablar con un broker real y cada test tarda 108 s en rendirse (lo pasó el 22-sep).
    extractor: list = []
    monkeypatch.setattr(
        tasks.extraer_expediente, "apply_async",
        lambda args, **kw: extractor.append((tuple(args), kw.get("countdown"))),
    )
    return encolado, resultados, extractor


async def test_el_eco_de_audio_de_la_duena_encola_la_transcripcion_y_el_extractor(eco_montado):
    encolado, _, extractor = eco_montado
    assert await webhook._procesar_eco(_eco()) == "eco"
    assert encolado == [(TEL, WA, "MEDIA1", "audio/ogg")]
    assert extractor == [((TEL, None), 90)]  # 90 s: que la transcripción aterrice y ella termine


async def test_el_eco_de_texto_no_transcribe_pero_si_va_al_extractor(eco_montado):
    encolado, _, extractor = eco_montado
    eco = _eco(tipo="text", media_id=None)
    eco["texto"] = "listo mi reina, ya me llegó"
    assert await webhook._procesar_eco(eco) == "eco"
    assert encolado == []
    assert extractor == [((TEL, None), 90)]


async def test_un_eco_de_foto_o_sticker_no_encola_nada(eco_montado):
    encolado, _, extractor = eco_montado
    eco = _eco(tipo="image", media_id="IMG1")
    assert await webhook._procesar_eco(eco) == "eco"
    assert encolado == [] and extractor == []


async def test_un_reintento_de_meta_no_transcribe_dos_veces(eco_montado):
    """La burbuja ya existía (`on_conflict_do_nothing` → rowcount 0): ni memoria, ni transcripción,
    ni extractor."""
    encolado, resultados, extractor = eco_montado
    resultados.extend([_Res(), _Res(), _Res(), _Res(rowcount=0)])  # mio, pausa, avisos, burbuja
    assert await webhook._procesar_eco(_eco()) == "eco"
    assert encolado == [] and extractor == []
    rc.guardar_historial.assert_not_awaited()


async def test_si_encolar_falla_el_eco_igual_termina(eco_montado, monkeypatch):
    """La pausa y la burbuja son lo que no se puede perder; la transcripción y el extractor son
    mejoras: si el broker está caído, el eco termina igual."""
    def _revienta(*a, **kw):
        raise RuntimeError("broker caído")

    monkeypatch.setattr(tasks.transcribir_eco, "apply_async", _revienta)
    monkeypatch.setattr(tasks.extraer_expediente, "apply_async", _revienta)
    assert await webhook._procesar_eco(_eco()) == "eco"
    rc.set_cache.assert_awaited()  # el candado del eco se puso igual


# ══════════════════════════════════════════════════════════════════════════════════
#  EL WORKER: privado falla cerrado, caducado calla, y el reemplazo es en su sitio
# ══════════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def worker(monkeypatch):
    """`_transcribir_eco` con Meta, Gemini y Redis de mentira. Devuelve (resultados, registro)."""
    resultados: list = []
    registro: list = []
    monkeypatch.setattr(tasks, "get_session_factory", lambda: (lambda: _Sesion(resultados, registro)))
    monkeypatch.setattr(tasks, "abrir_turno", lambda *a, **k: "turno")
    monkeypatch.setattr(tasks, "descargar_media", AsyncMock(return_value=(b"ogg", "audio/ogg")))
    monkeypatch.setattr(
        tasks, "transcribir_audio", AsyncMock(return_value="  te lo llevo mañana en la tarde ")
    )
    monkeypatch.setattr(rc, "reemplazar_en_historial", AsyncMock(return_value=True))
    monkeypatch.setattr(rc, "notificar_conversacion", AsyncMock())
    monkeypatch.setattr(
        tasks, "_avisar_a_la_duena",
        AsyncMock(side_effect=AssertionError("una nota de voz caducada no es una alarma")),
    )
    return resultados, registro


async def test_la_transcripcion_reemplaza_el_placeholder_en_postgres_y_en_redis(worker):
    resultados, registro = worker
    resultados.extend([_Res(escalar=False), _Res(rowcount=1)])  # no es privado; el UPDATE pegó

    assert await tasks._transcribir_eco(TEL, WA, "MEDIA1", "audio/ogg") == "ok"

    # El UPDATE apunta al mensaje EXACTO y solo si sigue siendo el placeholder (idempotente).
    params = set(registro[1].compile().params.values())
    assert {WA, "owner", parser.PLACEHOLDER_AUDIO, TRANSCRITO} <= params
    # En Redis se cambia la entrada heredada por el eco, no se apila otra.
    rc.reemplazar_en_historial.assert_awaited_once_with(
        TEL,
        memoria.mensaje_owner_para_historial(parser.PLACEHOLDER_AUDIO),
        memoria.mensaje_owner_para_historial(TRANSCRITO),
    )
    rc.notificar_conversacion.assert_awaited_once()


async def test_un_contacto_privado_no_se_transcribe(worker):
    resultados, registro = worker
    resultados.append(_Res(escalar=True))
    assert await tasks._transcribir_eco(TEL, WA, "MEDIA1", None) == "privado"
    tasks.descargar_media.assert_not_awaited()
    assert len(registro) == 1  # solo la pregunta; ni UPDATE ni nada


async def test_sin_poder_preguntar_si_es_privado_no_se_transcribe(monkeypatch, worker):
    """Falla CERRADO: la voz de un familiar no sale de casa por un hipo de la base."""
    def _explota():
        raise RuntimeError("bd caída")

    monkeypatch.setattr(tasks, "get_session_factory", _explota)
    assert await tasks._transcribir_eco(TEL, WA, "MEDIA1", None) == "sin_verificar"
    tasks.descargar_media.assert_not_awaited()


async def test_un_audio_que_meta_ya_borro_deja_el_placeholder_y_no_avisa(worker):
    resultados, registro = worker
    resultados.append(_Res(escalar=False))
    tasks.descargar_media.side_effect = RuntimeError("(#100) media does not exist — 404")
    assert await tasks._transcribir_eco(TEL, WA, "MEDIA1", None) == "caducado"
    tasks.transcribir_audio.assert_not_awaited()
    assert len(registro) == 1  # no hubo UPDATE
    rc.reemplazar_en_historial.assert_not_awaited()


async def test_una_transcripcion_vacia_no_toca_nada(worker):
    resultados, registro = worker
    resultados.append(_Res(escalar=False))
    tasks.transcribir_audio.return_value = "   "
    assert await tasks._transcribir_eco(TEL, WA, "MEDIA1", None) == "vacio"
    assert len(registro) == 1
    rc.reemplazar_en_historial.assert_not_awaited()


async def test_si_alguien_ya_la_transcribio_no_se_repite(worker):
    """El UPDATE no pegó (rowcount 0): otro reintento ya lo hizo. Redis no se toca dos veces."""
    resultados, _ = worker
    resultados.extend([_Res(escalar=False), _Res(rowcount=0)])
    assert await tasks._transcribir_eco(TEL, WA, "MEDIA1", None) == "ya_transcrito"
    rc.reemplazar_en_historial.assert_not_awaited()


def test_caducado_se_reconoce_por_el_error_de_meta():
    assert tasks._media_caducada(RuntimeError("Client error '404 Not Found'"))
    assert tasks._media_caducada(RuntimeError("(#100) media does not exist"))
    assert not tasks._media_caducada(RuntimeError("Server error '503'"))


# ══════════════════════════════════════════════════════════════════════════════════
#  REDIS: reemplazar EN SU SITIO
# ══════════════════════════════════════════════════════════════════════════════════

class _RedisLista:
    def __init__(self, filas):
        self.filas = list(filas)
        self.lsets: list[int] = []

    async def lrange(self, _clave, _a, _b):
        return list(self.filas)

    async def lset(self, _clave, i, valor):
        self.filas[i] = valor
        self.lsets.append(i)


async def test_reemplazar_en_historial_cambia_la_entrada_exacta_y_conserva_el_orden(monkeypatch):
    viejo = memoria.mensaje_owner_para_historial(parser.PLACEHOLDER_AUDIO)
    nuevo = memoria.mensaje_owner_para_historial(TRANSCRITO)
    falso = _RedisLista([
        json.dumps({"role": "user", "content": "hola"}),
        json.dumps({"role": "assistant", "content": viejo}),
        json.dumps({"role": "user", "content": "y a qué hora?"}),
    ])
    monkeypatch.setattr(rc, "_client", lambda: falso)

    assert await rc.reemplazar_en_historial(TEL, viejo, nuevo) is True
    assert falso.lsets == [1]
    assert [json.loads(f)["content"] for f in falso.filas] == ["hola", nuevo, "y a qué hora?"]
    assert json.loads(falso.filas[1])["role"] == "assistant"  # la autoría no cambia
    # Ya reemplazada: no está, y NO se apila nada al final.
    assert await rc.reemplazar_en_historial(TEL, viejo, nuevo) is False
    assert len(falso.filas) == 3


async def test_con_dos_notas_de_voz_seguidas_se_reemplaza_la_mas_reciente(monkeypatch):
    viejo = memoria.mensaje_owner_para_historial(parser.PLACEHOLDER_AUDIO)
    falso = _RedisLista([
        json.dumps({"role": "assistant", "content": viejo}),
        json.dumps({"role": "user", "content": "?"}),
        json.dumps({"role": "assistant", "content": viejo}),
    ])
    monkeypatch.setattr(rc, "_client", lambda: falso)
    assert await rc.reemplazar_en_historial(TEL, viejo, "🎤 segunda") is True
    assert falso.lsets == [2]


# ══════════════════════════════════════════════════════════════════════════════════
#  EL RESPALDO Y EL PLACEHOLDER
# ══════════════════════════════════════════════════════════════════════════════════

def test_el_placeholder_tiene_nombre_propio_y_es_el_mismo_en_todas_partes():
    assert parser.PLACEHOLDER_AUDIO == "[nota de voz]"
    assert parser.contenido_seguro("audio", None, None) == parser.PLACEHOLDER_AUDIO


def test_el_respaldo_devuelve_la_nota_de_voz_transcrita_de_la_duena():
    """`historial_desde_postgres` filtraba `tipo='text'`: la nota de voz transcrita de la dueña
    conserva tipo 'audio' (el panel muestra el reproductor) y se reconoce por la marca 🎤."""
    fuente = inspect.getsource(memoria.historial_desde_postgres)
    assert 'Mensaje.rol == "owner"' in fuente
    assert 'Mensaje.tipo == "audio"' in fuente
    assert 'like("🎤%")' in fuente
