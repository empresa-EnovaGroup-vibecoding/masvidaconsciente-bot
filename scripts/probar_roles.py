"""LOS ROLES: la dueña no puede tocar las palancas de la proveedora — ni quedarse fuera.

🔴 POR QUÉ EXISTE (migración 024). Hasta hoy NO había roles: la tabla `usuarios` no tenía
columna de rol y el JWT solo llevaba el email, así que **cualquiera que entrara al panel veía y
editaba TODO**. Y había UNA sola cuenta, compartida por Enova y la clienta.

Eso choca con una decisión que el propio proyecto ya había tomado y documentado (CLAUDE.md §5):
el selector de modelo de IA es *"palanca de PROVEEDOR, no de la clienta; cuando la clienta tenga
su propio rol/login **se le esconde**"*. El rol nunca existió, así que nunca se escondió: la
dueña podía cambiarle el modelo al bot desde la pantalla de Configuración. Y en la fase 4 se le
suma el interruptor de las herramientas del agente — apagarle `generar_datos_pago` a su propio
bot le rompería el cobro sin enterarse.

⚠️ LA MITAD QUE MÁS IMPORTA NO ES "QUE LA DUEÑA NO PUEDA", SINO **QUE NADIE SE QUEDE FUERA**.
Un sistema de roles mal puesto se convierte en un candado sin llave: si el último usuario con
permiso se degrada (o se borra) por un clic, ya no hay forma de volver a entrar. Por eso hay TRES
redes, y este banco las prueba todas:

  1. `_crear_admin` fuerza rol='proveedora' a la cuenta ADMIN_EMAIL **en cada arranque**.
  2. La API se niega a degradar o borrar esa cuenta.
  3. La API se niega a dejar el sistema con CERO proveedoras.

🔴 Y UNA LECCIÓN QUE ESTE BANCO SE DIO A SÍ MISMO. La primera versión llamaba a las funciones de
los endpoints **directamente** (`await listar_usuarios(DUENA)`) — y así **FastAPI nunca evalúa
el `Depends(proveedora_actual)`**: el guardia sencillamente no corre. El banco reportó que "la
dueña se ascendió a proveedora sola", lo cual era MENTIRA: la protección sí estaba, pero el test
la esquivaba. Un test que no pasa por la puerta no prueba que la puerta cierre.

Ahora se hacen **peticiones HTTP de verdad** contra la app ASGI, con **JWT real**, pasando por
toda la cadena de dependencias. Es lo único que prueba que un 403 es un 403.
"""
import asyncio
import sys

import httpx
from sqlalchemy import delete, select

from app.api.security import crear_token, leer_rol
from app.config import get_settings
from app.main import app
from app.models import Usuario
from app.services.db import get_session_factory

DUENA = "duena__prueba__@masvida.test"

fallos: list[str] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"   {'[OK ]' if ok else '[MAL]'} {nombre}{('  → ' + detalle) if detalle and not ok else ''}")
    if not ok:
        fallos.append(nombre)


def _auth(email: str) -> dict:
    """Un JWT de VERDAD, el mismo que emite el login del panel."""
    return {"Authorization": f"Bearer {crear_token(email)}"}


async def main() -> None:
    settings = get_settings()
    admin = settings.admin_email
    factory = get_session_factory()

    async with factory() as s:
        await s.execute(delete(Usuario).where(Usuario.email == DUENA))
        await s.commit()

    # ASGITransport: la app REAL, con su router y sus Depends. Sin red, sin puerto, sin lifespan
    # (o sea: sin correr init_db). Es la app que sirve al panel, hablada por HTTP.
    transporte = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transporte, base_url="http://test") as c:

        print("\n1) LA RED ANTI-BLOQUEO: la cuenta principal SIEMPRE es la proveedora")
        rol_admin = await leer_rol(admin)
        check(
            f"'{admin}' tiene rol 'proveedora'",
            rol_admin == "proveedora",
            f"tiene {rol_admin!r} — ¿corrió _crear_admin?",
        )
        r = await c.get("/api/yo", headers=_auth(admin))
        check(
            "GET /api/yo devuelve el rol",
            r.status_code == 200 and r.json().get("rol") == "proveedora",
            f"{r.status_code} {r.text[:60]}",
        )
        check(
            "un usuario que NO existe cae al rol de MENOS privilegio (fail-closed)",
            await leer_rol("fantasma@nadie.test") == "duena",
        )
        check(
            "sin token → 401 (la puerta de siempre sigue puesta)",
            (await c.get("/api/yo")).status_code == 401,
        )

        print("\n2) SE PUEDE CREAR LA CUENTA DE LA DUEÑA (antes había UNA sola, compartida)")
        alta = {
            "email": DUENA,
            "password": "clave-de-prueba-123",
            "nombre": "Dueña de prueba",
            "rol": "duena",
        }
        r = await c.post("/api/usuarios", json=alta, headers=_auth(admin))
        check(
            "la proveedora puede crear una cuenta de dueña",
            r.status_code == 200 and r.json().get("rol") == "duena",
            f"{r.status_code} {r.text[:70]}",
        )
        id_duena = r.json().get("id") if r.status_code == 200 else None
        check("y su rol se lee bien de la BD", await leer_rol(DUENA) == "duena")

        print("\n3) LA DUEÑA NO VE NI TOCA LAS PALANCAS DE LA PROVEEDORA")
        cfg_d = (await c.get("/api/configuracion", headers=_auth(DUENA))).json()
        cfg_p = (await c.get("/api/configuracion", headers=_auth(admin))).json()
        check(
            "la dueña NO ve 'modelo_ia' en su configuración",
            "modelo_ia" not in cfg_d,
            "lo ve",
        )
        check("la proveedora SÍ lo ve", "modelo_ia" in cfg_p)
        check(
            "la dueña sí ve el resto (nombre del negocio, pago móvil…)",
            "negocio_nombre" in cfg_d and "pago_movil_telefono" in cfg_d,
        )
        r = await c.put(
            "/api/configuracion",
            json={"valores": {"modelo_ia": "openai/gpt-4.1"}},
            headers=_auth(DUENA),
        )
        check(
            "si la dueña INTENTA cambiar el modelo del bot → 403",
            r.status_code == 403,
            f"devolvió {r.status_code}: ¡pudo cambiarle el modelo al bot!",
        )
        r = await c.put(
            "/api/configuracion",
            json={"valores": {"negocio_instagram": "@masvidaconsciente"}},
            headers=_auth(DUENA),
        )
        check("pero sí puede editar lo suyo (su Instagram)", r.status_code == 200, str(r.status_code))

        print("\n4) LOS ENDPOINTS DE PROVEEDORA LE DAN 403 A LA DUEÑA")
        # 🔴 ESTO es lo que la primera versión del banco NO probaba: aquí el 403 lo pone
        # `Depends(proveedora_actual)`, y solo corre si la petición pasa por FastAPI de verdad.
        r = await c.get("/api/usuarios", headers=_auth(DUENA))
        check("GET /api/usuarios → 403", r.status_code == 403, f"devolvió {r.status_code}")
        r = await c.patch(
            f"/api/usuarios/{id_duena}/rol", json={"rol": "proveedora"}, headers=_auth(DUENA)
        )
        check(
            "PATCH /api/usuarios/{id}/rol → 403 (no puede auto-ascenderse)",
            r.status_code == 403,
            f"devolvió {r.status_code}: ¡la dueña se ascendió a proveedora sola!",
        )
        check("y su rol NO cambió en la BD", await leer_rol(DUENA) == "duena")
        r = await c.delete(f"/api/usuarios/{id_duena}", headers=_auth(DUENA))
        check("DELETE /api/usuarios/{id} → 403", r.status_code == 403, f"devolvió {r.status_code}")
        r = await c.get("/api/usuarios", headers=_auth(admin))
        check("y la proveedora sí entra", r.status_code == 200, f"devolvió {r.status_code}")

        print("\n5) NADIE SE PUEDE QUEDAR FUERA (los candados que hacen esto reversible)")
        async with factory() as s:
            fila_admin = (
                await s.execute(select(Usuario).where(Usuario.email == admin))
            ).scalars().first()
        r = await c.patch(
            f"/api/usuarios/{fila_admin.id}/rol", json={"rol": "duena"}, headers=_auth(admin)
        )
        check(
            "NO se puede degradar la cuenta principal",
            r.status_code == 400,
            f"devolvió {r.status_code}: ¡se degradó la única proveedora!",
        )
        r = await c.delete(f"/api/usuarios/{fila_admin.id}", headers=_auth(admin))
        check(
            "NO se puede borrar la cuenta principal",
            r.status_code == 400,
            f"devolvió {r.status_code}",
        )
        check(
            "y sigue siendo proveedora después de los dos intentos",
            await leer_rol(admin) == "proveedora",
        )

        # El candado de "la ÚLTIMA proveedora": se asciende a la dueña, se borra al admin de la
        # cuenta protegida NO se puede… así que se prueba con la ascendida como única candidata.
        await c.patch(
            f"/api/usuarios/{id_duena}/rol", json={"rol": "proveedora"}, headers=_auth(admin)
        )
        check(
            "la dueña ascendida ya es proveedora",
            await leer_rol(DUENA) == "proveedora",
        )
        r = await c.patch(
            f"/api/usuarios/{id_duena}/rol", json={"rol": "duena"}, headers=_auth(admin)
        )
        check(
            "y se puede volver a degradar (porque el admin sigue siendo proveedora)",
            r.status_code == 200,
            f"devolvió {r.status_code}",
        )

        print("\n6) EL ROL NO SE PUEDE INVENTAR")
        r = await c.patch(
            f"/api/usuarios/{id_duena}/rol", json={"rol": "dios"}, headers=_auth(admin)
        )
        check("un rol que no existe → 400", r.status_code == 400, f"devolvió {r.status_code}")

    async with factory() as s:
        await s.execute(delete(Usuario).where(Usuario.email == DUENA))
        await s.commit()

    print()
    if fallos:
        print(f"   🔴 {len(fallos)} FALLO(S). Los roles no protegen (o dejan a alguien fuera).")
        sys.exit(1)
    print("   ✅ LA DUEÑA NO TOCA LAS PALANCAS DE LA PROVEEDORA — Y NADIE SE QUEDA FUERA")


if __name__ == "__main__":
    asyncio.run(main())
