"""LA FRANJA DE LA MADRUGADA — el bug que Maired vio a las 00:35 del 24-ago.

Ella escribió "Buenas noches" a las 00:35 y el bot contestó **"buenos días"**. El modelo
obedeció: `_saludo_hora_texto()` le inyectaba literalmente "son las 00:35 (buenos días)",
porque la franja era `h < 12 → buenos días` — la madrugada no existía.

En Venezuela (y en todo el español) a las 00:35 se dice "buenas noches": la madrugada
(00:00–05:59) sigue siendo noche. "Buenos días" empieza a las 06:00.
"""
from datetime import datetime

from app.agent.system_prompt import _saludo_hora_texto


def _franja(h: int, m: int = 0) -> str:
    return _saludo_hora_texto(ahora=datetime(2026, 8, 24, h, m))


def test_el_caso_LITERAL_de_maired():
    """00:35 → "buenas noches". Es el mensaje 6932 del 24-ago."""
    assert "buenas noches" in _franja(0, 35), _franja(0, 35)


def test_la_madrugada_entera_es_noche():
    for h in (0, 1, 2, 3, 4, 5):
        assert "buenas noches" in _franja(h), f"{h}:00 → {_franja(h)}"


def test_la_manana():
    for h in (6, 7, 9, 11):
        assert "buenos días" in _franja(h), f"{h}:00 → {_franja(h)}"


def test_la_tarde():
    for h in (12, 15, 18):
        assert "buenas tardes" in _franja(h), f"{h}:00 → {_franja(h)}"


def test_la_noche():
    for h in (19, 21, 23):
        assert "buenas noches" in _franja(h), f"{h}:00 → {_franja(h)}"


def test_los_bordes():
    assert "buenas noches" in _franja(5, 59)
    assert "buenos días" in _franja(6, 0)
    assert "buenos días" in _franja(11, 59)
    assert "buenas tardes" in _franja(12, 0)
    assert "buenas tardes" in _franja(18, 59)
    assert "buenas noches" in _franja(19, 0)


def test_sin_parametro_sigue_funcionando():
    """El uso real (sin `ahora`) no puede romperse: devuelve una franja válida."""
    t = _saludo_hora_texto()
    assert "HORA EN VENEZUELA" in t
    assert any(f in t for f in ("buenos días", "buenas tardes", "buenas noches"))
