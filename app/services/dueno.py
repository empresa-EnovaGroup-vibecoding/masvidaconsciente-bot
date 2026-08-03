"""EL TELÉFONO DE LA DUEÑA — UNA sola forma de resolverlo, para TODO el sistema.

🔴 POR QUÉ EXISTE (arreglo del cerebro partido, 2026-08-03). Había DOS formas y NO coincidían:
  · Los AVISOS (`workers/tasks.py`, `services/barredor.py`, `agent/tools.py`) leen la tabla
    `configuracion` y caen al entorno si allí no hay nada. Funcionan.
  · La IDENTIFICACIÓN ("¿quien me escribe ES la dueña?", `webhook/router.py:_es_la_duena` y
    `services/meta_client.py:es_numero_de_la_duena`) leía SOLO el entorno. Y en las cajas que
    monta Enova el entorno viene VACÍO y el número vive en la tabla (el taller, hoy mismo: sin
    `DUENO_TELEFONO` en los dos contenedores, y `dueno_telefono='573005690062'` en la base).
Traducido: la identificación devolvía False SIEMPRE. La dueña le escribe al número del negocio,
el bot LE VENDE A ELLA (le crea ficha, gasta tokens, ensucia el CRM) y la marca que abre su
ventana de 24h —la que decide si sus propios avisos van a poder llegarle— no se escribe nunca.

LA REGLA ES LA DE LOS AVISOS, QUE ES LA QUE FUNCIONA, promovida a función única:
    tabla `configuracion`  →  copia en Redis  →  variable de entorno
El entorno deja de ser "la verdad" y vuelve a ser lo que `config.py:67-69` siempre dijo que era:
la SEMILLA que se pone al montar la caja.

⚠️ LO QUE **NO** CAMBIA, PORQUE ES LO QUE EVITA EL DESASTRE (`webhook/router.py:195`):
**VACÍO SIGUE SIENDO "NADIE ES LA DUEÑA"**. Un `dueno_telefono` sin configurar NO puede convertir
a todos los clientes en 'la dueña' y dejar al bot mudo con el mundo entero. Por eso `es_la_duena`
devuelve False en cuanto el número resuelto está vacío — y por eso un valor de menos de 10
dígitos (un dedazo en el panel: "0", "si", medio número) se descarta como si no estuviera: en la
identificación, un falso positivo se paga con el bot MUDO frente a un cliente de verdad.

⚠️ EL CARRIL CALIENTE. `_es_la_duena` corre en el webhook, en CADA mensaje entrante, donde Meta
espera ~5 s; `es_numero_de_la_duena` corre dentro del manejo de CADA rechazo de Meta. Por eso la
consulta a Postgres NO se paga por mensaje: se paga UNA VEZ por proceso cada `_TTL_MEMO`
segundos y el resultado se memoriza en el proceso. Coste amortizado por mensaje: CERO consultas.
(Para comparar: el webhook YA paga al menos 4 idas a Postgres por mensaje — `_marcar_entrante`,
`_bot_activo`, `_cliente_pausado` y `_numero_permitido`.) El precio del memo es que un cambio del
número desde el panel tarda como mucho un minuto en verse: es un dato que cambia una vez al año.

🔴 Y DE REGALO, EL ÚLTIMO TESTIGO. Cada resolución que SÍ llegó a Postgres reescribe la copia de
Redis (`CLAVE_COPIA`, la misma que estrenó `_recordar_dueno` en `agent/tools.py`). Hasta hoy esa
copia solo se escribía cuando de verdad SALÍA un aviso —un pago o una escalada—, así que en una
caja recién montada estaba VACÍA justo el día que hacía falta: `avisar_relevo_caido` se ejecuta
cuando Postgres se cayó y no puede preguntarle a nadie. Ahora la reescribe el webhook con cada
mensaje que entra: cuando la base se caiga, el número ya está fuera de ella.
"""
import logging
import time

from app.config import get_settings
from app.services.redis_client import get_cache, set_cache

logger = logging.getLogger(__name__)

# La COPIA fuera de Postgres. Es LA MISMA clave que ya usaba `_recordar_dueno` (agent/tools.py):
# se reusa a propósito, no se estrena otra. Prefijo `cache:` por la convención de
# `redis_client.py:102-104`.
CLAVE_COPIA = "cache:dueno_telefono_ultimo_bueno"
_TTL_COPIA = 30 * 86400

# EL MEMO DEL PROCESO: lo que evita una consulta por mensaje en el carril caliente. Van como dos
# globales sueltas (y no en un dict ni en una clase) para que los bancos puedan fijarlas en una
# línea, igual que ya hacen con `settings.dueno_telefono` o con `mc._MARCA_VENTANA`.
_TTL_MEMO = 60.0
_memo_valor: str = ""
_memo_hasta: float = 0.0


def cola(s: str | None) -> str:
    """Los últimos 10 dígitos: 58412…, +58412… y 0412… son el MISMO número.

    Es el criterio que ya estaba repetido en `webhook/router.py`, en `meta_client.py` y en
    `_numero_permitido` (workers/tasks.py). Vive aquí para que sea UNO: comparar teléfonos con
    `==` sobre la cadena cruda es el bug clásico de este dominio.
    """
    d = "".join(c for c in (s or "") if c.isdigit())
    return d[-10:] if len(d) >= 10 else d


def _usable(valor: str | None) -> str:
    """Un valor solo cuenta como teléfono si tiene 10 dígitos o más; si no, es como si no hubiera.

    No es cosmética. Desde este arreglo el número puede venir de un campo que ESCRIBE UNA PERSONA
    en el panel, y este valor decide QUIÉN es la dueña: un "0" o un "si" que casara con alguien
    dejaría a ese cliente sin respuesta para siempre. Ante la basura, `""` — que es "nadie".
    """
    v = (valor or "").strip()
    return v if len(cola(v)) == 10 else ""


async def _guardar_copia(destino: str) -> None:
    """Deja el número fuera de Postgres. Nunca lanza: es una copia de cortesía."""
    try:
        await set_cache(CLAVE_COPIA, destino, _TTL_COPIA)
    except Exception:  # noqa: BLE001 — sin Redis se sigue resolviendo por la vía normal
        pass


async def _copia_o_entorno() -> str:
    """Sin Postgres: primero la COPIA (es lo último que dijo la base) y después el ENTORNO.

    El orden importa y es al revés del que tenía `avisar_relevo_caido`: el entorno es la SEMILLA
    que se puso el día que se montó la caja y puede estar vieja o vacía; la copia es la verdad
    más reciente que llegó a existir.
    """
    try:
        copia = _usable(await get_cache(CLAVE_COPIA))
        if copia:
            return copia
    except Exception:  # noqa: BLE001 — sin Redis queda el entorno
        pass
    return _usable(get_settings().dueno_telefono)


async def telefono_de_la_duena(*, sin_base: bool = False) -> str:
    """El teléfono de la dueña. `""` = no hay ninguno configurado (⇒ NADIE es la dueña).

    `sin_base=True` es para el ÚLTIMO TESTIGO (`avisar_relevo_caido`, agent/tools.py): ese aviso
    corre JUSTO cuando Postgres se cayó, así que ni siquiera le pregunta a la base — va a la
    copia de Redis y al entorno, que son los dos sitios que sobreviven a esa caída.
    """
    global _memo_valor, _memo_hasta

    ahora = time.monotonic()

    if sin_base:
        # El memo vale, pero SOLO si trae número: un memo vacío (la base contestó que no hay
        # nada) no puede taparle la copia de Redis al aviso de emergencia.
        if _memo_valor and ahora < _memo_hasta:
            return _memo_valor
        return await _copia_o_entorno()

    if ahora < _memo_hasta:
        return _memo_valor

    valor = ""
    contesto = False
    try:
        from sqlalchemy import select

        from app.models import Configuracion
        from app.services.db import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            valor = _usable(
                (
                    await session.execute(
                        select(Configuracion.valor).where(
                            Configuracion.clave == "dueno_telefono"
                        )
                    )
                ).scalars().first()
            )
        contesto = True
    except Exception:  # noqa: BLE001 — si la base no contesta quedan la copia y el entorno
        logger.warning(
            "No se pudo leer `dueno_telefono` de la base; se usa la copia de Redis o el entorno"
        )

    if valor:
        await _guardar_copia(valor)
    elif contesto:
        # La base contestó que NO hay número: queda la semilla del entorno. La copia de Redis
        # NO se borra a propósito — si esto fue un borrado por error desde el panel, el ÚLTIMO
        # TESTIGO conserva a quién avisar el día de la caída, y ese día un aviso al número de la
        # semana pasada es mejor que el silencio total.
        valor = _usable(get_settings().dueno_telefono)
    else:
        valor = await _copia_o_entorno()

    _memo_valor, _memo_hasta = valor, ahora + _TTL_MEMO
    return valor


async def es_la_duena(telefono: str | None) -> bool:
    """¿Este número es el de la DUEÑA (y no el de un cliente)?

    ⚠️ VACÍO = NADIE ES LA DUEÑA. Es la regla que blindó `webhook/router.py:195` y no se toca:
    un `dueno_telefono` sin configurar NO puede convertir a todos los clientes en 'la dueña' y
    dejar al bot mudo con el mundo entero. Los teléfonos internos ("__simulador__", los bancos)
    no tienen dígitos, así que tampoco casan nunca.
    """
    mio = cola(await telefono_de_la_duena())
    return bool(mio) and cola(telefono) == mio

