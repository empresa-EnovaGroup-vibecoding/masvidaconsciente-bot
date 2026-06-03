from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import router as api_router
from app.config import get_settings
from app.webhook.router import router as webhook_router

settings = get_settings()

app = FastAPI(title="masvidaconsciente-bot", version="0.1.0")

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
