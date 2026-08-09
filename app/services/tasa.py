"""Servicio de tasa BCV (conversion USD -> Bs).

Consulta una API JSON configurable (TASA_API_URL, p.ej. Cotizave), cachea el
valor en Redis, y SIEMPRE cae a un respaldo si la API falla: primero la clave
'tasa_manual' de la tabla configuracion, luego TASA_MANUAL_DEFAULT.

Regla de oro: el bot NUNCA se cae por culpa de la tasa. Cada paso esta
envuelto para que un fallo (API, Redis o BD) no rompa el flujo de cobro.

🔴 EL RESPALDO ERA INVISIBLE (SIL-14, arreglado el 2026-08-09). Hasta hoy la caida al respaldo
se anotaba con UN `logger.warning` dentro del contenedor y NADA MAS: ni sonda en /salud, ni
telemetria, ni marca de tiempo — el respaldo podia llevar semanas congelado y nadie lo sabria.

LO QUE SE MIDIO EN EL TALLER ESE DIA, y es el motivo entero de este bloque:
    API en vivo ................. 756,7083 Bs/$
    configuracion.tasa_manual ... 567,68      (tasa_manual_activa = 0, tasa_margen_pct = 0.0)
Un 25% POR DEBAJO. Con la API caida el bot cotiza los Pago Movil un 25% mas baratos EN SILENCIO:
el negocio cobra de menos en CADA venta y nadie lo nota. Es el camino del DINERO.

LO QUE SE AÑADIO ES **OBSERVABILIDAD, NADA MAS**: ni el valor de la tasa, ni el margen, ni
`tasa_manual`, ni la logica del cobro cambian una coma. La venta sigue saliendo aunque la API
este muerta — se DEGRADA, no se bloquea. Lo unico que cambia es que ahora se SABE:
  1. cada resolucion dice de DONDE salio (`Resolucion.origen`: cache | api | respaldo_bd | default);
  2. lo que no es cache queda escrito en `tasa_resoluciones` (migracion 033), que sobrevive al
     reinicio del proceso y a un Redis vaciado, y cruza del WORKER (que resuelve) a la API (que
     publica /salud);
  3. `/salud` lo publica y se pone en NO-ok cuando se esta cobrando con el respaldo;
  4. la duena recibe UN WhatsApp por episodio (candado `aviso_unico`), no uno por cotizacion.
"""
import asyncio
import logging
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import httpx
from sqlalchemy import select, text

from app.config import get_settings
from app.models import Configuracion
from app.services.db import get_session_factory
from app.services.redis_client import get_cache, set_cache

logger = logging.getLogger(__name__)
settings = get_settings()

CACHE_KEY = "cache:tasa:bcv"

# ─── DE DONDE SALIO LA TASA ──────────────────────────────────────────
# Los cuatro origenes posibles de la cadena. Son CONSTANTES y no cadenas sueltas porque el mismo
# nombre viaja a tres sitios: la columna `tasa_resoluciones.origen`, la sonda de /salud y el panel
# (`estado_tasa`). Un dedazo en cualquiera de ellos dejaria la sonda mirando un valor que no llega
# nunca — o sea, muda otra vez, que es justo lo que esto vino a matar.
ORIGEN_CACHE = "cache"
ORIGEN_API = "api"
ORIGEN_RESPALDO_BD = "respaldo_bd"
ORIGEN_DEFAULT = "default"
# Ni API ni respaldo: no hubo tasa ninguna y la llamada se fue en excepcion (como siempre). Se
# anota igual, porque es el peor estado posible y es el que menos rastro dejaba.
ORIGEN_SIN_TASA = "sin_tasa"
# Los dos que significan "NO se pudo hablar con la API pero SI se cobro": son los que disparan el
# aviso a la duena.
ORIGENES_DE_RESPALDO = (ORIGEN_RESPALDO_BD, ORIGEN_DEFAULT)

# EL CANDADO ANTI-SPAM DEL AVISO (mismo patron que `sin_respuesta:{telefono}` en workers/tasks.py).
# Con la API caida, CADA cotizacion cae al respaldo: sin candado, una tarde mala le manda a la
# duena un WhatsApp por venta y el aviso importante se ahoga entre veinte iguales — y un detector
# que grita se acaba ignorando (DAT-10).
#
# SEIS HORAS, y el numero esta pensado: es una jornada de trabajo. Lo unico que haria util un
# segundo aviso es que la averia SIGA, y para eso basta uno por turno de trabajo. La clave es UNA
# sola para todo el sistema (no lleva telefono) a proposito: la averia es UNA —la API de la tasa
# no responde—, no una por cliente.
CLAVE_AVISO_RESPALDO = "tasa_respaldo"
TTL_AVISO_RESPALDO = 6 * 3600

# EL TELEFONO DEL SISTEMA para la fila de la bandeja: este aviso no es de ningun cliente. Es el
# mismo que ya usa el barredor (`services/barredor.py:TEL_SISTEMA`); se repite aqui como constante
# propia en vez de importarlo para no atar este modulo —que corre en el camino del dinero— a otro
# servicio que a su vez importa de `workers.tasks`.
TEL_SISTEMA = "__sistema__"

# 🔴 LO QUE LA OBSERVABILIDAD PUEDE ROBARLE A UNA VENTA, ACOTADO (misma doctrina que
# services/telemetria.py). El apunte y el aviso corren DENTRO del turno de un cliente: si Postgres
# esta lento o Meta no contesta, esto NO puede convertirse en un cliente esperando. Tope duro, y
# si el apunte falla se APAGA SOLO un minuto para que un turno con 3 cotizaciones no pague 3
# esperas seguidas.
_TOPE_APUNTE = 2.0
_TOPE_AVISO = 10.0        # bandeja + UN POST a Meta (enviar_texto ya trae timeout=20)
_DESCANSO = 60.0
_APAGADA_HASTA: dict[str, float] = {"t": 0.0}


@dataclass(frozen=True)
class Resolucion:
    """De donde salio ESTA tasa. `valor` es lo unico que ve el cobro; el resto es el rastro."""

    valor: Decimal
    origen: str
    error: str | None = None

    @property
    def es_respaldo(self) -> bool:
        return self.origen in ORIGENES_DE_RESPALDO

# Fuente por defecto del BCV OFICIAL (Bs por USD). Devuelve {"promedio": <tasa>},
# que _parsear_tasa ya entiende. Se puede sobreescribir con la env var TASA_API_URL.
_FUENTE_BCV_DEFAULT = "https://ve.dolarapi.com/v1/dolares/oficial"


def _a_decimal(valor) -> Decimal | None:
    """Convierte a Decimal positivo, o None si no es un numero valido."""
    if valor is None:
        return None
    try:
        d = Decimal(str(valor).strip().replace(",", "."))
    except (InvalidOperation, ValueError, AttributeError):
        return None
    return d if d > 0 else None


def _parsear_tasa(payload: dict) -> Decimal:
    """Extrae la tasa BCV (Bs por USD) del JSON de la API.

    Cubre las formas mas comunes de las APIs de tasa venezolanas (Cotizave /
    BCV API). Si se adopta un endpoint con otra forma, ajustar aqui. Lanza
    ValueError si no encuentra una tasa valida.
    """
    if not isinstance(payload, dict):
        raise ValueError("la respuesta de tasa no es un objeto JSON")

    candidatos = [
        payload.get("bcv"),
        payload.get("usd"),
        payload.get("promedio"),
        payload.get("precio"),
        payload.get("rate"),
        payload.get("value"),
    ]
    # Estructuras anidadas: {"bcv": {"usd": 40.5}} o {"data": {...}}
    for sub in (payload.get("bcv"), payload.get("data"), payload.get("monitors")):
        if isinstance(sub, dict):
            candidatos += [
                sub.get("usd"), sub.get("bcv"), sub.get("price"),
                sub.get("precio"), sub.get("value"), sub.get("promedio"),
            ]

    for c in candidatos:
        tasa = _a_decimal(c)
        if tasa is not None:
            return tasa
    raise ValueError("no se encontro una tasa valida en la respuesta de la API")


async def _tasa_desde_api() -> Decimal:
    url = settings.tasa_api_url or _FUENTE_BCV_DEFAULT
    headers = {}
    if settings.tasa_api_key:
        headers["Authorization"] = f"Bearer {settings.tasa_api_key}"
    async with httpx.AsyncClient(timeout=8) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return _parsear_tasa(resp.json())


async def _tasa_de_respaldo() -> tuple[Decimal, str]:
    """Respaldo: clave 'tasa_manual' de configuracion, luego TASA_MANUAL_DEFAULT.

    Devuelve (tasa, origen) — `respaldo_bd` o `default`. La distincion NO es cosmetica: la de la
    BD la puso una persona en el panel (y puede llevar meses ahi, como los 567,68 del taller); la
    del entorno es la semilla que se escribio el dia que se monto la caja y es todavia mas vieja.
    Quien lo lee (la sonda, la telemetria y el WhatsApp a la duena) tiene que poder decir cual.
    """
    candidatos: list[tuple[str, str]] = []
    try:
        factory = get_session_factory()
        async with factory() as session:
            fila = (
                await session.execute(
                    select(Configuracion).where(Configuracion.clave == "tasa_manual")
                )
            ).scalar_one_or_none()
            if fila and fila.valor:
                candidatos.append((fila.valor, ORIGEN_RESPALDO_BD))
    except Exception as e:  # noqa: BLE001 — leer la BD nunca debe romper el cobro
        logger.warning("No se pudo leer tasa_manual de configuracion: %s", e)

    if settings.tasa_manual_default:
        candidatos.append((settings.tasa_manual_default, ORIGEN_DEFAULT))

    for v, origen in candidatos:
        tasa = _a_decimal(v)
        if tasa is not None:
            return tasa, origen
    raise ValueError(
        "no hay tasa de respaldo: configura 'tasa_manual' en la BD o TASA_MANUAL_DEFAULT"
    )


# ─── El rastro: telemetria y aviso ───────────────────────────────────

async def _escribir_apunte(origen: str, valor: Decimal | None, error: str | None) -> None:
    """El INSERT, en su propia sesion. SQL a pelo y no ORM: `tasa_resoluciones` no se lee desde
    ningun modelo (la sonda tambien la consulta con `text()`), y asi la migracion 033 no obliga a
    tocar `models.py`."""
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO tasa_resoluciones (origen, valor, error) "
                "VALUES (:origen, :valor, :error)"
            ),
            {"origen": origen, "valor": valor, "error": error},
        )
        await session.commit()


async def _anotar(origen: str, valor: Decimal | None, error: str | None = None) -> None:
    """Deja constancia de UNA resolucion que no salio de la cache. NUNCA lanza.

    🔴 ESTO NO PUEDE TUMBAR UNA VENTA, Y ESO MANDA SOBRE TODO LO DEMAS (misma regla, y por el
    mismo motivo, que `services/telemetria.py:registrar`). Corre dentro del turno de un cliente:
    se traga TODA excepcion, tiene tope de `_TOPE_APUNTE` segundos y si la base falla se apaga
    sola un minuto — sin ese fusible, con la 033 sin aplicar o Postgres lento, cada cotizacion
    del turno pagaria su espera de mas, y la observabilidad alargaria justo el turno que ya va mal.
    Si esto no se guarda, el cliente recibe su precio igual. Ese es el trato.
    """
    ahora = time.monotonic()
    if ahora < _APAGADA_HASTA["t"]:
        return
    try:
        await asyncio.wait_for(
            _escribir_apunte(origen, valor, (str(error)[:300] if error else None)),
            _TOPE_APUNTE,
        )
    except Exception as e:  # noqa: BLE001 — la observabilidad JAMAS puede tumbar una venta
        _APAGADA_HASTA["t"] = time.monotonic() + _DESCANSO
        logger.warning(
            "TASA: no se pudo anotar la resolucion '%s' (%s: %s). Se apaga %s s para no penalizar "
            "las ventas. ¿Esta aplicada la migracion 033?",
            origen, type(e).__name__, str(e)[:160], int(_DESCANSO),
        )


def _texto_del_aviso(res: Resolucion) -> str:
    """Lo que le llega a la DUEÑA por WhatsApp. Texto FIJO, cero LLM (esto tiene que salir
    tambien cuando el modelo esta muerto o sin saldo — mismo criterio que el barredor).

    LLEVA EL NUMERO A PROPOSITO, y es lo unico que hace el aviso accionable: sin el, "estoy usando
    la tasa de respaldo" no le dice a nadie si esta cobrando bien o un 25% por debajo. Este canal
    es PRIVADO (su WhatsApp); el que no lleva cifras es `/salud`, que es publico.
    """
    de_donde = (
        "la que tienes guardada en el panel"
        if res.origen == ORIGEN_RESPALDO_BD
        else "la que se dejo puesta al montar el sistema"
    )
    return (
        "⚠️ No estoy pudiendo consultar la tasa del BCV: la pagina no responde.\n"
        f"Mientras tanto estoy cobrando los pagos en bolivares a {res.valor} Bs por dolar "
        f"({de_donde}).\n"
        "Si esa tasa esta vieja, estas cobrando de MENOS en cada venta. Revisala en el panel → "
        "Configuracion → tasa, y avisale a Enova."
    )


async def _avisar_respaldo(res: Resolucion) -> None:
    """UN aviso por episodio: fila en la bandeja + WhatsApp a la duena. NUNCA lanza.

    ⚠️ EL CANDADO TAPA LAS DOS COSAS, y aqui es al reves que en `workers/tasks.py`. Alli la fila
    de la bandeja se escribe SIEMPRE (tiene que haber una por cliente, cada cliente importa) y el
    candado frena solo el WhatsApp. Aqui no hay cliente: la averia es UNA sola —la API de la tasa
    no contesta—, asi que veinte filas identicas en la bandeja serian veinte veces el mismo ruido.

    ⚠️ Y LA BANDEJA VA PRIMERO, que es la doctrina de META-15: si la ventana de 24h de la duena
    esta cerrada, `enviar_texto` LANZA y el WhatsApp no sale — el aviso solo sobrevive porque ya
    quedo escrito en el panel. Un aviso que solo existe si Meta coopera no es un aviso.

    Los imports van DENTRO a proposito (igual que en `barredor._whatsapp_duena`): asi los bancos
    pueden sustituir `meta_client.enviar_texto` por un espia y ninguna prueba manda un WhatsApp
    de verdad.
    """
    from app.services import redis_client as rc

    # 🔴 SIN CANDADO **NO SE AVISA**, y esta decision va escrita porque el descuido seria peor.
    # El candado vive en Redis: si Redis esta caido, `aviso_unico` LANZA. Y justo entonces es
    # cuando mas peligro hay — sin Redis tampoco hay cache, asi que CADA cotizacion va a la API,
    # y con la API caida CADA cotizacion cae al respaldo: avisar "por si acaso" seria un WhatsApp
    # a la duena por VENTA. Se prefiere el log (y el 503 que Redis ya provoca en /salud, que es
    # donde eso se ve) antes que veinte mensajes identicos que garantizan que el proximo aviso de
    # verdad se ignore. El aviso se cierra, no se abre.
    try:
        primero = await rc.aviso_unico(CLAVE_AVISO_RESPALDO, TTL_AVISO_RESPALDO)
    except Exception:  # noqa: BLE001
        logger.error(
            "TASA: sirviendo del respaldo (%s = %s) y SIN CANDADO (Redis no responde): el aviso "
            "a la duena NO se manda para no inundarla. Redis caido ya sale en /salud.",
            res.origen, res.valor,
        )
        return
    if not primero:
        logger.warning(
            "TASA: sirviendo del respaldo (%s = %s); el aviso a la duena NO se repite "
            "(candado '%s')", res.origen, res.valor, CLAVE_AVISO_RESPALDO,
        )
        return
    try:
        await asyncio.wait_for(_avisar_de_verdad(res), _TOPE_AVISO)
    except Exception:  # noqa: BLE001 — el aviso es lo ultimo que hay; que no tumbe la venta
        logger.exception("TASA: no se pudo avisar a la duena de que se esta usando el respaldo")


async def _avisar_de_verdad(res: Resolucion) -> None:
    from app.models import Intervencion
    from app.services.dueno import telefono_de_la_duena
    from app.services.meta_client import enviar_texto

    texto = _texto_del_aviso(res)
    logger.error("TASA: SE ESTA COBRANDO CON EL RESPALDO (%s = %s)", res.origen, res.valor)
    try:
        factory = get_session_factory()
        async with factory() as session:
            session.add(Intervencion(
                cliente_telefono=TEL_SISTEMA,
                motivo="tasa_de_respaldo",
                detalle=texto,
                mensaje_cliente="(no responde la API de la tasa BCV)",
            ))
            await session.commit()
    except Exception:  # noqa: BLE001 — sin bandeja queda el WhatsApp, que se intenta igual
        logger.exception("TASA: no se pudo dejar el aviso en la bandeja")

    destino = await telefono_de_la_duena()
    if destino:
        await enviar_texto(destino, texto)


# ─── La cadena ───────────────────────────────────────────────────────

async def _resolver_base() -> Resolucion:
    """Tasa BCV CRUDA (Bs por USD) **y de donde salio**, sin margen ni candado.

    Orden: cache Redis -> API en vivo -> tasa_manual (BD) -> TASA_MANUAL_DEFAULT. Exactamente el
    de siempre: el VALOR que devuelve esta funcion no cambia ni en un caso. Lo unico nuevo es el
    rastro que deja al pasar.

    ⚠️ EL CARRIL NORMAL NO PAGA NADA. La cache sirve la inmensa mayoria de las resoluciones y sale
    por el primer `return` sin tocar Postgres ni Redis de mas. Solo se anota cuando de verdad se
    habla con la API (una vez por `tasa_ttl`, hoy 1 hora) o cuando se cae al respaldo.
    """
    # 1) Cache
    try:
        cacheada = await get_cache(CACHE_KEY)
        tasa = _a_decimal(cacheada)
        if tasa is not None:
            return Resolucion(tasa, ORIGEN_CACHE)
    except Exception as e:  # noqa: BLE001
        logger.warning("Fallo leyendo cache de tasa: %s", e)

    # 2) API en vivo (y cachear si responde)
    try:
        tasa = await _tasa_desde_api()
        try:
            await set_cache(CACHE_KEY, str(tasa), settings.tasa_ttl)
        except Exception as e:  # noqa: BLE001
            logger.warning("Fallo guardando cache de tasa: %s", e)
        # El apunte del dato BUENO es el que le da sentido a todo lo demas: es la marca de tiempo
        # contra la que /salud mide la antiguedad. Sin filas 'api' no se puede decir "el ultimo
        # dato de verdad tiene 3 dias" — que es la frase que hay que poder decir.
        await _anotar(ORIGEN_API, tasa)
        return Resolucion(tasa, ORIGEN_API)
    except Exception as e:  # noqa: BLE001
        logger.warning("Tasa de la API no disponible (%s); usando respaldo", e)
        fallo = f"{type(e).__name__}: {e}"

    # 3) Respaldo (BD -> default). Aqui es donde el negocio empieza a cobrar de menos sin saberlo.
    try:
        tasa, origen = await _tasa_de_respaldo()
    except Exception as e:  # noqa: BLE001 — ni API ni respaldo: se anota y se relanza como antes
        await _anotar(ORIGEN_SIN_TASA, None, f"{fallo} | {type(e).__name__}: {e}")
        raise
    res = Resolucion(tasa, origen, fallo)
    await _anotar(origen, tasa, fallo)
    await _avisar_respaldo(res)
    return res


async def _tasa_base() -> Decimal:
    """Tasa BCV CRUDA (Bs por USD), sin margen ni candado. La firma de siempre, intacta."""
    return (await _resolver_base()).valor


async def _leer_ajustes_tasa() -> tuple[Decimal, Decimal | None, bool]:
    """Lee de la tabla configuracion: margen (%), tasa manual y si el candado
    manual esta activo. Si algo falla, devuelve valores neutros (sin margen,
    sin candado) para no romper el cobro."""
    margen = Decimal("0")
    manual_valor: Decimal | None = None
    manual_activa = False
    try:
        factory = get_session_factory()
        async with factory() as session:
            filas = (
                await session.execute(
                    select(Configuracion).where(
                        Configuracion.clave.in_(
                            ["tasa_margen_pct", "tasa_manual", "tasa_manual_activa"]
                        )
                    )
                )
            ).scalars().all()
            cfg = {f.clave: f.valor for f in filas}
        m = _a_decimal(cfg.get("tasa_margen_pct"))
        if m is not None:
            margen = m
        manual_valor = _a_decimal(cfg.get("tasa_manual"))
        manual_activa = (cfg.get("tasa_manual_activa") or "").strip().lower() in (
            "1", "true", "si", "sí", "on",
        )
    except Exception as e:  # noqa: BLE001 — leer ajustes nunca debe romper el cobro
        logger.warning("No se pudieron leer ajustes de tasa: %s", e)
    return margen, manual_valor, manual_activa


async def obtener_tasa_bcv() -> Decimal:
    """Tasa EFECTIVA que se le cobra al cliente (Bs por USD).

    - Si el CANDADO MANUAL esta activo: usa la tasa fijada por la duena (exacta).
    - Si no: tasa base (BCV) + el margen (%) que la duena configuro.

    Aditivo: sin margen ni candado configurados, devuelve exactamente la tasa
    base de siempre. Solo lanza si no hay ninguna fuente (mala configuracion).
    """
    margen, manual_valor, manual_activa = await _leer_ajustes_tasa()
    if manual_activa and manual_valor is not None:
        return manual_valor
    base = await _tasa_base()
    if margen > 0:
        return (base * (Decimal(1) + margen / Decimal(100))).quantize(Decimal("0.0001"))
    return base


async def estado_tasa() -> dict:
    """Para el panel: tasa base (BCV), margen, candado manual, tasa efectiva y **de donde salio**.

    `bcv_origen` es aditivo (una clave mas; el panel lee las que ya conocia y no se entera). Vale
    la pena porque esta pantalla es donde una persona mira el numero y decide: ver 567,68 no dice
    nada, ver "567,68 (respaldo_bd)" lo dice todo.
    """
    margen, manual_valor, manual_activa = await _leer_ajustes_tasa()
    base = origen = None
    try:
        resolucion = await _resolver_base()
        base, origen = resolucion.valor, resolucion.origen
    except Exception:  # noqa: BLE001
        pass
    try:
        efectiva = await obtener_tasa_bcv()
    except Exception:  # noqa: BLE001
        efectiva = None
    return {
        "bcv_base": float(base) if base is not None else None,
        "bcv_origen": origen,
        "margen_pct": float(margen),
        "manual_valor": float(manual_valor) if manual_valor is not None else None,
        "manual_activa": manual_activa,
        "tasa_efectiva": float(efectiva) if efectiva is not None else None,
    }
