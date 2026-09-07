"""🔇 LA HORA ES MUDA — nada de lo que LEE el modelo dice quién confirma la hora (7-sep-2026, 2ª vuelta).

Autopsia (SESIONES (27)): Maired escribió "a las 10" y el bot contestó *"La hora exacta la confirma
la dueña según su ruta, ella te escribe el martes para coordinarlo"*. No era confusión: la frase
estaba ESCRITA, literal, en lo que lee el modelo. El PR #44 la borró de 5 sitios y la dejó en 4
(el calendario, dos descripciones de herramientas, la guía del pago) — y encima la nombró 3 veces
"en negativo" (*"no digas 'la hora te la confirma la dueña'"*), que la hace MÁS presente, no menos.
Y *"Whuilianny es la dueña"* salía del paréntesis de la voz: "(ella es la dueña)".

Aquí se mira TODO lo que el modelo puede leer, en el CI y sin BD:
  · las reglas (`_REGLAS`, texto completo, antes de filtrar por modo),
  · los esquemas de las herramientas (`TOOL_SCHEMAS`: descripciones y parámetros),
  · los STRING LITERALES (por AST: ni comentarios ni docstrings) de los módulos que arman el prompt,
    las notas que devuelven las herramientas y los textos dinámicos,
  · la voz (el BRIEF que se promueve a los entornos con `promover_personalidad.py`) — solo en la
    máquina de Maired: `BRIEF-*.md` NO viaja en el repo (es público; .gitignore), en el CI se salta,
  · y las salidas reales de `_lineas_entrega_pendiente` y `_frase_entrega`.
La versión EN VIVO (con la personalidad y las guías de la BD) es el bloque 8 de
`scripts/probar_prompt_coherente.py`.
"""
from __future__ import annotations

import ast
import json
import pathlib
from types import SimpleNamespace

import pytest

from app.agent.system_prompt import _REGLAS, _lineas_entrega_pendiente
from app.agent.tools import TOOL_SCHEMAS
from app.services.mensajes import MENSAJES_DEFAULT, _frase_entrega

RAIZ = pathlib.Path(__file__).resolve().parents[1]

# Lo que el modelo NO debe leer en ninguna capa (en minúsculas; con y sin acento).
PROHIBIDAS = (
    "confirma la dueña",
    "según su ruta",
    "segun su ruta",
    "dueña te confirma",
    "dueña la confirma",
    "dueña le confirma",
    "te la confirma",
    "le anuncies",
    "lo anuncias",
    "ella es la dueña",
)

# Los módulos cuyos literales pueden llegar al modelo: el prompt, las herramientas (esquemas y
# notas), las guías del pago, la hoja del turno y las correcciones que inyecta el agente.
MODULOS = (
    "app/agent/system_prompt.py",
    "app/agent/tools.py",
    "app/services/mensajes.py",
    "app/agent/hoja.py",
    "app/agent/agent.py",
)

FRANJAS = ["de 10 a 12 de la mañana", "de 2 a 6 de la tarde"]


def _encontradas(texto: str) -> list[str]:
    bajo = texto.lower()
    return [p for p in PROHIBIDAS if p in bajo]


def _literales(ruta: pathlib.Path):
    """Los string literales del módulo, SIN docstrings (y los comentarios no son nodos)."""
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            cuerpo = getattr(nodo, "body", None) or []
            if (
                cuerpo
                and isinstance(cuerpo[0], ast.Expr)
                and isinstance(cuerpo[0].value, ast.Constant)
                and isinstance(cuerpo[0].value.value, str)
            ):
                docstrings.add(id(cuerpo[0].value))
    for nodo in ast.walk(arbol):
        if (
            isinstance(nodo, ast.Constant)
            and isinstance(nodo.value, str)
            and id(nodo) not in docstrings
        ):
            yield nodo.lineno, nodo.value


def test_las_reglas_no_dicen_quien_confirma_la_hora():
    assert _encontradas(_REGLAS) == [], "volvió a las reglas la frase que el modelo copiaba"


def test_los_esquemas_de_las_herramientas_tampoco():
    """Las descripciones de las tools también son prompt: `registrar_pedido.entrega` y
    `anotar_entrega` decían la frase completa y el #44 no las miró."""
    texto = json.dumps(TOOL_SCHEMAS, ensure_ascii=False)
    assert _encontradas(texto) == [], "la frase sigue en los esquemas de las herramientas"


def test_ningun_literal_de_los_modulos_la_dice():
    """Recorre lo que el código puede mandarle al modelo (notas, correcciones, bloques dinámicos),
    aunque esté partido en varias líneas (Python las junta en UN literal)."""
    malas = []
    for rel in MODULOS:
        for linea, texto in _literales(RAIZ / rel):
            for p in _encontradas(texto):
                malas.append(f"{rel}:{linea} → '{p}'")
    assert malas == [], "frases que el modelo puede leer:\n  " + "\n  ".join(malas)


def test_la_voz_no_presenta_a_la_duena():
    """El BRIEF es lo que se promueve a cada entorno. Línea 7: "Soy Alejandra, la asesora de
    masvidaconsciente" y nada más — sin explicar quién es Whuilianny ni qué hace."""
    brief = RAIZ / "BRIEF-personalidad-alejandra-2026-09-06.md"
    if not brief.exists():
        pytest.skip("el BRIEF no viaja en el repo (.gitignore); la voz se vigila EN VIVO en el "
                    "bloque 8 de probar_prompt_coherente")
    voz = brief.read_text(encoding="utf-8")
    assert _encontradas(voz) == [], "la voz volvió a explicar quién es la dueña"
    assert "(ella es la dueña" not in voz
    assert "Soy Alejandra, la asesora de masvidaconsciente" in voz


def test_las_salidas_dinamicas_tampoco():
    pedido = SimpleNamespace(entrega_franja=FRANJAS[0], entrega_referencia="frente a la plaza")
    for linea in _lineas_entrega_pendiente(pedido):
        assert _encontradas(linea) == [], linea
    for franja in (None, FRANJAS[1]):
        texto = _frase_entrega("Barquisimeto centro", False, None, franjas=FRANJAS, franja_elegida=franja)
        assert _encontradas(texto) == [], texto
    for clave, guia in MENSAJES_DEFAULT.items():
        assert _encontradas(guia) == [], clave
