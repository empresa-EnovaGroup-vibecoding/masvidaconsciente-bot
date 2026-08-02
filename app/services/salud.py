"""¿El bot está VIVO de verdad? — la respuesta que `GET /` no sabe dar.

🔴 NACIÓ DEL FALLO MUDO (auditoría 2026-08-02, SIL-4 + F2 + F5). Con Redis caído,
`rc.ya_procesado` revienta DENTRO del webhook, la excepción la atrapa el bucle de eventos
(webhook/router.py:68) y hasta hoy el webhook respondía 200 igual: Meta no reintentaba y el
mensaje del cliente se evaporaba, con el contenedor en VERDE. Aquí se TOCAN las dependencias, una
por una, en vez de suponer que están vivas.

LAS CINCO SONDAS, y por qué cada una está y no otra:
  1. POSTGRES — se cae y el bot deja de saber quién es nadie.
  2. REDIS ESCRIBIENDO (no PING) — es el que se comió la semana de mensajes.
  3. EL TOKEN DE META — caduca sin avisar; el bot sigue "funcionando" y no sale un solo mensaje.
  4. 🔴 EL SALDO DE OPENROUTER — LA SONDA DE MAYOR VALOR, y la que ningún ítem propuso. El
     incidente REAL documentado (2026-07-15) no fue Postgres ni Redis: fue que el saldo de
     OpenRouter bajó a $0.04, la API empezó a devolver 402 y el bot se quedó MUDO DURANTE DÍAS.
     Un `/salud` que mira todo menos eso mira todo menos lo que de verdad se cayó. Y avisa ANTES
     del 402 (por umbral), no después: al llegar el 402 ya se perdieron ventas.
  5. EL BARREDOR — el vigilante también necesita quien lo vigile (F5).

CONTRATO PARA EL MONITOR EXTERNO:
  · 200 + "estado":"ok"        → todo bien.
  · 200 + "estado":"degradado" → el bot atiende, pero algo está roto y REINICIAR NO LO ARREGLA
                                 (token caducado, saldo bajo, el barredor muerto).
  · 503 + "estado":"caido"     → Postgres o Redis no responden. AQUÍ ES DONDE SE COMEN LOS
                                 MENSAJES, y aquí sí un reinicio puede servir.
Se alerta si el código NO es 200 **O** si el cuerpo no trae `"estado": "ok"`. Las DOS reglas, no
una: mirando solo el código, un token de Meta muerto o un saldo en cero pasan desapercibidos.

CÓMO SE VIGILA ESTO HOY (SIL-4.3): un monitor externo (UptimeRobot / BetterStack / Healthchecks,
plan gratis basta) contra `https://api-masvida.enovagroup.tech/salud`, cada 60s, con las dos reglas
de arriba, avisando a Enova (NO a la dueña: esto es lenguaje de proveedor). Vigilar desde FUERA del
VPS es además MEJOR que un healthcheck de Docker para este caso: también caza que el VPS entero o
el proxy se caigan.

⚠️ PENDIENTE DE INFRA — el `healthcheck` del servicio `web` en docker-compose.yml NO se toca hoy:
exige releer el compose y Coolify está desconectado (el despliegue vive de `docker cp` + restart).
Cuando vuelva, va apuntando a `/salud` y NUNCA a `/`, con `retries: 3` y `start_period: 60s` (init_db
aplica migraciones al arrancar), y usando `python -c` en vez de `curl` (la imagen es
`python:3.12-slim` y no trae curl). Y aun así: con `restart: unless-stopped`, un healthcheck
agresivo puede reiniciar la API en bucle por un Redis con hipo — por eso el monitor externo entra
primero y el healthcheck después, con calma.
"""
import logging
import re
import time
from typing import Any

import httpx
from sqlalchemy import text

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
GRAPH_VERSION = "v21.0"

# CUÁNDO EMPEZÓ A CORRER ESTE PROCESO. Se estampa al importar el módulo, y `app/main.py` lo importa
# en el `lifespan` justo para que la marca sea la del ARRANQUE y no la de la primera visita.
# Existe por una sola razón: el testigo del barredor (abajo) tarda una pasada en escribirse, y sin
# esto cada despliegue estrenaría con un `degradado` de un minuto. Un detector que se equivoca en
# cada despliegue se acaba ignorando.
_ARRANQUE = time.monotonic()

# Caché en MEMORIA DEL PROCESO, NO en Redis: esto tiene que seguir funcionando justo cuando Redis
# es lo que está caído. Y evita que un monitor cada 30s martillee Postgres, o que un tercero use
# el endpoint público de ariete.
_CACHE: dict[str, Any] = {"en": 0.0, "cuerpo": None}
_CACHE_SEGUNDOS = 10.0
# Lo que sale por HTTP a terceros se mira mucho más de tarde en tarde: un monitor cada 60s serían
# 1.440 llamadas diarias a Graph y otras tantas a OpenRouter para preguntar lo mismo.
_META: dict[str, Any] = {"en": 0.0, "dato": None}
_SALDO: dict[str, Any] = {"en": 0.0, "dato": None}
_EXTERNO_SEGUNDOS = 300.0

# 🔴 EL UMBRAL DEL SALDO. Con Gemini 2.5 Flash este bot gasta céntimos al día, así que $2 son
# holgadamente varios días de aviso — tiempo de sobra para que alguien entre a openrouter.ai y
# recargue ANTES de que el primer cliente se quede sin respuesta. Bajarlo mucho sería llegar tarde;
# subirlo mucho, cansar. Si algún día el gasto sube, sube este número.
UMBRAL_SALDO_USD = 2.0

# El testigo del barredor se da por rancio pasado esto. El bucle pasa cada 5 minutos: 15 son tres
# pasadas perdidas, o sea "esto no es un tropiezo, esto está muerto".
MINUTOS_BARREDOR_RANCIO = 15

# Un usuario:contraseña dentro de una URL de error (un DSN que se cuele en el texto de una
# excepción). `/salud` es PÚBLICO: lo que salga de aquí lo puede leer cualquiera.
_CREDENCIAL = re.compile(r"://[^/@\s]+:[^/@\s]+@")


def _limpio(e: Exception) -> str:
    """El texto del error, recortado y SIN credenciales. Público significa público."""
    return _CREDENCIAL.sub("://***:***@", str(e))[:200]


def olvidar_cache() -> None:
    """Tira las tres cachés. La usa el banco (`probar_vigilante.py`) para que un assert no lea la
    respuesta del assert anterior; en producción no la llama nadie."""
    _CACHE.update(en=0.0, cuerpo=None)
    _META.update(en=0.0, dato=None)
    _SALDO.update(en=0.0, dato=None)


# ─── Las sondas ──────────────────────────────────────────────────────

async def _postgres() -> dict:
    from app.services.db import get_session_factory

    t0 = time.monotonic()
    try:
        factory = get_session_factory()
        async with factory() as s:
            await s.execute(text("SELECT 1"))
            # De paso, CUÁNTAS migraciones tiene aplicadas: es el número que delataba la deuda D1
            # (base a medias con el contenedor en verde). Informativo, no decide el 503.
            n = (await s.execute(text("SELECT count(*) FROM schema_migrations"))).scalar()
        return {"ok": True, "ms": int((time.monotonic() - t0) * 1000),
                "migraciones_aplicadas": int(n or 0)}
    except Exception as e:  # noqa: BLE001 — el chequeo NUNCA puede tumbar la API
        logger.error("SALUD: Postgres NO responde (%s)", e)
        return {"ok": False, "ms": int((time.monotonic() - t0) * 1000), "error": _limpio(e)}


async def _redis() -> dict:
    from app.services import redis_client as rc

    t0 = time.monotonic()
    try:
        # Se ESCRIBE a propósito (no PING): lo que hace el webhook es un SET NX EX
        # (`ya_procesado`, redis_client.py:29). Un Redis con `maxmemory` lleno o en modo réplica
        # contesta PONG y REVIENTA el SET — el PING daría VERDE justo en el fallo que hay que
        # cazar. La clave lleva el prefijo `cache:` que el propio módulo reserva
        # (redis_client.py:102) y no choca con msg:/buffer:/lock:/hist:.
        await rc.set_cache("cache:salud", "1", 30)
        if await rc.get_cache("cache:salud") != "1":
            raise RuntimeError("acepta el SET pero no devuelve el valor")
        return {"ok": True, "ms": int((time.monotonic() - t0) * 1000)}
    except Exception as e:  # noqa: BLE001
        logger.error("SALUD: Redis NO responde (%s)", e)
        return {"ok": False, "ms": int((time.monotonic() - t0) * 1000), "error": _limpio(e)}


async def _meta() -> dict:
    """¿El token de Meta sigue vivo? FALLA SOLO ANTE 401/403 (caducado o revocado).

    Un timeout o un 500 de Graph NO cuentan: Meta se cae sola, y un detector con falsos positivos
    se acaba ignorando — la peor avería posible en un detector (la lección de DAT-10, los ficheros
    AppleDouble que pusieron el vigilante en rojo con una base perfecta).
    """
    ahora = time.monotonic()
    if _META["dato"] is not None and ahora - _META["en"] < _EXTERNO_SEGUNDOS:
        return _META["dato"]
    if not settings.meta_access_token or not settings.meta_phone_number_id:
        dato = {"ok": False, "error": "META_ACCESS_TOKEN / META_PHONE_NUMBER_ID sin configurar"}
    else:
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get(
                    f"https://graph.facebook.com/{GRAPH_VERSION}/{settings.meta_phone_number_id}",
                    params={"fields": "id,quality_rating"},
                    headers={"Authorization": f"Bearer {settings.meta_access_token}"},
                )
            if r.status_code in (401, 403):
                dato = {"ok": False, "error": f"Meta RECHAZA el token ({r.status_code})"}
            elif r.status_code >= 400:
                dato = {"ok": True, "aviso": f"Graph devolvió {r.status_code} (no es el token)"}
            else:
                # De regalo, la CALIDAD del número: siendo Enova Tech Provider, verla bajar antes
                # de que Meta la restrinja vale oro.
                dato = {"ok": True, "calidad_numero": (r.json() or {}).get("quality_rating")}
        except Exception as e:  # noqa: BLE001 — la red se cae; el token no por eso
            dato = {"ok": True, "aviso": f"no se pudo consultar Graph ({_limpio(e)[:120]})"}
    _META.update(en=ahora, dato=dato)
    return dato


async def _saldo() -> dict:
    """🔴 EL SALDO DE OPENROUTER — la sonda que nace del incidente REAL del 2026-07-15.

    Ese día el saldo bajó a $0.04, OpenRouter empezó a devolver 402 y el bot se quedó MUDO durante
    días con el contenedor en verde, Postgres en verde y Redis en verde. Ninguna de las otras
    sondas lo habría visto. Por eso esto no es un extra: es la razón principal por la que existe
    `/salud`.

    Avisa POR UMBRAL, antes del 402 (`UMBRAL_SALDO_USD`): cuando llega el primer 402 ya hay un
    cliente sin respuesta. `GET /api/v1/credits` con la MISMA llave que usa el agente; el saldo es
    `total_credits - total_usage`.

    Misma doctrina anti-falso-positivo que Meta: si openrouter.ai no contesta, o contesta algo que
    no se entiende, esto NO se pone rojo (`ok: True` + aviso). Solo el 401 (llave inválida), el 402
    (sin saldo) y un saldo por debajo del umbral cuentan como fallo.
    """
    ahora = time.monotonic()
    if _SALDO["dato"] is not None and ahora - _SALDO["en"] < _EXTERNO_SEGUNDOS:
        return _SALDO["dato"]
    if not settings.openrouter_api_key:
        dato = {"ok": False, "error": "OPENROUTER_API_KEY sin configurar: el bot no puede pensar"}
    else:
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get(
                    "https://openrouter.ai/api/v1/credits",
                    headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
                )
            if r.status_code == 401:
                dato = {"ok": False, "error": "OpenRouter RECHAZA la llave (401)"}
            elif r.status_code == 402:
                dato = {"ok": False, "error": "OpenRouter dice SIN SALDO (402): el bot está MUDO"}
            elif r.status_code >= 400:
                dato = {"ok": True, "aviso": f"OpenRouter devolvió {r.status_code} (no es la llave)"}
            else:
                d = (r.json() or {}).get("data") or {}
                comprados, gastados = d.get("total_credits"), d.get("total_usage")
                if comprados is None or gastados is None:
                    # Cambió el formato de la respuesta. NO se inventa un veredicto sobre el
                    # dinero: se dice que no se pudo mirar y se sigue.
                    dato = {"ok": True, "aviso": "OpenRouter respondió sin total_credits/total_usage"}
                else:
                    saldo = round(float(comprados) - float(gastados), 4)
                    if saldo < UMBRAL_SALDO_USD:
                        dato = {
                            "ok": False,
                            "saldo_usd": saldo,
                            "error": (
                                f"SALDO DE OPENROUTER EN ${saldo} (umbral ${UMBRAL_SALDO_USD}). "
                                "Recarga en openrouter.ai → Credits ANTES de que el bot enmudezca."
                            ),
                        }
                    else:
                        dato = {"ok": True, "saldo_usd": saldo}
        except Exception as e:  # noqa: BLE001 — que openrouter.ai no conteste no es quedarse sin saldo
            dato = {"ok": True, "aviso": f"no se pudo consultar el saldo ({_limpio(e)[:120]})"}
    _SALDO.update(en=ahora, dato=dato)
    return dato


async def _barredor() -> dict:
    """🔴 EL TESTIGO DEL TESTIGO (F5): ¿el vigilante sigue vivo?

    El barredor corre dentro del proceso de uvicorn (`app/main.py`) y escribe
    `barredor_ultima_corrida` en cada turno. Si el bucle muere por una excepción fuera de su try,
    la red desaparece EN SILENCIO — el mismo modo de fallo que este bloque vino a matar, una capa
    más arriba. Aquí eso se convierte en `degradado`.

    Los primeros minutos tras arrancar NO cuentan: el bucle todavía no ha hecho su primera pasada,
    y marcar `degradado` en cada despliegue enseñaría a ignorar la alarma.
    """
    if (time.monotonic() - _ARRANQUE) < MINUTOS_BARREDOR_RANCIO * 60:
        return {"ok": True, "aviso": "proceso recién arrancado: el barredor todavía no cumple turno"}
    from datetime import UTC, datetime

    from app.services.barredor import ultima_corrida
    from app.services.db import get_session_factory

    try:
        factory = get_session_factory()
        async with factory() as s:
            cuando = await ultima_corrida(s)
        if cuando is None:
            return {"ok": False, "error": "el barredor no ha corrido NUNCA (¿falta la migración 028?)"}
        minutos = int((datetime.now(UTC) - cuando).total_seconds() // 60)
        if minutos > MINUTOS_BARREDOR_RANCIO:
            return {"ok": False, "hace_minutos": minutos,
                    "error": f"el barredor no corre desde hace {minutos} min: el vigilante está MUERTO"}
        return {"ok": True, "hace_minutos": minutos}
    except Exception as e:  # noqa: BLE001 — si esto no se puede leer, Postgres ya lo dijo
        return {"ok": True, "aviso": f"no se pudo leer el testigo del barredor ({_limpio(e)[:120]})"}


# ─── El veredicto ────────────────────────────────────────────────────

async def revisar() -> tuple[dict, int]:
    """(cuerpo, código HTTP).

    503 SOLO por Postgres o Redis: son los dos cuya caída hace que el bot se COMA los mensajes en
    silencio, y los dos que un reinicio puede arreglar. Un token de Meta caducado, un saldo en cero
    o un barredor muerto salen como `degradado` con 200 —reiniciar el contenedor no revive ninguno
    de los tres— y el monitor los caza por la PALABRA, no por el código.
    """
    ahora = time.monotonic()
    if _CACHE["cuerpo"] is not None and ahora - _CACHE["en"] < _CACHE_SEGUNDOS:
        cuerpo = _CACHE["cuerpo"]
        return cuerpo, (503 if cuerpo["estado"] == "caido" else 200)

    pg, rd, mt, sd, bd = await _postgres(), await _redis(), await _meta(), await _saldo(), await _barredor()
    piezas = (("postgres", pg), ("redis", rd), ("meta", mt), ("saldo_ia", sd), ("barredor", bd))
    fallos = [n for n, d in piezas if not d["ok"]]
    estado = "caido" if (not pg["ok"] or not rd["ok"]) else ("degradado" if fallos else "ok")

    cuerpo = {
        "estado": estado,          # ok | degradado | caido
        "fallos": fallos,
        "postgres": pg,
        "redis": rd,
        "meta": mt,
        "saldo_ia": sd,
        "barredor": bd,
        "negocio": settings.negocio_nombre,
    }
    _CACHE.update(en=ahora, cuerpo=cuerpo)
    return cuerpo, (503 if estado == "caido" else 200)
