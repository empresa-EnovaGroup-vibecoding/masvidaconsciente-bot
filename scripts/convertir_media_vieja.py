"""Convierte la media YA SUBIDA a R2 al formato que WhatsApp exige (una sola vez).

La puerta nueva (subir_media) convierte todo AL SUBIR — pero lo que se subió ANTES quedó
tal cual (caso real: el video .quicktime de la Torta keto, que WhatsApp rechaza siempre).
Este script repasa `producto_media`, baja de R2 lo sospechoso, lo convierte, sube el
archivo nuevo, actualiza la clave en la BD y borra el viejo — EN ESE ORDEN (primero el
nuevo vive, después muere el viejo: si algo falla a mitad, no se pierde nada). Si la
clave no cambia (ya era .mp4), se SOBREESCRIBE el mismo objeto y no se borra nada.

🔴 LA LECCIÓN DEL 2026-09-03: la corrida del 14-jul fue un NO-OP para los videos. Este
script confiaba en la EXTENSIÓN (`.endswith(".mp4")`) y `_video_ya_sirve` confiaba en la
subcadena "mp4" del format_name de ffprobe — y un QuickTime de iPhone pasa LAS DOS
pruebas mintiendo. Resultado: los 5 .mov quedaron renombrados a .mp4, byte a byte
idénticos, y el primer envío real de un video murió con Meta 131053. Por eso ahora los
VIDEOS se sondean SIEMPRE (se baja el archivo y ffprobe mira el contenedor real: el
`major_brand`); la extensión no vuelve a decidir. Las imágenes conservan su salto por
extensión: las 29 del catálogo se verificaron sanas en la autopsia y su pipeline (Pillow)
sí valida contenido real al subir.

⚠️ El bucket R2 es COMPARTIDO entre pruebas y producción: correr esto REESCRIBE objetos
que producción sirve en vivo. Solo con OK expreso de Maired y en hora valle.

Correr DENTRO del contenedor del bot (tiene ffmpeg):
  docker exec -w /app -e PYTHONPATH=/app <bot> python scripts/convertir_media_vieja.py
"""
import asyncio
import sys

from sqlalchemy import select

from app.models import ProductoMedia
from app.services import media_convert, r2
from app.services.db import get_session_factory

_EXT_IMAGEN_OK = (".jpeg", ".jpg", ".png")


def _se_salta_sin_sondear(tipo: str, clave: str) -> bool:
    """True si la fila se puede saltar SIN bajar el archivo.

    Los VIDEOS jamás se saltan: la extensión .mp4 es exactamente la mentira que dejó la
    corrida del 14-jul (QuickTime renombrado). El único juez del contenedor es ffprobe,
    y para eso hay que bajar el archivo. Las imágenes sí se saltan por extensión (ver
    docstring del módulo: verificadas sanas, y su puerta valida contenido real).
    """
    if (tipo or "") == "video":
        return False
    return (clave or "").lower().endswith(_EXT_IMAGEN_OK)


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
            if _se_salta_sin_sondear(m.tipo, m.clave):
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

            if m.tipo == "video" and nuevo == crudo:
                # ffprobe dice que YA es un MP4 ISO sano: nada que subir ni contar.
                print("   ✓ ya era un MP4 de verdad — sin tocar")
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
