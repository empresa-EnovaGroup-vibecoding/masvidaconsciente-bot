"""💾 La telemetría anota la LECTURA y la ESCRITURA de caché (043, SESIONES (50)).

Los `usage` de abajo son los REALES que devolvió OpenRouter en la sonda del 14-oct
(`scripts/sonda_cache.py`, Sonnet 4.6 por "Claude Platform on AWS"): la 1ª llamada escribe la caché
($0,16), la 2ª la lee ($0,011). Sin la escritura anotada no se distingue un fallo de caché.
"""
from decimal import Decimal

from app.services.telemetria import _tomar_usage

ESCRIBE = {
    "model": "anthropic/claude-sonnet-4.6", "provider": "Claude Platform on AWS",
    "usage": {
        "prompt_tokens": 26781, "completion_tokens": 60, "cost": 0.159525,
        "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 26094},
    },
}
LEE = {
    "model": "anthropic/claude-sonnet-4.6", "provider": "Claude Platform on AWS",
    "usage": {
        "prompt_tokens": 26781, "completion_tokens": 60, "cost": 0.0107892,
        "prompt_tokens_details": {"cached_tokens": 26094, "cache_write_tokens": 0},
    },
}


def test_la_llamada_que_falla_la_cache_queda_anotada_como_escritura():
    u = _tomar_usage(ESCRIBE)
    assert u["tokens_cache"] == 0 and u["tokens_cache_escritura"] == 26094
    assert u["costo_usd"] == Decimal("0.159525")


def test_la_que_lee_la_cache_no_escribe():
    u = _tomar_usage(LEE)
    assert u["tokens_cache"] == 26094 and u["tokens_cache_escritura"] == 0


def test_sin_el_campo_queda_en_cero():
    assert _tomar_usage({"usage": {"prompt_tokens": 10}})["tokens_cache_escritura"] == 0
    assert _tomar_usage(None)["tokens_cache_escritura"] == 0


def test_la_columna_existe_en_el_modelo_y_en_la_migracion():
    from pathlib import Path

    from app.models import LlamadaIA

    assert "tokens_cache_escritura" in LlamadaIA.__table__.columns
    sql = (Path(__file__).resolve().parent.parent / "migrations" / "043_tokens_cache_escritura.sql").read_text(
        encoding="utf-8"
    )
    assert "ADD COLUMN IF NOT EXISTS tokens_cache_escritura" in sql
