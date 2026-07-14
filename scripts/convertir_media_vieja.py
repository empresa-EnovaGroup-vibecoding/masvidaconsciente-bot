"""Convierte la media YA SUBIDA a R2 al formato que WhatsApp exige (una sola vez).

La puerta nueva (subir_media) convierte todo AL SUBIR — pero lo que se subió ANTES quedó
tal cual (caso real: el video .quicktime de la Torta keto, que WhatsApp rechaza siempre).
Este script repasa `producto_media`, y lo que no sea MP4 (videos) o JPEG/PNG (imágenes)
lo baja de R2, lo convierte, sube el archivo nuevo, actualiza la clave en la BD y borra
el viejo — EN ESE ORDEN (primero el nuevo vive, después muere el viejo: si algo falla a
mitad, no se pierde nada).

Correr DENTRO del contenedor del bot (tiene ffmpeg):
  docker exec -w /app -e PYTHONPATH=/app <bot> python scripts/convertir_media_vieja.py
"""
import asyncio
import sys

from sqlalchemy import select

from app.models import ProductoMedia
from app.services import media_convert, r2
from app.services.db import get_session_factory

_EXT_VIDEO_OK = (".mp4",)
_EXT_IMAGEN_OK = (".jpeg", ".jpg", ".png")


async def main() -> None:
    if not r2.configurado():
        print("R2 no está configurado: nada que hacer")
        return
    factory = get_session_factory()
    async with factory() as session:
        medios = (
            await session.execute(select(ProductoMedia).order_by(ProductoMedia.id))
        ).scalars().all()

        convertidos = 0
        fallidos: list[str] = []
        for m in medios:
            clave = (m.clave or "").lower()
            if m.tipo == "video" and clave.endswith(_EXT_VIDEO_OK):
                continue
            if m.tipo != "video" and clave.endswith(_EXT_IMAGEN_OK):
                continue

            print(f"→ media {m.id} ({m.tipo}): {m.clave}")
            crudo = await r2.bajar(m.clave)
            if not crudo:
                fallidos.append(f"media {m.id}: no se pudo bajar de R2")
                continue
            try:
                if m.tipo == "video":
                    nuevo, ct, ext = await media_convert.normalizar_video(crudo)
                else:
                    nuevo, ct, ext = await media_convert.normalizar_imagen(crudo, "")
            except media_convert.MediaInvalida as e:
                fallidos.append(f"media {m.id}: {e}")
                continue

            base = m.clave.rsplit(".", 1)[0]
            clave_nueva = f"{base}.{ext}"
            if not await r2.subir(clave_nueva, nuevo, ct):
                fallidos.append(f"media {m.id}: no se pudo subir el convertido")
                continue
            clave_vieja = m.clave
            m.clave = clave_nueva
            await session.commit()  # el nuevo ya manda; solo entonces muere el viejo
            if clave_vieja.lower() != clave_nueva.lower():
                await r2.borrar(clave_vieja)
            convertidos += 1
            print(f"   ✅ {clave_vieja} → {clave_nueva} ({len(nuevo)//1024} KB)")

    print(f"\nConvertidos: {convertidos} · Sin tocar: {len(medios) - convertidos - len(fallidos)}")
    if fallidos:
        print("🔴 FALLARON:")
        for f in fallidos:
            print(f"   - {f}")
        sys.exit(1)
    print("✅ TODA LA MEDIA GUARDADA ES ENVIABLE POR WHATSAPP")


if __name__ == "__main__":
    asyncio.run(main())
