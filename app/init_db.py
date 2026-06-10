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

        # 003: capa de cobro (tabla pagos + estados nuevos de pedido + datos de cobro).
        # Aditiva e idempotente: no toca 001 ni 002, segura de correr en cada arranque.
        for stmt in _statements(MIGRATIONS / "003_pagos.sql"):
            await session.execute(text(stmt))
        await session.commit()
        logger.info("Migracion 003 (pagos) aplicada")

        # 004: pago que no calza (estado 'parcial' + monto_recibido). Aditiva e idempotente.
        for stmt in _statements(MIGRATIONS / "004_pago_parcial.sql"):
            await session.execute(text(stmt))
        await session.commit()
        logger.info("Migracion 004 (pago parcial) aplicada")

        # 005: notas internas del cliente (CRM). Aditiva e idempotente.
        for stmt in _statements(MIGRATIONS / "005_cliente_notas.sql"):
            await session.execute(text(stmt))
        await session.commit()
        logger.info("Migracion 005 (notas cliente) aplicada")

        # 006: base de conocimiento del negocio (FAQ + info). Aditiva e idempotente.
        for stmt in _statements(MIGRATIONS / "006_conocimiento.sql"):
            await session.execute(text(stmt))
        await session.commit()
        logger.info("Migracion 006 (conocimiento) aplicada")

        # 007: pausar el bot por cliente. Aditiva e idempotente.
        for stmt in _statements(MIGRATIONS / "007_cliente_bot_pausado.sql"):
            await session.execute(text(stmt))
        await session.commit()
        logger.info("Migracion 007 (bot pausado por cliente) aplicada")

        total = (await session.execute(text("SELECT COUNT(*) FROM productos"))).scalar()
        if total and total > 0:
            logger.info("Catálogo ya cargado (%s productos), no se vuelve a sembrar", total)
        else:
            for stmt in _statements(MIGRATIONS / "002_seed_catalogo.sql"):
                await session.execute(text(stmt))
            await session.commit()
            logger.info("Catálogo sembrado")

        # El admin se crea/verifica siempre, exista o no el catálogo
        await _crear_admin(session)


async def _crear_admin(session) -> None:
    """Crea el usuario admin del dashboard si no existe."""
    from app.api.security import hash_password
    from app.config import get_settings
    from app.models import Usuario

    settings = get_settings()
    existe = (
        await session.execute(
            text("SELECT 1 FROM usuarios WHERE email = :email"),
            {"email": settings.admin_email},
        )
    ).scalar()
    nuevo_hash = hash_password(settings.admin_password)
    if existe:
        # Sincroniza la contrasena del admin con ADMIN_PASSWORD en cada arranque.
        # Antes el admin se creaba UNA sola vez: cambiar ADMIN_PASSWORD no actualizaba
        # el login. Ahora cambiar la variable (+ redeploy) si cambia la contrasena real.
        await session.execute(
            text("UPDATE usuarios SET password_hash = :ph WHERE email = :email"),
            {"ph": nuevo_hash, "email": settings.admin_email},
        )
        await session.commit()
        logger.info("Usuario admin: contrasena sincronizada con ADMIN_PASSWORD")
        return
    session.add(
        Usuario(
            email=settings.admin_email,
            password_hash=nuevo_hash,
            nombre="Administrador",
        )
    )
    await session.commit()
    logger.info("Usuario admin creado: %s", settings.admin_email)


if __name__ == "__main__":
    asyncio.run(main())
