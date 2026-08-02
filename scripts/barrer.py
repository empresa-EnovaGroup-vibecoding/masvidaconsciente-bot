"""EL BARREDOR A MANO — la puerta que funciona HOY, sin scheduler.

Se lanza por SSH, dentro del contenedor del BOT (el mismo patrón que los bancos):

  docker exec -w /app -e PYTHONPATH=/app <bot> python scripts/barrer.py --seco
  docker exec -w /app -e PYTHONPATH=/app <bot> python scripts/barrer.py --forzar

  --seco     LISTA lo que encontraría y NO ESCRIBE NADA. 🔴 Úsalo la PRIMERA vez, en el taller Y en
             producción, y mira la lista CON LOS OJOS antes de dejar el vigilante suelto. Un
             detector que se estrena inundando la bandeja se apaga el mismo día — y entonces no
             vigila nada. Esto no es una ceremonia: es el único ensayo posible contra datos reales.
  --forzar   Se salta la concesión de 5 minutos. Es lo que se lanza cuando la dueña llama diciendo
             "el bot no contesta" y no se quiere esperar al turno siguiente.
  (sin nada) Respeta la concesión: si el bucle de la API barrió hace menos de 5 min, no hace nada.
             Sirve para engancharlo a un cron externo el día que haya uno.

Sale con 0 si no hay nadie colgado y con 1 si SÍ lo hay: así, si algún día se cuelga de un cron o
de un GitHub Action, el rojo se ve sin leer la salida.

⚠️ `--seco` usa el MISMO SQL que el vigilante (`barredor.SQL_SIN_RESPUESTA`), a propósito. Si el
ensayo usara una consulta propia mentiría, que es exactamente lo que NO puede hacer un ensayo.
"""
import asyncio
import sys

from sqlalchemy import text

from app.services.barredor import (
    HORAS_MAX,
    MINUTOS_SIN_RESPUESTA,
    SQL_SIN_RESPUESTA,
    barrer,
    ultima_corrida,
)
from app.services.db import get_session_factory


async def seco() -> int:
    factory = get_session_factory()
    async with factory() as s:
        filas = (await s.execute(
            SQL_SIN_RESPUESTA, {"minutos": MINUTOS_SIN_RESPUESTA, "horas": HORAS_MAX}
        )).all()
        pend = (await s.execute(text(
            "SELECT count(*) FROM intervenciones WHERE estado = 'pendiente'"
        ))).scalar()
        cuando = await ultima_corrida(s)

    print(f"\nCLIENTES SIN RESPUESTA (>{MINUTOS_SIN_RESPUESTA} min, <{HORAS_MAX} h): {len(filas)}")
    for f in filas:
        print(f"   · {f.nombre or '(sin nombre)':<25} {f.telefono:<18} escribió {f.ultimo_entrante_at}")
    # OJO al mirar la lista: aquí NO están aplicados los filtros que sí aplica el vigilante en
    # vivo (la lista blanca del taller, el tope anti-abuso y el candado de un aviso por chat). Esta
    # lista es el TECHO de a quién avisaría: si ya se ve corta y creíble, el vigilante avisará de
    # menos, nunca de más.
    print(f"\nAvisos PENDIENTES ahora mismo en la bandeja: {pend}")
    print(f"Última corrida del barredor: {cuando or 'NUNCA'}")
    print("\n(modo SECO: no se escribió nada)")
    return 1 if filas else 0


def main() -> None:
    if "--seco" in sys.argv:
        sys.exit(asyncio.run(seco()))
    resumen = asyncio.run(barrer(forzar="--forzar" in sys.argv))
    print(resumen)
    sys.exit(1 if resumen.get("colgados") else 0)


if __name__ == "__main__":
    main()
