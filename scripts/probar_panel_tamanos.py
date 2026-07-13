"""EL PANEL Y EL BOT VEN LO MISMO — el precio se edita en UN SOLO SITIO.

El hueco que esto cierra (lo encontró la revisión adversarial del PRP):
la dueña sube el Pan Keto a $28 en el único campo que ve (el del PRODUCTO) y el bot sigue
cobrando $25 (el del TAMAÑO). **Nada la avisa.** Aquí se comprueba que eso ya no puede pasar.

Se corre DENTRO del contenedor del bot, contra la BD del taller.
"""
import asyncio
import sys

from sqlalchemy import select

from app.agent.tools import _precio_efectivo
from app.models import Producto, ProductoVariante
from app.services.db import get_session_factory

_mal: list[str] = []


def check(bien: bool, titulo: str, detalle: str = "") -> None:
    print(f"  {'[OK ]' if bien else '[MAL]'} {titulo}" + (f"  → {detalle}" if not bien else ""))
    if not bien:
        _mal.append(titulo)


async def main() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    factory = get_session_factory()

    # El token se FIRMA directamente: no hace falta la contraseña, y así la prueba no se salta
    # en silencio la mitad de los casos (un "verde" que en realidad no probó nada es peor que
    # un rojo).
    from app.api.security import crear_token
    token = crear_token("prueba@masvida.local")
    h = {"Authorization": f"Bearer {token}"}

    # httpx nuevo: la app va por transporte, no por `app=`.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:

        print("\n1) EL PANEL VE LOS TAMAÑOS")
        if token:
            r = await c.get("/api/productos", headers=h)
            prods = r.json()
            komb = next((p for p in prods if p["nombre"].lower() == "kombucha"), None)
            check(komb is not None, "el panel ve la Kombucha (una sola, ya no dos)")
            check(komb and len(komb.get("variantes") or []) == 2,
                  "y ve sus DOS tamaños con sus precios",
                  f"{len(komb.get('variantes') or []) if komb else 0} tamaño(s)")
            if komb:
                precios = sorted(v["precio"] for v in komb["variantes"])
                check(precios == [4.0, 7.0], "los precios son $4 y $7", str(precios))

            print("\n2) 🔴 EL PRECIO SOLO SE EDITA EN UN SITIO")
            # Intentar subir el precio en el PRODUCTO (el campo viejo) debe RECHAZARSE.
            cuerpo = {
                "nombre": komb["nombre"], "categoria": komb["categoria"],
                "descripcion": komb["descripcion"], "precio": 99.0,
                "presentacion": komb["presentacion"], "duracion": komb["duracion"],
                "dias_anticipacion": komb["dias_anticipacion"],
                "se_congela": komb["se_congela"], "apto_diabeticos": komb["apto_diabeticos"],
                "info": komb["info"], "disponible": komb["disponible"],
            }
            r = await c.patch(f"/api/productos/{komb['id']}", json=cuerpo, headers=h)
            check(r.status_code == 400,
                  "subir el precio en el PRODUCTO (con varios tamaños) → RECHAZA con un aviso "
                  "claro (antes: el bot seguía cobrando el viejo y nadie se enteraba)",
                  f"HTTP {r.status_code}")
            if r.status_code == 400:
                print(f"        el aviso dice: {r.json().get('detail','')[:80]}…")

            print("\n2b) 🔴 FUGA B4: editar un producto de UN tamaño NO pisa su precio")
            uni = next((p for p in prods if len(p.get("variantes") or []) == 1
                        and p["variantes"][0].get("precio") is not None), None)
            check(uni is not None, "hay un producto de un solo tamaño con precio para probar")
            if uni:
                precio_real = uni["variantes"][0]["precio"]
                base = {
                    "nombre": uni["nombre"], "categoria": uni["categoria"],
                    "descripcion": uni["descripcion"], "presentacion": uni["presentacion"],
                    "duracion": uni["duracion"], "dias_anticipacion": uni["dias_anticipacion"],
                    "se_congela": uni["se_congela"], "apto_diabeticos": uni["apto_diabeticos"],
                    "info": uni["info"], "disponible": uni["disponible"],
                }
                # (a) mandar un precio al editar el PRODUCTO → RECHAZA (antes pisaba en silencio)
                r = await c.patch(f"/api/productos/{uni['id']}", json={**base, "precio": 999.0}, headers=h)
                check(r.status_code == 400,
                      "mandar un precio al editar un producto de UN tamaño → RECHAZA",
                      f"HTTP {r.status_code}")
                # (b) el precio del tamaño sigue INTACTO (no lo pisó el intento de $999)
                uni2 = next((p for p in (await c.get("/api/productos", headers=h)).json()
                             if p["id"] == uni["id"]), None)
                check(uni2 and uni2["variantes"][0]["precio"] == precio_real,
                      f"el precio del tamaño sigue en ${precio_real} (no lo pisó el $999)",
                      f"quedó en {uni2['variantes'][0]['precio'] if uni2 else '?'}")
                # (c) editar SIN precio (como hace el panel ahora) → OK, y el precio sigue intacto
                r = await c.patch(f"/api/productos/{uni['id']}", json={**base, "precio": None}, headers=h)
                uni3 = next((p for p in (await c.get("/api/productos", headers=h)).json()
                             if p["id"] == uni["id"]), None)
                check(r.status_code == 200 and uni3 and uni3["variantes"][0]["precio"] == precio_real,
                      "editar SIN precio (como el panel ahora) → OK y el precio del tamaño intacto",
                      f"HTTP {r.status_code}, precio {uni3['variantes'][0]['precio'] if uni3 else '?'}")

            print("\n3) NO SE PUEDEN VOLVER A CREAR DOS PRODUCTOS CON EL MISMO NOMBRE")
            r = await c.post("/api/productos", json={
                "nombre": "Kombucha", "categoria": "bebidas", "descripcion": "otra",
                "precio": 9.0, "presentacion": "1lt", "duracion": None,
                "dias_anticipacion": 0, "se_congela": None, "apto_diabeticos": None,
                "info": None, "disponible": True,
            }, headers=h)
            check(r.status_code == 400,
                  "crear otro producto llamado 'Kombucha' → RECHAZA (era el origen de la fuga)",
                  f"HTTP {r.status_code}")

            print("\n4) EL BOTÓN 'AGOTADO' FUNCIONA EN LOS DE VARIOS TAMAÑOS")
            r = await c.patch(f"/api/productos/{komb['id']}/disponible",
                              json={"disponible": False}, headers=h)
            check(r.status_code == 200, "marcar agotada la Kombucha → OK", f"HTTP {r.status_code}")
            await c.patch(f"/api/productos/{komb['id']}/disponible",
                          json={"disponible": True}, headers=h)

            print("\n5) EL PRECIO DEL DÍA SE PIDE POR TAMAÑO")
            r = await c.get("/api/precio-dia", headers=h)
            filas = r.json()
            tortas = [f for f in filas if "torta" in f["nombre"].lower()]
            check(len(tortas) >= 3,
                  "las tortas piden el precio de CADA tamaño (250g, 500g, 1kg), no uno solo",
                  f"{len(tortas)} fila(s)")
            check(all(f.get("variante_id") for f in filas),
                  "cada fila trae su tamaño (sin eso, el precio se guardaría en el sitio que no es)")

    print("\n6) LA VERDAD FINAL: lo que el BOT cobraría")
    async with factory() as s:
        prod = (
            await s.execute(select(Producto).where(Producto.nombre.ilike("kombucha")))
        ).scalar_one_or_none()
        vs = (
            await s.execute(
                select(ProductoVariante)
                .where(ProductoVariante.producto_id == prod.id)
                .order_by(ProductoVariante.orden)
            )
        ).scalars().all()
        for v in vs:
            precio = await _precio_efectivo(s, v)
            print(f"        Kombucha {v.presentacion} → el bot cobra ${precio}")
        check(len(vs) == 2 and float(vs[0].precio) == 4.0 and float(vs[1].precio) == 7.0,
              "el bot cobra $4 la de 350ml y $7 la de 700ml")

    print()
    if _mal:
        print(f"  🔴 {len(_mal)} FALLO(S) — NO DESPLEGAR")
        for t in _mal:
            print(f"     - {t}")
        sys.exit(1)
    print("  ✅ EL PANEL Y EL BOT VEN LO MISMO — el precio vive en UN SOLO SITIO")


asyncio.run(main())
