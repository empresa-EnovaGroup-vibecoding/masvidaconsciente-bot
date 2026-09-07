"""CARGAR `sabores` EN LOS TAMAÑOS QUE LOS TENÍAN SOLO EN LA DESCRIPCIÓN (datos, no código).

El 6-sep-2026 se midió cuatro veces el mismo bug ("de cuál relleno te llevo?" sin nombrar ninguno)
y una de sus causas era de DATOS: Galletas New York, Mini New York y CHOCOLATE tenían los
rellenos escritos en la descripción libre y el campo estructurado `sabores` del tamaño VACÍO. El
código ahora rescata la descripción como respaldo (`_opciones_en_descripcion`), pero la casilla
correcta es `sabores`, y este script la llena por la MISMA puerta del panel (PATCH /api/variantes).

Corre DENTRO del contenedor del bot:

    ssh root@<vps> 'BOT=$(docker ps --format "{{.Names}}" | grep "^<prefijo>" | head -1);
        docker exec -i $BOT python scripts/promover_sabores.py'

Empareja por NOMBRE de producto + presentación (no por id: los ids pueden diferir entre entornos)
y solo escribe donde `sabores` está vacío — es idempotente y no pisa lo que la dueña ya cargó.
"""
import json
import os
import sys
import urllib.request

BASE = "http://localhost:8000"

# (nombre, presentación) TAL CUAL están en la BD VIVA de pruebas (variantes 9/10/34), NO en
# migrations/002: la semilla dice 'Galletas New York / 4 unidades' y no tiene CHOCOLATE (nació en el
# panel). Si en producción difiere ALGUNA, el script las resuelve todas primero, lo reporta en
# `no_encontrados` y sale con 1 SIN escribir ninguna — comparar con GET /api/productos y reintentar.
SABORES = {
    ("Galletas New York", "6 unidades"): "chocolate, limón pistacho, canela naranja o chocomerey",
    ("Mini New York", "10 unidades"): "chocolate, limón pistacho, canela naranja o chocomerey",
    ("CHOCOLATE", "1 UNIDAD"): "relleno de maca merey o de mantequilla de pistacho",
}


def _req(method: str, path: str, data=None, token: str | None = None):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(BASE + path, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    email = os.environ.get("ADMIN_EMAIL", "admin@masvidaconsciente.com")
    token = _req("POST", "/api/login", {"email": email, "password": os.environ["ADMIN_PASSWORD"]})[
        "access_token"
    ]
    productos = _req("GET", "/api/productos", token=token)
    # DOS FASES: primero se resuelven LAS TRES variantes; si falta alguna, se avisa y se sale
    # SIN escribir nada (así el "no escribe nada" de la liturgia es cierto, no aproximado).
    resueltas: dict[tuple[str, str], dict] = {}
    faltan: list[str] = []
    for nombre, presentacion in SABORES:
        variante = next(
            (
                v
                for p in productos
                if (p.get("nombre") or "").strip().lower() == nombre.lower()
                for v in (p.get("variantes") or [])
                if (v.get("presentacion") or "").strip().lower() == presentacion.lower()
            ),
            None,
        )
        if variante is None:
            faltan.append(f"{nombre} / {presentacion}")
        else:
            resueltas[(nombre, presentacion)] = variante
    if faltan:
        print(f"NO se escribió nada. no_encontrados={faltan} — compara con GET /api/productos "
              "de este entorno (nombre y presentación tal cual) y ajusta SABORES antes de reintentar.")
        return 1

    hechos, saltados = 0, 0
    for (nombre, presentacion), sabores in SABORES.items():
        variante = resueltas[(nombre, presentacion)]
        if (variante.get("sabores") or "").strip():
            print(f"= {nombre} / {presentacion}: ya tiene sabores ({variante['sabores']!r}), no se toca")
            saltados += 1
            continue
        _req(
            "PATCH",
            f"/api/variantes/{variante['id']}",
            {
                "presentacion": variante["presentacion"],
                "precio": variante.get("precio"),
                "sabores": sabores,
                "disponible": variante.get("disponible", True),
                "orden": variante.get("orden", 0),
            },
            token=token,
        )
        print(f"+ {nombre} / {presentacion}: sabores <- {sabores!r}")
        hechos += 1
    print(f"hechos={hechos} saltados={saltados} no_encontrados=[]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
