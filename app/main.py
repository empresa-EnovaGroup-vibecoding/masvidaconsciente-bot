import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import router as api_router
from app.config import get_settings
from app.webhook.router import router as webhook_router

logger = logging.getLogger(__name__)
settings = get_settings()

# Cada cuánto despierta el bucle. NO es cada cuánto barre: el barrido se autolimita solo con la
# concesión de Postgres (5 min). El minuto manda para el RESCATE de buffers huérfanos, que es el
# único de los dos que todavía puede salvar un mensaje a tiempo.
_SEGUNDOS_ENTRE_PASADAS = 60


async def _bucle_vigilante() -> None:
    """EL "SCHEDULER" QUE CABE POR docker cp (SIL-13.2 + SIL-3b, fusionados).

    🔴 NO HAY BEAT, NO HAY CRON, Y HOY NO SE PUEDE AÑADIR: Coolify está desconectado, el despliegue
    es `docker cp` de ARCHIVOS + restart, y ni el `command` del contenedor ni docker-compose se
    vuelven a leer. Un `celery beat` en un servicio aparte, o un `-B` en el worker, exigen justo lo
    único que no se puede tocar.

    Esto sí cabe, y además es EL SITIO CORRECTO. La avería que hay que cazar es "la API en pie
    contestando 200 mientras por detrás no responde nadie". Si ESTE proceso se cae, Meta recibe
    errores y REINTENTA: ese fallo es RUIDOSO y no necesita vigilante. El silencioso es el otro — y
    para el otro, este proceso está vivo por definición. En el WORKER no serviría: cuando el worker
    muere (la causa más probable del silencio) el vigilante moriría con él.

    UN SOLO `create_task` para las dos cosas (rescate + barrido), no dos bucles sueltos: dos tareas
    serían dos sitios donde morir en silencio y dos testigos que escribir. Lo que hace cada pasada
    está en `barredor.una_pasada`.

    El turno se lo reparte `barredor.tomar_turno` (una concesión en Postgres, no en Redis: Redis
    puede ser justo lo que esté caído), así que da igual si esto corre con `--workers 4`, si
    alguien lanza `scripts/barrer.py` por SSH a la vez, o si algún día llega el beat: barre UNO
    SOLO.
    """
    from app.services.barredor import una_pasada

    while True:
        try:
            # El sleep va PRIMERO a propósito: si el contenedor entra en bucle de reinicios, esto
            # no martillea la base en cada arranque, y le da aire a `init_db`.
            await asyncio.sleep(_SEGUNDOS_ENTRE_PASADAS)
            resumen = await una_pasada()
            barrido = resumen.get("barrido") or {}
            if barrido.get("avisos") or barrido.get("error") or (resumen.get("rescate") or {}).get("rescatados"):
                logger.warning("VIGILANTE: %s", resumen)
        except asyncio.CancelledError:
            raise  # apagado del contenedor: se sale limpio, sin ruido
        except Exception:  # noqa: BLE001 — el vigilante JAMÁS puede tumbar la API
            logger.exception("VIGILANTE: la pasada falló; se reintenta en la siguiente")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Al arrancar el bot: aplica las migraciones pendientes y sincroniza el admin.

    🔴 FALLA RUIDOSAMENTE, A PROPÓSITO (deuda D1, cerrada en la fase 0).

    Antes esto vivía dentro de un `try/except` que se TRAGABA la excepción "para no tumbar
    la app". Sonaba prudente y era justo lo contrario: si una migración reventaba, el
    contenedor arrancaba **VERDE** y servía tráfico con la base A MEDIAS. Ya pasó — la 019
    empezó a fallar por un dato que no cabía en su CHECK, y las migraciones 020-023 dejaron
    de aplicarse **durante días** sin que nadie se enterara. El síntoma no fue un error: fue
    que la dueña no podía cargar el precio del día.

    Un contenedor ROJO se ve en el acto y Coolify no promueve el despliegue.
    Un contenedor VERDE con la base rota cobra mal y nadie mira. Preferimos el rojo.
    """
    from app.init_db import main as preparar_bd

    await preparar_bd()  # si falla, LANZA: el arranque se aborta y el contenedor no sirve
    logger.info("init_db ejecutado en el arranque")
    # Importar `salud` AQUÍ no es decorativo: el módulo estampa la hora de arranque del proceso al
    # importarse, y de eso depende que `/salud` no cante "el barredor está muerto" durante el
    # primer minuto de cada despliegue (cuando el bucle aún no ha cumplido su primera pasada).
    import app.services.salud  # noqa: F401

    vigilante = asyncio.create_task(_bucle_vigilante())
    try:
        yield
    finally:
        # La referencia vive en esta variable local mientras viva la app (el GC no se la lleva).
        # `CancelledError` hereda de BaseException, así que el `except Exception` del bucle no se la
        # traga aunque el `raise` explícito no estuviera — está para que se lea.
        vigilante.cancel()


app = FastAPI(title="masvidaconsciente-bot", version="0.1.0", lifespan=lifespan)

_origenes = [o.strip() for o in settings.dashboard_origin.split(",") if o.strip()] or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origenes,  # dominio(s) del dashboard; vacio = cualquiera
    allow_credentials=False,  # el dashboard usa token Bearer, no cookies
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webhook_router)
app.include_router(api_router)


@app.get("/")
def health():
    """LIVENESS Y NADA MÁS: "¿el proceso está en pie?". Se queda TONTO a propósito.

    🔴 NO SE TOCA. Es la sonda que mira el orquestador. Si aprendiera a devolver 503, un Redis
    caído 40 segundos podría marcar el contenedor unhealthy y hacer que lo RECREEN — y hoy el
    despliegue vive de `docker cp` sobre contenedores que ya corren: una recreación se llevaría por
    delante todo el trabajo desplegado. La pregunta de verdad ("¿puede este bot hacer su trabajo?")
    se contesta en `/salud`, que va aparte justamente por esto.
    """
    return {"status": "ok", "negocio": settings.negocio_nombre, "salud": "/salud"}


@app.get("/salud")
async def salud():
    """LA SALUD DE VERDAD: toca Postgres, Redis, el token de Meta, EL SALDO DEL MODELO y el
    testigo del barredor, uno por uno. El detalle de cada sonda y el contrato para el monitor
    externo están en `app/services/salud.py`.

    Público y sin auth a propósito: un monitor no sabe hacer login JWT, y hoy no se pueden añadir
    variables de entorno (Coolify desconectado). No devuelve ni un secreto ni el número de
    WhatsApp: solo ok/no-ok por pieza, con los errores limpiados de credenciales. Si algún día se
    quiere cerrar, el token va en la tabla `configuracion` (editable desde el panel, sin redeploy),
    como `numeros_permitidos_extra`.
    """
    from app.services.salud import revisar

    cuerpo, codigo = await revisar()
    return JSONResponse(content=cuerpo, status_code=codigo)
