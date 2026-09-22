"""LOS GUARDIAS MIRAN AL CLIENTE — ningún guardia corta una RESPUESTA a lo que el cliente pidió.

🔴 EL CASO REAL (taller, 31-ago 21:08, verificado en los logs del worker y en Redis). La clienta
preguntó *"De que sabor tienes?"*. El modelo escribió la respuesta PERFECTA — *"Los sabores
disponibles son: limón, zanahoria, naranja, piña, vainilla, marmoleada, manzana canela y cambur.
Cuál te provoca?"* (los sabores recién cargados por Maired en `variantes.sabores`) — y la RED DEL
CIERRE la censuró: vio "pregunta el sabor otra vez" + "ya lo preguntó antes" + "sin registrar" y
creyó que era el bucle. El regaño empujó al modelo a saltarse el tema, y al cliente le llegó
*"Para cuándo la necesitas…?"* — como si el bot no supiera los sabores que SÍ sabía.

LA REGLA DE DISEÑO QUE ESTA SUITE FIJA (auditoría de los 13 guardias, 1-sep): un guardia que
juzga un BORRADOR tiene que mirar también lo que el CLIENTE acaba de pedir. Responder no es
insistir; negar no es prometer. La auditoría encontró DOS guardias con ese punto ciego:

  1. RED DEL CIERRE — no miraba la pregunta del cliente. Absolución: `_cliente_pidio_ese_dato`.
  2. RED DEL DÍA IMPOSIBLE — disparaba al NOMBRAR un día no entregable aunque el bot lo
     estuviera NEGANDO ("los domingos no entregamos" es la respuesta correcta a "¿entregas el
     domingo?"). La lección ya existía para "hoy" ("Para hoy ya cerraron las entregas" pasa);
     se extiende a los días con nombre, por CLÁUSULA (una negación no absuelve la promesa de
     al lado: "el domingo no puedo, pero el sábado sí te lo dejo" sigue contando el sábado).

Los otros 11 guardias NO tienen el punto ciego (queda escrito en SESIONES 1-sep): las redes de
MENTIRA y DINERO (dinero, datos bancarios, honestidad, pedido fantasma, envío fantasma, salud)
no dependen de la petición del cliente — lo prohibido está prohibido aunque el cliente lo pida —;
la asesoría, el tamaño adivinado, las fotos y el catálogo YA miran al cliente; y el relevo y el
bucle genérico no cortan el texto (solo avisan a la dueña).
"""
import json

import pytest

import app.agent.agent as ag
from app.agent.agent import (
    _cliente_pidio_ese_dato,
    _dias_nombrados,
)

# ══════════════════════════════════════════════════════════════════════════════════
#  1. LA ABSOLUCIÓN DE LA RED DEL CIERRE: el cliente PIDIÓ el dato
# ══════════════════════════════════════════════════════════════════════════════════


def test_pedir_la_lista_de_sabores_es_pedir_el_dato():
    """Las formas reales de pedirla: con signo, sin signo, y mandando a decir."""
    for pide in (
        "De que sabor tienes?",                    # la clienta del caso real, literal
        "¿Qué sabores hay?",
        "que sabores tienes",                      # sin signo — así se escribe por WhatsApp
        "cuales rellenos tienes",
        "dime los sabores porfa",
        "y de qué sabor las tienes",
    ):
        assert _cliente_pidio_ese_dato(pide), pide


def test_dar_el_dato_o_hablar_de_otra_cosa_NO_es_pedirlo():
    """🔴 La absolución NO puede tragarse el bucle real: si el cliente ya DIO el sabor (o habla
    de otra cosa), el bot re-preguntándolo sigue siendo el bucle que la red debe cortar."""
    for no_pide in (
        "quiero el sabor limón",                   # lo DIO, no lo pidió
        "para el domingo, retiro yo",              # el guion del bucle real (test_red_del_cierre)
        "ok",
        "listo, gracias",
        "",
        "[SISTEMA] Vuelves a atender este chat…",  # una orden interna JAMÁS es el cliente pidiendo
    ):
        assert not _cliente_pidio_ese_dato(no_pide), no_pide


# ══════════════════════════════════════════════════════════════════════════════════
#  2. EL CARRIL, con el caso del 31-ago de punta a punta
# ══════════════════════════════════════════════════════════════════════════════════

def _msg(content: str = "", tools: list | None = None) -> dict:
    m: dict = {"role": "assistant", "content": content}
    if tools:
        m["tool_calls"] = [
            {"id": f"call_{i}", "type": "function",
             "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}}
            for i, (n, a) in enumerate(tools)
        ]
    return m


@pytest.fixture(autouse=True)
def sin_bd(monkeypatch):
    """El mismo arnés de test_red_del_cierre: `responder()` real, sin Postgres."""
    async def _activas():
        return frozenset({"info_producto", "registrar_pedido", "generar_datos_pago", "pedir_ayuda"})

    async def _partes(nombre_cliente, telefono, *, activas=None, quien="uno"):
        return ("Eres Alejandra.\nTorta baja (id_para_pedir=31) = $20", "Hoy es viernes.")

    async def _config_uno():
        return "uno", "modelo/x", "modelo/x"

    async def _sin_productos(texto, tope=2):
        return []

    async def _no(*a, **kw):
        return None

    async def _falso(*a, **kw):
        return False

    async def _cero(*a, **kw):
        return 0.0

    monkeypatch.setattr(ag, "leer_tools_activas", _activas)
    monkeypatch.setattr(ag, "construir_partes_prompt", _partes)
    monkeypatch.setattr(ag, "leer_config_agente", _config_uno)
    monkeypatch.setattr(ag, "productos_enfocados", _sin_productos)
    monkeypatch.setattr(ag, "media_ya_mostrada", _falso)
    monkeypatch.setattr(ag, "etiqueta_recordada", _no)
    monkeypatch.setattr(ag, "horas_de_silencio", _cero)
    monkeypatch.setattr(ag, "abrir_turno", lambda *a, **kw: None)
    monkeypatch.setattr(ag, "registrar", lambda *a, **kw: None)


# El historial EXACTO de la clase del caso real: el bot ya había ofrecido elegir sabor.
HISTORIAL_DEL_CASO = [
    {"role": "user", "content": "quiero una torta baja en carbohidratos, de 250"},
    {"role": "assistant", "content": "Tienes algún sabor en mente o lo coordinas después con nosotros?"},
]

# El borrador que la red censuró aquella noche, literal (del log del worker).
RESPUESTA_CON_LA_LISTA = (
    "Los sabores disponibles son: limón, zanahoria, naranja, piña, vainilla, "
    "marmoleada, manzana canela y cambur.\n\nCuál te provoca?"
)


async def _correr(mensaje_cliente: str, respuestas: list[dict]):
    pendientes = list(respuestas)
    avisos: list[str] = []

    async def llm(messages, tools, model):
        avisos[:] = [
            str(m.get("content", "")) for m in messages
            if m.get("role") == "user" and str(m.get("content", "")).startswith("[SISTEMA]")
        ]
        return {"choices": [{"message": pendientes.pop(0) if pendientes else _msg("listo")}]}

    async def ejecutar(nombre, args, telefono, *a, **kw):
        return {"ok": True}

    texto = await ag.responder(
        "584120000000", mensaje_cliente, list(HISTORIAL_DEL_CASO), "Rosa",
        llm=llm, ejecutar=ejecutar,
    )
    return texto, avisos


@pytest.mark.asyncio
async def test_la_lista_de_sabores_que_el_cliente_PIDIO_sale_entera():
    """🔴 EL CASO DEL 31-AGO: el cliente pide la lista → el bot la da → NADIE lo regaña y la
    respuesta le llega COMPLETA. Antes de este arreglo, la red del cierre la censuraba y al
    cliente le llegaba la pregunta de la entrega, como si el bot no supiera los sabores."""
    texto, avisos = await _correr("De que sabor tienes?", [_msg(RESPUESTA_CON_LA_LISTA)])
    reganos = [a for a in avisos if "YA LE PREGUNTASTE" in a]
    assert reganos == [], f"la red censuró la RESPUESTA a lo que el cliente pidió: {reganos}"
    assert "limón" in texto and "cambur" in texto, f"la lista no llegó entera: {texto!r}"


@pytest.mark.asyncio
async def test_el_bucle_real_se_sigue_cortando():
    """🔴 LA ABSOLUCIÓN NO ABRE EL HUECO VIEJO: si el cliente NO pidió el sabor y el bot lo
    re-pregunta sin registrar, la red regaña igual que siempre (el caso medido: 5/5 turnos
    pidiendo el sabor y 0 pedidos en la base)."""
    texto, avisos = await _correr(
        "para el domingo, retiro yo",
        [
            _msg("Perfecto, retiro. Pero antes, de cuál sabor te las preparo?"),
            _msg("Listo Rosa, te lo dejo agendado",
                 tools=[("registrar_pedido", {"items": [{"variante_id": 31, "cantidad": 1}]})]),
            _msg("Listo Rosa, agendado para el domingo"),
        ],
    )
    assert any("YA LE PREGUNTASTE" in a for a in avisos), "el bucle real dejó de cortarse"


# ══════════════════════════════════════════════════════════════════════════════════
#  3. RED DEL DÍA IMPOSIBLE: negar/explicar un día NO es prometerlo
# ══════════════════════════════════════════════════════════════════════════════════

def test_negar_un_dia_no_cuenta_como_nombrarlo():
    """'¿Entregas los domingos?' → 'Los domingos no entregamos' es la respuesta CORRECTA: la
    red no puede obligar a reescribirla. La lección ya existía para 'hoy' ('Para hoy ya
    cerraron las entregas' pasa) — se extiende a los días con nombre."""
    assert "domingo" not in _dias_nombrados("Los domingos no entregamos")
    assert "domingo" not in _dias_nombrados("El domingo no hay entrega, te lo dejo el lunes")
    assert "manana" not in _dias_nombrados("Mañana no se puede, te lo tengo el jueves")


def test_la_negacion_NO_absuelve_la_promesa_de_al_lado():
    """🔴 Por CLÁUSULA, no por frase entera: en 'el domingo no entregamos, pero el sábado sí
    te lo dejo', el sábado está siendo PROMETIDO y tiene que seguir contando."""
    dias = _dias_nombrados("El domingo no entregamos, pero el sábado sí te lo dejo")
    assert "domingo" not in dias
    assert "sabado" in dias
    # Y el día prometido en la frase de al lado también cuenta.
    dias2 = _dias_nombrados("El domingo no hay entrega, te lo dejo el lunes")
    assert "lunes" in dias2


def test_prometer_sigue_contando_igual_que_siempre():
    """La frase EXACTA del caso del 22-ago (el bot prometiendo de cabeza) sigue cazada."""
    dias = _dias_nombrados("Te las dejo para mañana domingo, o prefieres el lunes?")
    assert "domingo" in dias
    assert "lunes" in dias
    # Y un 'no' que no niega la ENTREGA no absuelve nada:
    assert "domingo" in _dias_nombrados("No te preocupes, te lo dejo el domingo")


def test_lo_de_hoy_queda_exactamente_igual():
    """El mecanismo de 'hoy' (negación en el mensaje) NO se tocó: estaba probado y funcionando."""
    assert "hoy" not in _dias_nombrados("Para hoy ya cerraron las entregas")
    assert "hoy" in _dias_nombrados("Te lo dejo hoy mismo")
