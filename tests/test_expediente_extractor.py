"""🗂️ EL EXTRACTOR DEL EXPEDIENTE (PR3) — SESIONES (37).

Lo que la dueña dice a mano se vuelve DATO con procedencia, o PROPUESTA si hay duda, o NADA si no
consta. Sin IA real, sin red, sin base: el modelo se simula y el código se prueba entero. Lo que
estos tests fijan (frases SINTÉTICAS al estilo de ella; ningún dato real en el repo):

  · las ventanas agrupan sus mensajes y cortan cuando habla el cliente o pasan 5 min;
  · el intérprete solo acepta UNA llamada y un contrato cerrado (una clave de más lo tumba);
  · el validador: pedido claro → se puede escribir; nombre ambiguo, presentación sin decir, total
    que no cuadra o precio sin cargar → propuesta; UN PAGO ES SIEMPRE PROPUESTA; evidencia que no
    consta → descarta; entrega "mañana en la tarde" sobre el pedido abierto → fecha + franja;
  · en modo `propuestas` (el default) NADA se escribe: todo va a la Bandeja, y no se duplica;
  · `aplicar_propuesta` es la única puerta: pedido 'confirmado' con origen 'dueña', pago 'confirmado'
    firmado por quien tocó, y se niega sobre pedidos cancelados o ya pagados.
"""
import inspect
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent import expediente as ex
from app.agent.contratos_atencion import Contexto, EventoDuena, ItemDuena, PropuestaExpediente
from app.models import Conocimiento, Pago, Pedido

TEL = "584240000000"
HOY = date(2026, 9, 22)  # martes
FRANJAS = ["en la mañana (10 a 12)", "en la tarde (2 a 5)"]
T0 = datetime(2026, 9, 22, 14, 0, tzinfo=UTC)


def _var(vid, presentacion, precio):
    return {"id": vid, "presentacion": presentacion, "precio": precio, "sabores": None, "disponibilidad": True}


@pytest.fixture
def contexto():
    return Contexto(hoy=HOY, productos={
        1: {"id": 1, "nombre": "Quesillo", "categoria": "postres", "disponibilidad": True,
            "variantes": {11: _var(11, "500 g", Decimal("18.00"))}},
        2: {"id": 2, "nombre": "Empanadas", "categoria": "salado", "disponibilidad": True,
            "variantes": {21: _var(21, "x4", Decimal("7.00")), 22: _var(22, "x8", Decimal("14.00"))}},
        3: {"id": 3, "nombre": "Torta Keto", "categoria": "postres", "disponibilidad": True,
            "variantes": {31: _var(31, "1 kg", None)}},  # precio del día sin cargar
    }, zonas={2: {"id": 2, "nombre": "Cabudare", "referencias": "", "es_retiro": False, "costo": Decimal("2")}},
       metodos={"Zelle": {"id": 1, "tipo": "zelle"}})


def _msg(i, rol, contenido, minutos=0, tipo="text"):
    return {"id": i, "rol": rol, "tipo": tipo, "contenido": contenido, "created_at": T0 + timedelta(minutes=minutos)}


def _validar(ev, texto, ctx, pedido=None):
    return ex.validar(ev, texto, ctx, telefono=TEL, hoy=HOY, franjas=FRANJAS, pedido=pedido, evidencia_id=77)


PEDIDO_ABIERTO = {"id": 30, "estado": "confirmado", "fecha": None, "total": Decimal("36"), "items": []}


# ══════════════════════════════════════════════════════════════════════════════════
#  LAS VENTANAS
# ══════════════════════════════════════════════════════════════════════════════════

def test_ventanas_owner_agrupa_y_corta_con_el_cliente_y_con_el_hueco():
    mensajes = [
        _msg(1, "user", "hola, quiero quesillo"),
        _msg(2, "owner", "claro mi reina", 1),
        _msg(3, "owner", "te anoté 2 quesillos, son 36$", 2),
        _msg(4, "user", "listo, ya transferí", 3),
        _msg(5, "owner", "recibido 🙏", 4),
        _msg(6, "owner", "te lo llevo mañana en la tarde", 20),  # hueco > 5 min: ventana aparte
    ]
    ventanas = ex.ventanas_owner(mensajes)
    assert [[m["id"] for m in v.owner] for v in ventanas] == [[2, 3], [5], [6]]
    assert ventanas[0].ultimo_cliente == "hola, quiero quesillo"
    assert ventanas[1].ultimo_cliente == "listo, ya transferí"
    assert ventanas[0].texto == "claro mi reina\nte anoté 2 quesillos, son 36$"
    assert ventanas[0].ultimo_id == 3


def test_ventanas_owner_guarda_lo_que_pidio_la_clienta():
    """📏 La ventana lleva TODO lo que la clienta escribió desde la ventana anterior (no solo su último
    mensaje), y una ventana que sigue tras un hueco sin que la clienta hable conserva ese contexto."""
    mensajes = [
        _msg(1, "user", "hola amiga"),
        _msg(2, "user", "véndeme 1 kilo de harina"),
        _msg(3, "user", "paso mañana"),
        _msg(4, "owner", "Ok amiga", 1),
        _msg(5, "owner", "te espero en la tarde", 20),
        _msg(6, "user", "gracias"),
        _msg(7, "owner", "a ti", 21),
    ]
    v = ex.ventanas_owner(mensajes)
    assert v[0].cliente == ["hola amiga", "véndeme 1 kilo de harina", "paso mañana"]
    assert v[1].cliente == v[0].cliente
    assert v[2].cliente == ["gracias"]
    assert "1 kilo de harina" in v[0].texto_cliente


def test_una_ubicacion_sola_no_es_una_entrega_acordada(contexto):
    """📏 Medición 8-oct: mandar el enlace del mapa se escribía como entrega."""
    ev = EventoDuena(tipo="entrega_acordada", lugar_texto="https://maps.example/?q=10,-69", evidencia="https://maps.example/?q=10,-69")
    v = _validar(ev, "Voy amor\nhttps://maps.example/?q=10,-69", contexto, pedido=PEDIDO_ABIERTO)
    assert v.accion == "propuesta" and "solo dijo un lugar" in v.motivo


async def test_el_lector_recibe_lo_que_pidio_la_clienta(contexto):
    llm = _llm_con({"eventos": []})
    v = ex.Ventana(owner=[_msg(4, "owner", "Ok amiga")], cliente=["véndeme 1 kilo de harina", "paso mañana"])
    await ex.interpretar_duena(v, contexto, llm, "m")
    usuario = llm.await_args.args[0][1]["content"]
    assert "véndeme 1 kilo de harina" in usuario and "paso mañana" in usuario and "Ok amiga" in usuario


def test_ventanas_owner_salta_placeholders_y_mensajes_del_bot():
    mensajes = [
        _msg(1, "owner", "[nota de voz]", tipo="audio"),
        _msg(2, "owner", "[foto]", 1, tipo="image"),
        _msg(3, "assistant", "hola! en qué te ayudo", 2),
        _msg(4, "owner", "🎤 te lo llevo mañana", 3, tipo="audio"),
    ]
    ventanas = ex.ventanas_owner(mensajes)
    assert [[m["id"] for m in v.owner] for v in ventanas] == [[4]]


# ══════════════════════════════════════════════════════════════════════════════════
#  EL INTÉRPRETE: una llamada, un contrato cerrado
# ══════════════════════════════════════════════════════════════════════════════════

def _llm_con(args: dict):
    return AsyncMock(return_value={"choices": [{"message": {"tool_calls": [
        {"function": {"name": "proponer_eventos_duena", "arguments": json.dumps(args)}},
    ]}}]})


async def test_interpretar_acepta_el_contrato_y_no_manda_precios(contexto):
    llm = _llm_con({"eventos": [{"tipo": "pago_confirmado", "evidencia": "recibido", "monto_literal": "36$"}]})
    v = ex.Ventana(owner=[_msg(5, "owner", "recibido 🙏")], ultimo_cliente="ya transferí")
    r = await ex.interpretar_duena(v, contexto, llm, "modelo-barato")
    assert r.eventos[0].tipo == "pago_confirmado" and r.eventos[0].monto_literal == "36$"
    mensajes, herramientas, modelo = llm.await_args.args
    assert modelo == "modelo-barato" and herramientas[0]["function"]["name"] == "proponer_eventos_duena"
    sistema = mensajes[0]["content"]
    assert "18" not in sistema and "precio" not in json.loads(sistema.split("(solo nombres):\n")[1])["productos"][0]
    assert "ya transferí" in mensajes[1]["content"] and "recibido" in mensajes[1]["content"]


async def test_interpretar_rechaza_sin_llamada_o_con_claves_de_mas(contexto):
    v = ex.Ventana(owner=[_msg(5, "owner", "recibido")])
    sin_llamada = AsyncMock(return_value={"choices": [{"message": {"content": "Listo!"}}]})
    with pytest.raises(ValueError):
        await ex.interpretar_duena(v, contexto, sin_llamada, "m")
    con_extra = _llm_con({"eventos": [{"tipo": "pago_confirmado", "evidencia": "recibido", "monto": 99}]})
    with pytest.raises(ValueError):  # `monto` no existe en el contrato → sin respaldo, se lanza
        await ex.interpretar_duena(v, contexto, con_extra, "m")


# ── Lo que pasó la primera noche en pruebas (22-sep) y cómo se cubre ──

def test_el_esquema_de_la_herramienta_va_sin_referencias_internas():
    """Flash-Lite se perdió con `$ref`/`$defs` y devolvió palabras sueltas. El esquema va inline."""
    esquema = ex.esquema_sin_refs(ex.ExtraccionDuena.model_json_schema())
    assert "$defs" not in json.dumps(esquema) and "$ref" not in json.dumps(esquema)
    evento = esquema["properties"]["eventos"]["items"]
    assert evento["type"] == "object" and "tipo" in evento["properties"]
    assert evento["properties"]["items"]["items"]["properties"]["nombre_literal"]["type"] == "string"


async def test_palabras_sueltas_se_reintentan_con_el_respaldo_una_sola_vez(contexto):
    """`{"eventos": ["pedido_tomado"]}` (lo que devolvió Flash-Lite) NO se traga como vacío: se
    reintenta UNA vez con el modelo de respaldo, y si ese responde bien, vale."""
    v = ex.Ventana(owner=[_msg(3, "owner", "Te anoté 2 quesillos son 16$")])
    bueno = {"eventos": [{"tipo": "pedido_tomado", "items": [{"nombre_literal": "quesillos", "cantidad_literal": "2"}],
                          "total_literal": "16$", "evidencia": "Te anoté 2 quesillos son 16$"}]}
    llm = AsyncMock(side_effect=[
        _llm_con({"eventos": ["pedido_tomado"]}).return_value,
        _llm_con(bueno).return_value,
    ])
    r = await ex.interpretar_duena(v, contexto, llm, "barato", modelo_respaldo="respaldo")
    assert r.eventos[0].tipo == "pedido_tomado" and r.eventos[0].items[0].cantidad_literal == "2"
    assert llm.await_count == 2
    assert [c.args[2] for c in llm.await_args_list] == ["barato", "respaldo"]
    # Si el respaldo también falla, se lanza (y `procesar_ventana` descarta la ventana sin escribir).
    llm2 = AsyncMock(return_value=_llm_con({"eventos": ["pedido_tomado"]}).return_value)
    with pytest.raises(ValueError, match="palabras sueltas"):
        await ex.interpretar_duena(v, contexto, llm2, "barato", modelo_respaldo="respaldo")
    assert llm2.await_count == 2


async def test_sin_respaldo_o_con_el_mismo_modelo_no_se_reintenta(contexto):
    v = ex.Ventana(owner=[_msg(3, "owner", "recibido")])
    llm = AsyncMock(return_value=_llm_con({"eventos": ["pago_confirmado"]}).return_value)
    with pytest.raises(ValueError):
        await ex.interpretar_duena(v, contexto, llm, "barato", modelo_respaldo="barato")
    assert llm.await_count == 1


async def test_numeros_donde_iba_texto_y_textos_largos_se_normalizan(contexto):
    """`"cantidad_literal": 2` y `"total_literal": 16` (sin comillas) pasan a texto; una evidencia
    desbordada se recorta a su tope y sigue constando (es un prefijo literal)."""
    larga = "Te anoté 2 quesillos son 36 " + "y muchas gracias mi reina " * 20  # 2 × $18 del catálogo de prueba
    v = ex.Ventana(owner=[_msg(3, "owner", larga)])
    llm = _llm_con({"eventos": [{"tipo": "pedido_tomado", "items": [{"nombre_literal": "quesillos", "cantidad_literal": 2}],
                                 "total_literal": 36, "evidencia": larga}]})
    r = await ex.interpretar_duena(v, contexto, llm, "m")
    ev = r.eventos[0]
    assert ev.items[0].cantidad_literal == "2" and ev.total_literal == "36"
    assert len(ev.evidencia) == 300 and larga.startswith(ev.evidencia)
    veredicto = _validar(ev, larga, contexto)
    assert veredicto.accion == "escribe" and veredicto.propuesta.total == 36.0


# ══════════════════════════════════════════════════════════════════════════════════
#  EL VALIDADOR
# ══════════════════════════════════════════════════════════════════════════════════

def test_pedido_claro_con_total_que_cuadra_se_puede_escribir(contexto):
    ev = EventoDuena(tipo="pedido_tomado", items=[ItemDuena(nombre_literal="quesillos", cantidad_literal="2")],
                     total_literal="36$", evidencia="te anoté 2 quesillos, son 36$")
    v = _validar(ev, "claro mi reina\nte anoté 2 quesillos, son 36$", contexto)
    assert v.accion == "escribe" and v.confianza == 1.0
    p = v.propuesta
    assert [(i.producto_id, i.variante_id, i.cantidad, i.precio_unitario) for i in p.items] == [(1, 11, 2, 18.0)]
    assert p.total == 36.0 and p.moneda == "$" and p.evidencia_mensaje_id == 77 and p.telefono == TEL


def test_sin_cantidad_nunca_se_supone_uno_queda_como_pregunta(contexto):
    """📏 Medición 8-oct: el lector suponía "1" y lo escribía solo (4 pedidos falsos en 27)."""
    ev = EventoDuena(tipo="pedido_tomado", items=[ItemDuena(nombre_literal="quesillo")], evidencia="te anoté el quesillo")
    v = _validar(ev, "te anoté el quesillo", contexto)
    assert v.accion == "propuesta" and "nadie dijo la cantidad" in v.motivo
    # Una cantidad que el modelo "copió" pero no está escrita en ningún lado tampoco vale.
    ev2 = EventoDuena(tipo="pedido_tomado", items=[ItemDuena(nombre_literal="quesillo", cantidad_literal="3")],
                      total_literal="54$", evidencia="te anoté el quesillo, 54$")
    assert _validar(ev2, "te anoté el quesillo, 54$", contexto).accion == "propuesta"


def test_mencionar_un_producto_no_es_venderlo(contexto):
    """📏 Medición 8-oct: "3 dedos de kéfir en la mañana" (cómo se toma) se escribía como 3 kéfir.
    Sin total y sin que la clienta lo haya pedido, es una mención: pregunta, nunca escritura."""
    ev = EventoDuena(tipo="pedido_tomado", items=[ItemDuena(nombre_literal="quesillo", cantidad_literal="3")],
                     evidencia="3 dedos de quesillo en la mañana")
    v = ex.validar(ev, "3 dedos de quesillo en la mañana", contexto, telefono=TEL, hoy=HOY, franjas=FRANJAS,
                   pedido=None, evidencia_id=77, texto_cliente="(el cliente envió un sticker)")
    assert v.accion == "propuesta" and "no lo pidió" in v.motivo


def test_la_clienta_pide_y_ella_acepta_es_un_pedido(contexto):
    """📏 Lo más común en las conversaciones reales: la clienta pide, ella dice "Ok amiga". El
    producto y la cantidad salen de la clienta; la evidencia (el sí) es de ella."""
    ev = EventoDuena(tipo="pedido_tomado", items=[ItemDuena(nombre_literal="quesillo", cantidad_literal="2")],
                     evidencia="Ok amiga, claro que sí")
    v = ex.validar(ev, "Ok amiga, claro que sí", contexto, telefono=TEL, hoy=HOY, franjas=FRANJAS, pedido=None,
                   evidencia_id=77, texto_cliente="véndeme 2 quesillo, paso mañana")
    assert v.accion == "escribe" and v.propuesta.items[0].cantidad == 2 and "lo pidió la clienta" in v.motivo
    # El "sí" tiene que ser de ELLA: una evidencia que solo está en el mensaje de la clienta no consta.
    ev_mala = EventoDuena(tipo="pedido_tomado", items=[ItemDuena(nombre_literal="quesillo", cantidad_literal="2")],
                          evidencia="véndeme 2 quesillo")
    assert ex.validar(ev_mala, "Ok amiga", contexto, telefono=TEL, hoy=HOY, franjas=FRANJAS, pedido=None,
                      evidencia_id=77, texto_cliente="véndeme 2 quesillo").accion == "descarta"


def test_un_producto_que_no_consta_en_la_conversacion_es_pregunta(contexto):
    ev = EventoDuena(tipo="pedido_tomado", items=[ItemDuena(nombre_literal="quesillo", cantidad_literal="2")],
                     total_literal="36$", evidencia="son 36$")
    assert _validar(ev, "son 36$", contexto).accion == "propuesta"


def test_producto_con_varias_presentaciones_sin_decir_cual_es_propuesta(contexto):
    ev = EventoDuena(tipo="pedido_tomado", items=[ItemDuena(nombre_literal="empanadas", cantidad_literal="1")],
                     evidencia="te anoté las empanadas")
    v = _validar(ev, "te anoté las empanadas", contexto)
    assert v.accion == "propuesta" and "presentaciones" in v.motivo


def test_la_presentacion_dicha_en_la_ventana_resuelve_la_variante(contexto):
    ev = EventoDuena(tipo="pedido_tomado", items=[ItemDuena(nombre_literal="empanadas", cantidad_literal="una")],
                     total_literal="14", evidencia="te anoté una de empanadas x8, 14")
    v = _validar(ev, "te anoté una de empanadas x8, 14", contexto)
    assert v.accion == "escribe" and v.propuesta.items[0].variante_id == 22 and v.propuesta.total == 14.0


def test_total_que_no_cuadra_es_propuesta_y_conserva_lo_que_ella_dijo(contexto):
    ev = EventoDuena(tipo="pedido_tomado", items=[ItemDuena(nombre_literal="quesillos", cantidad_literal="2")],
                     total_literal="30$", evidencia="2 quesillos, te los dejo en 30$")
    v = _validar(ev, "2 quesillos, te los dejo en 30$", contexto)
    assert v.accion == "propuesta" and "no cuadra" in v.motivo
    assert v.propuesta.total == 30.0  # lo pactado por ella manda… si una persona lo confirma


def test_producto_desconocido_o_ambiguo_es_propuesta(contexto):
    ev = EventoDuena(tipo="pedido_tomado", items=[ItemDuena(nombre_literal="pan de yuca", cantidad_literal="1")],
                     evidencia="te anoté el pan de yuca")
    assert _validar(ev, "te anoté el pan de yuca", contexto).accion == "propuesta"


def test_precio_del_dia_sin_cargar_es_propuesta(contexto):
    ev = EventoDuena(tipo="pedido_tomado", items=[ItemDuena(nombre_literal="torta keto", cantidad_literal="1")],
                     evidencia="te anoté la torta keto")
    v = _validar(ev, "te anoté la torta keto", contexto)
    assert v.accion == "propuesta" and "precio" in v.motivo
    assert v.propuesta.items[0].precio_unitario is None


def test_un_pago_es_siempre_propuesta_aunque_todo_cuadre(contexto):
    ev = EventoDuena(tipo="pago_confirmado", monto_literal="36$", metodo="Zelle", evidencia="recibido, gracias")
    v = _validar(ev, "recibido, gracias 🙏", contexto, pedido=PEDIDO_ABIERTO)
    assert v.accion == "propuesta" and v.propuesta.monto == 36.0 and v.propuesta.metodo == "Zelle"
    assert v.propuesta.pedido_id == 30
    # Sin monto dicho, se propone el total del pedido abierto (una persona lo mira).
    ev2 = EventoDuena(tipo="pago_confirmado", evidencia="listo")
    assert _validar(ev2, "listo mi reina", contexto, pedido=PEDIDO_ABIERTO).propuesta.monto == 36.0


def test_evidencia_que_no_consta_o_nada_se_descartan(contexto):
    ev = EventoDuena(tipo="pago_confirmado", evidencia="ya me llegó el pago")
    assert _validar(ev, "hola mi reina, cómo está todo", contexto).accion == "descarta"
    assert _validar(EventoDuena(tipo="nada"), "bendiciones", contexto).accion == "descarta"


# ══ 💰 La regla sencilla (29-sep): la extracción ANOTA los pedidos sin pagar; no elige a ciegas ══
# A cuál va el pago (o si no cuadra y se le pasa a una persona) se decide al preguntar, con
# `tools.pago_cuadra` — ver tests/test_pago_por_whatsapp.py.

def _ped(id, total, pago_pendiente=None, estado="confirmado"):
    return {"id": id, "estado": estado, "total": Decimal(str(total)), "items": [], "pago_pendiente": pago_pendiente}


def _validar_pagos(ev, texto, ctx, pedidos):
    return ex.validar(ev, texto, ctx, telefono=TEL, hoy=HOY, franjas=FRANJAS,
                      pedido=pedidos[0] if pedidos else None, pedidos=pedidos, evidencia_id=77)


def test_con_varios_pedidos_sin_pagar_no_elige_ninguno_y_los_anota(contexto):
    ev = EventoDuena(tipo="pago_confirmado", monto_literal="20$", evidencia="me llegaron los 20")
    v = _validar_pagos(ev, "me llegaron los 20", contexto, [_ped(30, 36), _ped(29, 20)])
    assert v.propuesta.pedido_id is None and v.propuesta.candidatos == [30, 29]
    assert v.propuesta.monto == 20.0 and "2 pedidos sin pagar" in v.motivo


def test_varios_sin_monto_tampoco_elige_el_mas_reciente(contexto):
    ev = EventoDuena(tipo="pago_confirmado", evidencia="listo")
    v = _validar_pagos(ev, "listo mi reina", contexto, [_ped(30, 36), _ped(29, 20)])
    assert v.propuesta.pedido_id is None and v.propuesta.monto is None  # no inventa de cuál es el total


def test_un_solo_pedido_sin_pagar_es_ese(contexto):
    ev = EventoDuena(tipo="pago_confirmado", evidencia="me pagó")
    v = _validar_pagos(ev, "me pagó", contexto, [_ped(30, 36, pago_pendiente="confirmado"), _ped(29, 20)])
    assert v.propuesta.pedido_id == 29 and v.propuesta.candidatos == [29]
    assert v.propuesta.monto == 20.0  # sin monto dicho, el total del único sin pagar


def test_sin_lista_de_pedidos_el_pago_se_porta_como_antes(contexto):
    ev = EventoDuena(tipo="pago_confirmado", monto_literal="36$", metodo="Zelle", evidencia="recibido")
    v = _validar(ev, "recibido 🙏", contexto, pedido=PEDIDO_ABIERTO)  # pedidos=None (default)
    assert v.propuesta.pedido_id == 30 and v.propuesta.monto == 36.0


def test_procesar_ventana_le_pasa_la_lista_de_pedidos_a_validar():
    assert "pedidos=ctx.pedidos" in inspect.getsource(ex.procesar_ventana)


def test_entrega_manana_en_la_tarde_sobre_el_pedido_abierto_se_escribe(contexto):
    ev = EventoDuena(tipo="entrega_acordada", fecha_texto="mañana", momento_texto="en la tarde",
                     evidencia="te lo llevo mañana en la tarde")
    v = _validar(ev, "🎤 te lo llevo mañana en la tarde", contexto, pedido=PEDIDO_ABIERTO)
    assert v.accion == "escribe" and v.confianza == 0.9
    assert v.propuesta.fecha == "2026-09-23" and v.propuesta.franja == "en la tarde (2 a 5)"


def test_entrega_sin_pedido_abierto_o_con_fecha_rara_es_propuesta(contexto):
    ev = EventoDuena(tipo="entrega_acordada", fecha_texto="mañana", evidencia="te lo llevo mañana")
    assert _validar(ev, "te lo llevo mañana", contexto, pedido=None).accion == "propuesta"
    # Fecha rara pero CON lugar: hay algo que una persona puede confirmar → propuesta con la duda.
    ev2 = EventoDuena(tipo="entrega_acordada", fecha_texto="un día de estos", lugar_texto="frente a la plaza",
                      evidencia="te lo llevo un día de estos frente a la plaza")
    v2 = _validar(ev2, "te lo llevo un día de estos frente a la plaza", contexto, pedido=PEDIDO_ABIERTO)
    assert v2.accion == "propuesta" and "no entendí la fecha" in v2.motivo and v2.propuesta.lugar == "frente a la plaza"
    # Sin fecha, momento ni lugar reconocibles no hay NADA que aplicar: se descarta, no se molesta a nadie.
    ev3 = EventoDuena(tipo="entrega_acordada", fecha_texto="un día de estos", evidencia="te lo llevo un día de estos")
    assert _validar(ev3, "te lo llevo un día de estos", contexto, pedido=PEDIDO_ABIERTO).accion == "descarta"


def test_respuesta_general_con_tema_raro_cae_a_politica_y_es_propuesta(contexto):
    ev = EventoDuena(tipo="respuesta_general", tema="recetas", contenido="Todo lo que hago es sin gluten",
                     evidencia="Todo lo que hago es sin gluten")
    v = _validar(ev, "Todo lo que hago es sin gluten y sin azúcar", contexto)
    assert v.accion == "propuesta" and v.propuesta.tema == "politica"


def test_cancelacion_sin_pedido_se_descarta_y_con_pedido_se_propone(contexto):
    ev = EventoDuena(tipo="cancelado", evidencia="quedamos en que no")
    assert _validar(ev, "quedamos en que no, tranquila", contexto).accion == "descarta"
    assert _validar(ev, "quedamos en que no, tranquila", contexto, pedido=PEDIDO_ABIERTO).accion == "propuesta"


@pytest.mark.parametrize("texto,monto,moneda", [
    ("36$", Decimal("36"), "$"), ("$28", Decimal("28"), "$"), ("28,50 dólares", Decimal("28.50"), "$"),
    ("1.500 bs", Decimal("1500"), "Bs"), ("Bs 9.718,28", Decimal("9718.28"), "Bs"), ("14", Decimal("14"), ""),
    ("", None, ""), ("gracias", None, ""),
])
def test_parsear_monto(texto, monto, moneda):
    assert ex.parsear_monto(texto) == (monto, moneda)


@pytest.mark.parametrize("texto,cantidad", [("2", 2), ("dos", 2), ("una", 1), ("media docena", 6), ("", None), ("varios", None)])
def test_parsear_cantidad(texto, cantidad):
    assert ex.parsear_cantidad(texto) == cantidad


# ══════════════════════════════════════════════════════════════════════════════════
#  PROCESAR UNA VENTANA: en `propuestas` nada se escribe
# ══════════════════════════════════════════════════════════════════════════════════

class _Sesion:
    def __init__(self, pendientes=None, get=None, primero=None):
        self.added: list = []
        self.commits = 0
        self.rollbacks = 0
        self._pendientes = list(pendientes or [])
        self._get = get or {}
        self._primero = primero

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, modelo, pk):
        return self._get.get((modelo.__name__, pk))

    async def execute(self, _q):
        pend, primero = self._pendientes, self._primero
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: pend, first=lambda: primero),
            scalar_one_or_none=lambda: primero,
        )

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for n, o in enumerate(self.added, start=1):
            if getattr(o, "id", None) is None:
                o.id = 900 + n

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def _ventana_pedido():
    return ex.Ventana(owner=[_msg(3, "owner", "te anoté 2 quesillos, son 36$", 2)], ultimo_cliente="quiero quesillo")


async def test_un_pedido_claro_ya_no_deja_tarjeta_lo_sigue_alejandra(contexto):
    """🧠 UN SOLO CEREBRO (Maired, 10-oct, SESIONES (48)): antes un pedido claro iba a la Bandeja en
    `propuestas`. Ahora la venta la entiende Alejandra leyendo el chat: el lector no deja tarjeta."""
    ses = _Sesion()
    llm = _llm_con({"eventos": [{"tipo": "pedido_tomado", "items": [{"nombre_literal": "quesillos", "cantidad_literal": "2"}],
                                 "total_literal": "36$", "evidencia": "te anoté 2 quesillos, son 36$"}]})
    for escritura in ("propuestas", "auto"):
        r = await ex.procesar_ventana(lambda: ses, TEL, _ventana_pedido(), contexto, llm=llm, modelo="m",
                                      escritura=escritura, franjas=FRANJAS, hoy=HOY)
        assert r == [("pedido_tomado", "lo_sigue_alejandra")]
    assert ses.added == [] and ses.commits == 0


async def test_un_pago_pendiente_igual_no_se_duplica(contexto):
    ya = SimpleNamespace(propuesta={"tipo": "pago_confirmado", "evidencia_mensaje_id": 3})
    ses = _Sesion(pendientes=[ya])
    llm = _llm_con({"eventos": [{"tipo": "pago_confirmado", "evidencia": "son 36$"}]})
    r = await ex.procesar_ventana(lambda: ses, TEL, _ventana_pedido(), contexto, llm=llm, modelo="m",
                                  escritura="propuestas", franjas=FRANJAS, hoy=HOY)
    assert r == [("pago_confirmado", "repetida")] and ses.added == []


async def test_del_lector_solo_el_pago_va_a_la_bandeja(contexto, monkeypatch):
    aplicado = AsyncMock(return_value={"tipo": "pedido_tomado", "pedido_id": 901})
    monkeypatch.setattr(ex, "aplicar_propuesta", aplicado)
    ses = _Sesion()
    llm = _llm_con({"eventos": [
        {"tipo": "pedido_tomado", "items": [{"nombre_literal": "quesillos", "cantidad_literal": "2"}],
         "total_literal": "36$", "evidencia": "te anoté 2 quesillos, son 36$"},
        {"tipo": "pago_confirmado", "evidencia": "son 36$"},
        {"tipo": "nada", "evidencia": ""},
    ]})
    r = await ex.procesar_ventana(lambda: ses, TEL, _ventana_pedido(), contexto, llm=llm, modelo="m",
                                  escritura="auto", franjas=FRANJAS, hoy=HOY)
    assert r == [("pedido_tomado", "lo_sigue_alejandra"), ("pago_confirmado", "propuesta"), ("nada", "descarta")]
    aplicado.assert_not_awaited()  # en `auto` ya no escribe pedidos solo
    assert [a.motivo for a in ses.added] == [ex.MOTIVO_PROPUESTA]  # solo el pago fue a la Bandeja
    assert ses.added[0].propuesta["tipo"] == "pago_confirmado"


async def test_lo_que_no_esta_en_el_catalogo_se_sigue_avisando(contexto):
    ses = _Sesion()
    llm = _llm_con({"eventos": [{"tipo": "pedido_tomado", "items": [{"nombre_literal": "granola", "cantidad_literal": "1"}],
                                 "evidencia": "te anoté 1 granola"}]})
    ventana = ex.Ventana(owner=[_msg(3, "owner", "te anoté 1 granola", 2)], ultimo_cliente="quiero granola")
    fuera: list[str] = []
    await ex.procesar_ventana(lambda: ses, TEL, ventana, contexto, llm=llm, modelo="m",
                              escritura="propuestas", franjas=FRANJAS, hoy=HOY, fuera_de_catalogo=fuera)
    assert fuera and "granola" in " ".join(fuera).lower()
    assert ses.added == []


async def test_un_contrato_invalido_del_modelo_no_tumba_nada(contexto):
    ses = _Sesion()
    roto = AsyncMock(return_value={"choices": [{"message": {"content": "no sé"}}]})
    r = await ex.procesar_ventana(lambda: ses, TEL, _ventana_pedido(), contexto, llm=roto, modelo="m",
                                  escritura="propuestas", franjas=FRANJAS, hoy=HOY)
    assert r == [] and ses.added == []


# ══════════════════════════════════════════════════════════════════════════════════
#  LA ÚNICA PUERTA DE ESCRITURA
# ══════════════════════════════════════════════════════════════════════════════════

def _prop(**kw):
    base = {"tipo": "pedido_tomado", "telefono": TEL, "evidencia": "te anoté 2 quesillos", "evidencia_mensaje_id": 3,
            "confianza": 1.0, "items": [{"producto_id": 1, "variante_id": 11, "nombre": "Quesillo",
                                         "presentacion": "500 g", "cantidad": 2, "precio_unitario": 18.0}], "total": 36.0}
    base.update(kw)
    return base


async def test_aplicar_pedido_tomado_crea_el_pedido_confirmado_con_procedencia():
    ses = _Sesion()
    r = await ex.aplicar_propuesta(ses, _prop(), usuario="maired@enova")
    (pedido,) = ses.added
    assert isinstance(pedido, Pedido) and r == {"tipo": "pedido_tomado", "pedido_id": pedido.id}
    assert pedido.estado == "confirmado", "jamás 'esperando_pago': dispararía el cobro del bot"
    assert pedido.origen == "dueña" and pedido.evidencia_mensaje_id == 3 and pedido.extraido_at is not None
    assert pedido.total == Decimal("36") and pedido.items[0]["variante_id"] == 11 and pedido.items[0]["cantidad"] == 2
    assert "Whuilianny" in pedido.notas and "maired@enova" in pedido.notas


async def test_aplicar_pago_confirma_y_marca_el_pedido_pagado():
    pedido = SimpleNamespace(id=30, cliente_telefono=TEL, estado="confirmado", total=Decimal("36"), updated_at=None)
    ses = _Sesion(get={("Pedido", 30): pedido}, primero=None)
    r = await ex.aplicar_propuesta(ses, _prop(tipo="pago_confirmado", items=[], total=None, monto=36.0, moneda="$",
                                           metodo="Zelle", pedido_id=30), usuario="maired@enova")
    (pago,) = ses.added
    assert isinstance(pago, Pago) and r["pago_id"] == pago.id and r["pedido_id"] == 30
    assert pago.estado == "confirmado" and pago.confirmado_por == "maired@enova" and pago.origen == "dueña"
    assert pago.monto_usd == Decimal("36") and pago.monto_bs is None and pago.metodo == "zelle"
    assert pedido.estado == "pagado"


async def test_aplicar_pago_se_niega_sobre_cancelado_o_ya_pagado_o_de_otro_cliente():
    cancelado = SimpleNamespace(id=30, cliente_telefono=TEL, estado="cancelado", total=Decimal("36"))
    with pytest.raises(ValueError, match="cancelado"):
        await ex.aplicar_propuesta(_Sesion(get={("Pedido", 30): cancelado}), _prop(tipo="pago_confirmado", items=[], pedido_id=30), usuario="u")
    ok = SimpleNamespace(id=30, cliente_telefono=TEL, estado="confirmado", total=Decimal("36"))
    with pytest.raises(ValueError, match="ya tiene un pago confirmado"):
        await ex.aplicar_propuesta(_Sesion(get={("Pedido", 30): ok}, primero=5), _prop(tipo="pago_confirmado", items=[], pedido_id=30), usuario="u")
    ajeno = SimpleNamespace(id=30, cliente_telefono="584110000000", estado="confirmado", total=Decimal("36"))
    with pytest.raises(ValueError, match="no es de este cliente"):
        await ex.aplicar_propuesta(_Sesion(get={("Pedido", 30): ajeno}), _prop(tipo="pago_confirmado", items=[], pedido_id=30), usuario="u")


async def test_aplicar_entrega_escribe_fecha_franja_y_lugar():
    pedido = SimpleNamespace(id=30, cliente_telefono=TEL, estado="pagado", entrega_fecha=None, entrega_franja=None,
                             entrega_referencia=None, updated_at=None)
    await ex.aplicar_propuesta(_Sesion(get={("Pedido", 30): pedido}),
                               _prop(tipo="entrega_acordada", items=[], total=None, pedido_id=30,
                                     fecha="2026-09-23", franja="en la tarde (2 a 5)", lugar="frente a la plaza"), usuario="u")
    assert pedido.entrega_fecha == date(2026, 9, 23) and pedido.entrega_franja == "en la tarde (2 a 5)"
    assert pedido.entrega_referencia == "frente a la plaza"


async def test_aplicar_precio_especial_cambia_el_total_y_deja_la_huella(monkeypatch):
    from app.services import redis_client as rc

    monkeypatch.setattr(rc, "borrar_cobro", AsyncMock())
    pedido = SimpleNamespace(id=30, cliente_telefono=TEL, estado="confirmado", total=Decimal("36"), notas=None, updated_at=None)
    await ex.aplicar_propuesta(_Sesion(get={("Pedido", 30): pedido}),
                               _prop(tipo="precio_especial", items=[], total=None, pedido_id=30, monto=30.0, moneda="$"), usuario="u")
    assert pedido.total == Decimal("30") and "Precio especial" in pedido.notas
    rc.borrar_cobro.assert_awaited_once_with(TEL)
    with pytest.raises(ValueError, match="bolívares"):
        await ex.aplicar_propuesta(_Sesion(get={("Pedido", 30): pedido}),
                                   _prop(tipo="precio_especial", items=[], total=None, pedido_id=30, monto=1500.0, moneda="Bs"), usuario="u")


async def test_aplicar_cancelacion_no_toca_un_pedido_pagado():
    pagado = SimpleNamespace(id=30, cliente_telefono=TEL, estado="pagado", updated_at=None)
    with pytest.raises(ValueError, match="pagado"):
        await ex.aplicar_propuesta(_Sesion(get={("Pedido", 30): pagado}), _prop(tipo="cancelado", items=[], total=None, pedido_id=30), usuario="u")
    abierto = SimpleNamespace(id=30, cliente_telefono=TEL, estado="confirmado", updated_at=None)
    await ex.aplicar_propuesta(_Sesion(get={("Pedido", 30): abierto}), _prop(tipo="cancelado", items=[], total=None, pedido_id=30), usuario="u")
    assert abierto.estado == "cancelado"


async def test_aplicar_respuesta_general_crea_conocimiento_confirmado(monkeypatch):
    import app.services.embeddings as emb

    monkeypatch.setattr(emb, "obtener_embedding", AsyncMock(return_value=None))
    ses = _Sesion()
    r = await ex.aplicar_propuesta(ses, _prop(tipo="respuesta_general", items=[], total=None, tema="politica",
                                              contenido="Todo lo que hago es sin gluten y sin azúcar"), usuario="u")
    (c,) = ses.added
    assert isinstance(c, Conocimiento) and r["conocimiento_id"] == c.id
    assert c.confirmado is True and c.tema_confirmado == "politica" and c.activo is True


async def test_aplicar_rechaza_una_propuesta_con_claves_de_mas():
    with pytest.raises(ValueError):
        await ex.aplicar_propuesta(_Sesion(), _prop(sorpresa=1), usuario="u")


def test_el_resumen_habla_como_una_pregunta_con_procedencia():
    p = PropuestaExpediente.model_validate(_prop(tipo="pago_confirmado", items=[], total=None, monto=14.0, moneda="$", metodo="Zelle"))
    texto = ex.resumen_humano(p, tipo_mensaje="audio", hora="21-Sep 10:32")
    assert texto.startswith("Parece que Whuilianny confirmó el pago de $14 por Zelle")
    assert "¿correcto?" in texto and "nota de voz del 21-Sep 10:32" in texto
