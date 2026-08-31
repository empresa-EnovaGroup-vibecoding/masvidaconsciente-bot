"""LA COLA DE LA MEDIA — el texto sale PRIMERO, la foto DESPUÉS.

🔴 POR QUÉ EXISTE (lo reportó Erwin el 2026-08-21, viendo un turno real):

    *"Una persona real responde breve. Y saluda primero antes de enviar imágenes.
      Pero en este caso saluda después de enviar varias imágenes."*

Y tenía razón: era **estructural, no un despiste del modelo**. El orden que corría era

    agent.py::responder()
        · el modelo llama `enviar_fotos_producto` en el bucle de tools  → enviar_imagen() SALE YA
        · o la RED DE LA FOTO la llama al final (agent.py:2024)         → enviar_imagen() SALE YA
        · `_asegurar_saludo` (agent.py:2052-2058) solo construye un STRING
        · return texto
    tasks.py::_procesar()
        · _enviar_en_partes(telefono, texto) → enviar_texto()           → SALE DESPUÉS

O sea: **en CUALQUIER turno con fotos, la media le llegaba al cliente antes que el texto** — y
con ella el saludo, que va dentro del texto. Reproducido antes de tocar nada: 3 imágenes y en la
posición 4 el *"Hola, Ana, buenas noches"*.

## Lo que hace Whuilianny (los documentos de las 42 conversaciones reales)

Ella ANUNCIA y DESPUÉS MUESTRA, sin una sola excepción en la muestra:

    [00:54] "Hola carlos buenas noches bendiciones."              ← texto
    [00:54] "Por aquí te dejo nuestro catálogo. Por aquí a la orden."  ← texto
    [00:54] (documento)                                            ← media

Este módulo hace que el bot no pueda hacerlo al revés.

## El mecanismo

Un `ContextVar` con una lista de envíos pendientes. Mientras la cola está ABIERTA, las tools de
media no llaman a Meta: **encolan**. `tasks.py` manda el texto y recién entonces la vacía.

Se muta la LISTA (nunca se rebindea la variable dentro del turno), así que si algo lanza una
`asyncio.Task` hija —que copia el contexto por valor— los `append` de la hija se ven igual desde
el padre. Sin la cola abierta, `encolar()` devuelve False y **quien llama envía como siempre**:
los carriles que no pasan por `tasks.py` (el worker de visión, los avisos a la dueña) no cambian
de comportamiento ni por error.

## El regalo que vino de arriba

Si la dueña toma el chat mientras el bot piensa, `_enviar_en_partes` no manda nada — y hasta hoy
**las fotos ya habían salido**: el cliente recibía 3 imágenes huérfanas, sin una línea de texto,
justo cuando una persona acababa de entrar a atenderlo. Ahora se descartan con el texto.
"""
import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

logger = logging.getLogger(__name__)

# Cada entrada: (descripcion_para_el_log, corutina_sin_argumentos)
_Pendiente = tuple[str, Callable[[], Awaitable[None]]]

# None = la cola está CERRADA ⇒ quien llama envía en el momento, como siempre.
_COLA: ContextVar[list[_Pendiente] | None] = ContextVar("cola_media", default=None)


def abrir() -> None:
    """Empieza a diferir la media de ESTE turno. Idempotente a propósito: si ya hay una cola
    abierta (un carril anidado), se respeta la de fuera — la que sabe cuándo sale el texto."""
    if _COLA.get() is None:
        _COLA.set([])


def cerrar() -> None:
    """Cierra la cola. Si quedó algo dentro es un BUG del que llama (abrió y no vació ni
    descartó): se avisa fuerte y se tira, porque mandarlo aquí sería mandarlo fuera de orden."""
    pendientes = _COLA.get()
    if pendientes:
        logger.error(
            "COLA DE MEDIA: se cerró con %d envío(s) dentro sin vaciar ni descartar — %s. "
            "El cliente NO los recibió.",
            len(pendientes), [d for d, _ in pendientes],
        )
    _COLA.set(None)


def activa() -> bool:
    """True si la media de este turno se está difiriendo."""
    return _COLA.get() is not None


def cuantos() -> int:
    """Cuántos archivos esperan salir. Para que quien llama pueda decidir y para los logs."""
    return len(_COLA.get() or [])


def encolar(descripcion: str, envio: Callable[[], Awaitable[None]]) -> bool:
    """Deja un envío para DESPUÉS del texto.

    Devuelve True si quedó encolado y **False si la cola está cerrada** — en ese caso quien
    llama tiene que enviarlo él mismo, en el momento, como se hacía siempre. Ese False no es un
    error: es el camino de los carriles que no mandan texto después (visión, avisos a la dueña).
    """
    pendientes = _COLA.get()
    if pendientes is None:
        return False
    pendientes.append((descripcion, envio))
    return True


def ya_encolada(descripcion: str) -> bool:
    """True si un envío con EXACTAMENTE esta descripción ya espera en la cola de este turno.

    Existe para el candado intra-turno de `enviar_fotos_producto` (rama fotos-con-memoria): la
    fila de `mensajes` que deja `_guardar_media_saliente` se escribe recién al VACIAR la cola
    —después del texto—, así que si el modelo llama la herramienta dos veces en el MISMO turno,
    la BD todavía no sabe nada del primer envío. La única que ya lo sabe es esta cola.
    Con la cola cerrada devuelve False: en ese carril el envío fue inmediato y la fila ya existe.
    """
    return any(d == descripcion for d, _ in (_COLA.get() or []))


async def vaciar() -> int:
    """Manda lo encolado, EN ORDEN, y devuelve cuántos salieron.

    Cada envío va en su propio `try`: si Meta rechaza la primera foto, las demás se intentan
    igual (mismo criterio que el bucle original de `enviar_fotos_producto`). La media es un
    empujón de venta y **jamás puede tumbar el turno**: el texto ya salió y es lo que importa.
    """
    pendientes = _COLA.get()
    if not pendientes:
        return 0
    # Se vacía la lista ANTES de enviar: si algo revienta a mitad, `cerrar()` no vuelve a
    # avisar de lo que ya se intentó ni nadie puede mandarlo dos veces.
    tanda, pendientes[:] = list(pendientes), []
    salieron = 0
    for descripcion, envio in tanda:
        try:
            await envio()
            salieron += 1
        except Exception:  # noqa: BLE001 — el texto ya salió; una foto no rompe el turno
            logger.exception("COLA DE MEDIA: falló el envío diferido de %s", descripcion)
    logger.info("COLA DE MEDIA: salieron %d de %d archivo(s) tras el texto", salieron, len(tanda))
    return salieron


def descartar(motivo: str) -> int:
    """Tira lo encolado SIN enviarlo y devuelve cuántos se tiraron.

    El caso real: la dueña tomó el chat mientras el bot pensaba. El texto no sale, así que la
    media tampoco debe salir — antes de esta cola ya había salido, y el cliente se quedaba con
    fotos huérfanas encima de la conversación que una persona acababa de tomar.
    """
    pendientes = _COLA.get()
    if not pendientes:
        return 0
    cuantos_habia = len(pendientes)
    logger.info(
        "COLA DE MEDIA: descartados %d archivo(s) sin enviar (%s) — %s",
        cuantos_habia, motivo, [d for d, _ in pendientes],
    )
    pendientes[:] = []
    return cuantos_habia
