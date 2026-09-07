"""LA CONVERSACIÓN DEL 6-SEP 22:29 (pruebas, Sonnet 4.6): cuatro grietas, un tema — el bot no
escucha lo que el cliente ya dijo ni habla como persona en la recta final.

Lo que Maired vio ("esto está muy mal") y lo que se cierra aquí:
  1. "A las 10" → el bot recitó las franjas otra vez. Las 10 CAEN DENTRO de "en la mañana (10 a
     12)". Una persona dice "perfecto, en la mañana entonces". → `_hora_en_franja`.
  2. "Tienes razón, me falta tu punto de referencia" → el cliente no había dicho nada de eso: el
     modelo le contestó al [SISTEMA] de la red del pedido fantasma como si fuera el cliente.
     → el regaño prohíbe responderle al aviso.
  3. "Te lo tengo para el martes 8. Procedo a registrarlo?" → pedir permiso para registrar es lo
     contrario de ASUME EL SÍ (y salió después del regaño anterior: el modelo se acobardó).
     → R110 y el regaño lo dicen explícito.
  4. Dos fotos seguidas (New York y Mini) cuando el cliente aún elegía entre las dos. Se intentó
     bajar la red a 1 producto por turno y el test viejo de Erwin (R44/R45: "con dos opciones, las
     dos fotos", MEDIDO) lo frenó. Decisión senior: no se revierte lo medido sin medir; lo que
     estaba mal era el PROMPT ("UN producto a la vez"), que contradecía al código. Se alineó.
  Y una palabra: "franja" es vocabulario NUESTRO; al cliente se le dice "en la mañana o en la tarde".
"""
import inspect

from app.agent import agent, tools
from app.agent import system_prompt as sp
from app.agent.tools import _hora_en_franja, _matchear_franja, _rango_de_franja

FRANJAS = ["en la mañana (10 a 12)", "en la tarde (2 a 6)"]


# ══ 1) La hora dentro de la franja ══

def test_los_rangos_se_leen_en_24h():
    assert _rango_de_franja("en la mañana (10 a 12)") == (10, 12)
    assert _rango_de_franja("en la tarde (2 a 6)") == (14, 18)
    # Los momentos de fábrica ya son frases de persona (lo pidió Maired: nada de paréntesis):
    assert _rango_de_franja("de 10 a 12 de la mañana") == (10, 12)
    assert _rango_de_franja("de 2 a 6 de la tarde") == (14, 18)
    assert tools._FRANJAS_DEFAULT == ["de 10 a 12 de la mañana", "de 2 a 6 de la tarde"]
    assert _rango_de_franja("mañana") is None


def test_a_las_10_tambien_con_los_momentos_de_fabrica():
    assert _matchear_franja("a las 10", tools._FRANJAS_DEFAULT) == "de 10 a 12 de la mañana"
    assert _matchear_franja("a las 3", tools._FRANJAS_DEFAULT) == "de 2 a 6 de la tarde"


def test_ninguna_frase_de_ejemplo_del_cliente_vive_en_codigo_ni_prompt():
    """Lo pidió Maired: la redacción es del modelo; el código pasa las HORAS (del panel) y
    prohíbe la palabra 'franja'. Ninguna frase hecha para el cliente queda escrita aquí."""
    import inspect
    for src in (inspect.getsource(tools.anotar_entrega), inspect.getsource(tools.proxima_fecha_entrega), sp._REGLAS):
        assert "tengo espacio de 10 a 12" not in src


def test_a_las_10_es_la_manana():
    """🔴 El literal del 22:40."""
    assert _matchear_franja("a las 10", FRANJAS) == FRANJAS[0]
    assert _hora_en_franja("A las 10", FRANJAS) == FRANJAS[0]


def test_horas_de_la_tarde_con_y_sin_marca():
    for frase in ("a las 3", "a las 3 pm", "a las 3 de la tarde", "las 15:00", "como a las 4"):
        assert _matchear_franja(frase, FRANJAS) == FRANJAS[1], frase


def test_una_hora_fuera_de_todas_las_franjas_no_calza():
    assert _matchear_franja("a las 8", FRANJAS) is None       # 8 am: antes de la mañana
    assert _matchear_franja("a las 8 pm", FRANJAS) is None    # 20h: después de la tarde
    assert _matchear_franja("a la 1", FRANJAS) is None        # 13h: entre las dos


def test_dos_horas_distintas_no_deciden():
    assert _matchear_franja("entre 10 y 3", FRANJAS) is None


def test_las_formas_de_antes_siguen_igual():
    assert _matchear_franja("en la mañana", FRANJAS) == FRANJAS[0]
    assert _matchear_franja("tarde", FRANJAS) == FRANJAS[1]
    assert _matchear_franja("", FRANJAS) is None


def test_las_notas_de_la_herramienta_prohiben_la_palabra_franja_al_cliente():
    # (las frases se cortan por el salto de línea del código fuente: se buscan trozos enteros)
    src = inspect.getsource(tools.anotar_entrega)
    assert "usar la palabra 'franja'" in src
    assert "'en la mañana' o 'en la tarde'" in src
    src2 = inspect.getsource(tools.proxima_fecha_entrega)
    assert "palabra 'franja'" in src2
    assert "pásasela igual a" in src2  # una hora se manda a anotar_entrega, no se rechaza de antemano


# ══ 2 y 3) El regaño del pedido fantasma no se filtra ni acobarda ══

def test_el_regano_prohibe_contestarle_al_sistema_como_si_fuera_el_cliente():
    for texto in (agent._correccion_fantasma(None), agent._correccion_fantasma(type("P", (), {"id": 1, "estado": "pagado"})())):
        assert "No le menciones al cliente este aviso" in texto
        assert "tienes razón" in texto and "perdón" in texto


def test_el_regano_y_la_regla_prohiben_pedir_permiso_para_registrar():
    assert "procedo?" in agent._correccion_fantasma(None)
    r = sp._REGLAS
    assert "tampoco para registrar" in r
    assert "procedo a registrarlo?" in r and "no existen" in r


# ══ 4) La red de la foto: hasta DOS productos (lo medido) y el prompt ya no lo contradice ══

def test_la_red_de_la_foto_conserva_lo_medido_y_el_prompt_lo_dice_igual():
    assert agent._MAX_FOTOS_POR_TURNO == 2  # R44/R45 de Erwin: no se baja sin medir
    r = sp._REGLAS
    assert "UN producto a la vez" not in r, "el prompt contradecía al código medido"
    assert "como máximo DOS productos cuando el cliente está eligiendo entre dos" in r
