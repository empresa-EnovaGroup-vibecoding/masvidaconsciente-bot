"""DRIFT DE ESQUEMA: ¿la base de datos es la que el código cree que es?

🔴 POR QUÉ EXISTE. `probar_migraciones.py` (su hermano) comprueba una LISTA ESCRITA A MANO de
columnas concretas — una por cada incidente que ya nos mordió. Es valiosa, pero tiene el mismo
defecto de clase que tenía `init_db`: **si añades una migración y te olvidas de añadir su columna
a esa lista, nadie se entera.** Protege contra los bugs de ayer, no contra los de mañana.

Esto es lo GENÉRICO: coge `app/models.py` —que es lo que el código cree que hay— y lo compara
contra el esquema REAL de Postgres. Sin listas. Si una migración no llegó a correr, la columna que
el código espera NO estará, y esto se pone ROJO.

Comprueba tres cosas, de más grave a menos:

1. 🔴 UNA MIGRACIÓN EN DISCO QUE NUNCA SE APLICÓ. El detector directo de la deuda D1: hay un
   `.sql` en `migrations/` que no está anotado en `schema_migrations`. (Antes de la fase 0 esto
   era invisible: las migraciones se re-corrían enteras en cada arranque y no había registro.)

2. 🔴 EL CÓDIGO ESPERA ALGO QUE NO ESTÁ. Una tabla o columna declarada en `models.py` que no
   existe en la base. Esto REVIENTA en caliente, con un cliente delante: es exactamente lo que
   pasó cuando la 022 no llegó a aplicarse y la dueña no podía cargar el precio del día.

3. ⚠️ LA BASE TIENE ALGO QUE EL CÓDIGO NO CONOCE. Una columna que existe en Postgres pero que
   `models.py` no declara. No rompe nada hoy (el código simplemente la ignora), pero significa
   que el modelo se quedó atrás. Avisa, no falla.
"""
import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

from app.models import Base
from app.services.db import get_session_factory

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"

# Tablas que existen en la BD a propósito y que `models.py` NO declara (no son del dominio).
FUERA_DEL_MODELO = {"schema_migrations"}

fallos: list[str] = []
avisos: list[str] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"   {'[OK ]' if ok else '[MAL]'} {nombre}{('  → ' + detalle) if detalle and not ok else ''}")
    if not ok:
        fallos.append(nombre)


async def main() -> None:
    factory = get_session_factory()
    async with factory() as session:
        # ── 1. ¿Alguna migración en disco se quedó sin aplicar? (el detector de la D1) ──
        print("\n1) LAS MIGRACIONES: ¿se aplicaron TODAS?")
        try:
            anotadas = set(
                (await session.execute(text("SELECT nombre FROM schema_migrations"))).scalars().all()
            )
        except Exception:  # noqa: BLE001
            await session.rollback()
            check(
                "existe la tabla schema_migrations",
                False,
                "no existe: esta base es anterior a la fase 0, o init_db no llegó a correr",
            )
            anotadas = set()
        else:
            check("existe la tabla schema_migrations", True)

        en_disco = {f.name for f in MIGRATIONS.glob("*.sql")}
        sin_aplicar = sorted(en_disco - anotadas)
        check(
            f"las {len(en_disco)} migraciones de disco están aplicadas",
            not sin_aplicar,
            f"NUNCA se aplicaron: {', '.join(sin_aplicar)}",
        )

        # ── 2 y 3. models.py contra el esquema REAL ──
        filas = (
            await session.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public'"
                )
            )
        ).all()

    real: dict[str, set[str]] = {}
    for tabla, columna in filas:
        real.setdefault(tabla, set()).add(columna)

    print("\n2) EL CÓDIGO CONTRA LA BASE: ¿está todo lo que models.py espera?")
    for tabla in sorted(Base.metadata.tables):
        modelo = Base.metadata.tables[tabla]
        if tabla not in real:
            check(f"la tabla '{tabla}' existe", False, "models.py la declara y en la BD NO está")
            continue
        esperadas = {c.name for c in modelo.columns}
        faltan = sorted(esperadas - real[tabla])
        check(
            f"'{tabla}' tiene sus {len(esperadas)} columnas",
            not faltan,
            f"FALTAN en la BD: {', '.join(faltan)} (¿una migración sin aplicar?)",
        )

    print("\n3) LA BASE CONTRA EL CÓDIGO: ¿hay columnas que models.py no conoce?")
    for tabla in sorted(real):
        if tabla in FUERA_DEL_MODELO:
            continue
        if tabla not in Base.metadata.tables:
            avisos.append(f"la tabla '{tabla}' existe en la BD y models.py no la declara")
            continue
        declaradas = {c.name for c in Base.metadata.tables[tabla].columns}
        sobran = sorted(real[tabla] - declaradas)
        if sobran:
            avisos.append(f"'{tabla}': la BD tiene {', '.join(sobran)} y models.py no lo declara")
    if avisos:
        for a in avisos:
            print(f"   [⚠️ ] {a}")
        print("   (no rompe nada hoy — pero models.py se quedó atrás)")
    else:
        print("   [OK ] models.py está al día con la base")

    print()
    if fallos:
        print(f"   🔴 DRIFT DE ESQUEMA: {len(fallos)} problema(s). La base NO es la que el código cree.")
        sys.exit(1)
    print("   ✅ SIN DRIFT: la base y el código dicen lo mismo")


if __name__ == "__main__":
    asyncio.run(main())
