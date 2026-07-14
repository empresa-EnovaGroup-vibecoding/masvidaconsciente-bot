"""ENSAYO A/B — un agente vs dos, contra el bot VIVO, con un JUEZ que es otro modelo.

🔴 POR QUÉ ESTE ENSAYO Y NO UN "confía en mí". Encender el modo de dos agentes es un cambio de
arquitectura en el bot que le cobra a clientes reales. Un banco de pruebas dice que **no se rompe**;
solo un ensayo dice si **es MEJOR**. Y lo que este rediseño promete es justamente lo que ningún
test unitario mide: que suene más humano y obedezca más.

Dos reglas de la casa, que este ensayo respeta:

  1. *"Nunca compares un A/B entre servidores distintos. Misma máquina, cambia UNA sola variable."*
     (CLAUDE.md §8 — el A/B viciado del 2026-07-11, donde lo que cambiaba era el servidor, no el
     prompt.) Aquí la bandera `agente_modo` vive en la BD: mismo contenedor, misma base, mismos
     modelos, misma hora. Cambia UNA variable.

  2. *"Verifica el cobro en la BD, no en la respuesta."* El bot puede **hablar** perfecto y **cobrar**
     mal — ya pasó. **El juez opina; la BD manda.** Un solo GRAVE (dinero inventado, pedido fantasma,
     frase prohibida) tumba el modo, por bonito que suene.

EL JUEZ ES OTRO MODELO (el de respaldo, GPT-4.1) juzgando al del bot: no comparte sus puntos ciegos.
"""
import asyncio
import json
import sys

import httpx
from sqlalchemy import delete, select

from app.agent.agent import (
    OPENROUTER_URL,
    RESPUESTA_SEGURA,
    _afirma_pedido_registrado,
    _frase_prohibida,
    _suena_a_sistema,
    responder,
)
from app.agent.system_prompt import leer_modelo_ia
from app.config import get_settings
from app.models import Configuracion, Intervencion, Mensaje, Pedido
from app.services.db import get_session_factory

settings = get_settings()

# Guiones REALES: lo que un cliente escribe de verdad, cubriendo los carriles que importan.
GUIONES = [
    ("catalogo", ["hola! que panes tienen?", "el keto de que esta hecho?"]),
    ("precio", ["hola, cuanto cuesta el quesillo?"]),
    ("diabetico", ["buenas, mi mama es diabetica, que me recomiendas?"]),
    ("negacion", ["hola, tienen pizza?"]),
    ("cierre", ["hola! quiero 2 paquetes de empanadas de platano", "de pollo, para el sabado"]),
    ("humano", ["hola, eres un bot? quiero hablar con una persona"]),
]

JUEZ = """Eres un JUEZ de calidad de un bot de ventas por WhatsApp de una señora venezolana que
vende comida saludable. Te doy la conversación. Puntúa SOLO lo que ves, con dureza.

Devuelve SOLO este JSON, sin nada más:
{"naturalidad": 1-5,        // 5 = suena a una persona real por WhatsApp; 1 = suena a robot
 "brevedad": 1-5,           // 5 = corto y al grano; 1 = muro de texto tipo folleto
 "cierra_la_venta": 1-5,    // 5 = avanza la venta, pregunta, invita a decidir; 1 = corta
 "suena_a_folleto": true/false,   // ¿soltó una lista de beneficios/ingredientes de golpe?
 "frase_de_robot": "la frasecita robótica que usó, o null",
 "una_linea": "tu veredicto en una línea"}"""


async def _juzgar(conv: str) -> dict:
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            json={
                "model": settings.openrouter_model_fallback,  # OTRO modelo: no comparte sus ciegos
                "messages": [{"role": "system", "content": JUEZ},
                             {"role": "user", "content": conv}],
                "temperature": 0,
            },
        )
        r.raise_for_status()
        t = (r.json()["choices"][0]["message"].get("content") or "").strip()
    i, j = t.find("{"), t.rfind("}")
    try:
        return json.loads(t[i:j + 1])
    except Exception:  # noqa: BLE001
        return {"naturalidad": 0, "brevedad": 0, "cierra_la_venta": 0, "una_linea": "el juez no respondió"}


async def _modo(m: str) -> None:
    f = get_session_factory()
    async with f() as s:
        await s.execute(delete(Configuracion).where(Configuracion.clave == "agente_modo"))
        s.add(Configuracion(clave="agente_modo", valor=m))
        await s.commit()


async def _limpiar(tags: list[str]) -> None:
    f = get_session_factory()
    async with f() as s:
        for t in tags:
            await s.execute(delete(Mensaje).where(Mensaje.cliente_telefono == t))
            await s.execute(delete(Pedido).where(Pedido.cliente_telefono == t))
            await s.execute(delete(Intervencion).where(Intervencion.cliente_telefono == t))
        await s.commit()


async def _correr(modo: str) -> tuple[list[dict], list[str]]:
    await _modo(modo)
    notas, graves = [], []
    tags = []
    for nombre, guion in GUIONES:
        tel = f"__ab_{modo}_{nombre}__"
        tags.append(tel)
        hist, lineas = [], []
        for q in guion:
            r = await responder(tel, q, historial=list(hist))
            lineas.append(f"CLIENTE: {q}\nBOT: {r}")
            hist += [{"role": "user", "content": q}, {"role": "assistant", "content": r}]

            # ── LOS GRAVES: los comprueba el CÓDIGO y la BD, NO el juez ──
            if r.strip() == RESPUESTA_SEGURA:
                graves.append(f"{nombre}: respuesta ENLATADA (el turno murió)")
            if _frase_prohibida(r):
                graves.append(f"{nombre}: FRASE PROHIBIDA ({_frase_prohibida(r)})")
            if _suena_a_sistema(r):
                graves.append(f"{nombre}: SUENA A SISTEMA")
            if _afirma_pedido_registrado(r):
                f = get_session_factory()
                async with f() as s:
                    hay = (
                        await s.execute(select(Pedido).where(Pedido.cliente_telefono == tel))
                    ).scalars().first()
                if hay is None:
                    graves.append(f"{nombre}: 🔴 PEDIDO FANTASMA (dijo que lo agendó y NO existe)")
        conv = "\n\n".join(lineas)
        n = await _juzgar(conv)
        n["escena"] = nombre
        notas.append(n)
        print(f"     {nombre:<11} nat {n.get('naturalidad', 0)}/5 · brev {n.get('brevedad', 0)}/5 "
              f"· cierra {n.get('cierra_la_venta', 0)}/5   {str(n.get('una_linea', ''))[:44]}")
    await _limpiar(tags)
    return notas, graves


def _media(notas: list[dict], campo: str) -> float:
    vals = [n.get(campo, 0) or 0 for n in notas]
    return sum(vals) / max(len(vals), 1)


async def main() -> None:
    modelo = await leer_modelo_ia()
    print(f"\n   bot = {modelo}   ·   juez = {settings.openrouter_model_fallback} (OTRO modelo)")
    print(f"   {len(GUIONES)} escenas · misma máquina, misma BD, cambia UNA variable\n")

    print("  ═══ A) UN AGENTE (el de siempre) ═══")
    a, ga = await _correr("uno")
    print("\n  ═══ B) DOS AGENTES (Operador + Voz) ═══")
    b, gb = await _correr("dos")

    print("\n  ═══════════════════ EL VEREDICTO ═══════════════════")
    print(f"  {'':<18} {'UNO':>6}  {'DOS':>6}")
    print("  " + "─" * 34)
    mejor = 0
    for campo, etiqueta in (("naturalidad", "naturalidad"), ("brevedad", "brevedad"),
                            ("cierra_la_venta", "cierra la venta")):
        ma, mb = _media(a, campo), _media(b, campo)
        flecha = "▲" if mb > ma else ("▼" if mb < ma else "=")
        mejor += (mb > ma) - (mb < ma)
        print(f"  {etiqueta:<18} {ma:>6.1f}  {mb:>6.1f}  {flecha}")
    fa = sum(1 for n in a if n.get("suena_a_folleto"))
    fb = sum(1 for n in b if n.get("suena_a_folleto"))
    print(f"  {'suena a folleto':<18} {fa:>6}  {fb:>6}  {'▲' if fb < fa else ('▼' if fb > fa else '=')}")
    print()
    print(f"  🔴 GRAVES (BD/código)  {len(ga):>6}  {len(gb):>6}")
    for g in ga:
        print(f"     UNO · {g}")
    for g in gb:
        print(f"     DOS · {g}")

    print()
    # ── EL CRITERIO DE PROMOCIÓN. El dinero manda sobre el tono: si sube la naturalidad pero
    #    aparece UN SOLO grave, NO se promueve.
    if gb:
        print("   🔴 EL MODO DE DOS AGENTES TIENE GRAVES: NO SE ENCIENDE.")
        await _modo("uno")
        sys.exit(1)
    if mejor < 0:
        print("   🟡 Sin graves, pero el juez lo puntúa PEOR. No se enciende solo: decídelo tú.")
        await _modo("uno")
        sys.exit(1)
    print("   ✅ CERO GRAVES y el juez lo puntúa igual o mejor: el modo de DOS es promovible.")


if __name__ == "__main__":
    asyncio.run(main())
