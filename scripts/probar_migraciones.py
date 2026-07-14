"""EL ESQUEMA DE LA BASE ESTÁ COMPLETO (o no arrancamos).

🔴 POR QUÉ EXISTE (2026-07-14, encontrado de casualidad, y llevaba DÍAS mordiendo PRODUCCIÓN):

No hay tabla de migraciones aplicadas (deuda D1), así que `init_db` vuelve a correr **las 24
migraciones ENTERAS en cada arranque**. La 019 ponía un candado estrecho a `mensajes.tipo`; la 021
lo amplió después. En cuanto un cliente real mandó un contacto, esa fila dejó de caber en el
candado de la 019 → la 019 **reventaba** → y `main.py` **se tragaba la excepción**, así que el
contenedor arrancaba **VERDE**… con las migraciones 020, 021, 022 y 022b **SIN APLICAR**.

Coste real: la 015 volvía a crear el índice viejo `ux_precio_dia_producto_fecha` y la 022 (que lo
borra) no llegaba a correr ⇒ **en producción NO se podía cargar el precio del día de dos tamaños
del mismo producto**. El bug que la 022 vino a matar seguía vivo, y las pruebas del taller decían
que todo estaba bien: **el taller no tenía esos datos raros**.

Lección: *un contenedor en verde NO significa que la base esté como el código cree.* Esto lo
comprueba. Si algún día una migración vuelve a fallar en silencio, este banco se pone ROJO.
"""
import asyncio
import sys

from sqlalchemy import text

from app.services.db import get_session_factory

fallos: list[str] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"   {'[OK ]' if ok else '[MAL]'} {nombre}{('  → ' + detalle) if detalle and not ok else ''}")
    if not ok:
        fallos.append(nombre)


# (tabla, columna) que TIENEN que existir — cada una es una migración que sí llegó a correr.
COLUMNAS = [
    ("mensajes", "tipo", "019 · la bandeja"),
    ("mensajes", "media_url", "021 · el comprobante se ve en el chat"),
    ("mensajes", "wa_message_id", "021 · saber si el mensaje LLEGÓ"),
    ("mensajes", "estado", "021 · entregado / leído / FALLÓ"),
    ("clientes", "pausado_por", "020 · quién apretó el freno"),
    ("clientes", "ultimo_entrante_at", "019 · el reloj de las 24h de Meta"),
    ("producto_variantes", "precio", "022 · el precio vive en el TAMAÑO"),
    ("precio_dia", "variante_id", "022 · el precio del día es POR TAMAÑO"),
    ("pedidos", "entrega_fecha", "016 · sin fecha no se cobra"),
]

# Índices que TIENEN que estar… y el que NO puede estar.
INDICES_VIVOS = [
    ("ux_precio_dia_variante_fecha", "022 · un precio por TAMAÑO y día"),
]
INDICES_MUERTOS = [
    # 🔴 EL DELATOR. Si este vuelve a aparecer, es que la 022 NO corrió: alguna migración anterior
    # está reventando y el arranque se lo está tragando. Y la dueña NO puede cargar el precio del
    # día de la torta de 250g y la de 1kg a la vez.
    ("ux_precio_dia_producto_fecha",
     "015 · el índice VIEJO: si está, la 022 no corrió y el precio del día por tamaño está ROTO"),
]

TIPOS_QUE_DEBEN_CABER = ["text", "image", "audio", "document", "sticker", "video",
                         "location", "contacts", "reaction", "otro"]


async def main() -> None:
    factory = get_session_factory()

    print("\n1) LAS COLUMNAS QUE CADA MIGRACIÓN DEBÍA DEJAR")
    async with factory() as s:
        filas = (await s.execute(text(
            "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='public'"
        ))).all()
    existentes = {(t, c) for t, c in filas}
    for tabla, col, quien in COLUMNAS:
        check(f"{tabla}.{col:<18} ({quien})", (tabla, col) in existentes)

    print("\n2) LOS ÍNDICES (y el DELATOR que no puede volver)")
    async with factory() as s:
        idx = {r[0] for r in (await s.execute(text(
            "SELECT indexname FROM pg_indexes WHERE schemaname='public'"
        ))).all()}
    for nombre, quien in INDICES_VIVOS:
        check(f"existe {nombre}  ({quien})", nombre in idx)
    for nombre, quien in INDICES_MUERTOS:
        check(f"🔴 NO existe {nombre}  ({quien})", nombre not in idx,
              "ESTÁ AHÍ ⇒ una migración está fallando en el arranque y nadie se entera")

    print("\n3) EL CANDADO DE `mensajes.tipo` ACEPTA TODO LO QUE MANDA WHATSAPP")
    print("   (fue justo esto lo que reventó el arranque de producción durante días)")
    async with factory() as s:
        definicion = (await s.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid='mensajes'::regclass AND conname='ck_mensaje_tipo'"
        ))).scalar_one_or_none()
    check("el candado ck_mensaje_tipo existe", definicion is not None)
    if definicion:
        for tipo in TIPOS_QUE_DEBEN_CABER:
            check(f"cabe un mensaje de tipo '{tipo}'", f"'{tipo}'" in definicion,
                  "un cliente que mande esto REVIENTA el arranque en el próximo despliegue")

    print("\n4) QUE NO HAYA FILAS QUE YA NO QUEPAN (la bomba, antes de que estalle)")
    async with factory() as s:
        raros = (await s.execute(text(
            "SELECT tipo, count(*) FROM mensajes WHERE tipo NOT IN "
            "('text','image','audio','document','sticker','video','location','contacts','reaction','otro') "
            "GROUP BY tipo"
        ))).all()
    check("ninguna fila de `mensajes` tiene un tipo desconocido", not raros, str(raros))

    print()
    if fallos:
        print(f"   🔴 {len(fallos)} FALLO(S) — LA BASE NO ESTÁ COMO EL CÓDIGO CREE:")
        for f in fallos:
            print(f"      · {f}")
        print("   (mira el arranque: `docker logs <bot> | grep -i 'init_db fallo'`)")
        sys.exit(1)
    print("   ✅ EL ESQUEMA ESTÁ COMPLETO: todas las migraciones llegaron a aplicarse de verdad")


asyncio.run(main())
