"""🔇 CUÁNDO SE CALLA EL BOT AL PEDIR AYUDA — la decisión del 14-jul (commit 0722794), fijada.

Pausa (le deja el chat a una persona) SOLO cuando el cliente pide una persona, reclama, o hay un acuerdo
especial (devolver envases, pagar en partes, cambiar un producto, llevarlo fuera de las zonas). Para un
PRECIO DEL DÍA que no está cargado o un dato que no sabe (`no_se`) deja el aviso pero SIGUE VENDIENDO:
muestra fotos, ofrece otros productos, toma el pedido. El 22-sep un cambio pasó a pausar por los cinco
motivos sin que nadie lo decidiera; este test existe para que eso no vuelva a colarse en silencio.
"""
from app.agent import tools


def test_pausa_solo_cuando_toca():
    assert tools._MOTIVOS_DE_PAUSA == {"pide_persona", "reclamo", "acuerdo_especial"}


def test_precio_del_dia_y_no_se_avisan_pero_no_callan_al_bot():
    assert set(tools._MOTIVO_TITULO) >= {"precio_del_dia", "no_se"}  # siguen siendo motivos de aviso
    assert "precio_del_dia" not in tools._MOTIVOS_DE_PAUSA
    assert "no_se" not in tools._MOTIVOS_DE_PAUSA
