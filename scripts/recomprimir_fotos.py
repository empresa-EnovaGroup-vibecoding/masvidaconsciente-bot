"""Re-comprime las fotos de producto que ya están en R2 (una sola vez).

🔴 Las subidas ANTES del 2026-07-14 pasaron sin comprimir (el código solo tocaba las >5MB), así
que pesan 1-2 MB y Meta las rechaza bajo ráfagas. Esto las baja a ~500KB / 1600px, idénticas en
el teléfono. Solo IMÁGENES; los videos ya se normalizaban aparte. Idempotente: si una foto ya
está liviana, la deja (la conversión la dejaría casi igual, pero nos ahorramos la subida)."""
import asyncio

from sqlalchemy import select

from app.models import ProductoMedia
from app.services import r2
from app.services.db import get_session_factory
from app.services.media_convert import OBJETIVO_IMAGEN, normalizar_imagen


async def main() -> None:
    f = get_session_factory()
    async with f() as s:
        fotos = (await s.execute(
            select(ProductoMedia).where(ProductoMedia.tipo == "imagen")
        )).scalars().all()
    print(f"  {len(fotos)} fotos que revisar")
    bajadas = 0
    for m in fotos:
        crudo = await r2.bajar(m.clave)
        if not crudo:
            print(f"    · media {m.id}: no se pudo bajar, se salta")
            continue
        if len(crudo) <= OBJETIVO_IMAGEN:
            continue  # ya está liviana
        try:
            nuevo, ct, _ = await normalizar_imagen(crudo, "image/jpeg")
        except Exception as e:  # noqa: BLE001
            print(f"    · media {m.id}: no se pudo comprimir ({e})")
            continue
        if len(nuevo) >= len(crudo):
            continue  # no mejoró
        ok = await r2.subir(m.clave, nuevo, ct)  # MISMA clave: la URL no cambia
        if ok:
            bajadas += 1
            print(f"    ✅ media {m.id}: {len(crudo)//1024} KB → {len(nuevo)//1024} KB")
    print(f"\n  ✅ {bajadas} fotos re-comprimidas")


if __name__ == "__main__":
    asyncio.run(main())
