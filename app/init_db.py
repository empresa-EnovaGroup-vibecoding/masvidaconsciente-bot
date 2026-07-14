"""Prepara la base de datos al desplegar:
1. Crea las tablas (idempotente: CREATE TABLE IF NOT EXISTS).
2. Carga el catálogo seed solo si la tabla de productos está vacía.

Se ejecuta al arrancar el contenedor web. Seguro de correr varias veces.
"""
import asyncio
import json
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

        # 008: catálogo PDF guardado en la BD (sobrevive redeploys). Aditiva e idempotente.
        for stmt in _statements(MIGRATIONS / "008_catalogo_pdf.sql"):
            await session.execute(text(stmt))
        await session.commit()
        logger.info("Migracion 008 (catalogo pdf en BD) aplicada")

        # 009: metodos de pago (varias cuentas: Pago Movil, Banesco, Binance, Zelle...).
        for stmt in _statements(MIGRATIONS / "009_metodos_pago.sql"):
            await session.execute(text(stmt))
        await session.commit()
        logger.info("Migracion 009 (metodos de pago) aplicada")

        # 010: numero de cuenta bancaria en metodos de pago (transferencias).
        for stmt in _statements(MIGRATIONS / "010_metodo_cuenta.sql"):
            await session.execute(text(stmt))
        await session.commit()
        logger.info("Migracion 010 (cuenta en metodos de pago) aplicada")

        # 011: busqueda difusa (pg_trgm + unaccent) para tolerar typos y acentos.
        # Cada extension/indice va en su PROPIA transaccion: si una falla (p.ej. la
        # imagen no trae la extension), no tumba las demas ni el arranque del bot.
        for stmt in _statements(MIGRATIONS / "011_busqueda_difusa.sql"):
            try:
                await session.execute(text(stmt))
                await session.commit()
            except Exception as e:  # noqa: BLE001 — la busqueda difusa es mejora, no debe romper el deploy
                await session.rollback()
                logger.warning("Migracion 011: '%s...' no aplico (%s)", stmt[:40], e)
        logger.info("Migracion 011 (busqueda difusa) aplicada")

        # 012: columna embedding (busqueda semantica del Conocimiento). Aditiva e idempotente.
        for stmt in _statements(MIGRATIONS / "012_conocimiento_embedding.sql"):
            await session.execute(text(stmt))
        await session.commit()
        logger.info("Migracion 012 (embedding conocimiento) aplicada")

        # 013: ficha del producto (duracion, se_congela, apto_diabeticos, info). Aditiva.
        for stmt in _statements(MIGRATIONS / "013_producto_ficha.sql"):
            await session.execute(text(stmt))
        await session.commit()
        logger.info("Migracion 013 (ficha de producto) aplicada")

        # 014: fotos/videos de productos (media en R2; en la BD solo la ruta). Aditiva.
        for stmt in _statements(MIGRATIONS / "014_producto_media.sql"):
            await session.execute(text(stmt))
        await session.commit()
        logger.info("Migracion 014 (media de producto) aplicada")

        # 015: "el bot te necesita" (intervenciones) + precio del día. Aditiva.
        for stmt in _statements(MIGRATIONS / "015_intervenciones.sql"):
            await session.execute(text(stmt))
        await session.commit()
        logger.info("Migracion 015 (intervenciones + precio del dia) aplicada")

        # 016: la ENTREGA del pedido (para cuándo y cómo). Aditiva.
        # OJO: esta lista está escrita A MANO. Si una migración nueva no se agrega aquí,
        # su archivo .sql NUNCA se ejecuta (y nadie se entera).
        for stmt in _statements(MIGRATIONS / "016_pedido_entrega.sql"):
            await session.execute(text(stmt))
        await session.commit()
        logger.info("Migracion 016 (entrega del pedido) aplicada")

        # 017: horario de entrega (días, feriados, anticipación por producto). Aditiva.
        for stmt in _statements(MIGRATIONS / "017_horario_entrega.sql"):
            await session.execute(text(stmt))
        await session.commit()
        logger.info("Migracion 017 (horario de entrega) aplicada")

        # 018: las HORAS (atención + hora de corte para pedidos del mismo día). Aditiva.
        for stmt in _statements(MIGRATIONS / "018_horas.sql"):
            await session.execute(text(stmt))
        await session.commit()
        logger.info("Migracion 018 (horas) aplicada")

        # 019: LA BANDEJA (rol 'owner', tipo/media, estado del envío, reloj de 24h, no leídos).
        for stmt in _statements(MIGRATIONS / "019_bandeja.sql"):
            await session.execute(text(stmt))
        await session.commit()
        logger.info("Migracion 019 (bandeja) aplicada")

        # 020: QUIÉN pausó el chat (la dueña o el propio bot). Aditiva e idempotente.
        for stmt in _statements(MIGRATIONS / "020_quien_paso.sql"):
            await session.execute(text(stmt))
        await session.commit()
        logger.info("Migracion 020 (quien paso el chat) aplicada")

        # 021: el hilo dice la verdad (media del cliente, tipos del eco, candado de estados).
        for stmt in _statements(MIGRATIONS / "021_hilo_completo.sql"):
            await session.execute(text(stmt))
        await session.commit()
        logger.info("Migracion 021 (hilo completo) aplicada")

        # 022: PRODUCTO · TAMAÑO · OPCIÓN — solo la ESTRUCTURA (tablas e índices).
        for stmt in _statements(MIGRATIONS / "022_variantes.sql"):
            await session.execute(text(stmt))
        await session.commit()
        logger.info("Migracion 022 (tamaños: estructura) aplicada")

        total = (await session.execute(text("SELECT COUNT(*) FROM productos"))).scalar()
        if total and total > 0:
            logger.info("Catálogo ya cargado (%s productos), no se vuelve a sembrar", total)
        else:
            for stmt in _statements(MIGRATIONS / "002_seed_catalogo.sql"):
                await session.execute(text(stmt))
            await session.commit()
            logger.info("Catálogo sembrado")

        # 022b: los DATOS de los tamaños. Va DESPUÉS del seed a propósito: en una BD NUEVA (un
        # cliente nuevo) las migraciones corren ANTES de sembrar el catálogo, así que un
        # backfill ahí vería `productos` VACÍA ⇒ CERO tamaños ⇒ el bot no podría vender NADA,
        # y sin un solo error en el log. Es idempotente en DATOS (se re-ejecuta en cada arranque).
        for stmt in _statements(MIGRATIONS / "022b_variantes_datos.sql"):
            await session.execute(text(stmt))
        await session.commit()
        logger.info("Migracion 022b (tamaños: datos) aplicada")

        # 023: EL DELIVERY — las zonas y su costo (el "código de barras" del envío).
        for stmt in _statements(MIGRATIONS / "023_zonas_entrega.sql"):
            await session.execute(text(stmt))
        await session.commit()
        logger.info("Migracion 023 (zonas de entrega) aplicada")

        # El admin se crea/verifica siempre, exista o no el catálogo
        await _crear_admin(session)

        # Indexa (embeddings) las entradas de Conocimiento que aún no lo tengan.
        await _backfill_embeddings(session)


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


async def _backfill_embeddings(session) -> None:
    """Calcula el embedding de las entradas de Conocimiento que aún no lo tienen
    (las creadas antes de la búsqueda semántica). Una sola llamada en lote. Fail-safe:
    si los embeddings fallan (sin saldo, etc.) no pasa nada — la búsqueda léxica sigue."""
    try:
        filas = (
            await session.execute(
                text("SELECT id, titulo, contenido FROM conocimiento WHERE embedding IS NULL")
            )
        ).all()
        if not filas:
            return
        from app.services.embeddings import obtener_embeddings

        textos = [f"{f.titulo}. {f.contenido or ''}" for f in filas]
        vectores = await obtener_embeddings(textos)
        actualizados = 0
        for fila, vec in zip(filas, vectores):
            if vec is None:
                continue
            await session.execute(
                text("UPDATE conocimiento SET embedding = CAST(:emb AS JSONB) WHERE id = :id"),
                {"emb": json.dumps(vec), "id": fila.id},
            )
            actualizados += 1
        await session.commit()
        logger.info("Embeddings backfill: %s entradas indexadas", actualizados)
    except Exception as e:  # noqa: BLE001 — el backfill es mejora; nunca rompe el arranque
        await session.rollback()
        logger.warning("Backfill de embeddings no se hizo (%s)", e)


if __name__ == "__main__":
    asyncio.run(main())
