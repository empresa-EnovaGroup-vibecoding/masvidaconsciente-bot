from fastapi import FastAPI

from app.config import get_settings
from app.webhook.router import router as webhook_router

settings = get_settings()

app = FastAPI(title="masvidaconsciente-bot", version="0.1.0")
app.include_router(webhook_router)


@app.get("/")
def health():
    return {"status": "ok", "negocio": settings.negocio_nombre}
