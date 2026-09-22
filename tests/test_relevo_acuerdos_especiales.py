"""Los acuerdos que Whuilianny resuelve no pueden quedar como una duda genérica del bot."""

from app.agent import system_prompt
from app.agent import tools


def test_los_acuerdos_especiales_ordenan_el_relevo_y_el_no_cobro():
    reglas = system_prompt._REGLAS

    for caso in (
        "envases",
        "abono",
        "saldo a favor",
        "delivery fuera de las zonas",
        "personalización",
        "sustitución",
        "entrega parcial",
    ):
        assert caso in reglas
    assert "motivo='acuerdo_especial'" in reglas
    assert "ANTES de registrar, cambiar, cobrar, recalcular" in reglas


def test_el_motivo_de_acuerdo_especial_pausa_el_chat():
    assert "acuerdo_especial" in tools._MOTIVOS_DE_PAUSA
    assert "acuerdo_especial" in tools._MOTIVO_TITULO

    schema = next(
        tool for tool in tools.TOOL_SCHEMAS
        if tool["function"]["name"] == "pedir_ayuda"
    )
    motivos = schema["function"]["parameters"]["properties"]["motivo"]["enum"]
    assert "acuerdo_especial" in motivos
