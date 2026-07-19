"""Compara dos modelos como closers sin enviar un solo WhatsApp.

La capa dura valida herramientas, pedido y cobro contra la BD. Un juez separado
puntua el lenguaje comercial, pero nunca vuelve verde un fallo duro. El ensayo
crea registros temporales y los borra, por eso exige confirmar el TALLER.

Uso dentro del contenedor del bot:
  PYTHONPATH=/app python scripts/ensayo_closer.py --confirmar-taller
  PYTHONPATH=/app python scripts/ensayo_closer.py --confirmar-taller --repeticiones 3
  PYTHONPATH=/app python scripts/ensayo_closer.py --confirmar-taller --sin-juez
  PYTHONPATH=/app python scripts/ensayo_closer.py --confirmar-taller --modelos \
    anthropic/claude-haiku-4.5 deepseek/deepseek-v4-flash
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path

from app.agent import agent as ag
from app.config import get_settings
from ensayo_closer_dominio import (
    Escenario,
    LlamadaTool,
    Resultado,
    cargar_contexto,
    crear_doble,
    crear_escenarios,
    estado_pedidos,
    limpiar,
)
from ensayo_closer_evaluacion import (
    evaluar_advertencias,
    evaluar_duro,
    juzgar,
    redactar_para_juez,
)

settings = get_settings()
CRITERIOS_JUEZ = (
    "conduccion_al_cierre",
    "tono_humano",
    "manejo_del_momento",
    "brevedad",
)
Llamador = Callable[
    [list[object], list[object], str],
    Awaitable[dict[str, object]],
]


def _normalizar_uso(data: dict[str, object]) -> tuple[int, int, float]:
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return 0, 0, 0.0
    prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    costo = float(usage.get("cost") or 0)
    return prompt, completion, costo


def _dialogo(escenario: Escenario, respuestas: list[str]) -> str:
    lineas: list[str] = []
    for turno, respuesta in zip(escenario.turnos, respuestas):
        lineas.extend([f"CLIENTE: {turno}", f"AGENTE: {respuesta}"])
    return "\n".join(lineas)


async def _conversar(
    telefono: str,
    escenario: Escenario,
    llm: Llamador,
    llamadas: list[LlamadaTool],
) -> list[str]:
    historial: list[dict[str, str]] = []
    respuestas: list[str] = []
    ejecutor = crear_doble(llamadas)
    for turno in escenario.turnos:
        respuesta = await ag.responder(
            telefono,
            turno,
            historial=list(historial),
            pregunta_cliente=turno,
            llm=llm,
            ejecutar=ejecutor,
        )
        respuestas.append(respuesta)
        historial.extend(
            [
                {"role": "user", "content": turno},
                {"role": "assistant", "content": respuesta},
            ]
        )
    return respuestas


async def _correr_escenario(
    modelo: str,
    escenario: Escenario,
    indice: int,
    juez_modelo: str | None,
    repeticion: int,
) -> Resultado:
    telefono = f"__closer_{int(time.time())}_{indice}__"
    llamadas: list[LlamadaTool] = []
    usos: list[tuple[int, int, float]] = []
    modelos_usados: list[str] = []
    leer_original = ag.leer_modelo_ia

    async def leer_modelo() -> str:
        return modelo

    async def llm(messages: list[object], tools: list[object], model: str) -> dict[str, object]:
        modelos_usados.append(model)
        data = await ag._llamar_openrouter(messages, tools, model)
        usos.append(_normalizar_uso(data))
        return data

    await limpiar(telefono)
    try:
        ag.leer_modelo_ia = leer_modelo
        inicio = time.perf_counter()
        respuestas = await _conversar(telefono, escenario, llm, llamadas)
        latencia = time.perf_counter() - inicio
        pedidos = await estado_pedidos(telefono)
        fallos = evaluar_duro(escenario, respuestas, llamadas, pedidos)
        advertencias = evaluar_advertencias(respuestas, llamadas)
        uso_fallback = any(usado != modelo for usado in modelos_usados)
        if uso_fallback:
            fallos.append("uso el fallback; la comparacion queda contaminada")
        juez = await _ejecutar_juez(escenario, respuestas, llamadas, juez_modelo)
        return Resultado(
            modelo,
            f"{escenario.id}#r{repeticion}",
            respuestas,
            [llamada.nombre for llamada in llamadas],
            fallos,
            advertencias,
            juez,
            sum(uso[0] for uso in usos),
            sum(uso[1] for uso in usos),
            sum(uso[2] for uso in usos),
            uso_fallback,
            latencia,
        )
    finally:
        ag.leer_modelo_ia = leer_original
        await limpiar(telefono)


async def _ejecutar_juez(
    escenario: Escenario,
    respuestas: list[str],
    llamadas: list[LlamadaTool],
    modelo: str | None,
) -> dict[str, object] | None:
    if not modelo:
        return None
    dialogo = redactar_para_juez(_dialogo(escenario, respuestas), llamadas)
    return await juzgar(escenario, dialogo, modelo, settings.openrouter_api_key)


def _imprimir(resultado: Resultado) -> None:
    estado = "ROJO" if resultado.fallos_duros else "OK"
    print(f"[{estado:4}] {resultado.modelo} / {resultado.escenario}")
    print(f"       tools: {', '.join(resultado.tools) or 'ninguna'}")
    print(f"       tokens: {resultado.prompt_tokens} in + {resultado.completion_tokens} out")
    por_turno = resultado.latencia_segundos / max(1, len(resultado.respuestas))
    print(f"       latencia: {por_turno:.2f}s por turno")
    if resultado.costo_reportado:
        print(f"       costo del candidato reportado: ${resultado.costo_reportado:.6f}")
    for fallo in resultado.fallos_duros:
        print(f"       [MAL] {fallo}")
    for advertencia in resultado.advertencias:
        print(f"       [AVISO] {advertencia}")
    if resultado.juez:
        print(f"       juez: {json.dumps(resultado.juez, ensure_ascii=False)}")


def _guardar(resultados: list[Resultado], salida: str | None) -> None:
    if not salida:
        return
    Path(salida).write_text(
        json.dumps([asdict(resultado) for resultado in resultados], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nDetalle JSON: {salida}")


def _puntaje_juez(resultado: Resultado) -> float | None:
    if not resultado.juez or resultado.juez.get("error"):
        return None
    valores: list[float] = []
    for criterio in CRITERIOS_JUEZ:
        valor = resultado.juez.get(criterio)
        if isinstance(valor, (int, float)) and not isinstance(valor, bool):
            valores.append(max(0.0, min(5.0, float(valor))))
    presion = resultado.juez.get("presion_indebida")
    if isinstance(presion, bool):
        valores.append(0.0 if presion else 5.0)
    return sum(valores) / len(valores) if valores else None


def _resumen_modelos(resultados: list[Resultado]) -> None:
    print("\nCOMPARACION POR MODELO")
    modelos = list(dict.fromkeys(resultado.modelo for resultado in resultados))
    candidatos: list[tuple[float, str]] = []
    for modelo in modelos:
        grupo = [resultado for resultado in resultados if resultado.modelo == modelo]
        verdes = [resultado for resultado in grupo if not resultado.fallos_duros]
        puntajes = [p for resultado in grupo if (p := _puntaje_juez(resultado)) is not None]
        promedio = sum(puntajes) / len(puntajes) if puntajes else None
        costo = sum(resultado.costo_reportado for resultado in grupo)
        avisos = sum(len(resultado.advertencias) for resultado in grupo)
        turnos = sum(len(resultado.respuestas) for resultado in grupo)
        latencia = sum(resultado.latencia_segundos for resultado in grupo) / max(1, turnos)
        nota = f"{promedio:.2f}/5" if promedio is not None else "sin nota"
        print(
            f"- {modelo}: duro {len(verdes)}/{len(grupo)} | "
            f"juez {nota} | avisos {avisos} | {latencia:.2f}s/turno | "
            f"costo reportado ${costo:.6f}"
        )
        if len(verdes) == len(grupo) and promedio is not None:
            candidatos.append((promedio, modelo))
    if candidatos:
        _, mejor = max(candidatos)
        print(f"Mejor puntaje entre los modelos sin fallos duros: {mejor} (orientativo).")
    else:
        print("No hay ganador elegible: falta verde duro o una nota comercial valida.")


async def main(args: argparse.Namespace) -> int:
    if not args.confirmar_taller:
        print("ROJO: escribe datos temporales. Usa --confirmar-taller solo en el TALLER.")
        return 2
    if not settings.openrouter_api_key:
        print("ROJO: falta OPENROUTER_API_KEY")
        return 2
    if args.repeticiones < 1:
        print("ROJO: --repeticiones debe ser al menos 1")
        return 2
    escenarios = crear_escenarios(await cargar_contexto())
    if args.solo:
        escenarios = [e for e in escenarios if e.id in set(args.solo)]
    if not escenarios:
        print("ROJO: ningun escenario seleccionado")
        return 2
    juez_modelo = None if args.sin_juez else args.juez_modelo
    resultados: list[Resultado] = []
    casos = (
        (modelo, escenario, repeticion)
        for modelo in args.modelos
        for escenario in escenarios
        for repeticion in range(1, args.repeticiones + 1)
    )
    for indice, (modelo, escenario, repeticion) in enumerate(
        casos,
        start=1,
    ):
        resultado = await _correr_escenario(
            modelo,
            escenario,
            indice,
            juez_modelo,
            repeticion,
        )
        resultados.append(resultado)
        _imprimir(resultado)
    _guardar(resultados, args.salida)
    _resumen_modelos(resultados)
    rojos = [resultado for resultado in resultados if resultado.fallos_duros]
    print(f"\nENSAYO CLOSER: {len(resultados) - len(rojos)}/{len(resultados)} sin fallos duros")
    if rojos:
        print("ROJO: un modelo con fallos duros no se promueve a produccion.")
        return 1
    print("VERDE DURO. La aprobacion final del tono sigue siendo humana.")
    return 0


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A/B seguro del closer de masvida")
    parser.add_argument("--confirmar-taller", action="store_true")
    parser.add_argument(
        "--modelos",
        nargs="+",
        default=["anthropic/claude-haiku-4.5", "deepseek/deepseek-v4-flash"],
    )
    parser.add_argument("--juez-modelo", default=settings.openrouter_model_fallback)
    parser.add_argument("--sin-juez", action="store_true")
    parser.add_argument("--repeticiones", type=int, default=1)
    parser.add_argument("--solo", nargs="+")
    parser.add_argument("--salida", default="/tmp/ensayo_closer.json")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(_args())))
