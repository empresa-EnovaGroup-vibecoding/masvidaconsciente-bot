"""EL HILO, EXTENDIDO A TAMAÑO Y SABOR (rama C) — lo ya elegido no se repregunta.

🔴 LA MISMA CLASE QUE EL #6 (la masa de yuca), en las otras dos elecciones pre-registro. El #6
sigue la VERSIÓN que vive en el NOMBRE del producto; esto sigue el TAMAÑO (`presentacion`) y el
SABOR (`variantes.sabores`), que viven en las casillas de la BD. El hueco es idéntico: entre
que el cliente dice "de 250, de limón" y que la tool registra, la elección vive solo como chat
crudo y una ficha fresca (info_producto trae los 3 tamaños y los 8 sabores) la reabre.

Lo que fija esta suite:
  1. El reconocedor de sabor es de vocabulario CERRADO y distingue por tokens (`_sabores_tocados`).
  2. La atribución por producto — reusada del #6 — evita el bug del Kéfir de cabra vs el sabor
     'queso de cabra' (ROADMAP rama C): un sabor solo cuenta para el producto que el mensaje
     nombra, o si ningún otro lo reclama.
  3. La más reciente gana; nombrar DOS valores de una dimensión la deja SIN elección.
  4. El carril: `responder()` inyecta la elección en la línea EL HILO DE LA VENTA.

🔒 El tamaño NO es palanca de dinero aquí: la línea solo evita la repregunta. El precio sigue
naciendo del `variante_id` al registrar y la RED DEL TAMAÑO ADIVINADO sigue vigilando — por eso
esta suite también comprueba los FALSOS POSITIVOS (que 'de 250' no se le pegue al producto
equivocado, que '1' —cantidad— no cuente como tamaño).
"""
import pytest

import app.agent.agent as ag
from app.agent.agent import (
    _sabores_tocados,
    elecciones_de_variante_en,
)

# Un catálogo enriquecido de mentira, con la forma REAL de la BD del taller (verificada por SSH).
CATALOGO = [
    {
        "nombre": "Torta baja en carbohidratos",
        "tamanos": ["250g", "500g", "1kg"],
        "sabores": ["limón", "zanahoria", "naranja", "piña", "vainilla"],
    },
    {
        "nombre": "Empanadas de masa de yuca o de masa de plátano",
        "tamanos": [],
        "sabores": ["carne mechada", "pollo", "queso de cabra"],
    },
]
# El catálogo COMPLETO de nombres (para la atribución) incluye productos SIMPLES que no están en
# el enriquecido — entre ellos el Kéfir de cabra, que es la trampa del bug que la regla evita.
NOMBRES = [
    "Torta baja en carbohidratos",
    "Empanadas de masa de yuca o de masa de plátano",
    "Kéfir de cabra",
    "Pan de sándwich",
]


def _u(texto):
    return {"role": "user", "content": texto}


def _a(texto):
    return {"role": "assistant", "content": texto}


# ══════════════════════════════════════════════════════════════════════════════════
#  1. EL RECONOCEDOR DE SABOR (vocabulario cerrado, por tokens)
# ══════════════════════════════════════════════════════════════════════════════════

def test_sabor_una_palabra():
    sabores = ["limón", "zanahoria", "naranja", "piña", "vainilla"]
    assert _sabores_tocados(sabores, "de limón porfa") == ["limón"]
    assert _sabores_tocados(sabores, "quiero la de naranja") == ["naranja"]


def test_sabor_dos_palabras_por_cualquiera_de_sus_tokens():
    """'carne mechada' se reconoce por 'carne' o por 'mechada'; 'queso de cabra' por 'queso' o
    'cabra' — como escribe una persona ('de mechada', 'la de queso')."""
    sabores = ["carne mechada", "pollo", "queso de cabra"]
    assert _sabores_tocados(sabores, "de carne mechada") == ["carne mechada"]
    assert _sabores_tocados(sabores, "quiero mechada") == ["carne mechada"]
    assert _sabores_tocados(sabores, "de queso") == ["queso de cabra"]
    assert _sabores_tocados(sabores, "la de cabra") == ["queso de cabra"]
    assert _sabores_tocados(sabores, "pollo") == ["pollo"]


def test_nombrar_dos_sabores_es_ambiguo():
    sabores = ["carne mechada", "pollo", "queso de cabra"]
    assert set(_sabores_tocados(sabores, "carne o pollo?")) == {"carne mechada", "pollo"}


def test_un_solo_sabor_en_la_lista_no_hay_nada_que_elegir():
    """Con un único sabor no hay elección (ni, por tanto, repregunta que evitar)."""
    assert _sabores_tocados(["natural"], "de natural") == []


def test_mensaje_sin_sabor_no_toca_nada():
    sabores = ["limón", "naranja", "vainilla"]
    assert _sabores_tocados(sabores, "para el sábado, retiro yo") == []
    assert _sabores_tocados(sabores, "") == []


# ══════════════════════════════════════════════════════════════════════════════════
#  2. EL DESTILADO PURO — tamaño y sabor, con atribución
# ══════════════════════════════════════════════════════════════════════════════════

def test_tamano_elegido_en_el_mensaje_actual():
    got = elecciones_de_variante_en(
        CATALOGO, "de 250", [_u("quiero una torta baja en carbohidratos")], NOMBRES
    )
    assert ("Torta baja en carbohidratos", "tamaño", "250g") in got


def test_sabor_elegido_turnos_atras_sigue_vigente():
    """🔴 EL CASO DE MAIRED ('carne mechada'): el sabor se eligió y dos turnos después, al pedir
    otra cosa, la ficha fresca NO puede reabrirlo. El hilo lo mantiene."""
    hist = [
        _u("quiero las empanadas de yuca"),
        _a("Perfecto. ¿De qué relleno?"),
        _u("de carne mechada"),
        _a("Listo. ¿Para cuándo?"),
    ]
    got = elecciones_de_variante_en(CATALOGO, "para el sábado", hist, NOMBRES)
    valores = {(dim, v) for _, dim, v in got}
    assert ("sabor", "carne mechada") in valores


def test_la_mas_reciente_gana():
    hist = [_u("una torta baja en carbohidratos de 250"), _a("¿algo más?")]
    got = elecciones_de_variante_en(CATALOGO, "mejor de 1kg", hist, NOMBRES)
    tams = [v for _, dim, v in got if dim == "tamaño"]
    assert tams == ["1kg"], tams


def test_nombrar_dos_tamanos_deja_sin_eleccion():
    got = elecciones_de_variante_en(
        CATALOGO, "250 o 500?", [_u("una torta baja en carbohidratos")], NOMBRES
    )
    assert [x for x in got if x[1] == "tamaño"] == []


def test_el_uno_es_cantidad_no_tamano():
    """🔴 La trampa que abrió la RED DEL TAMAÑO ADIVINADO: 'quiero 1' es CUÁNTOS, no el tamaño
    (1kg). El reconocedor de tamaños que se reusa ya lo sabe; aquí se fija que el hilo también."""
    got = elecciones_de_variante_en(
        CATALOGO, "quiero 1", [_u("una torta baja en carbohidratos")], NOMBRES
    )
    assert [x for x in got if x[1] == "tamaño"] == []


# ══════════════════════════════════════════════════════════════════════════════════
#  3. LA ATRIBUCIÓN: el bug del Kéfir de cabra vs el sabor 'queso de cabra'
# ══════════════════════════════════════════════════════════════════════════════════

def test_el_kefir_de_cabra_no_activa_el_sabor_queso_de_cabra():
    """🔴 EL BUG QUE LA REGLA EVITA (ROADMAP rama C). El cliente habla del KÉFIR DE CABRA (un
    producto), no del sabor 'queso de cabra' de las empanadas. Sin atribución, el token 'cabra'
    activaría el sabor. Con ella, la elección no se atribuye porque el mensaje nombra OTRO
    producto."""
    got = elecciones_de_variante_en(
        CATALOGO, "quiero el kéfir de cabra", None, NOMBRES
    )
    assert got == [], f"se coló una elección cruzada: {got}"


def test_el_sabor_si_se_atribuye_cuando_el_mensaje_nombra_su_producto():
    got = elecciones_de_variante_en(
        CATALOGO, "de las empanadas quiero queso de cabra", None, NOMBRES
    )
    assert ("Empanadas de masa de yuca o de masa de plátano", "sabor", "queso de cabra") in got


def test_sabor_solo_sin_otro_producto_nombrado_si_se_atribuye():
    """Si el mensaje toca UN solo producto y no nombra ningún otro, la elección es suya (el
    'de platano' pelado del #6, aplicado al sabor)."""
    hist = [_u("quiero empanadas")]
    got = elecciones_de_variante_en(CATALOGO, "de pollo", hist, NOMBRES)
    assert ("Empanadas de masa de yuca o de masa de plátano", "sabor", "pollo") in got


# ══════════════════════════════════════════════════════════════════════════════════
#  4. EL CARRIL: responder() inyecta la elección en el prompt
# ══════════════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def sin_bd(monkeypatch):
    """`responder()` real, sin Postgres, con el catálogo enriquecido y los nombres falseados."""
    async def _activas():
        return frozenset({"info_producto", "registrar_pedido", "generar_datos_pago", "pedir_ayuda"})

    async def _partes(nombre_cliente, telefono, *, activas=None, quien="uno"):
        return ("Eres Alejandra.", "Hoy es viernes.")

    async def _config_uno():
        return "uno", "modelo/x", "modelo/x"

    async def _cat():
        return CATALOGO

    async def _sin_productos(texto, tope=2):
        return []

    async def _no(*a, **kw):
        return None

    async def _falso(*a, **kw):
        return False

    async def _cero(*a, **kw):
        return 0.0

    async def _sin_hilo(*a, **kw):
        return []

    monkeypatch.setattr(ag, "leer_tools_activas", _activas)
    monkeypatch.setattr(ag, "construir_partes_prompt", _partes)
    monkeypatch.setattr(ag, "leer_config_agente", _config_uno)
    monkeypatch.setattr(ag, "catalogo_variantes_para_hilo", _cat)
    monkeypatch.setattr(ag, "hilo_de_la_venta", _sin_hilo)  # aislar: solo la rama C
    monkeypatch.setattr(ag, "productos_enfocados", _sin_productos)
    monkeypatch.setattr(ag, "media_ya_mostrada", _falso)
    monkeypatch.setattr(ag, "etiqueta_recordada", _no)
    monkeypatch.setattr(ag, "horas_de_silencio", _cero)
    monkeypatch.setattr(ag, "abrir_turno", lambda *a, **kw: None)
    monkeypatch.setattr(ag, "registrar", lambda *a, **kw: None)
    # Los nombres para la atribución: se leen de la BD dentro de elecciones_de_variante.
    # Se falsea esa función entera para no tocar Postgres, reusando la pura.

    async def _elecciones(mensaje, historial):
        return ag.elecciones_de_variante_en(CATALOGO, mensaje, historial, NOMBRES)

    monkeypatch.setattr(ag, "elecciones_de_variante", _elecciones)


async def _correr(mensaje: str, historial: list):
    """Un turno real; devuelve el prompt DINÁMICO que vio el modelo (donde va el hilo)."""
    visto = {}

    async def llm(messages, tools, model):
        sistema = messages[0]["content"]
        # El system va como lista [estable, dinamico]; el hilo se pega al dinámico.
        visto["dinamico"] = sistema[1]["text"] if isinstance(sistema, list) else sistema
        return {"choices": [{"message": {"role": "assistant", "content": "listo 💚"}}]}

    async def ejecutar(nombre, args, telefono, *a, **kw):
        return {"ok": True}

    await ag.responder("584120000000", mensaje, list(historial), "Rosa", llm=llm, ejecutar=ejecutar)
    return visto.get("dinamico", "")


@pytest.mark.asyncio
async def test_el_hilo_del_sabor_llega_al_prompt():
    """🔴 De punta a punta: el sabor elegido turnos atrás viaja en EL HILO DE LA VENTA, así la
    ficha fresca no lo reabre."""
    hist = [
        _u("quiero las empanadas de yuca"),
        _a("¿De qué relleno?"),
        _u("de carne mechada"),
        _a("¿Para cuándo?"),
    ]
    dinamico = await _correr("para el sábado", hist)
    assert "EL HILO DE LA VENTA" in dinamico
    assert "CARNE MECHADA" in dinamico
    assert "NO le vuelvas a preguntar" in dinamico


@pytest.mark.asyncio
async def test_el_hilo_del_tamano_llega_al_prompt():
    dinamico = await _correr("de 250", [_u("una torta baja en carbohidratos")])
    assert "EL HILO DE LA VENTA" in dinamico
    assert "250G" in dinamico


@pytest.mark.asyncio
async def test_sin_eleccion_no_ensucia_el_prompt():
    """Si el cliente no eligió nada de variante, la línea NO aparece (no hay ruido)."""
    dinamico = await _correr("hola, buenas", [])
    assert "EL HILO DE LA VENTA" not in dinamico
