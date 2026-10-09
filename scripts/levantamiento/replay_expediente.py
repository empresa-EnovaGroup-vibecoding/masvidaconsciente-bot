"""📏 EL REPLAY DEL EXPEDIENTE: ¿el lector entiende lo que Whuilianny le dice a sus clientas?

Mide el extractor (`app/agent/expediente.py`) contra las conversaciones REALES, sin tocar ninguna
base de datos ni escribirle a nadie. Los datos (el corpus anónimo, la hoja de respuestas y los
resultados) viven FUERA del repo (`respaldos-masvida/levantamiento-2026-09/`, "LEV"): este
archivo no trae ni una frase real.

Subcomandos:
    ventanas   corta cada conversación de clienta en ventanas de la dueña con la MISMA función que
               usa el bot en vivo (`ventanas_owner`) y las guarda con su contexto, en lotes, para
               que los etiquetadores escriban la hoja de respuestas (lo que de verdad pasó).
    correr     pasa cada ventana por el lector REAL (`interpretar_duena` + `validar`) con el catálogo
               del snapshot, el mismo modelo barato y un pedido abierto SIMULADO (el que el propio
               replay habría escrito). Gasta saldo de OpenRouter: tope duro `--tope` en dólares.
    comparar   cruza lo que hizo el lector con la hoja de respuestas y escribe el informe.

Uso (desde la raíz del bot):
    .venv/Scripts/python.exe scripts/levantamiento/replay_expediente.py ventanas --lev <LEV>
    OPENROUTER_API_KEY=... .venv/Scripts/python.exe scripts/levantamiento/replay_expediente.py correr --lev <LEV> --tope 3
    .venv/Scripts/python.exe scripts/levantamiento/replay_expediente.py comparar --lev <LEV>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))
# `app/config.py` exige estas variables al importar; aquí nadie se conecta a nada (como en
# tests/conftest.py). OPENROUTER_API_KEY se respeta si viene puesta (la usará el replay).
for _k, _v in {
    "JWT_SECRET": "replay-local-sin-servidor-no-es-un-secreto-real",
    "ADMIN_PASSWORD": "replay-local",
    "DATABASE_URL": "postgresql+asyncpg://replay:replay@localhost/replay",
    "REDIS_URL": "redis://localhost:6379/0",
    "OPENROUTER_API_KEY": "sk-or-v1-sin-llave",
    "PUBLIC_BASE_URL": "https://replay.example.test",
}.items():
    os.environ.setdefault(_k, _v)

import httpx  # noqa: E402

from app.agent import expediente as ex  # noqa: E402
from app.agent.contratos_atencion import Contexto  # noqa: E402
from app.agent.resolver_atencion import normalizar  # noqa: E402
from app.agent.tools import _parsear_franjas  # noqa: E402
from app.config import get_settings  # noqa: E402

settings = get_settings()
ventanas_owner = ex.ventanas_owner
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Los tipos que son HECHOS de la venta (los que se miden). `respuesta_general` y `nada` no cambian
# el expediente de la venta: se cuentan aparte.
TIPOS_VENTA = ("pedido_tomado", "pago_confirmado", "entrega_acordada", "entregado", "cancelado", "precio_especial")

CONTEXTO_ANTES = 8
CONTEXTO_DESPUES = 4
POR_LOTE = 250


def _leer_jsonl(ruta: Path):
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if linea.strip():
            yield json.loads(linea)


def _mensajes(conv: dict) -> list[dict]:
    """El corpus anónimo al formato que espera `ventanas_owner` (el de `mensajes` en la BD)."""
    return [
        {"id": m["id"], "rol": m["rol"], "tipo": m["tipo"], "contenido": m["texto"],
         "created_at": datetime.fromisoformat(m["t"])}
        for m in conv["mensajes"]
    ]


def _linea(m: dict) -> str:
    return f"#{m['id']} {m['t']} {m['rol']}: {m['texto']}".replace("\n", " / ")


def conversaciones_de_clientas(lev: Path) -> list[dict]:
    """Solo clientas (fuera familia, proveedores, repartidores y pruebas), según la clasificación
    del levantamiento. Las que no compraron se quedan: ahí el lector NO debe anotar nada."""
    clasif = json.loads((lev / "rescate-codex-2026-09-15" / "clasificacion-PRELIMINAR.json").read_text(encoding="utf-8"))
    tipos = {c["cid"]: c for c in clasif["detalle"]}
    salida = []
    for conv in _leer_jsonl(lev / "anon" / "corpus.jsonl"):
        c = tipos.get(conv["cid"])
        if c and c["tipo"] == "clienta":
            salida.append({**conv, "hubo_venta": bool(c["hubo_venta"])})
    return salida


def exportar_ventanas(lev: Path) -> dict:
    destino = lev / "medicion"
    destino.mkdir(exist_ok=True)
    filas = []
    for conv in conversaciones_de_clientas(lev):
        crudos = conv["mensajes"]
        posicion = {m["id"]: i for i, m in enumerate(crudos)}
        for v in ventanas_owner(_mensajes(conv)):
            ids = [m["id"] for m in v.owner]
            ini, fin = posicion[ids[0]], posicion[ids[-1]]
            filas.append({
                "cid": conv["cid"], "vid": f"{conv['cid']}-{ids[-1]}", "hubo_venta": conv["hubo_venta"],
                "ids": ids, "t": crudos[fin]["t"], "ultimo_cliente": v.ultimo_cliente,
                "duena": [_linea(crudos[posicion[i]]) for i in ids],
                "antes": [_linea(m) for m in crudos[max(0, ini - CONTEXTO_ANTES):ini]],
                "despues": [_linea(m) for m in crudos[fin + 1:fin + 1 + CONTEXTO_DESPUES]],
            })
    # Lotes por conversación entera (un etiquetador ve la historia completa de cada clienta).
    lotes: list[list[str]] = [[]]
    tam = 0
    for cid in dict.fromkeys(f["cid"] for f in filas):
        n = sum(1 for f in filas if f["cid"] == cid)
        if tam and tam + n > POR_LOTE:
            lotes.append([])
            tam = 0
        lotes[-1].append(cid)
        tam += n
    lote_de = {cid: i for i, cids in enumerate(lotes) for cid in cids}
    with (destino / "ventanas.jsonl").open("w", encoding="utf-8") as f:
        for fila in filas:
            f.write(json.dumps({**fila, "lote": lote_de[fila["cid"]]}, ensure_ascii=False) + "\n")
    # El texto de cada lote para el etiquetador: cada conversación entera, cada mensaje UNA vez, y una
    # marca «⟦V vid⟧» al cerrar cada ventana de la dueña (es la unidad que se etiqueta).
    por_cid = {c["cid"]: c for c in conversaciones_de_clientas(lev)}
    cierre = {f["ids"][-1]: f["vid"] for f in filas}
    for i, cids in enumerate(lotes):
        partes = []
        for cid in cids:
            partes.append(f"\n===== {cid} =====")
            for m in por_cid[cid]["mensajes"]:
                partes.append(_linea(m))
                if m["id"] in cierre:
                    partes.append(f"⟦V {cierre[m['id']]}⟧")
        (destino / f"lote_{i}.txt").write_text("\n".join(partes) + "\n", encoding="utf-8")
    resumen = {"conversaciones": len(lote_de), "ventanas": len(filas), "lotes": [
        {"lote": i, "cids": len(c), "ventanas": sum(1 for f in filas if lote_de[f["cid"]] == i)}
        for i, c in enumerate(lotes)
    ]}
    (destino / "ventanas_resumen.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=1), encoding="utf-8")
    return resumen


# ══════════════════════════════════════════════════════════════════════════════════
#  correr: el lector real sobre las ventanas reales
# ══════════════════════════════════════════════════════════════════════════════════

def contexto_del_snapshot(lev: Path) -> tuple[Contexto, list[str]]:
    """El catálogo, las zonas y los métodos como los arma `cargar_contexto`, desde el snapshot
    del 15-sep (los precios de julio-agosto pudieron ser otros: eso empuja hacia PROPUESTA, nunca
    hacia una escritura falsa). Sin pedidos: esos los simula el replay."""
    snap = json.loads((lev / "anon" / "sistema_snapshot_publico.json").read_text(encoding="utf-8"))
    ctx = Contexto()
    for p in snap["productos"]:
        ctx.productos[p["id"]] = {"id": p["id"], "nombre": p["nombre"], "variantes": {}, "precio_base": p.get("precio")}
    for v in snap["variantes"]:
        prod = ctx.productos.get(v["producto_id"])
        if prod is not None:
            precio = v.get("precio") if v.get("precio") is not None else prod["precio_base"]
            prod["variantes"][v["id"]] = {"id": v["id"], "presentacion": v.get("presentacion"), "precio": precio}
    for z in snap["zonas"]:
        if z.get("disponible", True):
            ctx.zonas[z["id"]] = {"id": z["id"], "nombre": z["nombre"]}
    for m in snap["metodos_pago"]:
        ctx.metodos[m.get("titulo") or m.get("tipo")] = {"id": m.get("id"), "tipo": m.get("tipo")}
    return ctx, _parsear_franjas(snap["configuracion"].get("franjas_entrega"))


class Gasto:
    """Lo que OpenRouter dice que costó cada llamada (`usage.cost`). Al pasar el tope, no sale
    ninguna llamada más: el replay se detiene y lo dice."""

    def __init__(self, tope: float):
        self.tope, self.usd, self.llamadas = tope, 0.0, 0

    def agotado(self) -> bool:
        return self.usd >= self.tope


def _llm_con_gasto(gasto: Gasto):
    async def llm(mensajes, herramientas, modelo):
        if gasto.agotado():
            raise RuntimeError("tope de gasto alcanzado")
        async with httpx.AsyncClient(timeout=60) as cliente:
            r = await cliente.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
                # Igual que `agent._llamar_openrouter` (temperatura y proveedor) + el costo de vuelta.
                json={"model": modelo, "messages": mensajes, "tools": herramientas, "temperature": 0.15,
                      "provider": {"require_parameters": True}, "usage": {"include": True}},
            )
            r.raise_for_status()
            datos = r.json()
        gasto.llamadas += 1
        gasto.usd += float((datos.get("usage") or {}).get("cost") or 0)
        return datos
    return llm


def _veredicto_json(ev, v) -> dict:
    p = v.propuesta
    return {
        "tipo": ev.tipo, "accion": v.accion, "motivo": v.motivo, "confianza": v.confianza,
        "evidencia": ev.evidencia,
        "items": [{"nombre": i.nombre, "presentacion": i.presentacion, "cantidad": i.cantidad} for i in p.items] if p else [],
        "total": p.total if p else None, "monto": p.monto if p else None,
        "fecha": p.fecha if p else None, "franja": p.franja if p else "",
    }


async def _correr_conversacion(conv, ctx_base, franjas, llm, modelo, respaldo, salida, gasto) -> int:
    """Una conversación en orden (el pedido simulado depende de lo anterior). Devuelve ventanas hechas."""
    ctx = Contexto(productos=ctx_base.productos, zonas=ctx_base.zonas, metodos=ctx_base.metodos)
    hechas = 0
    for v in ventanas_owner(_mensajes(conv)):
        if gasto.agotado():
            break
        ultimo = v.owner[-1]["created_at"]
        hoy = (ultimo - timedelta(hours=4)).date()
        fila = {"vid": f"{conv['cid']}-{v.ultimo_id}", "cid": conv["cid"], "veredictos": []}
        try:
            extraccion = await ex.interpretar_duena(v, ctx, llm, modelo, modelo_respaldo=respaldo)
        except Exception as e:  # noqa: BLE001 — en vivo, `procesar_ventana` también la descarta
            fila["error"] = str(e)[:200]
            extraccion = None
        for ev in (extraccion.eventos if extraccion else []):
            ver = ex.validar(ev, v.texto, ctx, telefono=conv["cid"], hoy=hoy, franjas=franjas,
                             pedido=ctx.pedido, pedidos=ctx.pedidos, evidencia_id=v.ultimo_id,
                             texto_cliente=v.texto_cliente)
            fila["veredictos"].append(_veredicto_json(ev, ver))
            # Lo que en vivo quedaría ESCRITO cambia el pedido abierto que ven las ventanas siguientes.
            if ver.accion == "escribe" and ver.propuesta is not None:
                p = ver.propuesta
                if ev.tipo == "pedido_tomado":
                    ctx.pedido = {"id": v.ultimo_id, "estado": "confirmado", "fecha": None,
                                  "total": p.total, "pago_pendiente": None, "items": [i.model_dump() for i in p.items]}
                    ctx.pedidos = [ctx.pedido]
                elif ev.tipo == "entrega_acordada" and ctx.pedido:
                    ctx.pedido["fecha"] = p.fecha or ctx.pedido.get("fecha")
        salida.write(json.dumps(fila, ensure_ascii=False) + "\n")
        salida.flush()
        hechas += 1
    return hechas


async def correr(lev: Path, tope: float, modelo: str, solo: list[str] | None, paralelo: int) -> dict:
    destino = lev / "medicion" / f"resultados_{modelo.replace('/', '_')}.jsonl"
    hechas = set()
    if destino.exists():  # se reanuda: una conversación a medias se repite entera
        filas = [json.loads(linea) for linea in destino.read_text(encoding="utf-8").splitlines() if linea.strip()]
        convs_lote = {c["cid"]: len(ventanas_owner(_mensajes(c))) for c in conversaciones_de_clientas(lev)}
        por_cid: dict[str, int] = {}
        for f in filas:
            por_cid[f["cid"]] = por_cid.get(f["cid"], 0) + 1
        hechas = {cid for cid, n in por_cid.items() if n == convs_lote.get(cid)}
        destino.write_text("".join(json.dumps(f, ensure_ascii=False) + "\n" for f in filas if f["cid"] in hechas), encoding="utf-8")
    ctx_base, franjas = contexto_del_snapshot(lev)
    gasto = Gasto(tope)
    llm = _llm_con_gasto(gasto)
    convs = [c for c in conversaciones_de_clientas(lev) if c["cid"] not in hechas and (not solo or c["cid"] in solo)]
    sem = asyncio.Semaphore(paralelo)
    total = 0
    with destino.open("a", encoding="utf-8") as salida:
        async def una(c):
            nonlocal total
            async with sem:
                hechas_c = await _correr_conversacion(
                    c, ctx_base, franjas, llm, modelo, settings.openrouter_model_extractor_respaldo, salida, gasto)
                total += hechas_c  # sumar DESPUÉS del await: `total += await …` pierde cuentas entre tareas
        await asyncio.gather(*(una(c) for c in convs))
    return {"archivo": str(destino), "conversaciones": len(convs), "ventanas": total,
            "llamadas": gasto.llamadas, "usd": round(gasto.usd, 4), "tope_alcanzado": gasto.agotado()}


# ══════════════════════════════════════════════════════════════════════════════════
#  comparar: lo que hizo el lector contra lo que de verdad pasó
# ══════════════════════════════════════════════════════════════════════════════════

def _nombre_catalogo(ctx: Contexto | None, nombre: str) -> str:
    """El nombre del anotador, llevado al del catálogo con el MISMO resolvedor del lector (así un
    espacio doble o un plural no cuentan como error)."""
    if ctx is not None:
        prod, _ = ex.producto_por_nombre(ctx, nombre)
        if prod is not None:
            nombre = prod["nombre"]
    return " ".join(normalizar(nombre).split())


def _productos_iguales(items_lector: list[dict], items_verdad: list[dict], ctx: Contexto | None = None) -> tuple[bool, str]:
    """Mismo producto y misma cantidad, línea por línea. Una cantidad que la verdad deja en `null`
    (ella no la dijo) y el lector pone es una cantidad SUPUESTA: error."""
    lector = sorted((_nombre_catalogo(None, i["nombre"]), i["cantidad"]) for i in items_lector)
    verdad = sorted((_nombre_catalogo(ctx, i.get("producto") or ""), i.get("cantidad")) for i in items_verdad)
    if [n for n, _ in lector] != [n for n, _ in verdad]:
        return False, f"productos distintos: lector {[n for n, _ in lector]} · verdad {[n for n, _ in verdad]}"
    for (n, cl), (_, cv) in zip(lector, verdad, strict=True):
        if cv is None:
            return False, f"cantidad de '{n}' supuesta en {cl}: ella no la dijo"
        if cl != cv:
            return False, f"cantidad de '{n}': lector {cl} · verdad {cv}"
    return True, ""


def comparar(lev: Path, modelo: str) -> dict:
    med = lev / "medicion"
    verdad = {}
    for f in sorted(med.glob("respuestas_lote_*.jsonl")):
        for fila in _leer_jsonl(f):
            verdad[fila["vid"]] = fila
    lector = {f["vid"]: f for f in _leer_jsonl(med / f"resultados_{modelo.replace('/', '_')}.jsonl")}
    ventanas = {f["vid"]: f for f in _leer_jsonl(med / "ventanas.jsonl")}
    ctx, _ = contexto_del_snapshot(lev)
    vids = sorted(set(verdad) & set(lector))
    escritas, bien, malas = 0, 0, []
    falsas, escapes = [], []
    detectado = {t: [0, 0] for t in TIPOS_VENTA}  # [verdad tiene, lector lo vio]
    for vid in vids:
        gv = [e for e in verdad[vid].get("eventos", []) if e.get("tipo") in TIPOS_VENTA]
        lv = [x for x in lector[vid]["veredictos"] if x["tipo"] in TIPOS_VENTA and x["accion"] != "descarta"]
        tipos_v = {e["tipo"] for e in gv}
        frase = ""
        if vid in ventanas:
            w = ventanas[vid]
            ella = " / ".join(x.split(": ", 1)[-1] for x in w["duena"])
            frase = f"Clienta: {w['ultimo_cliente'][:140] or '—'} → Whuilianny: {ella}"
        for x in lv:
            if x["accion"] == "escribe":
                escritas += 1
                ok, porque = False, f"la verdad no tiene {x['tipo']} aquí"
                for e in gv:
                    if e["tipo"] != x["tipo"]:
                        continue
                    if x["tipo"] == "pedido_tomado":
                        ok, porque = _productos_iguales(x["items"], e.get("items") or [], ctx)
                    else:
                        ok, porque = True, ""
                    if ok:
                        break
                if ok:
                    bien += 1
                else:
                    malas.append({"vid": vid, "tipo": x["tipo"], "por_que": porque, "frase": frase,
                                  "lector": x, "seguro_verdad": verdad[vid].get("seguro", True)})
            elif x["tipo"] not in tipos_v:
                falsas.append({"vid": vid, "tipo": x["tipo"], "frase": frase, "motivo": x["motivo"]})
        vistos = {x["tipo"] for x in lv}
        for t in tipos_v:
            detectado[t][0] += 1
            if t in vistos:
                detectado[t][1] += 1
            else:
                escapes.append({"vid": vid, "tipo": t, "frase": frase, "nota": verdad[vid].get("nota", "")})
    resumen = {
        "ventanas_comparadas": len(vids), "sin_verdad": len(set(lector) - set(verdad)),
        "escritas_solas": escritas, "escritas_bien": bien,
        "precision_escritas": round(bien / escritas, 3) if escritas else None,
        "detectado_por_tipo": {t: {"pasaron": a, "las_vio": b} for t, (a, b) in detectado.items()},
        "propuestas_sin_motivo": len(falsas), "errores_lector": sum(1 for f in lector.values() if f.get("error")),
    }
    (med / "comparacion.json").write_text(json.dumps(
        {"resumen": resumen, "escritas_mal": malas, "propuestas_sin_motivo": falsas, "escapes": escapes},
        ensure_ascii=False, indent=1), encoding="utf-8")
    (med / f"INFORME-{modelo.replace('/', '_')}.md").write_text(
        _informe(resumen, malas, falsas, escapes), encoding="utf-8")
    return resumen


_NOMBRE = {"pedido_tomado": "tomó un pedido", "pago_confirmado": "confirmó un pago",
           "entrega_acordada": "acordó una entrega", "entregado": "dijo que ya entregó",
           "cancelado": "canceló", "precio_especial": "dio un precio especial o cortesía"}


def _informe(r: dict, malas: list, falsas: list, escapes: list) -> str:
    """El informe para Maired: números en palabras y cada error con la frase real al lado."""
    pagos_falsos = [f for f in falsas if f["tipo"] == "pago_confirmado"]
    lineas = [
        "# ¿El bot entiende lo que Whuilianny les dice a sus clientas?", "",
        f"Se leyeron {r['ventanas_comparadas']} bloques de mensajes de Whuilianny en conversaciones reales.", "",
        "## 1. Lo que el bot anota SOLO, sin preguntarle a nadie",
        f"- Anotó solo **{r['escritas_solas']}** cosas; **{r['escritas_bien']}** estaban bien."
        + (f" Acierto: **{round(100 * r['precision_escritas'])} de cada 100** (la meta es 98)." if r["precision_escritas"] is not None else ""),
        "", "## 2. Lo que pasó de verdad y cuánto vio el bot (solo o como pregunta)",
    ]
    for t, d in r["detectado_por_tipo"].items():
        if d["pasaron"]:
            lineas.append(f"- Whuilianny {_NOMBRE[t]} {d['pasaron']} veces → el bot lo vio {d['las_vio']} ({round(100 * d['las_vio'] / d['pasaron'])}%).")
    lineas += ["", "## 3. Preguntas que el bot habría hecho sin motivo",
               f"- {len(falsas)} en total; de ellas **{len(pagos_falsos)} preguntas de pago** a Whuilianny por WhatsApp que no venían al caso.",
               "", "## Errores de lo anotado solo (con su frase)"]
    for m in malas[:40]:
        lineas.append(f"- `{m['vid']}` · {_NOMBRE.get(m['tipo'], m['tipo'])}: {m['por_que']}  \n  «{m['frase'][:220]}»")
    lineas += ["", "## Ejemplos de lo que se le escapó"]
    for e in escapes[:25]:
        lineas.append(f"- `{e['vid']}` · {_NOMBRE.get(e['tipo'], e['tipo'])}: «{e['frase'][:200]}»")
    lineas += ["", "## Ejemplos de preguntas de pago sin motivo"]
    for f in pagos_falsos[:15]:
        lineas.append(f"- `{f['vid']}`: «{f['frase'][:200]}»")
    return "\n".join(lineas) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("ventanas")
    v.add_argument("--lev", type=Path, required=True)
    c = sub.add_parser("correr")
    c.add_argument("--lev", type=Path, required=True)
    c.add_argument("--tope", type=float, required=True, help="dólares; al pasarlo no sale ni una llamada más")
    c.add_argument("--modelo", default=settings.openrouter_model_extractor)
    c.add_argument("--solo", nargs="*", help="cids concretos (para una prueba corta)")
    c.add_argument("--paralelo", type=int, default=6)
    c.add_argument("--llave-archivo", type=Path,
                   help="archivo FUERA del repo con la llave de OpenRouter (una línea); así no pasa por la consola")
    k = sub.add_parser("comparar")
    k.add_argument("--lev", type=Path, required=True)
    k.add_argument("--modelo", default=settings.openrouter_model_extractor)
    a = p.parse_args()
    if a.cmd == "ventanas":
        print(json.dumps(exportar_ventanas(a.lev), ensure_ascii=False, indent=1))
    elif a.cmd == "correr":
        if a.llave_archivo:
            settings.openrouter_api_key = a.llave_archivo.read_text(encoding="utf-8").strip()
        if settings.openrouter_api_key.endswith("sin-llave"):
            p.error("falta OPENROUTER_API_KEY en el entorno")
        print(json.dumps(asyncio.run(correr(a.lev, a.tope, a.modelo, a.solo, a.paralelo)), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(comparar(a.lev, a.modelo), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
