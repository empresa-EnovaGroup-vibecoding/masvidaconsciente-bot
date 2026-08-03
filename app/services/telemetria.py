"""TELEMETRÍA DEL MODELO — qué modelo respondió, cuántos tokens y cuánto costó.

🔴 POR QUÉ EXISTE. OpenRouter devuelve un bloque `usage` en CADA respuesta (tokens Y costo en
dólares, ya calculado) y hasta hoy se tiraba entero: en todo el repo no había una sola mención a
`usage`, `prompt_tokens` ni `cost`. Y `modelo_ia` se cambia desde el panel SIN redeploy, así que
el modelo podía cambiar un martes y nadie podía decir después cuál contestó el lunes.

CÓMO SE ATRIBUYE EL GASTO (la decisión, y su porqué):
UNA FILA POR LLAMADA AL MODELO. Ni por turno, ni por globo enviado. Un turno gasta VARIAS
llamadas (hasta 6 vueltas del bucle + la Voz + el reintento del dinero + el fallback) y en
`mensajes` hay UNA FILA POR GLOBO: 5 contra 4, no casan. Repartir el gasto entre globos INVENTA
un número; copiarlo en cada globo lo cuenta cuatro veces al sumar. Una llamada HTTP ⇄ un bloque
`usage` ⇄ un `cost` es la única correspondencia 1:1 que existe. `turno_id` reagrupa después.

EL `turno_id` VIAJA POR CONTEXTO, NO POR PARÁMETRO. `_llamar_openrouter(messages, tools, model)`
NO PUEDE CAMBIAR DE FIRMA: es inyectable y media docena de bancos le pasan dobles con esa firma
exacta (`probar_dos_agentes` incluso la vigila). Un `ContextVar` se COPIA por Task, así que no se
filtra ni entre tareas de Celery (que comparten un solo loop, tasks.py `_run`) ni entre requests
de uvicorn: cada turno ve el suyo y nada más.

🔴 ESTO NO PUEDE TUMBAR UN TURNO, Y ESO MANDA SOBRE TODO LO DEMÁS. `registrar()` se traga TODA
excepción, tiene un tope de 2 segundos, y si la base falla SE APAGA SOLA un minuto. Ese apagado
no es un adorno: sin él, con Postgres lento, un solo turno pagaría SEIS esperas de 2 s de más —
la telemetría alargaría justo el turno que ya va mal. Si esto no se guarda, el cliente recibe su
mensaje igual. Ese es el trato.

NO SE GUARDA NI UNA LETRA DE LO QUE SE HABLÓ: solo números, modelos y el texto del error. El
contenido ya vive en `mensajes` y en el historial de Redis; duplicarlo aquí sería engordar por
nada la tabla que más rápido crece.
"""
import asyncio
import contextvars
import logging
import time
from decimal import Decimal
from uuid import uuid4

logger = logging.getLogger(__name__)

# EL TURNO EN CURSO. ContextVar y no una variable global: el worker de Celery reusa UN solo loop
# por proceso (tasks.py `_run`), así que una global se pegaría del turno de un cliente al del
# siguiente y el gasto acabaría cargado al cliente equivocado.
_TURNO: contextvars.ContextVar[dict | None] = contextvars.ContextVar("turno_ia", default=None)

# EL FUSIBLE. Si un apunte falla (Postgres caído, la 032 sin aplicar), se deja de intentar durante
# un minuto. En memoria del proceso a propósito: tiene que funcionar justo cuando la base no está.
_APAGADA_HASTA: dict[str, float] = {"t": 0.0}
_DESCANSO = 60.0
# Lo máximo que la telemetría puede robarle a un turno. Un INSERT sano tarda milisegundos.
# ⚠️ El fusible solo salta si algo LANZA (incluido el TimeoutError de `wait_for`). Un Postgres
# lento-pero-vivo (1,9 s por INSERT) NO lo dispara, y un turno de 6 llamadas pagaría ~11 s de
# más. Se acepta a sabiendas: es el precio de no perder el apunte de la ÚLTIMA llamada del turno,
# que es la que más falta hace. Si algún día se ve, se baja este número — no se cambia el diseño.
_TOPE_SEGUNDOS = 2.0


def abrir_turno(telefono: str | None, carril: str) -> str:
    """Marca el principio de un turno y devuelve su id. NO PISA uno ya abierto.

    Que no pise es lo que hace que una nota de voz salga con UN solo turno_id: `_procesar_audio`
    lo abre (carril 'audio'), la transcripción y las 6 vueltas del bucle que vienen detrás caen
    dentro del mismo, y el `paso` distingue quién gastó qué. Igual con el comprobante: la visión
    y el mensaje que la Voz le escribe al cliente son el MISMO turno.
    """
    abierto = _TURNO.get()
    if abierto:
        return str(abierto["id"])
    turno = {"id": uuid4().hex[:16], "telefono": (telefono or None), "carril": carril}
    _TURNO.set(turno)
    return str(turno["id"])


def _contexto() -> tuple[str | None, str | None, str]:
    """(turno_id, telefono, carril) del turno en curso, o el hueco si nadie lo abrió.

    'sin_turno' NO es un error: los embeddings del panel y los del arranque no pertenecen a
    ningún turno de ningún cliente, y aun así cuestan dinero y tienen que contarse.
    """
    t = _TURNO.get()
    if not t:
        return None, None, "sin_turno"
    return t.get("id"), t.get("telefono"), str(t.get("carril") or "sin_turno")


def _tomar_usage(datos: dict | None) -> dict:
    """Saca los números del bloque `usage`. Verificado contra la respuesta REAL (2026-08-03).

    OpenRouter manda `usage` SIEMPRE, sin pedirlo (el viejo `usage: {include: true}` está
    deprecado y no hace nada), y `cost` viene YA EN DÓLARES: no hay que buscar el precio del
    modelo en ningún sitio, ni recalcular nada cuando la proveedora cambia de modelo. Si algún día
    no viniera, `costo_usd` queda en NULL — que no es lo mismo que cero.

    `bool` es subclase de `int` en Python: se excluye a mano para que un `cost: true` de un
    proveedor raro no se anote como "un dólar".
    """
    d = datos or {}
    u = d.get("usage") or {}
    detalle = u.get("prompt_tokens_details") or {}
    costo = u.get("cost")
    return {
        "modelo_real": d.get("model"),
        "proveedor": d.get("provider"),
        "tokens_entrada": int(u.get("prompt_tokens") or 0),
        "tokens_salida": int(u.get("completion_tokens") or 0),
        "tokens_cache": int(detalle.get("cached_tokens") or 0),
        "costo_usd": (
            Decimal(str(costo))
            if isinstance(costo, int | float) and not isinstance(costo, bool)
            else None
        ),
    }


async def _escribir(fila: dict) -> None:
    """El INSERT, en su propia sesión y sin tocar nada más. Import perezoso: importar este módulo
    no puede exigir que la base esté disponible (mismo criterio que services/db.py)."""
    from app.models import LlamadaIA
    from app.services.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        session.add(LlamadaIA(**fila))
        await session.commit()


async def registrar(
    *,
    paso: str,
    modelo_pedido: str | None,
    t0: float,
    datos: dict | None = None,
    ok: bool = True,
    error: str | None = None,
) -> None:
    """Anota UNA llamada al modelo. NUNCA lanza. NUNCA bloquea más de `_TOPE_SEGUNDOS`.

    Se hace con `await` y no con `create_task` a propósito: el worker cierra el turno y sigue con
    la siguiente tarea, así que un apunte lanzado al aire se perdería EN SILENCIO justo en la
    última llamada de cada turno — que es la que más falta hace. El coste de esperar está acotado
    por el tope y por el fusible.
    """
    ahora = time.monotonic()
    if ahora < _APAGADA_HASTA["t"]:
        return
    try:
        turno_id, telefono, carril = _contexto()
        fila = {
            "turno_id": turno_id,
            "cliente_telefono": telefono,
            "carril": carril,
            "paso": paso,
            "modelo_pedido": modelo_pedido,
            "ms": int((ahora - t0) * 1000),
            "ok": ok,
            "error": str(error)[:300] if error else None,
            **_tomar_usage(datos),
        }
        await asyncio.wait_for(_escribir(fila), _TOPE_SEGUNDOS)
    except Exception as e:  # noqa: BLE001 — la telemetría JAMÁS puede tumbar un turno
        _APAGADA_HASTA["t"] = time.monotonic() + _DESCANSO
        logger.warning(
            "TELEMETRÍA: no se pudo anotar la llamada (%s: %s). Se apaga %s s para no penalizar "
            "los turnos. ¿Está aplicada la migración 032?",
            type(e).__name__, str(e)[:160], int(_DESCANSO),
        )
