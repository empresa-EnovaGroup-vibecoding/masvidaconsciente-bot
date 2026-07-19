"""EL VIGILANTE — corre TODOS los bancos de prueba y, si algo sale ROJO, avisa solo.

🔴 Por qué existe (deuda D2, y la petición de Maired del 2026-07-14: *"no quiero estar
diciendo a cada rato 'se arregló o se dañó'"*): la regla "si un banco sale rojo, no se
despliega" dependía de que un humano se ACORDARA de correrlos por SSH. Un humano se
olvida; este script no. El workflow de GitHub lo ejecuta DESPUÉS de cada despliegue del
taller: si algo se rompió, (1) el flujo queda ROJO en GitHub y (2) a la dueña/proveedora
le llega un WhatsApp — nadie tiene que estar mirando.

Correr a mano (igual que siempre):
  docker exec -w /app -e PYTHONPATH=/app <bot> python scripts/correr_bancos.py
"""
import asyncio
import os
import subprocess
import sys

# El orden importa: primero el esquema (si la base está a medias, lo demás miente).
BANCOS = [
    "probar_migraciones",
    # DRIFT (fase 0): el hermano GENÉRICO del de arriba. `probar_migraciones` comprueba una lista
    # de columnas escrita a mano (los incidentes de ayer); este compara `models.py` ENTERO contra
    # el esquema real y detecta cualquier migración que no llegó a aplicarse (los de mañana).
    # Va aquí, pegado a él: si la base no es la que el código cree, TODO lo de abajo miente.
    "probar_drift",
    # EL BUSCADOR (fase 1): el bot NO puede negar lo que sí vende. Vigila las dos mitades:
    # que la ASESORÍA encuentre ("bebidas", "postres", "pan sin gluten"…) y que arreglarla NO
    # haya aflojado el COBRO (ambos carriles comparten `_coincide_texto` y la difusa).
    "probar_buscador",
    # LA MULTIMEDIA (fase 2): lo que el bot manda por WhatsApp, la dueña lo ve en el panel.
    # Vigila LAS DOS mitades del arreglo: que el bot GUARDE la fila, y que el endpoint SEPA
    # servirla (las fotos viven en R2: `os.path.exists("https://…")` daba 404).
    "probar_media",
    # LOS ROLES (fase 3): la dueña no toca las palancas de la proveedora — y NADIE se queda
    # fuera. Hace peticiones HTTP REALES contra la app ASGI: llamar a las funciones de los
    # endpoints a pelo NO evalúa los `Depends`, así que el guardia ni siquiera correría.
    "probar_roles",
    # LAS HERRAMIENTAS (fase 4): se apagan desde el panel sin romper el cobro ni las redes.
    # Vigila los 3 riesgos: que el filtro NO toque `_DISPATCH` (o apagar una tool le arranca
    # el brazo a una red), que la red del DINERO no se quede ciega (el bug invisible), y que
    # apagar las fotos no convierta al bot en una máquina de respuestas enlatadas.
    "probar_herramientas",
    # LOS DOS AGENTES (fase 5): la VOZ no puede inventar porque NO TIENE DE DÓNDE (sin
    # catálogo, sin zonas, sin calendario). Vigila que las 9 redes sigan con su nombre y su
    # firma (3 bancos las importan así), que NO se toque ninguna temperatura, y que la lista
    # blanca del dinero deje de tragarse los `id_para_pedir` (el bug del "$23").
    "probar_dos_agentes",
    "probar_cobro",
    "probar_datos_bancarios",
    "probar_delivery",
    "probar_carril_dinero",
    "probar_recibo_visible",
    "probar_honestidad",
    "probar_retomar",
    "probar_bandeja",
    "probar_fase2",
    "probar_panel_tamanos",
]


def correr() -> list[str]:
    rojos: list[str] = []
    env = dict(os.environ, PYTHONPATH="/app")
    for banco in BANCOS:
        try:
            r = subprocess.run(
                [sys.executable, f"scripts/{banco}.py"],
                capture_output=True, text=True, timeout=900, env=env, cwd="/app",
            )
            ok = r.returncode == 0
        except subprocess.TimeoutExpired:
            ok, r = False, None
        print(f"[{'OK ' if ok else 'ROJO'}] {banco}")
        if not ok:
            rojos.append(banco)
            if r is not None:
                # Las últimas líneas del banco rojo: ahí está el [MAL] que importa.
                print((r.stdout or "")[-1500:])
                print((r.stderr or "")[-600:])
            else:
                print("   (se pasó de tiempo)")
    return rojos


async def _avisar(rojos: list[str]) -> None:
    """WhatsApp a la dueña/proveedora. Best-effort: si no sale (ventana de 24h, sin
    número), el flujo de GitHub igual queda ROJO — el aviso nunca es el único testigo."""
    try:
        from sqlalchemy import select

        from app.config import get_settings
        from app.models import Configuracion
        from app.services.db import get_session_factory
        from app.services.meta_client import enviar_texto

        factory = get_session_factory()
        async with factory() as s:
            fila = (
                await s.execute(
                    select(Configuracion).where(Configuracion.clave == "dueno_telefono")
                )
            ).scalar_one_or_none()
        destino = (fila.valor if fila else None) or get_settings().dueno_telefono
        if not destino:
            print("(sin dueno_telefono: el aviso queda solo en GitHub)")
            return
        lista = "\n".join(f"· {b}" for b in rojos)
        await enviar_texto(
            destino,
            "🔴 *LOS BANCOS DE PRUEBA SALIERON ROJOS* tras el último despliegue del taller.\n\n"
            f"Fallaron:\n{lista}\n\n"
            "Algo que funcionaba se rompió. NO promover a producción hasta arreglarlo. "
            "El detalle está en GitHub → Actions.",
        )
        print(f"Aviso enviado a {destino}")
    except Exception as e:  # noqa: BLE001 — el aviso no puede tapar el rojo
        print(f"(no se pudo avisar por WhatsApp: {e})")


def main() -> None:
    rojos = correr()
    print()
    if rojos:
        print(f"🔴 {len(rojos)} BANCO(S) EN ROJO: {', '.join(rojos)}")
        asyncio.run(_avisar(rojos))
        sys.exit(1)
    print(f"✅ LOS {len(BANCOS)} BANCOS EN VERDE")


if __name__ == "__main__":
    main()
