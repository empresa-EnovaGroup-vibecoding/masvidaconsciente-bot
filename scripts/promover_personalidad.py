"""PROMOVER LA PERSONALIDAD: copia el texto de la voz a un entorno por la MISMA puerta del panel.

Corre DENTRO del contenedor del bot (ahí no hay curl, pero sí Python y las credenciales del admin
en el entorno). Lee el texto por stdin y lo guarda con `PUT /api/personalidad`, verificando letra
por letra que lo guardado coincide. Nunca toca la BD a mano (regla dura de CLAUDE.md §3).

Uso, desde la máquina de Maired (el texto viaja por la tubería de ssh):

    ssh root@<vps> 'BOT=$(docker ps --format "{{.Names}}" | grep "^<prefijo-app>" | head -1);
        docker exec -i $BOT python scripts/promover_personalidad.py' < personalidad.txt

Para SACAR la personalidad viva de un entorno (y llevarla a otro):

    ssh root@<vps> '...; docker exec -i $BOT python scripts/promover_personalidad.py --leer' > personalidad.txt

Nació el 6-sep-2026: la personalidad nueva (auditoría de las tres capas) la pegó Maired en PRUEBAS
desde el panel; este script nació para promoverla a PRODUCCIÓN por esa misma puerta (y para sacar
la vieja con --leer antes de pisarla). La fuente canónica de la voz sigue siendo la BD.
"""
import hashlib
import json
import os
import sys
import urllib.request

BASE = "http://localhost:8000"


def _req(method: str, path: str, data=None, token: str | None = None):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(BASE + path, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _token() -> str:
    email = os.environ.get("ADMIN_EMAIL", "admin@masvidaconsciente.com")
    return _req("POST", "/api/login", {"email": email, "password": os.environ["ADMIN_PASSWORD"]})[
        "access_token"
    ]


def _md5(texto: str) -> str:
    return hashlib.md5(texto.encode("utf-8")).hexdigest()[:12]


def main() -> int:
    token = _token()
    if "--leer" in sys.argv:
        viva = _req("GET", "/api/personalidad", token=token).get("personalidad") or ""
        sys.stdout.write(viva)
        print(f"\n# md5={_md5(viva)} chars={len(viva)}", file=sys.stderr)
        return 0

    texto = sys.stdin.read().strip()
    if len(texto) < 500:
        print(f"Texto sospechosamente corto ({len(texto)} chars): no se guarda.", file=sys.stderr)
        return 2
    antes = _req("GET", "/api/personalidad", token=token).get("personalidad") or ""
    print(f"antes:   {len(antes)} chars md5={_md5(antes)}")
    _req("PUT", "/api/personalidad", {"personalidad": texto}, token=token)
    despues = _req("GET", "/api/personalidad", token=token).get("personalidad") or ""
    ok = despues.strip() == texto
    print(f"despues: {len(despues)} chars md5={_md5(despues)} coincide={ok}")
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
