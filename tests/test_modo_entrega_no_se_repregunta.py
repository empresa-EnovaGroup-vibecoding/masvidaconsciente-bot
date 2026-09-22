"""LO QUE EL CLIENTE YA ELIGIÓ NO SE REPREGUNTA: el MODO DE ENTREGA (6-sep, lo cazó Maired).

EL CASO (pruebas, 21:22): "Me gustaría que me lo enviaras por delivery" → el bot: "En qué zona
estás, Barquisimeto centro, oeste, este, **o retiras en La Mendera?**". El cliente ya había
elegido delivery y el bot le volvió a ofrecer el retiro.

LAS DOS CAUSAS, y por qué ninguna regla del prompt bastaba:
  · La elección vivía SOLO en el chat: el pedido aún no existía, así que ESTADO DEL CLIENTE no
    tenía nada que decir (ese bloque nace del pedido). El hilo de la venta destilaba producto,
    masa, tamaño y sabor — pero no el modo de entrega.
  · La lista CERRADA de zonas trae la de retiro como una zona más, y la orden "léele las zonas"
    la incluía. El texto de la caja ("pregúntale si lo retira o si quiere delivery") también era
    incondicional.

LA CLASE ENTERA (cacería): ¿qué elecciones del cliente tienen casilla para no repreguntarse?
  producto/masa/tamaño/sabor → hilo de la venta · cantidad, zona, fecha, método de pago, franja,
  referencia → ESTADO DEL CLIENTE (cuando el pedido existe) · nombre → FICHA DEL CLIENTE.
  Faltaba UNA: el modo de entrega ANTES de que exista el pedido. Esta es su casilla.
"""
import inspect

from app.agent import agent, tools
from app.agent import system_prompt as sp
from app.agent.tools import modo_de_entrega_en


def _hist(*textos_cliente: str) -> list[dict]:
    return [{"role": "user", "content": t} for t in textos_cliente]


# ══ La detección, con frases reales ══

def test_el_caso_literal_es_delivery():
    assert modo_de_entrega_en("Me gustaría que me lo enviaras por delivery", []) == "delivery"


def test_formas_de_pedir_delivery():
    for frase in (
        "por delivery",
        "me lo pueden llevar a casa?",
        "envíenmelo por favor",
        "que me lo traigan",
        "a domicilio",
        "mandamelo a mi casa",
    ):
        assert modo_de_entrega_en(frase, []) == "delivery", frase


def test_formas_de_pedir_retiro():
    for frase in (
        "lo retiro yo",
        "yo lo paso buscando",
        "voy a buscarlo el martes",
        "prefiero retirar",
        "lo recojo en la mendera",
    ):
        assert modo_de_entrega_en(frase, []) == "retiro", frase


def test_sin_mencion_no_decide():
    assert modo_de_entrega_en("las de pistacho, un paquete", []) is None
    assert modo_de_entrega_en("", []) is None
    assert modo_de_entrega_en("", None) is None


def test_la_eleccion_viaja_desde_el_historial():
    """El bot pregunta la zona en el turno siguiente: la elección está a un turno de distancia."""
    h = _hist("hola, tienes galletas?", "Me gustaría que me lo enviaras por delivery")
    assert modo_de_entrega_en("Barquisimeto centro", h) == "delivery"


def test_la_mas_reciente_gana():
    h = _hist("me lo llevan a casa?", "mejor lo retiro yo")
    assert modo_de_entrega_en("ok", h) == "retiro"


def test_una_duda_no_se_resuelve_por_mayoria():
    """"no sé si retirar o que me lo lleven" no decide — y borra lo anterior."""
    h = _hist("por delivery")
    assert modo_de_entrega_en("no se si retirar o que me lo lleven", h) is None


def test_no_confunde_al_bot_con_el_cliente():
    """Solo cuentan los turnos del CLIENTE: que el bot haya dicho 'delivery' no es una elección."""
    h = [{"role": "assistant", "content": "lo retiras o te lo llevo por delivery?"}]
    assert modo_de_entrega_en("las de pistacho", h) is None


# ══ El cableado: la elección llega como HECHO, y las tres frases que repreguntaban ya no ══

def test_responder_inyecta_el_modo_en_la_parte_dinamica():
    src = inspect.getsource(agent.responder)
    i_modo = src.index("MODO DE ENTREGA YA ELEGIDO por el cliente: DELIVERY")
    i_msgs = src.index('"role": "system"', i_modo)
    assert i_modo < i_msgs, "va en `dinamico`, antes de armar los messages (la estable es la cacheada)"
    assert "NO le nombres la zona de retiro" in src
    assert "MODO DE ENTREGA YA ELEGIDO por el cliente: RETIRO" in src
    assert "modo_de_entrega_en(mensaje_usuario, historial)" in src


def test_el_bloque_de_zonas_ya_no_manda_preguntar_incondicionalmente():
    src = inspect.getsource(sp._zonas_bloque)
    assert "si el cliente AÚN NO lo dijo, pregunta si lo RETIRA o quiere" in src
    assert "LO QUE YA ELIGIÓ NO SE REPREGUNTA" in src
    assert "ofrécela SOLO si él dijo que retira" in src


def test_la_caja_tampoco_repregunta_lo_ya_dicho():
    src = inspect.getsource(tools.generar_datos_pago)
    assert "Pregúntale si lo retira o si quiere delivery, y en ese caso" not in src
    # (las frases se cortan por el salto de línea del código fuente: se buscan trozos enteros)
    assert "NO le vuelvas a ofrecer retiro" in src
    assert "Solo si no ha dicho cómo lo recibe" in src
    assert "(y si es retiro o delivery)" not in src
