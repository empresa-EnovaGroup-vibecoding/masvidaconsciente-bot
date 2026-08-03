"""BANCO: UNA FILA RETIRADA DE CONOCIMIENTO NO LE LLEGA AL BOT (migración 030).

🔴 POR QUÉ EXISTE. De los 23 bancos, NINGUNO tocaba `buscar_info`. `probar_buscador` vigila el
otro buscador (`ver_catalogo` / `_buscar_producto`, el carril del catálogo y del dinero);
`probar_prompt_coherente` solo comprueba que la regla 70 se lea bien cuando la tool está apagada.
La base de Conocimiento —lo que la dueña escribe y el bot REPITE— no la miraba nadie.

Y el bug que la 030 puede traer ROMPE EN SILENCIO. El WHERE de `_buscar_info_lexical`
(tools.py:1610-1631) son TRES ramas unidas por OR. Si el filtro se escribe como

    ... A LIKE ... OR B >= :umbral OR C >= :umbral AND activo IS TRUE

la precedencia AND > OR lo convierte en `A OR B OR (C AND activo)`: las dos primeras ramas siguen
devolviendo las filas RETIRADAS. Nadie ve un error, ningún log se enciende, ningún banco se pone
rojo — el bot simplemente sigue diciendo "todos los panes duran tres meses". Comprobado contra la
base del taller: con "Las tortas" retirada y la consulta 'tortas', el WHERE ingenuo la devuelve
igual y el WHERE con paréntesis no. Este banco es la única forma de que eso no vuelva a colarse.

⚠️ ESCRIBE EN LA BASE REAL DEL TALLER, Y POR ESO NUNCA HACE COMMIT. Las filas sonda se meten en
UNA sesión que termina en ROLLBACK: `buscar_info` recibe ESA misma sesión, así que las ve, pero
nadie más las ve nunca y no pueden quedar colgadas en el prompt aunque el banco reviente a mitad.
Aun así se limpia por título AL PRINCIPIO (por si una corrida anterior murió con un commit hecho
a mano) y AL FINAL, que es la regla de la casa para todo banco que toque la BD del taller.
"""
import asyncio
import inspect
import sys

from sqlalchemy import delete, select, text

from app.agent import tools
from app.agent.system_prompt import _conocimiento_indice
from app.agent.tools import buscar_info
from app.models import Conocimiento
from app.services.db import get_session_factory

TEL = "__banco_conocimiento_activo__"
# Un sello que NO puede aparecer en nada de la dueña: así el borrado de limpieza es quirúrgico.
SELLO = "zzbancoactivo"
TIT_VIVA = f"{SELLO} fila viva del banco"
TIT_RETIRADA = f"{SELLO} fila retirada del banco"

fallos: list[str] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"   {'[OK ]' if ok else '[MAL]'} {nombre}{('  → ' + detalle) if detalle and not ok else ''}")
    if not ok:
        fallos.append(nombre)


async def _limpiar(session) -> int:
    """Borra CUALQUIER fila con el sello. Solo puede tocar filas de este banco."""
    r = await session.execute(delete(Conocimiento).where(Conocimiento.titulo.like(f"{SELLO}%")))
    await session.commit()
    return r.rowcount or 0


async def main() -> None:
    factory = get_session_factory()

    # ── 0. LA COLUMNA EXISTE (si no, todo lo de abajo miente) ──
    print("\n0) LA MIGRACIÓN 030 CORRIÓ")
    async with factory() as s:
        cols = {
            r[0]
            for r in (
                await s.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='conocimiento'"
                    )
                )
            ).all()
        }
    check("conocimiento.activo existe (030)", "activo" in cols)
    if "activo" not in cols:
        print("\n   🔴 Sin la columna no tiene sentido seguir.")
        sys.exit(1)

    # ── LIMPIEZA DE ENTRADA (restos de una corrida que murió a medias) ──
    async with factory() as s:
        borradas = await _limpiar(s)
    if borradas:
        print(f"   (limpieza de entrada: {borradas} fila(s) sonda de una corrida anterior)")

    try:
        # ── 1. EL PARÉNTESIS DEL WHERE (el bug que rompe en silencio) ──
        # La sonda RETIRADA lleva el sello EN EL TÍTULO, así que la primera rama del OR —el
        # LIKE por substring— calza SEGURO. Es justo la rama que se cuela cuando el filtro se
        # pega al final sin paréntesis. Si alguien lo escribe mal, esto se pone ROJO.
        print("\n1) UNA FILA RETIRADA NO SALE POR NINGUNA DE LAS TRES RAMAS DEL OR")
        async with factory() as s:
            s.add(Conocimiento(categoria="faq", titulo=TIT_VIVA, contenido=f"{SELLO} contenido vivo", activo=True))
            s.add(Conocimiento(categoria="faq", titulo=TIT_RETIRADA, contenido=f"{SELLO} contenido retirado", activo=False))
            await s.flush()  # visibles para ESTA sesión, para nadie más

            # Sin saldo de OpenRouter el camino semántico devuelve [] solo; lo forzamos para que
            # el caso sea determinista y mida SOLO lo léxico.
            import app.services.embeddings as emb
            original = emb.obtener_embedding
            emb.obtener_embedding = lambda _t: _async_none()
            try:
                r = await buscar_info(s, TEL, SELLO)
            finally:
                emb.obtener_embedding = original

            temas = [x["tema"] for x in r.get("resultados", [])]
            check("la fila VIVA sí se encuentra (el filtro no rompió la búsqueda)", TIT_VIVA in temas, f"devolvió {temas}")
            check(
                "🔴 la fila RETIRADA NO se cuela por la rama del LIKE (el paréntesis)",
                TIT_RETIRADA not in temas,
                f"¡se coló! devolvió {temas}",
            )
            await s.rollback()

        # ── 2. EL CAMINO SEMÁNTICO TAMBIÉN FILTRA ──
        # `_coseno` devuelve 0.0 si los vectores no miden lo mismo (tools.py:1596-1597), así que
        # con un vector de 3 posiciones las 9 filas reales puntúan 0 y solo compiten las sondas.
        print("\n2) EL CAMINO SEMÁNTICO (EMBEDDINGS) TAMBIÉN LA DEJA FUERA")
        async with factory() as s:
            s.add(Conocimiento(categoria="faq", titulo=TIT_VIVA, contenido="da igual", embedding=[1.0, 0.0, 0.0], activo=True))
            s.add(Conocimiento(categoria="faq", titulo=TIT_RETIRADA, contenido="da igual", embedding=[1.0, 0.0, 0.0], activo=False))
            await s.flush()

            import app.services.embeddings as emb
            original = emb.obtener_embedding
            emb.obtener_embedding = lambda _t: _async_vec([1.0, 0.0, 0.0])
            try:
                filas = await tools._buscar_info_semantico(s, "lo que sea")
            finally:
                emb.obtener_embedding = original

            titulos = [f.titulo for f in filas]
            check("la fila VIVA sí puntúa", TIT_VIVA in titulos, f"devolvió {titulos}")
            check(
                "🔴 la fila RETIRADA no puntúa aunque su vector calce perfecto",
                TIT_RETIRADA not in titulos,
                f"¡se coló! devolvió {titulos}",
            )
            await s.rollback()

        # ── 3. EL ÍNDICE DEL PROMPT (el que viaja en CADA turno) ──
        # Sin este filtro, el TÍTULO de una fila retirada sigue anunciándose como un tema que el
        # bot "SÍ SABE" en el prompt estable de todos los mensajes. Genérico a propósito: no
        # nombra ninguna fila de la dueña, solo comprueba la regla.
        print("\n3) EL ÍNDICE DEL PROMPT NO ANUNCIA NINGÚN TEMA RETIRADO")
        async with factory() as s:
            retirados = [
                t for (t,) in (
                    await s.execute(select(Conocimiento.titulo).where(Conocimiento.activo.is_(False)))
                ).all()
            ]
        check(
            "la 030 retiró al menos una fila (si no, lo de abajo no prueba nada)",
            len(retirados) >= 1,
            "no hay ninguna fila con activo=FALSE",
        )
        indice = await _conocimiento_indice()
        check("el índice NO viene vacío (el bot sigue teniendo temas)", bool(indice.strip()), repr(indice[:60]))
        colados = [t for t in retirados if t and t in indice]
        check(
            "🔴 ningún título retirado aparece en TEMAS QUE SÍ SABES",
            not colados,
            f"se colaron {colados}",
        )

        # ── 4. TRIPWIRE DEL RESPALDO SIN pg_trgm ──
        # Esa rama solo corre si `unaccent`/`similarity` no existen, y en el taller SÍ existen:
        # no hay forma honesta de dispararla desde aquí. Lo que sí se puede vigilar es que nadie
        # toque el SQL crudo y se olvide del respaldo que vive tres líneas más abajo.
        print("\n4) EL RESPALDO SIN pg_trgm NO SE QUEDÓ ATRÁS (tripwire de código)")
        fuente = inspect.getsource(tools._buscar_info_lexical)
        check(
            "`activo` aparece en el SQL crudo Y en el respaldo del except",
            fuente.count("activo") >= 2,
            f"solo aparece {fuente.count('activo')} vez/veces",
        )

    finally:
        # ── LIMPIEZA DE SALIDA (regla de la casa: el taller queda como estaba) ──
        async with factory() as s:
            b = await _limpiar(s)
        print(f"\n   (limpieza de salida: {b} fila(s) sonda borradas)")

    print()
    if fallos:
        print(f"   🔴 {len(fallos)} FALLO(S). El bot puede estar repitiendo algo que la dueña RETIRÓ.")
        sys.exit(1)
    print("   ✅ LO RETIRADO NO LE LLEGA AL BOT — NI POR TEXTO, NI POR SIGNIFICADO, NI POR EL ÍNDICE")


async def _async_none():
    return None


async def _async_vec(v):
    return v


if __name__ == "__main__":
    asyncio.run(main())
