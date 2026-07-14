import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import router as api_router
from app.config import get_settings
from app.webhook.router import router as webhook_router

logger = logging.getLogger(__name__)
settings = get_settings()


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
    yield


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
    return {"status": "ok", "negocio": settings.negocio_nombre}
