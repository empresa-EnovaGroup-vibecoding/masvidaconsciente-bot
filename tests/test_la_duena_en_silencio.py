"""🔇 LA DUEÑA EN SILENCIO — "la dueña" no existe como personaje en nada de lo que lee el modelo.

Autopsia del 7-sep-2026 (SESIONES (27) y (29)): *"La hora exacta la confirma la dueña según su
ruta, ella te escribe"*, *"Whuilianny es la dueña, ella es quien prepara todo y confirma la
entrega"*. El modelo no se confundía: NARRABA el reparto que el prompt le pintaba — "la dueña"
aparecía 47 veces en lo que leía (reglas, catálogo, herramientas), como un tercer personaje que
cocina, revisa el pago y pone la hora. Decisión de Maired: que desaparezca. El negocio habla en
primera persona ("lo revisamos", "se coordina después", "una persona del negocio entra al chat");
"Whuilianny" solo queda como NOMBRE (para responder "¿eres Whuilianny?" y en el titular de los
datos de pago), nunca presentada como la dueña.

Tres candados, en el orden de la doctrina "el prompt sugiere, el código impide":
  1. Lo que LEE el modelo no la nombra: `_REGLAS`, los esquemas de las herramientas y todos los
     string literales de los módulos que arman el prompt, las notas y las correcciones (por AST:
     sin docstrings y sin los mensajes de `logger`, que son para nosotros).
  2. Lo que ESCRIBE el modelo la frena: `frase_prohibida_siempre` caza "la/nuestra/mi/una
     dueña|propietaria|jefa" en los dos carriles (charla y dinero).
  3. La voz (BRIEF, fuera del repo) tampoco la nombra — solo en la máquina de Maired.
Y de paso: la lista blanca "extra" pasa a ser editable por la API del panel (`CLAVES_CONFIG`).
"""
from __future__ import annotations

import ast
import json
import pathlib
import re

import pytest

from app.agent.agent import frase_prohibida_siempre
from app.agent.system_prompt import _REGLAS
from app.agent.tools import TOOL_SCHEMAS
from app.api.router import CLAVES_CONFIG

RAIZ = pathlib.Path(__file__).resolve().parents[1]
DUENA = re.compile(r"due[ñn]a", re.I)
LOGS = {"debug", "info", "warning", "error", "exception", "critical"}

MODULOS = (
    "app/agent/system_prompt.py",
    "app/agent/tools.py",
    "app/services/mensajes.py",
    "app/agent/hoja.py",
    "app/agent/agent.py",
)


def _literales_que_lee_el_modelo(ruta: pathlib.Path):
    """String literales del módulo SIN docstrings y SIN los argumentos de `logger.*` (esos son
    mensajes para nosotros, el modelo no los ve)."""
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    excluidos: set[int] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            cuerpo = getattr(nodo, "body", None) or []
            if (
                cuerpo
                and isinstance(cuerpo[0], ast.Expr)
                and isinstance(cuerpo[0].value, ast.Constant)
                and isinstance(cuerpo[0].value.value, str)
            ):
                excluidos.add(id(cuerpo[0].value))
        if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute) and nodo.func.attr in LOGS:
            for hijo in ast.walk(nodo):
                if isinstance(hijo, ast.Constant):
                    excluidos.add(id(hijo))
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str) and id(nodo) not in excluidos:
            yield nodo.lineno, nodo.value


# ══════════════════════════════════════════════════════════════════════════════════
#  1. LO QUE LEE
# ══════════════════════════════════════════════════════════════════════════════════

def test_las_reglas_no_nombran_a_la_duena():
    assert not DUENA.search(_REGLAS), "volvió 'la dueña' a las reglas: el modelo la va a narrar"


def test_las_herramientas_no_nombran_a_la_duena():
    texto = json.dumps(TOOL_SCHEMAS, ensure_ascii=False)
    assert not DUENA.search(texto), "los esquemas de las herramientas nombran a la dueña"


def test_ningun_literal_que_lea_el_modelo_la_nombra():
    malas = [
        f"{rel}:{linea} → …{texto[max(0, m.start() - 50):m.end() + 40].replace(chr(10), ' ')}…"
        for rel in MODULOS
        for linea, texto in _literales_que_lee_el_modelo(RAIZ / rel)
        for m in [DUENA.search(texto)]
        if m
    ]
    assert malas == [], "el modelo puede leer 'la dueña' aquí:\n  " + "\n  ".join(malas)


def test_whuilianny_solo_queda_como_nombre_en_la_regla_de_identidad():
    """R130 la nombra para contestar "¿eres Whuilianny?"; nada más en las reglas debe hablar de ella."""
    veces = len(re.findall(r"whuilianny", _REGLAS, re.I))
    assert veces <= 2, f"las reglas nombran a Whuilianny {veces} veces; solo cabe la regla de identidad"


# ══════════════════════════════════════════════════════════════════════════════════
#  2. LO QUE ESCRIBE
# ══════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("frase", [
    "La hora exacta la confirma la dueña según su ruta, ella te escribe el martes.",
    "No, soy Alejandra. Whuilianny es la dueña, ella es quien prepara todo y confirma la entrega.",
    "Ya recibí tu comprobante, la dueña lo revisa en su banco.",
    "Déjame consultarlo con la propietaria y te aviso.",
    "Eso lo decide mi jefa.",
    "Te paso con nuestra dueña.",
])
def test_la_red_frena_a_la_duena_como_personaje(frase):
    assert frase_prohibida_siempre(frase) is not None, f"se escapó: {frase!r}"


@pytest.mark.parametrize("frase", [
    "Soy Alejandra, la asesora de masvidaconsciente. En qué más te puedo ayudar?",
    "Perfecto, en la mañana entonces. Que las disfrutes mucho!",
    "Pago Móvil · Banco: Banesco (0134) · Titular: Whuiliany Zabala · Monto: 13.833,51 Bs",
    "Listo, ya recibí tu comprobante. Lo estoy revisando y en un momentito te confirmo.",
    "Eso te lo confirmo enseguida, dame un momentito.",
    "Ese día estamos en contacto contigo para coordinar la entrega.",
])
def test_la_red_deja_pasar_la_primera_persona_del_negocio(frase):
    assert frase_prohibida_siempre(frase) is None, f"frenó un mensaje correcto: {frase!r}"


# ══════════════════════════════════════════════════════════════════════════════════
#  3. LA VOZ (solo en la máquina de Maired: el BRIEF no viaja en el repo)
# ══════════════════════════════════════════════════════════════════════════════════

def test_la_voz_tampoco_la_nombra():
    brief = RAIZ / "BRIEF-personalidad-alejandra-2026-09-06.md"
    if not brief.exists():
        pytest.skip("el BRIEF no viaja en el repo (.gitignore); la voz viva se revisa en el banco 8")
    assert not DUENA.search(brief.read_text(encoding="utf-8")), "la voz volvió a presentar a la dueña"


# ══════════════════════════════════════════════════════════════════════════════════
#  Y LA LISTA BLANCA EDITABLE
# ══════════════════════════════════════════════════════════════════════════════════

def test_la_lista_blanca_extra_se_edita_por_la_api():
    """El 7-sep hubo que cargar los 3 primeros clientes con un UPDATE porque la clave no estaba
    en CLAVES_CONFIG. Soltar el bot cliente a cliente es una palanca del panel, no de psql."""
    assert "numeros_permitidos_extra" in CLAVES_CONFIG
