"""LA RED DE LA ASESORÍA — no se recomienda de memoria, se consulta.

EL CASO REAL (smoke de 7 turnos contra el bot real, 2026-08-08, corrido DOS veces con el mismo
resultado): la clienta dijo *"es para compartir en familia el domingo, algo dulce"* y el bot
listó OCHO categorías desde el bloque de catálogo del prompt y preguntó *"¿cuántas personas van
a ser?"*. En 6 de 7 turnos no consultó NINGUNA herramienta: sin ficha no hay con qué asesorar,
sin producto concreto no hay foto, y en la BD quedaron CERO pedidos. Las reglas que ordenan
consultar YA están en el prompt (con mayúsculas) y el modelo las ignora — por eso esto es una
RED: el prompt sugiere, el código impide.

La diferencia deliberada con su hermana (`_dictamina_salud_sin_ficha`): esto es VENTA, no salud.
Se corrige UNA vez y, si el modelo insiste sin consultar, el texto SALE IGUAL — jamás se bloquea
ni se escala por esto. Frenar la venta para exigir mejor asesoría sería matar lo que se quiere
salvar; esa mitad también se prueba aquí.

⚠️ Como toda red, frenar de MÁS es tan malo como frenar de menos (lección pagada dos veces en
este repo): la mitad de este archivo son los casos que NO deben disparar — el cliente que ya
nombró su producto, el que da la fecha de entrega ("para el domingo, retiro yo" es el turno 6
del MISMO smoke: estaba comprando), el que pide el catálogo, el saludo y la despedida.
"""

import json

import pytest

from app.agent import agent as ag
from app.agent.agent import RESPUESTA_SEGURA, _pide_asesoria

# ══════════════════════════════════════════════════════════════════════════════════
# EL DETECTOR: qué es pedir asesoría (y qué no)
# ══════════════════════════════════════════════════════════════════════════════════


def test_el_caso_real_del_smoke():
    """Palabra por palabra el turno 3 del smoke: una necesidad clarísima."""
    assert _pide_asesoria("es para compartir en familia el domingo, algo dulce") is True


@pytest.mark.parametrize("mensaje", [
    "que me recomiendas?",
    "no se que llevar",
    "algo dulcito pa la tarde",
    "cual es mejor?",
    "que es lo mas rico que tienen?",
    "tienen algo para diabeticos?",
    "busco algo para regalar",
    "sugiereme algo tu que conoces",
])
def test_detecta_las_formas_venezolanas_de_pedir_consejo(mensaje: str):
    assert _pide_asesoria(mensaje) is True


def test_la_fecha_de_entrega_NO_es_pedir_consejo():
    """El turno 6 del MISMO smoke: la clienta ya estaba comprando y dio la fecha. Dispararle
    ahí empujaría recomendaciones a quien ya eligió — los días de la semana no son ocasión."""
    assert _pide_asesoria("para el domingo, retiro yo") is False


@pytest.mark.parametrize("mensaje", [
    "quiero el pan keto",            # nombró su producto: no hay nada que asesorar
    "las de platano por favor",      # ídem, con variante
    "hola buenas tardes",            # saludo
    "gracias! chao",                 # despedida
    "mandame el catalogo",           # pidió el catálogo explícito: ese turno es del PDF
    "cuanto cuesta el quesillo?",    # precio de un producto concreto
    "ok esa quiero",                 # ya decidió (turno 4 del smoke)
    "1",                             # cantidad pelada (turno 5 del smoke)
    "mejor dame 2 paquetes",         # "mejor" de preferencia, no de ranking
    "quiero algo para llevar ya",    # "para llevar" es retiro, no una ocasión
])
def test_una_venta_normal_nunca_lo_dispara(mensaje: str):
    """Si esto disparara en turnos de compra, el re-prompt sabotearía el cierre."""
    assert _pide_asesoria(mensaje) is False


@pytest.mark.parametrize("basura", ["", "   ", None])
def test_no_revienta_con_basura(basura):
    assert _pide_asesoria(basura) is False


# ══════════════════════════════════════════════════════════════════════════════════
# LA RED COMPLETA, por la puerta real (`responder`, modo uno)
# ══════════════════════════════════════════════════════════════════════════════════
#
# Mismo molde que test_modo_dos.py: se falsean SOLO las lecturas de configuración y las dos
# consultas de la red de la foto (que aquí no se prueba); el bucle, las redes y el re-prompt
# son el CÓDIGO REAL. Sin OpenRouter, sin Postgres, sin WhatsApp.

ACTIVAS = frozenset({
    "ver_catalogo", "info_producto", "enviar_catalogo", "pedir_ayuda", "enviar_fotos_producto",
})
HISTORIAL = [{"role": "assistant", "content": "hola 💚 dime, en que te puedo ayudar?"}]


@pytest.fixture(autouse=True)
def modo_uno(monkeypatch):
    """`autouse` a propósito (la lección de test_modo_dos.py): un caso nuevo que se olvide de
    pedir el fixture saldría a buscar Postgres de verdad y fallaría por la razón equivocada."""

    async def _config():
        return "uno", "modelo/uno", "modelo/uno"

    async def _modelo():
        return "modelo/uno"

    async def _activas():
        return ACTIVAS

    async def _partes(nombre_cliente, telefono, *, activas=None, quien="uno"):
        return ("Eres Alejandra. CATÁLOGO (para ti): Quesillo — $8.00", "Hoy es viernes.")

    async def _sin_producto(texto):
        return None  # la red de la FOTO tiene su propio archivo; aquí no se mete

    async def _ya_mostrada(telefono, nombre):
        return True

    monkeypatch.setattr(ag, "leer_config_agente", _config)
    monkeypatch.setattr(ag, "leer_modelo_ia", _modelo)
    monkeypatch.setattr(ag, "leer_tools_activas", _activas)
    monkeypatch.setattr(ag, "construir_partes_prompt", _partes)
    monkeypatch.setattr(ag, "producto_enfocado", _sin_producto)
    monkeypatch.setattr(ag, "media_ya_mostrada", _ya_mostrada)
    return None


def _msg(content: str = "", tools: list | None = None) -> dict:
    m: dict = {"role": "assistant", "content": content}
    if tools:
        m["tool_calls"] = [
            {
                "id": f"call_{i}",
                "type": "function",
                "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)},
            }
            for i, (n, a) in enumerate(tools)
        ]
    return m


async def _correr(
    *,
    cliente: str,
    respuestas: list[dict],
    resultados_tool: dict | None = None,
    historial: list | None = None,
    marca: str = "RECOMENDACIÓN",
):
    """Un turno del modo uno. Devuelve (salida, tools_llamadas, correcciones_con_esa_marca)."""
    pendientes = list(respuestas)
    llamadas: list[tuple[str, dict]] = []
    resultados_tool = resultados_tool or {}
    capturado: dict = {}

    async def llm(messages, tools, model):
        capturado["messages"] = messages  # la MISMA lista que muta el bucle: al final está todo
        return {"choices": [{"message": pendientes.pop(0) if pendientes else _msg("listo 💚")}]}

    async def ejecutar(nombre, args, telefono):
        llamadas.append((nombre, args))
        base = {"ok": True}
        return resultados_tool.get(nombre, base)

    salida = await ag.responder(
        "584240000000", cliente, list(historial if historial is not None else HISTORIAL), "Ana",
        llm=llm, ejecutar=ejecutar,
    )
    correcciones = [
        m for m in capturado.get("messages", [])
        if isinstance(m, dict) and m.get("role") == "user"
        and marca in str(m.get("content") or "")
    ]
    return salida, llamadas, correcciones


TEXTO_RECITA = "tenemos tortas, galletas, panes, quesillos, mermeladas y mas 😊 cuantas personas van a ser?"
TEXTO_RECITA_2 = "hay opciones dulces y saladas para todos los gustos 😊 que prefieren ustedes?"
TEXTO_CONCRETO = "para compartir el domingo te recomiendo el Quesillo 💚 es cremosito y rinde full. te lo dejo listo?"


async def test_recomendar_de_memoria_se_corrige_y_consulta():
    """EL CASO QUE CAMBIA LOS 7 TURNOS: recita categorías sin herramientas → una corrección
    [SISTEMA] → el modelo consulta y recomienda un producto CONCRETO. Eso es lo que sale."""
    salida, llamadas, correcciones = await _correr(
        cliente="es para compartir en familia el domingo, algo dulce",
        respuestas=[
            _msg(TEXTO_RECITA),                                     # de memoria, cero tools
            _msg("", tools=[("ver_catalogo", {"busqueda": "dulce"})]),  # tras el aviso: consulta
            _msg(TEXTO_CONCRETO),
        ],
        resultados_tool={"ver_catalogo": {"ok": True, "productos": ["Quesillo — $8.00"]}},
    )
    assert salida == TEXTO_CONCRETO
    assert ("ver_catalogo", {"busqueda": "dulce"}) in llamadas, "nunca consultó el catálogo"
    assert len(correcciones) == 1


async def test_si_insiste_el_texto_sale_igual_jamas_se_bloquea():
    """LA MITAD QUE PROTEGE LA VENTA. Esto es venta, no salud: si tras la corrección sigue sin
    consultar, lo que escribió SALE — nada de RESPUESTA_SEGURA, nada de escalar a la dueña,
    y UNA sola corrección (cero bucles)."""
    salida, llamadas, correcciones = await _correr(
        cliente="que me recomiendas?",
        respuestas=[_msg(TEXTO_RECITA), _msg(TEXTO_RECITA_2)],  # insiste: sigue de memoria
    )
    assert salida == TEXTO_RECITA_2, "el segundo texto tenía que salir tal cual"
    assert salida != RESPUESTA_SEGURA
    assert not any(n == "pedir_ayuda" for n, _ in llamadas), "escaló por asesoría: prohibido"
    assert len(correcciones) == 1, "se le corrigió más de una vez en el mismo turno"


async def test_si_uso_una_herramienta_no_se_corrige():
    """El bot SÍ consultó (aunque fuera enviar_catalogo): la red no tiene nada que decir."""
    salida, llamadas, correcciones = await _correr(
        cliente="que me recomiendas?",
        respuestas=[
            _msg("", tools=[("enviar_catalogo", {})]),
            _msg("te acabo de enviar el catalogo 💚 dime que te provoca"),
        ],
    )
    assert correcciones == []
    assert ("enviar_catalogo", {}) in llamadas
    assert salida == "te acabo de enviar el catalogo 💚 dime que te provoca"


async def test_pidio_el_catalogo_ese_turno_es_del_pdf():
    """"no sé qué pedir, mándame el catálogo" pide consejo Y el catálogo: gana el catálogo
    (la red del catálogo ya garantiza el PDF). Corregir aquí pelearía con la regla 59."""
    salida, llamadas, correcciones = await _correr(
        cliente="no se que pedir, mandame el catalogo mejor",
        respuestas=[_msg("claro 💚 ya te lo mando")],  # sin tools: la RED DEL CATÁLOGO lo envía
    )
    assert correcciones == []
    assert ("enviar_catalogo", {}) in llamadas, "el PDF lo garantiza la red del catálogo"
    assert salida == "claro 💚 ya te lo mando"


async def test_con_las_tools_de_consulta_apagadas_la_red_no_existe(monkeypatch):
    """Ordenar consultar una herramienta APAGADA es el bucle que la red del envío fantasma ya
    pagó ("EL REGAÑO SABE SI LA HERRAMIENTA EXISTE"). Sin ver_catalogo ni info_producto, esta
    red no existe ese turno."""

    async def _sin_consulta():
        return frozenset({"pedir_ayuda", "enviar_catalogo"})

    monkeypatch.setattr(ag, "leer_tools_activas", _sin_consulta)
    salida, _, correcciones = await _correr(
        cliente="que me recomiendas?",
        respuestas=[_msg(TEXTO_RECITA)],
    )
    assert correcciones == []
    assert salida == TEXTO_RECITA


async def test_producto_concreto_no_dispara_por_la_puerta_real():
    """El cliente nombró su producto: aunque el bot conteste sin tools, esta red no se mete."""
    texto = "el pan keto es buenisimo 💚 cuantos paquetes te preparo?"
    salida, _, correcciones = await _correr(
        cliente="quiero el pan keto",
        respuestas=[_msg(texto)],
    )
    assert correcciones == []
    assert salida == texto


# ══════════════════════════════════════════════════════════════════════════════════
# LA RED DEL PITCH: cuando el cliente ELIGE, se le vende — no se le toma nota
# ══════════════════════════════════════════════════════════════════════════════════
#
# El caso real de Erwin (simulador, 2026-08-08): el bot ofreció las dos masas, la clienta dijo
# "de platano" y la confirmación fue "Listo. Las Empanadas de masa de plátano vienen en paquete
# de 8 unidades. ¿Cuántos paquetes quieres y de qué relleno?" — ni un dato de la ficha, ni un
# gancho. Confirma como recepcionista, no vende.

OFERTA = (
    "Tengo Empanadas de masa de plátano y Empanadas de masa de yuca — ambas son saludables "
    "y sin gluten. ¿De cuál prefieres?"
)
HIST_ELECCION = [{"role": "assistant", "content": OFERTA}]
CONFIRMACION_PLANA = (
    "Listo. Las Empanadas de masa de plátano vienen en paquete de 8 unidades. "
    "¿Cuántos paquetes quieres y de qué relleno?"
)
CONFIRMACION_CON_PITCH = (
    "buena eleccion 💚 las de plátano llevan harina de yuca y duran hasta 3 meses congeladas. "
    "cuantos paquetes te preparo?"
)


def test_elegir_es_contestar_corto_a_la_oferta_del_bot():
    """El turno real: el bot preguntó '¿De cuál prefieres?' y ella contestó 'de platano'."""
    assert ag._elige_entre_opciones("de platano", HIST_ELECCION) is True


@pytest.mark.parametrize(("mensaje", "historial"), [
    ("relleno de hay?", HIST_ELECCION),   # pregunta un DATO, no elige (turno 3 real)
    ("1", HIST_ELECCION),                 # un número contesta CUÁNTOS, no CUÁL
    ("hola buenas", HIST_ELECCION),       # charla pura
    ("de platano", [{"role": "assistant", "content": "Cuantos paquetes quieres?"}]),  # sin oferta
    ("quiero saber si las de platano llevan azucar o algo raro", HIST_ELECCION),      # largo
    ("de platano", []),                   # sin historial no hay oferta previa
])
def test_lo_que_no_es_una_eleccion(mensaje: str, historial: list):
    assert ag._elige_entre_opciones(mensaje, historial) is False


def test_la_confirmacion_real_de_erwin_es_plana():
    """La presentación ("paquete de 8") es transaccional, no pitch: la clienta sigue sin saber
    por qué llevarse ESA. Y "masa de plátano" es el NOMBRE, no un dato."""
    assert ag._confirma_sin_pitch(CONFIRMACION_PLANA) is True


@pytest.mark.parametrize("texto", [
    CONFIRMACION_CON_PITCH,                                        # ya vende: llevan/duran
    "Son sin gluten y aptas para diabéticos. cuantos paquetes?",   # ya vende: ficha presente
    "Tengo relleno de carne mechada, pollo o queso de cabra.",     # dato de relleno (turno 3)
    "¿Cuántos paquetes quieres?",                                  # puro preguntar: nada que enriquecer
])
def test_lo_que_ya_vende_o_no_confirma_no_es_plano(texto: str):
    assert ag._confirma_sin_pitch(texto) is False


async def test_la_eleccion_plana_se_corrige_consulta_y_vende():
    """EL CASO DE ERWIN completo: elección → confirmación plana → corrección [SISTEMA] →
    info_producto → confirmación con 1-2 datos REALES. Eso es lo que sale."""
    salida, llamadas, correcciones = await _correr(
        cliente="de platano",
        historial=HIST_ELECCION,
        marca="ELEGIR",
        respuestas=[
            _msg(CONFIRMACION_PLANA),
            _msg("", tools=[("info_producto", {"nombre": "Empanadas de masa de yuca o de masa de plátano"})]),
            _msg(CONFIRMACION_CON_PITCH),
        ],
        resultados_tool={
            "info_producto": {
                "encontrado": True,
                "nombre": "Empanadas de masa de yuca o de masa de plátano",
                "descripcion": "masa de plátano macho o yuca, con harina de yuca",
                "duracion": "3 meses congeladas",
            },
        },
    )
    assert salida == CONFIRMACION_CON_PITCH
    assert any(n == "info_producto" for n, _ in llamadas), "nunca abrió la ficha"
    assert len(correcciones) == 1


async def test_si_insiste_la_confirmacion_sale_igual():
    """Venta, no salud: si la segunda pasada tampoco consulta, el texto sale tal cual —
    sin RESPUESTA_SEGURA, sin escalar, y con UNA sola corrección."""
    segunda = "perfecto, las de plátano entonces 💚 me dices cuantos paquetes?"
    salida, llamadas, correcciones = await _correr(
        cliente="de platano",
        historial=HIST_ELECCION,
        marca="ELEGIR",
        respuestas=[_msg(CONFIRMACION_PLANA), _msg(segunda)],
    )
    assert salida == segunda
    assert salida != RESPUESTA_SEGURA
    assert not any(n == "pedir_ayuda" for n, _ in llamadas), "escaló por el pitch: prohibido"
    assert len(correcciones) == 1


async def test_si_ya_vende_no_se_corrige():
    """La confirmación ya trae datos de ficha: no hay nada que corregir."""
    salida, _, correcciones = await _correr(
        cliente="de platano",
        historial=HIST_ELECCION,
        marca="ELEGIR",
        respuestas=[_msg(CONFIRMACION_CON_PITCH)],
    )
    assert correcciones == []
    assert salida == CONFIRMACION_CON_PITCH


async def test_el_dato_puntual_respondido_no_se_toca():
    """"relleno de hay?" pide un dato y el bot lo dio: ese turno no es de esta red."""
    texto = "Tengo relleno de carne mechada, pollo o queso de cabra."
    salida, _, correcciones = await _correr(
        cliente="relleno de hay?",
        historial=[{"role": "assistant", "content": CONFIRMACION_PLANA}],
        marca="ELEGIR",
        respuestas=[_msg(texto)],
    )
    assert correcciones == []
    assert salida == texto


async def test_sin_info_producto_la_red_del_pitch_no_existe(monkeypatch):
    """Ordenar abrir una ficha APAGADA es el bucle ya conocido: sin `info_producto`, nada."""

    async def _sin_ficha():
        return frozenset({"ver_catalogo", "enviar_catalogo", "pedir_ayuda"})

    monkeypatch.setattr(ag, "leer_tools_activas", _sin_ficha)
    salida, _, correcciones = await _correr(
        cliente="de platano",
        historial=HIST_ELECCION,
        marca="ELEGIR",
        respuestas=[_msg(CONFIRMACION_PLANA)],
    )
    assert correcciones == []
    assert salida == CONFIRMACION_PLANA
