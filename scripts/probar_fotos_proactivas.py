"""FOTO PROACTIVA: el bot MUESTRA el producto sin que se lo pidan — pero SIN bombardear.

🔴 POR QUÉ EXISTE (pruebas reales del usuario con su celular, 2026-07-15): el bot esperaba a que le
pidieran la foto para mandarla. El prompt ya le pide mostrarla proactivamente, pero es probabilístico;
esta red lo vuelve determinista: si el cliente se enfoca en UN producto con fotos y el modelo no las
mandó, el CÓDIGO se las muestra — la doctrina de `_asegurar_catalogo`/`_asegurar_saludo`.

🐛 EL BUG QUE ARREGLÓ ESTE BANCO: la versión vieja buscaba el NOMBRE COMPLETO del producto en el
texto. Con 'Pan Keto' funcionó; con 'Empanadas de masa de yuca o de masa de plátano' NO —el bot
nunca escribe ese nombre largo, dice 'empanadas' + 'masa de plátano'—, así que al elegir "la de
plátano" no se mostraba nada. Ahora se guía por las PALABRAS DISTINTIVAS que dijo el cliente:
'plátano' apunta a ESE producto y a ningún otro.

Vigila: MUESTRA cuando una palabra del cliente apunta a UN producto con fotos · NO dispara con una
palabra compartida por varios (empate: sigue eligiendo) · NO con productos sin fotos · NO repite…
salvo que el cliente PIDA la foto otra vez.
"""
import asyncio
import sys
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import delete, select

from app.agent.tools import _palabras_distintivas, producto_para_mostrar
from app.models import Mensaje, Producto, ProductoMedia
from app.services.db import get_session_factory

TEL = "999000333444"  # NO empieza por "__": la ruta real, no el simulador

fallos: list[str] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"   {'[OK ]' if ok else '[MAL]'} {nombre}{('  → ' + detalle) if detalle and not ok else ''}")
    if not ok:
        fallos.append(nombre)


async def _clasificar(session):
    prods = (
        await session.execute(select(Producto).where(Producto.disponible.is_(True)))
    ).scalars().all()
    con, sin = [], []
    for p in prods:
        tiene = (
            await session.execute(
                select(ProductoMedia.id).where(ProductoMedia.producto_id == p.id).limit(1)
            )
        ).first()
        (con if tiene else sin).append(p)
    return con, sin


async def _sembrar_foto(factory, prod):
    async with factory() as s:
        s.add(Mensaje(
            cliente_telefono=TEL, rol="assistant", tipo="image",
            contenido=f"(foto de {prod.nombre})", media_url="https://x/x.jpg",
            estado="enviado", created_at=datetime.now(UTC),
        ))
        await s.commit()


async def main() -> None:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(delete(Mensaje).where(Mensaje.cliente_telefono == TEL))
        await session.commit()
        con, sin = await _clasificar(session)

    if len(con) < 2:
        check("hay al menos 2 productos con fotos para probar", False, f"solo {len(con)}")
        return

    # Mapa: palabra distintiva → productos con fotos que la llevan.
    palabra_prods = defaultdict(list)
    for p in con:
        for w in _palabras_distintivas(p.nombre):
            palabra_prods[w].append(p)
    unica = next(((w, ps[0]) for w, ps in palabra_prods.items() if len(ps) == 1), None)
    compartida = next((w for w, ps in palabra_prods.items() if len(ps) >= 2), None)

    # 1. EL FIX: nombrar UNA palabra distintiva única detecta el producto (aunque no sea su nombre completo).
    if unica:
        w, prod = unica
        r = await producto_para_mostrar(f"quiero la de {w}", TEL)
        check(f"1. detecta por palabra distintiva '{w}' (no el nombre completo)",
              r == prod.nombre, f"devolvió {r!r}, esperaba {prod.nombre!r}")
    else:
        check("1. (sin palabra distintiva única en el catálogo — caso omitido)", True)

    # 2. EMPATE: una palabra que comparten varios productos con fotos → no elige ninguno.
    if compartida:
        r = await producto_para_mostrar(f"tienes {compartida}?", TEL)
        check(f"2. NO dispara con palabra compartida '{compartida}' (empate, no bombardea)",
              r is None, f"devolvió {r!r}")
    else:
        check("2. (sin palabra compartida en el catálogo — caso omitido)", True)

    # 3. SIN FOTOS: nombrar una palabra de un producto sin media no dispara.
    if sin:
        pal_sin = next((w for w in _palabras_distintivas(sin[0].nombre) if w not in palabra_prods), None)
        if pal_sin:
            r = await producto_para_mostrar(f"quiero {pal_sin}", TEL)
            check("3. NO dispara si el producto no tiene fotos", r is None, f"devolvió {r!r}")
        else:
            check("3. (el producto sin fotos comparte palabras con otros — caso omitido)", True)
    else:
        check("3. (no hay productos sin fotos — caso omitido)", True)

    # 4. SIN FOCO: charla sin ningún producto → None.
    r = await producto_para_mostrar("hola, buenas noches, cómo estás", TEL)
    check("4. NO dispara si no hay ningún producto en foco", r is None, f"devolvió {r!r}")

    # 5 y 6. NO REPETIR (sin pedir) vs REENVIAR (si el cliente la PIDE).
    if unica:
        w, prod = unica
        await _sembrar_foto(factory, prod)
        r_no = await producto_para_mostrar(f"quiero la de {w}", TEL)
        r_si = await producto_para_mostrar(f"mándame la foto de la de {w}", TEL, pidio_fotos=True)
        check("5. NO repite la foto si ya se mostró (sin pedirla)", r_no is None, f"devolvió {r_no!r}")
        check("6. SÍ la reenvía si el cliente la PIDE de nuevo", r_si == prod.nombre, f"devolvió {r_si!r}")
    else:
        check("5-6. (sin palabra única — casos omitidos)", True)

    async with factory() as session:
        await session.execute(delete(Mensaje).where(Mensaje.cliente_telefono == TEL))
        await session.commit()


if __name__ == "__main__":
    asyncio.run(main())
    if fallos:
        print(f"\n❌ {len(fallos)} FALLO(S): {fallos}")
        sys.exit(1)
    print("\n✅ FOTO PROACTIVA: todo en verde")
