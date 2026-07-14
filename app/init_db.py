"""Prepara la base de datos al desplegar.

🔴 LA DEUDA D1, CERRADA (fase 0). Antes este fichero era una LISTA ESCRITA A MANO que
re-ejecutaba las 24 migraciones EN CADA ARRANQUE, y su propio comentario avisaba:
*"Si una migración nueva no se agrega aquí, su archivo .sql NUNCA se ejecuta (y nadie se
entera)"*. Peor: `main.py` se tragaba la excepción, así que el contenedor podía arrancar
**VERDE con la base a medias**. Ya mordió: la 019 puso un CHECK estrecho, un cliente real
mandó un `contacts` que no cabía, la 019 empezó a reventar en cada arranque, y las
migraciones 020-023 **dejaron de aplicarse durante días**, en silencio.

AHORA:
1. Las migraciones se DESCUBREN SOLAS (`migrations/*.sql`, en orden). Olvidarse de
   registrar una es imposible: no hay lista que actualizar.
2. Cada una se aplica UNA VEZ y queda anotada en `schema_migrations`. No se re-ejecutan.
3. Si algo falla, se LANZA. `main.py` ya no lo tapa: el contenedor NO arranca.
   Un contenedor rojo es mucho mejor que uno verde con la base a medias.

El ORDEN alfabético es el correcto y no es casualidad:
    001_init → 002_seed → 003…022_variantes → 022b_variantes_datos → 023_zonas
('_' < 'b' en ASCII, así que `022_` va antes que `022b`). La 022b es un backfill que
necesita que el catálogo YA esté sembrado por la 002: en una BD nueva, ese orden se cumple.
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

# ⚠️ LA ÚNICA MIGRACIÓN QUE NO ES IDEMPOTENTE. Su `INSERT INTO productos` NO lleva
# `ON CONFLICT`: correrla dos veces DUPLICA el catálogo entero. Por eso el código viejo la
# protegía con un contador ("siembra solo si `productos` está vacía") en vez de dejarla en la
# lista con las demás. Ese candado se conserva aquí — es lo que hace seguro estrenar
# `schema_migrations` contra una base que YA tiene datos (producción).
SEED = "002_seed_catalogo.sql"


def _statements(archivo: Path) -> list[str]:
    sql = archivo.read_text(encoding="utf-8")
    # Quita comentarios de línea y separa por ';'.
    # ⚠️ Es un partidor INGENUO: un ';' dentro de un literal o de un bloque `DO $$ … $$` lo
    # rompería. Hoy ninguna migración los usa (las que necesitaban un DO dinámico lo esquivan
    # a mano; ver el comentario de 019_bandeja.sql). Toda migración nueva debe seguir
    # evitándolos, o hay que endurecer esto primero.
    lineas = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lineas).split(";") if s.strip()]


async def _asegurar_tabla_migraciones(session) -> None:
    """La tabla que recuerda qué ya se aplicó. Se crea sola, es idempotente."""
    await session.execute(
        text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "  nombre TEXT PRIMARY KEY,"
            "  aplicada_en TIMESTAMPTZ NOT NULL DEFAULT now()"
            ")"
        )
    )
    await session.commit()


async def _ya_aplicadas(session) -> set[str]:
    filas = (await session.execute(text("SELECT nombre FROM schema_migrations"))).scalars().all()
    return set(filas)


async def _catalogo_vacio(session) -> bool:
    """¿La tabla de productos está vacía? Decide si la 002 siembra o solo se anota.

    En una BD NUEVA: vacía ⇒ se siembra.
    En producción (ya tiene 32 productos): NO se siembra, pero SÍ se anota como aplicada —
    que es la verdad: se sembró hace meses. Sin esto, estrenar `schema_migrations` contra
    producción volvería a correr el seed y duplicaría el catálogo entero.
    """
    try:
        total = (await session.execute(text("SELECT COUNT(*) FROM productos"))).scalar()
    except Exception:  # noqa: BLE001 — si `productos` aún no existe, la BD está vacía
        await session.rollback()
        return True
    return not total


async def _aplicar(session, archivo: Path) -> None:
    for stmt in _statements(archivo):
        await session.execute(text(stmt))
    await session.execute(
        text("INSERT INTO schema_migrations (nombre) VALUES (:n) ON CONFLICT DO NOTHING"),
        {"n": archivo.name},
    )
    await session.commit()


async def main() -> None:
    """Aplica las migraciones que falten y sincroniza el admin. LANZA si algo falla."""
    factory = get_session_factory()
    async with factory() as session:
        await _asegurar_tabla_migraciones(session)
        aplicadas = await _ya_aplicadas(session)

        # Descubrimiento AUTOMÁTICO. Adiós a la lista escrita a mano: un .sql nuevo en
        # `migrations/` se aplica solo. Ya no se puede "olvidar registrar" una migración.
        archivos = sorted(MIGRATIONS.glob("*.sql"), key=lambda f: f.name)
        if not archivos:
            raise RuntimeError(f"No hay migraciones en {MIGRATIONS} — ¿falta el COPY del Dockerfile?")

        pendientes = [f for f in archivos if f.name not in aplicadas]
        logger.info(
            "Migraciones: %d en disco, %d ya aplicadas, %d pendientes",
            len(archivos), len(aplicadas), len(pendientes),
        )

        for archivo in pendientes:
            # EL SEED es el único caso especial: no es idempotente (ver la constante SEED).
            # Si el catálogo YA tiene productos, NO se siembra — pero SÍ se anota, porque
            # se sembró hace meses. Así producción estrena `schema_migrations` sin duplicar nada.
            if archivo.name == SEED and not await _catalogo_vacio(session):
                await session.execute(
                    text(
                        "INSERT INTO schema_migrations (nombre) VALUES (:n) "
                        "ON CONFLICT DO NOTHING"
                    ),
                    {"n": archivo.name},
                )
                await session.commit()
                logger.info("%s: el catálogo YA estaba sembrado, se anota sin re-sembrar", SEED)
                continue

            try:
                await _aplicar(session, archivo)
            except Exception:
                await session.rollback()
                # 🔴 SE LANZA A PROPÓSITO. `main.py` ya NO lo tapa: si una migración falla, el
                # contenedor NO arranca. Antes se tragaba la excepción y la app quedaba viva con
                # la base a medias — verde por fuera, rota por dentro, durante días.
                logger.error("🔴 MIGRACIÓN FALLIDA: %s — el arranque se aborta", archivo.name)
                raise
            logger.info("Migración aplicada: %s", archivo.name)

        if not pendientes:
            logger.info("Base de datos al día: nada que migrar")

        # Esto NO son migraciones: se sincronizan en CADA arranque, a propósito.
        await _crear_admin(session)
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
        for fila, vec in zip(filas, vectores, strict=False):
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
