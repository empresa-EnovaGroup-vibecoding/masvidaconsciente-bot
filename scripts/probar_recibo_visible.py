"""Banco determinista: pedido y cobro calculados siempre deben quedar visibles.

No llama OpenRouter, Meta, Redis ni la BD. Simula el loop completo del agente.
"""
from __future__ import annotations

import asyncio

from app.agent import agent as ag

TEL = "__banco_recibo_visible__"
HISTORIAL = [
    {"role": "user", "content": "hola"},
    {"role": "assistant", "content": "¡Hola! ¿Qué deseas?"},
]
RECIBO = (
    "Arepas Andinas x1 (paquete de 6 unidades) = $12\n"
    "Retiro en La Mendera — sin costo\n"
    "Total: $12\nEntrega: lunes 20 de julio"
)
COBRO = (
    "Por Pago Móvil o transferencia son 8.729,41 Bs (precio completo). "
    "Si pagas en dólares —Zelle, Binance o efectivo— son $9.60, "
    "con el 20% de descuento"
)


def check(nombre: str, condicion: bool, detalle: str = "") -> None:
    estado = "OK" if condicion else "MAL"
    print(f"[{estado}] {nombre}")
    if not condicion:
        raise AssertionError(detalle or nombre)


def tool_call(identificador: str, nombre: str) -> dict[str, object]:
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": identificador,
                    "type": "function",
                    "function": {"name": nombre, "arguments": "{}"},
                }],
            }
        }]
    }


def respuesta(texto: str) -> dict[str, object]:
    return {"choices": [{"message": {"role": "assistant", "content": texto}}]}


async def ejecutar(nombre: str, args: dict[str, object], telefono: str) -> dict[str, object]:
    if nombre == "registrar_pedido":
        return {"ok": True, "pedido_id": 91, "total_usd": 12.0, "resumen": RECIBO}
    if nombre == "generar_datos_pago":
        return {"ok": True, "pedido_id": 91, "monto_bs": 8729.41, "resumen_cobro": COBRO}
    raise AssertionError(f"herramienta inesperada: {nombre}")


async def probar_recibo_omitido() -> None:
    turnos = iter([tool_call("t1", "registrar_pedido"), respuesta("Listo, ¿cómo pagas?")])

    async def llm(messages: list[object], tools: list[object], model: str) -> dict[str, object]:
        return next(turnos)

    texto = await ag.responder(TEL, "lo retiro el lunes", HISTORIAL, llm=llm, ejecutar=ejecutar)
    check("recibo omitido: el código lo inserta literal", RECIBO in texto, texto)
    check("la respuesta humana se conserva", "¿cómo pagas?" in texto, texto)


async def probar_recibo_reescrito() -> None:
    alterado = "**Arepas x1 = $12**\n**Total: $12**"
    turnos = iter([tool_call("t1", "registrar_pedido"), respuesta(alterado)])

    async def llm(messages: list[object], tools: list[object], model: str) -> dict[str, object]:
        return next(turnos)

    texto = await ag.responder(TEL, "confirma el pedido", HISTORIAL, llm=llm, ejecutar=ejecutar)
    check("recibo reescrito: aparece también la verdad exacta", texto.startswith(RECIBO), texto)


async def probar_pedido_y_cobro_omitidos() -> None:
    turnos = iter([
        tool_call("t1", "registrar_pedido"),
        tool_call("t2", "generar_datos_pago"),
        respuesta("Te paso los datos y me mandas la captura."),
    ])

    async def llm(messages: list[object], tools: list[object], model: str) -> dict[str, object]:
        return next(turnos)

    texto = await ag.responder(TEL, "voy a pagar", HISTORIAL, llm=llm, ejecutar=ejecutar)
    check("pedido + cobro: inserta ambos en orden", texto.startswith(f"{RECIBO}\n\n{COBRO}"), texto)


async def probar_ya_visible_no_se_repite() -> None:
    historial = [*HISTORIAL, {"role": "assistant", "content": RECIBO}]
    turnos = iter([tool_call("t1", "registrar_pedido"), respuesta("¿Cómo prefieres pagar?")])

    async def llm(messages: list[object], tools: list[object], model: str) -> dict[str, object]:
        return next(turnos)

    texto = await ag.responder(TEL, "pago ahora", historial, llm=llm, ejecutar=ejecutar)
    check("un recibo ya visible no se repite", RECIBO not in texto, texto)


async def main() -> None:
    original_prompt = ag.construir_partes_prompt
    original_modelo = ag.leer_modelo_ia

    async def prompt(nombre: str | None, telefono: str, **kwargs: object) -> tuple[str, str]:
        # **kwargs: el agente nuevo pasa `activas=` (herramientas apagables, fase 4);
        # el doble las acepta y las ignora para sobrevivir a firmas futuras.
        return "reglas de prueba", "estado de prueba"

    async def modelo() -> str:
        return "modelo/de-prueba"

    ag.construir_partes_prompt = prompt
    ag.leer_modelo_ia = modelo
    try:
        await probar_recibo_omitido()
        await probar_recibo_reescrito()
        await probar_pedido_y_cobro_omitidos()
        await probar_ya_visible_no_se_repite()
    finally:
        ag.construir_partes_prompt = original_prompt
        ag.leer_modelo_ia = original_modelo
    print("\nTODO OK — el cliente siempre ve pedido y cobro exactos")


if __name__ == "__main__":
    asyncio.run(main())
