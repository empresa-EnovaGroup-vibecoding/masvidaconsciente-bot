"""EL DOMINGO QUE VOLVIÓ — y por qué la herramienta sola no bastó.

**LA SEGUNDA VEZ** (2026-08-22, 18:55, prueba de Maired **después** de desplegar el calendario):

    🤖 "Te las dejo para mañana domingo, o prefieres el lunes?"

🔴 **Y las llamadas a `proxima_fecha_entrega` en esa conversación fueron CERO.** El modelo tenía
la herramienta activa en su lista, leyó en su descripción que era **OBLIGATORIA** antes de nombrar
cualquier fecha, y calculó el día de cabeza igual.

Eso es **L40 al pie de la letra** —*el prompt SUGIERE, el código IMPIDE*— y es la lección que este
proyecto ya había aprendido tres veces (el sabor, el nombre completo, la hora). La herramienta le
da al modelo la posibilidad de acertar; **esta red le quita la de equivocarse**: el calendario se
consulta desde el CÓDIGO en cada turno, sin esperar a que el modelo se acuerde.

Maired reportó dos cosas más en el mismo mensaje, y las dos están aquí:
  · *"Los domingos no se trabaja **y no se deja al cliente la opción de decidir el día**"*
  · *"Debe decir para el día lunes que se le entregará"* — se AFIRMA, no se pregunta.
"""

import pytest

from app.agent.agent import _dias_imposibles, _dias_nombrados

# El calendario REAL de aquel sábado 22 a las 18:55 (ya pasada la hora de corte).
CAL_SABADO_TARDE = {
    "ok": True,
    "hoy_es": "sábado 22 de agosto",
    "hoy_se_puede_entregar": False,
    "proximas_fechas": [
        {"fecha": "2026-08-24", "cuando": "lunes 24 de agosto"},
        {"fecha": "2026-08-25", "cuando": "martes 25 de agosto"},
        {"fecha": "2026-08-26", "cuando": "miércoles 26 de agosto"},
    ],
}


# ══════════════════════════════════════════════════════════════════════════════════
#  EL CASO REAL
# ══════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_la_frase_exacta_que_escribio_el_bot():
    """La del reporte de Maired, copiada tal cual del chat."""
    malos = await _dias_imposibles(
        "Te las dejo para mañana domingo, o prefieres el lunes?", CAL_SABADO_TARDE
    )
    assert malos, "no cazó el domingo — es el bug exacto que reportó Maired"
    assert any("domingo" in m for m in malos)


@pytest.mark.asyncio
async def test_el_domingo_SOLO_sin_la_palabra_manana():
    """🔴 SOBREDETERMINACIÓN (L47): la frase real del bot lleva "mañana" Y "domingo", así que la
    cazan DOS caminos y romper uno no se nota. Se vio al revertir: quitando "domingo" de los días
    detectados, el test del caso real seguía verde por culpa de "mañana".
    Este caso nombra el domingo SIN "mañana", y por eso sí distingue."""
    assert await _dias_imposibles("Te las dejo el domingo entonces.", CAL_SABADO_TARDE) != []


@pytest.mark.asyncio
async def test_el_lunes_solo_NO_dispara():
    """La respuesta correcta no puede frenarse: el lunes sí es día de entrega."""
    assert await _dias_imposibles("Perfecto, te las dejo para el lunes.", CAL_SABADO_TARDE) == []


@pytest.mark.asyncio
async def test_manana_cuando_manana_es_domingo():
    """Sin nombrar el día: "mañana" un sábado cae domingo, y eso también se caza."""
    malos = await _dias_imposibles("Te lo tengo listo mañana temprano.", CAL_SABADO_TARDE)
    assert malos and "manana" in malos[0]


@pytest.mark.asyncio
async def test_hoy_cuando_ya_paso_la_hora():
    """A las 18:55 ya no salen entregas: prometer hoy es prometer lo que no se puede."""
    assert await _dias_imposibles("Te lo dejo para hoy mismo.", CAL_SABADO_TARDE) != []


# ══════════════════════════════════════════════════════════════════════════════════
#  LO QUE **NO** DEBE TOCAR — frenar de más aquí cuesta ventas
# ══════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("texto", [
    "Las galletas duran 2 semanas.",              # duración, no fecha
    "Te atiendo de 8 de la mañana a 6 de la tarde.",  # "la mañana" es una HORA, no un día
    "Buenos días, en qué te ayudo?",
    "Son aptas para diabéticos.",
    "Te lo dejo para el martes.",                 # martes SÍ está en el calendario
])
@pytest.mark.asyncio
async def test_no_dispara_en_mensajes_normales(texto):
    assert await _dias_imposibles(texto, CAL_SABADO_TARDE) == [], f"frenó de más: {texto!r}"


@pytest.mark.asyncio
async def test_fail_open_sin_calendario():
    """🔴 Si no hay calendario (falló la BD), la red CALLA. Frenar una venta por no poder
    comprobar sería peor que el bug que arregla."""
    assert await _dias_imposibles("Te lo dejo para mañana domingo", None) == []
    assert await _dias_imposibles("Te lo dejo para mañana domingo", {}) == []
    assert await _dias_imposibles("Te lo dejo para mañana domingo", {"ok": False}) == []


def test_detecta_los_dias_por_su_nombre():
    assert "domingo" in _dias_nombrados("te las dejo el domingo")
    assert "manana" in _dias_nombrados("te lo tengo mañana")
    assert "pasado manana" in _dias_nombrados("te lo tengo pasado mañana")
    # "de la mañana" es una hora, no un día
    assert "manana" not in _dias_nombrados("abrimos a las 8 de la mañana")
