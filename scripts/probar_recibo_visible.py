"""Banco determinista: el recibo es TAREA DEL MODELO — el código ni lo inserta ni lo recorta.

🪦 HASTA EL 2026-08-24 este banco fijaba lo contrario: que `_asegurar_resumenes_exactos`
insertara el recibo/cobro EXACTOS cuando el modelo los omitía. Esa red se QUITÓ por decisión
de Erwin (ver SESIONES.md 24-ago): el 23-ago la cadena inserción→historial→recorte MUTILÓ el
cobro de Maired ("7.799,52 Bs" salió como "799,52 Bs", entregado — L28, las redes peleándose).
Presentar el recibo es ahora conducta del LLM (reglas 107-108 de _REGLAS); si lo omite, esa es
la MEDIDA del modelo, no un hueco del código.

Lo que este banco fija DESDE HOY:
  1. El texto del modelo pasa INTACTO — nada se inserta delante (una reintroducción silenciosa
     de la red vieja pone esto en rojo).
  2. Un cobro con decimales y separador de miles pasa ENTERO — nada lo recorta ni lo parte
     (el bug exacto del mensaje 6784 de Maired no puede volver sin que esto lo cace).
  3. El DINERO FALSO sigue frenado: `_dinero_inventado` es OTRA red, del dinero, y se queda.

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
# El texto que de verdad arma `generar_datos_pago` hoy (retiro: $12, sin envío, EFECTIVO).
# Lleva a propósito separador de miles Y decimales: son los que el recorte viejo partía.
COBRO = (
    "Por Pago Móvil o transferencia son 8.729,41 Bs (precio completo). "
    "Si pagas en efectivo en dólares son $9.60, "
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


async def probar_nada_se_inserta() -> None:
    """El modelo omite el recibo → su texto sale TAL CUAL. Si esto se pone rojo con un RECIBO
    dentro, alguien reintrodujo la inserción sin pasar por la decisión del 24-ago."""
    turnos = iter([tool_call("t1", "registrar_pedido"), respuesta("Listo, ¿cómo pagas?")])

    async def llm(messages: list[object], tools: list[object], model: str) -> dict[str, object]:
        return next(turnos)

    texto = await ag.responder(TEL, "lo retiro el lunes", HISTORIAL, llm=llm, ejecutar=ejecutar)
    check("el código NO inserta el recibo delante", RECIBO not in texto, texto)
    check("la respuesta del modelo se conserva", "¿cómo pagas?" in texto, texto)


async def probar_el_cobro_pasa_entero() -> None:
    """El caso REAL de Maired (mensaje 6784): el cobro ya está en el historial y el modelo lo
    repite en el turno del pago. NADA lo recorta: los decimales y el separador de miles llegan
    enteros. El recorte viejo lo dejaba en "729,41 Bs … son $9." — diez veces menos."""
    historial = [*HISTORIAL, {"role": "assistant", "content": COBRO}]
    turnos = iter([
        tool_call("t1", "generar_datos_pago"),
        respuesta(f"{COBRO}\n\n¿Cuál método prefieres?"),
    ])

    async def llm(messages: list[object], tools: list[object], model: str) -> dict[str, object]:
        return next(turnos)

    texto = await ag.responder(TEL, "voy a pagar", historial, llm=llm, ejecutar=ejecutar)
    check("los bolívares llegan ENTEROS (8.729,41)", "8.729,41" in texto, texto)
    check("los dólares llegan ENTEROS ($9.60)", "$9.60" in texto, texto)
    check("no quedó un monto decapitado", "729,41 Bs" not in texto.replace("8.729,41", ""), texto)


async def probar_dinero_falso_sigue_frenado() -> None:
    """Quitar las redes de ESTILO no tocó la del DINERO: un monto inventado se corrige igual.
    El modelo dice $99 (nadie se lo dio), recibe el regaño [SISTEMA], y la segunda pasada sale
    con el monto bueno."""
    turnos = iter([
        tool_call("t1", "registrar_pedido"),
        respuesta("Perfecto, son $99 en total. ¿Cómo pagas?"),
        respuesta("Perfecto, el total es $12. ¿Cómo pagas?"),
    ])

    async def llm(messages: list[object], tools: list[object], model: str) -> dict[str, object]:
        return next(turnos)

    texto = await ag.responder(TEL, "confirma el pedido", HISTORIAL, llm=llm, ejecutar=ejecutar)
    check("el $99 inventado NO llegó al cliente", "$99" not in texto, texto)
    check("el monto bueno sí llegó", "$12" in texto, texto)


async def main() -> None:
    original_prompt = ag.construir_partes_prompt
    original_modelo = ag.leer_modelo_ia

    async def prompt(nombre: str | None, telefono: str, **kwargs: object) -> tuple[str, str]:
        # **kwargs: el agente pasa `activas=` (herramientas apagables, fase 4);
        # el doble las acepta y las ignora para sobrevivir a firmas futuras.
        return "reglas de prueba", "estado de prueba"

    async def modelo() -> str:
        return "modelo/de-prueba"

    ag.construir_partes_prompt = prompt
    ag.leer_modelo_ia = modelo
    try:
        await probar_nada_se_inserta()
        await probar_el_cobro_pasa_entero()
        await probar_dinero_falso_sigue_frenado()
    finally:
        ag.construir_partes_prompt = original_prompt
        ag.leer_modelo_ia = original_modelo
    print("\nTODO OK — el recibo es del modelo; el código ni lo inserta ni lo recorta")


if __name__ == "__main__":
    asyncio.run(main())
