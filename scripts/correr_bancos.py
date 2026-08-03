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
    # LO RETIRADO NO LE LLEGA AL BOT (030): el OTRO buscador, el de Conocimiento —lo que la dueña
    # escribe y el bot REPITE—, que no vigilaba ningún banco. Va pegado a `probar_buscador` porque
    # es su hermano. Caza el bug que rompe EN SILENCIO: si el `activo IS TRUE` se pega al final del
    # WHERE sin paréntesis, la precedencia AND > OR deja dos ramas colando filas retiradas, sin
    # error y sin log — el bot simplemente sigue diciendo lo que ella apagó.
    "probar_conocimiento_activo",
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
    # EL DINERO DESDE EL PANEL (auditoría 2026-08-02): los endpoints que la dueña toca con el
    # ratón escriben en las MISMAS filas que el bot, por otra puerta — y esa puerta no la
    # vigilaba nadie. Por ahí se coló que corregir los items BORRABA el flete del total, no
    # invalidaba la cotización y no revalidaba la fecha. Habla HTTP de verdad (ASGI + JWT).
    "probar_cobro_panel",
    "probar_carril_dinero",
    # QUE EL MENSAJE DEL CLIENTE NO SE EVAPORE (auditoría 2026-08-02): el lock tomado descartaba
    # el turno sin reencolar, el buffer se vaciaba ANTES de pensar y el historial se guardaba
    # DESPUÉS, así que un 402 borraba el mensaje de Redis Y de la tabla `mensajes` — en el panel
    # quedaba un hueco. Y el bot recordaba haber dicho globos que el cliente nunca recibió.
    "probar_no_se_evapora",
    "probar_recibo_visible",
    "probar_honestidad",
    # EL PROMPT CONTRA EL CÓDIGO (auditoría 2026-08-02): reglas que el prompt ORDENA y una red
    # CASTIGA. El cliente pedía "muéstrame lo que tienen", el bot mandaba el catálogo como manda
    # la regla 58, y la red del envío fantasma lo mataba: el cliente se quedaba con el PDF en la
    # mano y un "dame un momentito". Este banco vigila que las dos mitades sigan de acuerdo.
    "probar_prompt_coherente",
    "probar_retomar",
    "probar_bandeja",
    # CONTACTOS PRIVADOS (migración 031): que el bot NO le hable a la familia. El freno vive en el
    # webhook, ANTES de `_marcar_entrante`, así que un mensaje privado no sube `no_leidos`, no se
    # guarda, no marca leído y no gasta un céntimo de IA. Sin este banco, el caso de oro (ni
    # contador huérfano ni hilo vacío, la lección de SIL-7) no lo vigila nadie.
    "probar_contacto_privado",
    # EL RELEVO (auditoría 2026-08-02): que una escalada FALLIDA no se dé por buena. `ejecutar_tool`
    # se tragaba toda excepción sin loguear y el bucle marcaba `pidio_ayuda=True` mirando el NOMBRE
    # de la tool, no el resultado: si `pedir_ayuda` reventaba, no había Intervencion, no salía el
    # WhatsApp, el chat no se pausaba, y el bot igual se despedía con un "te confirmo enseguida".
    "probar_relevo",
    # EL VIGILANTE (auditoría 2026-08-02): que un cliente sin respuesta no se quede en silencio.
    # Se ancla en `clientes.ultimo_entrante_at` —que escribe el webhook, antes de que el worker
    # pueda fallar— así que caza incluso los mensajes que nunca llegaron a la tabla `mensajes`.
    "probar_vigilante",
    # META / TECH PROVIDER (auditoría 2026-08-02): lo que arriesga la cuenta de Meta de TODOS los
    # clientes futuros de Enova. El interruptor de apagado no cubría el único carril que le habla
    # al cliente días después; los 6 avisos a la dueña salían sin comprobar la ventana de 24h; y
    # los `failed` de CALIDAD (131049 = "no entregado para mantener un ecosistema sano") morían en
    # un log que nadie mira — justo la telemetría que un Tech Provider no puede ignorar.
    "probar_meta",
    # LA TELEMETRÍA (032): que el gasto se cuente POR LLAMADA y no por globo (una fila por globo
    # contaría el mismo turno hasta cuatro veces), que el `usage` real se lea entero —incluido el
    # `cost` de 10 decimales, que con NUMERIC(10,2) se redondearía a cero— y, sobre todo, que con
    # la base caída `registrar()` NO LANCE: si anotar falla, el mensaje del cliente sale igual.
    "probar_telemetria",
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
