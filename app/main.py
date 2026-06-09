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
    """Al arrancar el bot: prepara la BD (tablas idempotentes + siembra catalogo
    si esta vacio) y SINCRONIZA el usuario admin con ADMIN_PASSWORD.

    Sin esto, init_db no corria en el arranque, asi que cambiar ADMIN_PASSWORD
    no actualizaba el login. Es idempotente y seguro de correr en cada arranque.
    Si falla (BD caida un instante), no tumba la app: la responde igual.
    """
    try:
        from app.init_db import main as preparar_bd
        await preparar_bd()
        logger.info("init_db ejecutado en el arranque")
    except Exception:
        logger.exception("init_db fallo en el arranque (la app sigue funcionando)")
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
