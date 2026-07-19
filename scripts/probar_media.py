"""LA MULTIMEDIA SE VE EN EL PANEL: lo que el bot manda por WhatsApp, la dueña lo ve.

🔴 POR QUÉ EXISTE (auditoría 2026-07-14, verificado contra la BD REAL de producción):

El bot SÍ enviaba la multimedia por WhatsApp —las fotos de producto y el catálogo PDF llegaban
al cliente— pero **NO la guardaba**. `enviar_fotos_producto` y `enviar_catalogo` hacían el POST a
Meta y se acababa ahí: cero filas en `mensajes`.

La prueba, contra producción: las **130 filas eran TODAS `tipo='text'`** y **NINGUNA** tenía
`media_url` — aunque el esquema admite `image`/`video`/`document` desde la migración 021 y las
columnas `media_id`/`media_url`/`media_mime` existen desde entonces. **El esquema estaba listo;
nadie lo escribía.** La dueña abría el chat interno y veía una conversación donde el bot "nunca"
mandó una foto. Y como el `wa_message_id` de Meta también se tiraba, una foto que Meta RECHAZARA
se perdía en silencio, sin rastro de "falló" en ninguna parte.

⚠️ EL ARREGLO TENÍA DOS MITADES, Y CON UNA SOLA NO SE VE NADA:
  1. Que el bot GUARDE la fila (`_guardar_media_saliente`).
  2. Que el endpoint SEPA SERVIRLA. `/api/mensajes/{id}/media` solo hacía `os.path.exists()`
     sobre disco local — y las fotos de producto viven en Cloudflare R2 (`https://…`), así que
     `os.path.exists("https://…")` daba **False** ⇒ **404** ⇒ el panel decía "No se pudo cargar
     el archivo". Guardar el dato sin esto habría sido guardar un dato invisible.

Este banco comprueba LAS DOS.
"""
import asyncio
import sys

from sqlalchemy import delete, select

from app.agent import tools
from app.models import Mensaje, Producto, ProductoMedia
from app.services.db import get_session_factory

TEL = "999000111222"  # NO empieza por "__": prueba el envío REAL (Meta va mockeado), no el simulador

fallos: list[str] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"   {'[OK ]' if ok else '[MAL]'} {nombre}{('  → ' + detalle) if detalle and not ok else ''}")
    if not ok:
        fallos.append(nombre)


# Doble de Meta: no se manda NADA de verdad. Devuelve lo mismo que la Cloud API real,
# incluido el `wa_message_id` — que es justo lo que antes se tiraba.
ENVIADOS: list[tuple[str, str]] = []  # [(tipo, url)]


def _falso(tipo: str):
    async def _f(telefono, link, *a, **k):
        ENVIADOS.append((tipo, link))
        return {"messages": [{"id": f"wamid.FALSO.{tipo}.{len(ENVIADOS)}"}]}
    return _f


async def main() -> None:
    factory = get_session_factory()

    # El doble de Meta, puesto donde las tools lo buscan (importan DENTRO de la función).
    import app.services.meta_client as meta

    meta.enviar_imagen = _falso("image")
    meta.enviar_video = _falso("video")
    meta.enviar_documento = _falso("document")
    tools.enviar_imagen = _falso("image")
    tools.enviar_video = _falso("video")

    async with factory() as session:
        # Limpia lo que dejó una corrida anterior.
        await session.execute(delete(Mensaje).where(Mensaje.cliente_telefono == TEL))
        await session.commit()

        # Un producto que SÍ tenga fotos cargadas (23 de los 32 las tienen).
        prod = (
            await session.execute(
                select(Producto)
                .join(ProductoMedia, ProductoMedia.producto_id == Producto.id)
                .where(Producto.disponible.is_(True))
                .limit(1)
            )
        ).scalars().first()

        print("\n1) EL BOT MANDA FOTOS → TIENEN QUE QUEDAR EN EL HILO")
        if prod is None:
            check("hay algún producto con fotos cargadas", False, "ninguno tiene media")
        else:
            r = await tools.enviar_fotos_producto(session, TEL, prod.nombre)
            enviadas = r.get("enviadas", 0)
            check(f"enviar_fotos_producto('{prod.nombre}') envía algo", enviadas > 0, str(r)[:60])

            async with factory() as s2:
                filas = (
                    await s2.execute(
                        select(Mensaje)
                        .where(Mensaje.cliente_telefono == TEL)
                        .order_by(Mensaje.id)
                    )
                ).scalars().all()

            # ⚠️ TODAS estas comprobaciones exigen `filas` NO VACÍA a propósito. `all()` sobre una
            # lista vacía devuelve True, así que sin este candado el banco se pondría VERDE contra
            # el código roto — que es justo el que NO guarda ninguna fila. Un test que pasa cuando
            # no hay datos no prueba nada.
            check(
                f"quedan {enviadas} fila(s) en `mensajes` (una por archivo)",
                len(filas) == enviadas and enviadas > 0,
                f"envió {enviadas} y guardó {len(filas)}",
            )
            check(
                "el rol es 'assistant' (lo mandó el BOT, no el cliente)",
                bool(filas) and all(f.rol == "assistant" for f in filas),
                str([f.rol for f in filas]) or "no hay filas",
            )
            check(
                "el tipo es image/video, NO 'text' (era el bug: las 130 filas eran 'text')",
                bool(filas) and all(f.tipo in ("image", "video") for f in filas),
                str([f.tipo for f in filas]) or "no hay filas",
            )
            check(
                "TODAS tienen media_url (era el bug: CERO la tenían)",
                bool(filas) and all(bool(f.media_url) for f in filas),
                str([f.media_url for f in filas]) or "no hay filas",
            )
            check(
                "se guardó el wa_message_id de Meta (antes se TIRABA)",
                bool(filas) and all(bool(f.wa_message_id) for f in filas),
                "sin wa_message_id ⇒ una foto que Meta rechace se pierde sin rastro",
            )
            check(
                "el estado queda en 'enviado'",
                bool(filas) and all(f.estado == "enviado" for f in filas),
                str([f.estado for f in filas]) or "no hay filas",
            )
            check(
                "el contenido NO va vacío (`mensajes.contenido` es NOT NULL)",
                bool(filas) and all((f.contenido or "").strip() for f in filas),
            )

        print("\n2) EL PANEL TIENE QUE PODER SERVIRLO (la otra mitad del arreglo)")
        async with factory() as s3:
            fila = (
                await s3.execute(
                    select(Mensaje).where(Mensaje.cliente_telefono == TEL).limit(1)
                )
            ).scalars().first()
        if fila is None:
            check("hay una fila que servir", False)
        else:
            # 🔴 EL BUG DE LA OTRA MITAD: el endpoint hacía `os.path.exists(media_url)`. Con una
            # URL de R2 eso da False ⇒ 404 ⇒ "No se pudo cargar el archivo". Se comprueba que la
            # URL guardada es REMOTA y que el endpoint la reconoce como tal (rama de proxy).
            import os

            es_remota = (fila.media_url or "").startswith(("http://", "https://"))
            check(
                "la media_url guardada es una URL remota (R2)",
                es_remota,
                f"media_url={fila.media_url!r}",
            )
            check(
                "os.path.exists() NO la encuentra — por eso hacía falta la rama de proxy",
                es_remota and not os.path.exists(fila.media_url or ""),
                "si esto pasara, el endpoint viejo habría funcionado y no haría falta el arreglo",
            )
            from app.api import router as api_router

            check(
                "el endpoint sabe servir remoto (importa StreamingResponse + httpx)",
                hasattr(api_router, "StreamingResponse") and hasattr(api_router, "httpx"),
            )

        print("\n3) LA FOTO DE LA DUEÑA (solo trae media_id, sin archivo descargado)")
        async with factory() as s4:
            s4.add(
                Mensaje(
                    cliente_telefono=TEL,
                    rol="owner",
                    tipo="image",
                    contenido="(foto de la dueña)",
                    media_id="FALSO_MEDIA_ID",
                    media_url=None,   # ← el eco de Meta NO descarga el archivo
                )
            )
            await s4.commit()

        # 🔴 SE LLAMA AL ENDPOINT DE VERDAD, no a una expresión copiada aquí. La primera versión de
        # este banco comprobaba `bool(m.media_url or m.media_id)` — o sea, se probaba a sí mismo:
        # una TAUTOLOGÍA que salía verde incluso contra el código roto. Ahora se ejecuta
        # `detalle_conversacion`, que es lo que el panel consume de verdad.
        from app.api.router import detalle_conversacion

        hilo = await detalle_conversacion(TEL, "banco")
        burbuja = next((x for x in hilo if x["rol"] == "owner"), None)
        check(
            "la foto de la dueña aparece en el hilo",
            burbuja is not None,
            "no salió en detalle_conversacion",
        )
        # `tiene_media` era `bool(media_url)` ⇒ False ⇒ burbuja VACÍA en el panel.
        check(
            "con solo media_id, `tiene_media` es True (era el bug MED-5)",
            bool(burbuja and burbuja.get("tiene_media")),
            "la foto de la dueña salía como una burbuja vacía en el chat interno",
        )

        # Limpieza: este banco escribe de verdad, así que se lleva lo suyo.
        async with factory() as s5:
            await s5.execute(delete(Mensaje).where(Mensaje.cliente_telefono == TEL))
            await s5.commit()

    print()
    if fallos:
        print(f"   🔴 {len(fallos)} FALLO(S). La multimedia no llega al panel.")
        sys.exit(1)
    print("   ✅ LO QUE EL BOT MANDA POR WHATSAPP, LA DUEÑA LO VE EN EL PANEL")


if __name__ == "__main__":
    asyncio.run(main())
