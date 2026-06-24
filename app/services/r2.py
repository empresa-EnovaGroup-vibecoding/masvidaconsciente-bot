"""Almacenamiento de fotos/videos de productos en Cloudflare R2 (almacén de objetos,
compatible con S3). Guardamos el ARCHIVO en R2 y en la BD solo la RUTA (clave); la URL
pública se compone con R2_PUBLIC_URL al mostrar/enviar → cambiar el dominio público
después NO obliga a migrar nada.

Fail-safe: si R2 no está configurado o algo falla, las funciones devuelven False/"" y el
panel muestra el error, sin tumbar el bot.
"""
import asyncio
import logging

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def configurado() -> bool:
    """True si las 4 variables esenciales de R2 están puestas."""
    return bool(
        settings.r2_account_id
        and settings.r2_access_key_id
        and settings.r2_secret_access_key
        and settings.r2_bucket
    )


def _cliente():
    import boto3  # import perezoso: si no se usa media, no se carga boto3

    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    )


async def subir(clave: str, contenido: bytes, content_type: str) -> bool:
    """Sube bytes a R2 bajo `clave`. True si ok; False si no está configurado o falla.
    boto3 es síncrono: lo corremos en un hilo para no bloquear el servidor."""
    if not configurado() or not contenido:
        return False

    def _put() -> None:
        _cliente().put_object(
            Bucket=settings.r2_bucket, Key=clave, Body=contenido, ContentType=content_type
        )

    try:
        await asyncio.to_thread(_put)
        return True
    except Exception as e:  # noqa: BLE001 — la media es mejora; nunca debe tumbar el bot
        logger.error("R2 subir falló (%s): %s", clave, e)
        return False


async def borrar(clave: str) -> bool:
    """Borra un objeto de R2. True si ok; False si no configurado o falla."""
    if not configurado() or not clave:
        return False

    def _del() -> None:
        _cliente().delete_object(Bucket=settings.r2_bucket, Key=clave)

    try:
        await asyncio.to_thread(_del)
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("R2 borrar falló (%s): %s", clave, e)
        return False


def url_publica(clave: str) -> str:
    """URL pública del objeto (R2_PUBLIC_URL + clave). Si un día se cambia el dominio
    público, solo se cambia R2_PUBLIC_URL: las rutas guardadas en la BD no se tocan."""
    base = (settings.r2_public_url or "").rstrip("/")
    return f"{base}/{clave.lstrip('/')}" if base else ""
