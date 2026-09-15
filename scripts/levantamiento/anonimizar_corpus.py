"""LEVANTAMIENTO · A1 — del volcado crudo de `mensajes` a un corpus ANÓNIMO por conversación.

Entrada (local, fuera del repo): `LEV/crudo/mensajes.jsonl` (una línea JSON por mensaje, tal como sale
de `extraer_corpus.sh`), `LEV/crudo/sistema_snapshot_*.json` (para los nombres de las clientas) y,
opcionalmente, `LEV/crudo/transcripciones_owner.jsonl` (A2) para sustituir "[nota de voz]" por el texto.

Salida (local, fuera del repo):
  · `LEV/anon/corpus.jsonl` — una línea por conversación: {cid, n, mensajes:[{t, rol, tipo, texto}]}
  · `LEV/anon/indice.csv`  — por conversación: mensajes, de la dueña, audios, fechas, pin, pedido
  · `LEV/crudo/mapa_clientes.json` — C001 → teléfono. NUNCA sale de crudo/.

Anonimización: teléfonos → C001…; cualquier número de 9+ dígitos dentro del texto → [telefono];
referencias de 6-8 dígitos → [ref]; nombres de clientas (de la tabla `clientes`) → [cliente];
coordenadas de Maps truncadas a 2 decimales (~1 km: sirve para zonas, no identifica casas). El estilo
("mi reina", "amor", "sra") se conserva: es parte de cómo vende.

Los scripts de esta carpeta RECHAZAN escribir dentro del repositorio: el repo es público.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path


def _rechazar_repo(ruta: Path) -> None:
    for p in [ruta.resolve(), *ruta.resolve().parents]:
        if (p / ".git").exists():
            sys.exit(f"NO: {ruta} está dentro de un repositorio git. El corpus va fuera del repo.")


def _sin_acentos(t: str) -> str:
    t = unicodedata.normalize("NFKD", t)
    return "".join(c for c in t if not unicodedata.combining(c))


_TEL = re.compile(r"\+?\d[\d\s.-]{8,}\d")
_REF = re.compile(r"(?<!\d)\d{6,8}(?!\d)")
_MAPS = re.compile(r"(maps\.google\.com/\?q=)(-?\d+\.\d+),(-?\d+\.\d+)")


def _anonimizar_texto(texto: str, nombres: list[re.Pattern]) -> str:
    if not texto:
        return texto
    t = _MAPS.sub(lambda m: f"{m.group(1)}{float(m.group(2)):.2f},{float(m.group(3)):.2f}", texto)
    t = _TEL.sub("[telefono]", t)
    t = _REF.sub("[ref]", t)
    for patron in nombres:
        t = patron.sub("[cliente]", t)
    return t


def main(args: argparse.Namespace) -> int:
    crudo = Path(args.crudo)
    salida = Path(args.salida)
    _rechazar_repo(salida)
    salida.mkdir(parents=True, exist_ok=True)

    snap = json.loads(next(crudo.glob("sistema_snapshot_*.json")).read_text(encoding="utf-8"))
    nombres_clientes = [
        c["nombre"] for c in (snap.get("clientes") or []) if c.get("nombre") and len(c["nombre"].strip()) >= 3
    ]
    # Solo nombres "de persona" (letras, 3+ chars); no emojis ni "A". Patrón por palabra completa.
    patrones = []
    for n in sorted(set(nombres_clientes), key=len, reverse=True):
        limpio = re.sub(r"[^\wáéíóúñÁÉÍÓÚÑ ]", " ", n).strip()
        for parte in limpio.split():
            if len(parte) >= 3 and parte.lower() not in {"sra", "señora", "mama", "mami"}:
                patrones.append(re.compile(rf"\b{re.escape(parte)}\b", re.I))

    transcripciones: dict[int, str] = {}
    tr = crudo / "transcripciones_owner.jsonl"
    if tr.exists():
        for linea in tr.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(linea)
            except json.JSONDecodeError:
                continue
            if d.get("estado") == "ok" and d.get("texto"):
                transcripciones[int(d["id"])] = d["texto"]

    por_tel: dict[str, list[dict]] = defaultdict(list)
    for linea in (crudo / "mensajes.jsonl").read_text(encoding="utf-8").splitlines():
        if not linea.strip():
            continue
        m = json.loads(linea)
        por_tel[m["cliente_telefono"]].append(m)

    telefonos = sorted(por_tel, key=lambda t: min(x["created_at"] for x in por_tel[t]))
    mapa = {f"C{i + 1:03d}": tel for i, tel in enumerate(telefonos)}
    inverso = {v: k for k, v in mapa.items()}

    filas_indice = []
    with (salida / "corpus.jsonl").open("w", encoding="utf-8") as out:
        for tel in telefonos:
            cid = inverso[tel]
            msgs = sorted(por_tel[tel], key=lambda x: (x["created_at"], x["id"]))
            conv = []
            for m in msgs:
                texto = m.get("contenido") or ""
                if m.get("tipo") == "audio" and m["rol"] == "owner" and m["id"] in transcripciones:
                    texto = "🎤 " + transcripciones[m["id"]]
                conv.append({
                    "id": m["id"],
                    "t": m["created_at"][:16],
                    "rol": m["rol"],
                    "tipo": m.get("tipo") or "text",
                    "texto": _anonimizar_texto(texto, patrones),
                })
            out.write(json.dumps({"cid": cid, "n": len(conv), "mensajes": conv}, ensure_ascii=False) + "\n")
            filas_indice.append({
                "cid": cid,
                "n": len(conv),
                "owner": sum(1 for c in conv if c["rol"] == "owner"),
                "user": sum(1 for c in conv if c["rol"] == "user"),
                "bot": sum(1 for c in conv if c["rol"] == "assistant"),
                "audios_owner": sum(1 for c in conv if c["rol"] == "owner" and c["tipo"] == "audio"),
                "audios_transcritos": sum(1 for c in conv if c["texto"].startswith("🎤")),
                "desde": conv[0]["t"][:10],
                "hasta": conv[-1]["t"][:10],
                "pin": any("maps.google" in c["texto"] or "ubicaci" in _sin_acentos(c["texto"]).lower() for c in conv),
                "precio": any(re.search(r"\$ ?\d|\d ?\$|\bbcv\b|zelle", c["texto"], re.I) for c in conv if c["rol"] == "owner"),
            })

    with (salida / "indice.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(filas_indice[0].keys()))
        w.writeheader()
        w.writerows(sorted(filas_indice, key=lambda r: -r["n"]))
    (crudo / "mapa_clientes.json").write_text(json.dumps(mapa, ensure_ascii=False, indent=1), encoding="utf-8")

    total = sum(r["n"] for r in filas_indice)
    print(json.dumps({
        "conversaciones": len(filas_indice),
        "mensajes": total,
        "con_15_o_mas": sum(1 for r in filas_indice if r["n"] >= 15),
        "audios_owner": sum(r["audios_owner"] for r in filas_indice),
        "audios_transcritos": sum(r["audios_transcritos"] for r in filas_indice),
        "con_pin": sum(1 for r in filas_indice if r["pin"]),
        "nombres_enmascarados": len(patrones),
    }, ensure_ascii=False))
    return 0


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Corpus anónimo por conversación a partir del volcado crudo")
    p.add_argument("--crudo", required=True, help="carpeta LEV/crudo (mensajes.jsonl + snapshot)")
    p.add_argument("--salida", required=True, help="carpeta LEV/anon (fuera del repo)")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(main(_args()))
