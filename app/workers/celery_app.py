from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "masvidaconsciente",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="America/Caracas",
    task_track_started=True,
)

# Importa las tareas para que Celery las registre
import app.workers.tasks  # noqa: E402,F401
