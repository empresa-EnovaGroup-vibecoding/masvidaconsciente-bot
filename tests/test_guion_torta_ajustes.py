"""🎂 LO QUE SALIÓ DEL GUION DE LA TORTA (16-oct, decisiones de Maired; SESIONES (52)).

En pruebas, con Sonnet 5.5, Alejandra siguió bien el acuerdo de los $30, pero:
  · dijo "el precio lo defino yo" (como si fuera la dueña);
  · eligió la torta keto sin preguntar (también hay "baja en carbohidratos");
  · dijo "te la llevo" — lo copiaba de un EJEMPLO del propio sistema ("¿a qué punto de referencia te lo
    llevo?");
  · soltó la lista de zonas con precios, porque una regla vieja ordenaba "primero el sector" y decía que
    la ubicación "nunca se exige".
Regla de Maired, sacada del corpus real: Whuilianny pide "pásame la ubicación", y de ahí sale la zona.
Estos tests fijan que las instrucciones dicen eso y que los ejemplos viejos ya no están.
"""
import json

from app.agent import system_prompt as sp
from app.agent import tools
from app.services import mensajes


def _todo_lo_que_lee_el_modelo() -> str:
    """Las reglas, las herramientas y las notas de la caja: lo que Alejandra puede copiar."""
    import inspect

    return "\n".join([
        sp._REGLAS,
        json.dumps(tools.TOOL_SCHEMAS, ensure_ascii=False),
        tools._NOTA_FALTA_REFERENCIA,
        inspect.getsource(tools.generar_datos_pago),
        inspect.getsource(sp._zonas_bloque),
        inspect.getsource(mensajes._frase_entrega),
    ])


def test_la_direccion_se_pide_como_la_pide_whuilianny():
    assert "pásame tu ubicación o la dirección" in sp._REGLAS
    assert "nunca la lista completa con precios" in sp._REGLAS


def test_ya_no_estan_las_instrucciones_que_soltaban_la_lista_de_zonas():
    texto = _todo_lo_que_lee_el_modelo()
    for vieja in ("primero el sector", "LÉELE las zonas", "nómbrale únicamente las zonas",
                  "ÚNICAMENTE las zonas de delivery", "nunca se exige"):
        assert vieja not in texto, vieja


def test_ningun_ejemplo_le_ensena_te_lo_llevo():
    assert "te lo llevo" not in _todo_lo_que_lee_el_modelo()


def test_habla_como_asesora_del_equipo():
    reglas = sp._REGLAS
    assert "HABLAS COMO ASESORA DEL EQUIPO" in reglas
    assert "te la enviamos" in reglas and "lo defino yo" in reglas


def test_si_no_esta_en_el_chat_pregunta():
    reglas = sp._REGLAS
    assert "SI NO ESTÁ EN EL CHAT, PREGUNTA" in reglas
    assert "torta keto y torta baja en carbohidratos" in reglas and "no elijas por él" in reglas
