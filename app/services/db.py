from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings


@lru_cache
def get_session_factory():
    """Crea el engine y la factory de sesiones la primera vez que se usan.

    Perezoso a propósito: importar este módulo no abre conexiones ni exige
    que el driver de la base de datos esté disponible hasta que de verdad
    se consulta la BD.
    """
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
