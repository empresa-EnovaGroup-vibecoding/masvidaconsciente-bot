"""Prepara la base de datos al desplegar:
1. Crea las tablas (idempotente: CREATE TABLE IF NOT EXISTS).
2. Carga el catálogo seed solo si la tabla de productos está vacía.

Se ejecuta al arrancar el contenedor web. Seguro de correr varias veces.
"""
import asyncio
import logging
from pathlib import Path

from sqlalchemy import text

from app.services.db import get_session_factory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("init_db")

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def _statements(archivo: Path) -> list[str]:
    sql = archivo.read_text(encoding="utf-8")
    # quita comentarios de línea y separa por ';'
    lineas = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lineas).split(";") if s.strip()]


async def main() -> None:
    factory = get_session_factory()
    async with factory() as session:
        for stmt in _statements(MIGRATIONS / "001_init.sql"):
            await session.execute(text(stmt))
        await session.commit()
        logger.info("Tablas listas")

        total = (await session.execute(text("SELECT COUNT(*) FROM productos"))).scalar()
        if total and total > 0:
            logger.info("Catálogo ya cargado (%s productos), no se vuelve a sembrar", total)
            return

        for stmt in _statements(MIGRATIONS / "002_seed_catalogo.sql"):
            await session.execute(text(stmt))
        await session.commit()
        logger.info("Catálogo sembrado")


if __name__ == "__main__":
    asyncio.run(main())
