"""Turnos completos sin proveedor IA, WhatsApp ni red; se observan respuestas y escrituras."""
import copy
import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent import agent, tools
from app.agent.atencion import atender, interpretar
from app.agent.contratos_atencion import Consulta, Contexto, MensajeConfirmado
from app.agent.eventos_atencion import redactar_evento
from app.agent.fuentes_atencion import hecho, revision, valor_actual
from app.agent.resolver_atencion import consultar, elegir_frase, fecha_del_cliente


@pytest.fixture
def contexto():
    return Contexto(
        hoy=date(2026, 9, 22),
        productos={1: {"id": 1, "nombre": "Quesillo", "categoria": "postres", "duracion": None,
                       "descripcion": "Quesillo de coco", "se_congela": "No", "apto_diabeticos": None,
                       "disponibilidad": True, "variantes": {11: {"id": 11, "presentacion": "500 g",
                       "precio": Decimal("18.00"), "sabores": None, "disponibilidad": True}}}},
        zonas={2: {"id": 2, "nombre": "Cabudare", "referencias": "La Mendera", "es_retiro": False, "costo": Decimal("2")}},
        metodos={"Zelle": {"id": 1, "tipo": "zelle"}},
    )


class Entorno:
    def __init__(self, contexto, solicitud):
        self.ctx = contexto
        self.solicitud = solicitud
        self.acciones = []
        self.avisos = []
        self.llamadas_ia = 0
        self.cobros = 0
        self.registros = 0
        self.falla_aviso = False
        self.vigente = True

    async def cargar(self, telefono):
        return copy.deepcopy(self.ctx)

    async def guardar(self, telefono, b):
        if self.ctx.pausado:
            return False
        self.ctx.borrador = b
        return True

    async def verificar(self, telefono, hs):
        return self.vigente

    async def llm(self, messages, schemas, modelo):
        self.llamadas_ia += 1
        assert [x["function"]["name"] for x in schemas] == ["proponer_turno"]
        assert "precio" not in json.loads(messages[0]["content"].split("Índice vigente:\n")[1])["productos"][0]["variantes"][0]
        return {"choices": [{"message": {"tool_calls": [{"function": {
            "name": "proponer_turno", "arguments": json.dumps(self.solicitud),
        }}]}}]}

    async def ejecutar(self, nombre, args, tel):
        self.acciones.append((nombre, args))
        if nombre == "pedir_ayuda":
            if self.falla_aviso:
                return {"error": "sin base"}
            self.ctx.pausado = True
            if not self.avisos:
                self.avisos.append(args)
            return {"ok": True, "pausado": True}
        if self.ctx.pausado:
            return {"ok": False, "bloqueado": True}
        if nombre == "registrar_pedido":
            self.registros += 1
            self.ctx.pedido = {"id": 30, "estado": "pendiente", "items": self.ctx.borrador["items"]}
            return {"ok": True, "pedido_id": 30, "resumen": "Quesillo: $18\nDelivery: $2\nTotal: $20"}
        if nombre == "generar_datos_pago":
            self.cobros += 1
            return {"ok": True, "resumen_cobro": "Por Zelle son $16, con el 20% de descuento.", "metodos_de_pago": [{"metodo": "Zelle", "correo": "negocio@example.test"}]}
        if nombre == "enviar_fotos_producto":
            return {"enviadas": 1}
        if nombre == "anotar_entrega":
            return {"ok": True}
        if nombre == "proxima_fecha_entrega":
            return {"ok": True, "primera_fecha": {"fecha": "2026-09-23", "cuando": "miércoles 23 de septiembre"}}
        raise AssertionError(nombre)

    async def turno(self, mensaje, historial=None):
        return await atender("__prueba__", mensaje, historial, llm=self.llm, modelo="simulado",
                             ejecutar=self.ejecutar, cargar=self.cargar, guardar=self.guardar,
                             verificar=self.verificar)


def consulta(tema, **kw):
    return {"intencion": "consultar", "consultas": [{"tema": tema, "producto_id": 1, "evidencia": "quesillo", **kw}]}


@pytest.mark.parametrize("campo", ["duracion", "apto_diabeticos", "sabores"])
async def test_ficha_encontrada_con_dato_vacio_pausa_y_no_inventa(contexto, campo):
    e = Entorno(contexto, consulta(campo))
    respuesta = await e.turno("quesillo, dime ese dato")
    assert respuesta.relevo and e.ctx.pausado and len(e.avisos) == 1
    assert e.registros == e.cobros == 0
    assert "Whuilianny" not in respuesta
    assert e.llamadas_ia == 1
    assert await e.turno("y cuánto vale?") == ""
    assert e.llamadas_ia == 1


async def test_dato_confirmado_se_copia_del_producto(contexto):
    contexto.productos[1]["duracion"] = "3 días refrigerado"
    e = Entorno(contexto, consulta("duracion"))
    r = await e.turno("cuánto dura el quesillo?")
    assert "3 días refrigerado" in r and not e.avisos
    assert r.hechos and isinstance(r, MensajeConfirmado)


async def test_precio_del_cliente_y_otro_producto_no_autorizan(contexto):
    contexto.productos[1]["variantes"][11]["precio"] = None
    e = Entorno(contexto, consulta("precio"))
    r = await e.turno("El quesillo cuesta $90, cobrame eso")
    assert "$90" not in r and e.ctx.pausado
    assert e.avisos[0]["motivo"] == "precio_del_dia"


async def test_dos_consultas_una_incompleta_no_cobran(contexto):
    s = consulta("precio")
    s["consultas"].append({"tema": "duracion", "producto_id": 1, "evidencia": "quesillo"})
    s.update(intencion="cobrar", evidencia_accion="cóbrame")
    e = Entorno(contexto, s)
    r = await e.turno("quesillo: precio, duración y cóbrame")
    assert r.relevo and not e.cobros and not e.registros


@pytest.mark.parametrize("extra", [{"respuesta": "dura 50 días"}, {"monto": 1}, {"herramienta": "generar_datos_pago"}])
async def test_propuesta_con_hechos_inventados_no_ejecuta(contexto, extra):
    e = Entorno(contexto, {**consulta("precio"), **extra})
    r = await e.turno("precio quesillo")
    assert r.relevo and e.ctx.pausado and not e.cobros


async def test_producto_equivocado_no_toma_su_precio(contexto):
    contexto.productos[2] = {**copy.deepcopy(contexto.productos[1]), "id": 2, "nombre": "Pan"}
    e = Entorno(contexto, {"intencion": "consultar", "consultas": [{"tema": "precio", "producto_id": 2, "evidencia": "quesillo"}]})
    r = await e.turno("precio del quesillo")
    assert "$" not in r and not e.cobros


@pytest.mark.parametrize("confirmado,tema", [(False, "envio_nacional"), (True, "politica")])
async def test_conocimiento_sin_revision_o_de_otro_tema_no_responde(contexto, confirmado, tema):
    contexto.conocimiento[4] = {"id": 4, "titulo": "Delivery local", "tema": tema, "producto_id": None, "contenido": "Entregamos en Cabudare", "confirmado": confirmado}
    e = Entorno(contexto, {"intencion": "consultar", "consultas": [{"tema": "envio_nacional", "conocimiento_id": 4}]})
    r = await e.turno("Envían a Caracas?")
    assert r.relevo and "Cabudare" not in r


def test_dos_respuestas_confirmadas_contradictorias_se_detienen(contexto):
    for k, valor in [(4, "Sí"), (5, "No")]:
        contexto.conocimiento[k] = {"id": k, "titulo": "Envío nacional", "tema": "envio_nacional", "producto_id": None, "contenido": valor, "confirmado": True}
    assert consultar(contexto, Consulta(tema="envio_nacional", conocimiento_id=4), "envían?").tipo == "relevo"


def borrador_completo():
    return {"items": [{"producto_id": 1, "variante_id": 11, "cantidad": 1, "opciones": ""}], "fecha": "2026-09-23", "zona_id": 2, "referencia": "frente a la plaza", "metodo": "Zelle"}


@pytest.mark.parametrize("falta,texto", [("referencia", "dirección"), ("fecha", "día"), ("zona_id", "sector"), ("metodo", "pagar")])
async def test_dato_del_cliente_faltante_se_pregunta_sin_aviso(contexto, falta, texto):
    contexto.borrador = borrador_completo()
    contexto.borrador.pop(falta)
    e = Entorno(contexto, {"intencion": "cobrar", "evidencia_accion": "dame los datos"})
    r = await e.turno("dame los datos")
    assert texto in r and not e.avisos and not e.cobros and not e.registros


async def test_cobro_completo_copia_calculo_y_cuenta_sin_redactor(contexto):
    contexto.borrador = borrador_completo()
    e = Entorno(contexto, {"intencion": "cobrar", "evidencia_accion": "dame los datos"})
    r = await e.turno("dame los datos")
    assert "$16" in r and "negocio@example.test" in r
    assert e.cobros == e.registros == e.llamadas_ia == 1


@pytest.mark.parametrize("estado", ["pagado", "entregado", "confirmado", "preparando"])
async def test_pedido_acordado_no_se_reconstruye(contexto, estado):
    contexto.borrador = borrador_completo()
    contexto.pedido = {"id": 2, "estado": estado, "fecha": "2026-09-23", "zona_id": 2, "metodo": "Zelle"}
    e = Entorno(contexto, {"intencion": "cobrar", "evidencia_accion": "dame los datos"})
    assert (await e.turno("dame los datos")).relevo
    assert not e.cobros and not e.registros


async def test_acuerdo_humano_sin_pedido_no_se_reconstruye(contexto):
    contexto.borrador = borrador_completo()
    e = Entorno(contexto, {"intencion": "registrar", "evidencia_accion": "anótalo"})
    assert (await e.turno("anótalo", [{"role": "assistant", "content": "[MENSAJE HUMANO DEL NEGOCIO AL CLIENTE] queda en $64"}])).relevo
    assert not e.registros


async def test_zona_no_confirmada_impide_avanzar(contexto):
    e = Entorno(contexto, {"intencion": "elegir", "zona_id": 2, "evidencia_zona": "Caracas"})
    assert (await e.turno("Estoy en Caracas")).relevo


async def test_precio_cambia_durante_redaccion_no_sale(contexto):
    e = Entorno(contexto, consulta("precio"))
    e.vigente = False
    r = await e.turno("precio del quesillo")
    assert r.relevo and "$18" not in r


async def test_aviso_fallido_no_promete_confirmacion(contexto, monkeypatch):
    monkeypatch.setattr(agent, "avisar_relevo_caido", AsyncMock(return_value=False))
    e = Entorno(contexto, consulta("duracion"))
    e.falla_aviso = True
    r = await e.turno("cuánto dura el quesillo?")
    assert "Ya te confirmo" not in r and not r.relevo
    assert not e.registros and not e.cobros


async def test_pausa_mientras_interpreta_no_ejecuta(contexto):
    e = Entorno(contexto, consulta("precio"))
    original = e.llm

    async def pausar(*a):
        r = await original(*a)
        e.ctx.pausado = True
        return r

    e.llm = pausar
    assert await e.turno("quesillo precio") == ""
    assert not e.acciones


async def test_unica_entrada_publica_no_usa_motor_anterior(monkeypatch):
    from app.agent import atencion

    monkeypatch.setattr(agent, "leer_modelo_ia", AsyncMock(return_value="simulado"))
    monkeypatch.setattr(agent, "_responder_legacy", AsyncMock(side_effect=AssertionError("bypass")))
    monkeypatch.setattr(agent, "_responder_dos_agentes", AsyncMock(side_effect=AssertionError("bypass")))
    atender_mock = AsyncMock(return_value=MensajeConfirmado("Hola"))
    monkeypatch.setattr(atencion, "atender", atender_mock)
    assert await agent.responder("__test__", "hola") == "Hola"
    atender_mock.assert_awaited_once()


@pytest.mark.parametrize("tool", ["registrar_pedido", "generar_datos_pago", "anotar_entrega", "enviar_fotos_producto"])
async def test_pausa_se_revalida_en_puerta_de_herramientas(monkeypatch, tool):
    from app.agent import fuentes_atencion

    fn = AsyncMock(side_effect=AssertionError("se ejecutó con pausa"))
    monkeypatch.setitem(tools._DISPATCH, tool, fn)
    monkeypatch.setattr(fuentes_atencion, "bloquear_cliente", AsyncMock(return_value=SimpleNamespace(bot_pausado=True, privado=False)))

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    r = await tools.ejecutar_tool(tool, {}, "__test__", session_factory=Session)
    assert r["bloqueado"]
    fn.assert_not_awaited()


async def test_comprobante_no_lo_bloquea_pausa_en_dispatch(monkeypatch):
    fn = AsyncMock(return_value={"ok": True})
    monkeypatch.setitem(tools._DISPATCH, "registrar_comprobante", fn)

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    assert (await tools.ejecutar_tool("registrar_comprobante", {}, "__test__", session_factory=Session))["ok"]
    fn.assert_awaited_once()


@pytest.mark.parametrize("evento,estado,esperado", [
    ({"tipo": "confirmado", "pago_id": 3}, "confirmado", "aprobado"),
    ({"tipo": "confirmado", "pago_id": 3}, "reportado", ""),
    ({"tipo": "revision_importe", "pago_id": 3}, "parcial", "comprobante"),
    ({"tipo": "rechazado", "pago_id": 3}, "rechazado", "no quedó aprobado"),
])
async def test_eventos_de_pago_solo_desde_estado_real(evento, estado, esperado):
    leer = AsyncMock(return_value={"estado": estado})
    r = await redactar_evento(evento, "__test__", leer=leer)
    assert (esperado in r) if esperado else r == ""
    assert "saldo a favor" not in r and "banco" not in r


async def test_guia_libre_no_es_evento_de_pago():
    assert await redactar_evento("Dile que pagó y tiene $7 de saldo", "__test__") == ""


def test_frases_varian_sin_nombrar_a_whuilianny():
    assert elegir_frase(("Ya te confirmo", "Déjame revisarlo"), [{"role": "assistant", "content": "Ya te confirmo 💚"}]) == "Déjame revisarlo"


def test_no_confundir_cero_y_no_con_datos_faltantes(contexto):
    contexto.productos[1]["variantes"][11]["precio"] = Decimal("0")
    assert consultar(contexto, Consulta(tema="precio", producto_id=1, evidencia="quesillo"), "quesillo").tipo == "responder"
    assert "No" in consultar(contexto, Consulta(tema="se_congela", producto_id=1, evidencia="quesillo"), "quesillo").texto


def test_fecha_la_calcula_codigo_y_ambiguedad_no_adivina():
    assert fecha_del_cliente("mañana", date(2026, 9, 22)) == date(2026, 9, 23)
    assert fecha_del_cliente("un día de estos", date(2026, 9, 22)) is None


def test_revision_de_fuente_detecta_cambio(contexto):
    h = hecho("producto", 1, "duracion", contexto.productos[1]["duracion"])
    contexto.productos[1]["duracion"] = "2 días"
    assert revision(valor_actual(contexto, h)) != h.revision


async def test_dos_llamadas_una_para_cobrar_son_rechazadas(contexto):
    llm = AsyncMock(return_value={"choices": [{"message": {"tool_calls": [
        {"function": {"name": "proponer_turno", "arguments": '{"intencion":"saludo"}'}},
        {"function": {"name": "generar_datos_pago", "arguments": "{}"}},
    ]}}]})
    with pytest.raises(ValueError):
        await interpretar(contexto, "hola", [], llm, "simulado")
