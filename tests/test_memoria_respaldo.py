"""EL BOT YA NO OLVIDA A LAS 24 H: el historial se rescata de Postgres (`services/memoria.py`).

Nace del fallo que reportó Maired el 2026-08-18 y de la causa raíz hallada el 08-20: el historial
vivía SOLO en Redis con TTL de 24 h, y pasadas esas horas el bot arrancaba de cero — con cuatro
redes de seguridad ciegas de paso.

🔴 **MÁS DE LA MITAD DE ESTOS TESTS SON CASOS QUE NO DEBEN RESCATAR.** Una memoria que trae lo que
no debe es peor que no tener memoria: recordar un globo FALLIDO hace que el bot dé por dichos unos
datos bancarios que el cliente nunca recibió (SIL-8), y desenterrar un pedido de hace dos meses lo
hace tratarlo como vivo. El bug al revés también es un bug.
"""
import pytest

from app.services import memoria as mem

pytestmark = pytest.mark.asyncio

TEL = "584264399792"

# El turno REAL que destapó el fallo (tabla `mensajes`, ids 4034-4036 y 4038).
FILAS_REALES = [
    ("user", "de repente tengas empanadas de plátano"),
    ("assistant", "Perfecto, tengo Empanadas de masa de plátano con relleno de carne "
                  "mechada, pollo o queso de cabra. Vienen en paquete de 8 unidades."),
    ("assistant", "De cuál relleno te gustaría?"),
]


# ══════════════════════════════════════════════════════════════════════════════════
# Dobles de Postgres y Redis
# ══════════════════════════════════════════════════════════════════════════════════

class _Resultado:
    def __init__(self, filas):
        self._filas = filas

    def all(self):
        return self._filas


class _Sesion:
    def __init__(self, filas, espia):
        self._filas, self._espia = filas, espia

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _stmt):
        self._espia.append("consulta")
        return _Resultado(self._filas)


def _falsear_postgres(monkeypatch, filas, espia=None):
    """Devuelve la lista-espía de consultas. Se CUENTAN los intentos, no solo se mira el
    resultado: la lección del 2026-08-09 (un test que solo mira el retorno pasa con el código
    roto, porque el `except` del envoltorio devuelve lo mismo)."""
    espia = espia if espia is not None else []
    monkeypatch.setattr(mem, "get_session_factory", lambda: (lambda: _Sesion(filas, espia)))
    return espia


def _falsear_redis(monkeypatch, vivo, *, sembrado=None, explota_al_sembrar=False):
    async def _obtener(_tel):
        return list(vivo)

    async def _sembrar(_tel, mensajes):
        if explota_al_sembrar:
            raise RuntimeError("Redis no responde")
        if sembrado is not None:
            sembrado.extend(mensajes)

    monkeypatch.setattr(mem, "obtener_historial", _obtener)
    monkeypatch.setattr(mem, "sembrar_historial", _sembrar)


# ══════════════════════════════════════════════════════════════════════════════════
# SÍ RESCATA (el arreglo)
# ══════════════════════════════════════════════════════════════════════════════════

async def test_redis_vacio_rescata_de_postgres_en_orden(monkeypatch):
    _falsear_postgres(monkeypatch, list(reversed(FILAS_REALES)))  # la consulta va DESC
    _falsear_redis(monkeypatch, [])
    hist = await mem.historial_con_respaldo(TEL)
    assert [h["content"] for h in hist] == [c for _, c in FILAS_REALES]
    assert hist[0]["role"] == "user"


async def test_el_caso_del_08_18_el_bot_vuelve_a_ver_las_empanadas(monkeypatch):
    """La prueba que importa: con la memoria rescatada, el ofrecimiento de empanadas vuelve a
    estar delante del modelo, así que 'De queso de cabra' ya no es un mensaje huérfano."""
    _falsear_postgres(monkeypatch, list(reversed(FILAS_REALES)))
    _falsear_redis(monkeypatch, [])
    hist = await mem.historial_con_respaldo(TEL)
    ultimo_del_bot = next(h["content"] for h in reversed(hist) if h["role"] == "assistant")
    assert "relleno" in ultimo_del_bot.lower()
    assert any("empanadas" in h["content"].lower() for h in hist)


async def test_la_red_del_pitch_vuelve_a_VER_con_la_memoria_rescatada(monkeypatch):
    """🔴 El corazón del arreglo. `_elige_entre_opciones` es la RED DEL PITCH del 08-08, hecha
    para 'el cliente elige y el bot confirma pelado'. Con el historial expirado devolvía False y
    no protegía nada. Con el rescatado, vuelve a disparar."""
    from app.agent.agent import _elige_entre_opciones

    _falsear_postgres(monkeypatch, list(reversed(FILAS_REALES)))
    _falsear_redis(monkeypatch, [])
    hist = await mem.historial_con_respaldo(TEL)
    assert _elige_entre_opciones("De queso de cabra", hist) is True
    assert _elige_entre_opciones("De queso de cabra", []) is False  # el bug de antes


async def test_el_bot_ya_no_cree_que_es_el_primer_contacto(monkeypatch):
    """`_es_inicio_conversacion` es la otra red ciega: con historial vacío daba True y por eso
    el bot saludaba como si nunca hubiera hablado con esa persona."""
    from app.agent.agent import _es_inicio_conversacion

    _falsear_postgres(monkeypatch, list(reversed(FILAS_REALES)))
    _falsear_redis(monkeypatch, [])
    hist = await mem.historial_con_respaldo(TEL)
    assert _es_inicio_conversacion(hist) is False
    assert _es_inicio_conversacion([]) is True  # el bug de antes


async def test_sembrar_deja_lo_rescatado_en_redis(monkeypatch):
    """Sin sembrar, el turno SIGUIENTE encuentra en Redis solo los 2 mensajes de este turno,
    no vuelve a rescatar, y el bot olvida otra vez. Sembrar es corrección, no rendimiento."""
    sembrado = []
    _falsear_postgres(monkeypatch, list(reversed(FILAS_REALES)))
    _falsear_redis(monkeypatch, [], sembrado=sembrado)
    await mem.historial_con_respaldo(TEL, sembrar=True)
    assert [m["content"] for m in sembrado] == [c for _, c in FILAS_REALES]


async def test_el_eco_de_la_duena_entra_como_assistant(monkeypatch):
    """En Postgres el eco de la dueña es rol 'owner'; en Redis el bot lo HEREDA como 'assistant'
    (una sola voz ante el cliente). El rescate tiene que hacer lo mismo: mandar 'owner' al LLM
    sería un rol que no conoce, y omitirlo dejaría un hueco donde alguien sí habló."""
    _falsear_postgres(monkeypatch, [("owner", "Te lo confirmo yo en un rato")])
    _falsear_redis(monkeypatch, [])
    hist = await mem.historial_con_respaldo(TEL)
    assert hist == [{"role": "assistant", "content": "Te lo confirmo yo en un rato"}]


async def test_trae_los_ULTIMOS_no_los_primeros(monkeypatch):
    """La consulta va DESC + LIMIT y se invierte: el tope tiene que recortar la conversación
    VIEJA, no la reciente."""
    from app.services.redis_client import MAX_TURNOS_HISTORIAL

    muchas = [("user", f"m{i}") for i in range(MAX_TURNOS_HISTORIAL + 10)]
    _falsear_postgres(monkeypatch, list(reversed(muchas))[:MAX_TURNOS_HISTORIAL])
    _falsear_redis(monkeypatch, [])
    hist = await mem.historial_con_respaldo(TEL)
    assert len(hist) == MAX_TURNOS_HISTORIAL
    assert hist[-1]["content"] == "m29"  # el más reciente sobrevive


# ══════════════════════════════════════════════════════════════════════════════════
# NO RESCATA / NO TOCA — los controles (más de la mitad del banco)
# ══════════════════════════════════════════════════════════════════════════════════

async def test_si_redis_tiene_memoria_POSTGRES_NI_SE_TOCA(monkeypatch):
    """Redis es la fuente VIVA: si tiene algo, manda. Y no se paga una consulta de más en el
    99% de los turnos — que es el carril normal."""
    espia = _falsear_postgres(monkeypatch, list(reversed(FILAS_REALES)))
    vivo = [{"role": "user", "content": "hola"}]
    _falsear_redis(monkeypatch, vivo)
    assert await mem.historial_con_respaldo(TEL) == vivo
    assert espia == [], "con Redis vivo la BD no se consulta"


async def test_los_telefonos_internos_no_rescatan_NI_CONSULTAN(monkeypatch):
    """`__simulador__` y `__prueba_*` no son un WhatsApp real, no sufren el problema de las 24 h,
    y los bancos dependen de arrancar con la memoria limpia."""
    for tel in ("__simulador__", "__simulador__smoke4", "__prueba_dinero__"):
        espia = _falsear_postgres(monkeypatch, list(reversed(FILAS_REALES)))
        _falsear_redis(monkeypatch, [])
        assert await mem.historial_con_respaldo(tel) == []
        assert espia == [], f"{tel} no debería consultar la BD"


async def test_un_telefono_vacio_no_consulta(monkeypatch):
    espia = _falsear_postgres(monkeypatch, list(reversed(FILAS_REALES)))
    _falsear_redis(monkeypatch, [])
    assert await mem.historial_con_respaldo("") == []
    assert espia == []


async def test_postgres_caido_devuelve_vacio_y_NO_LANZA(monkeypatch):
    """Degradar, nunca bloquear (L16). El respaldo es una MEJORA: si falla, el turno sigue
    exactamente como antes del arreglo. Una excepción aquí mataría la venta."""
    def _explota():
        raise RuntimeError("Postgres no responde")

    monkeypatch.setattr(mem, "get_session_factory", _explota)
    _falsear_redis(monkeypatch, [])
    assert await mem.historial_con_respaldo(TEL, sembrar=True) == []


async def test_si_redis_falla_al_sembrar_el_turno_SIGUE(monkeypatch):
    """La siembra es un extra: que Redis la rechace no puede costar el historial que ya se
    rescató, ni el turno."""
    _falsear_postgres(monkeypatch, list(reversed(FILAS_REALES)))
    _falsear_redis(monkeypatch, [], explota_al_sembrar=True)
    hist = await mem.historial_con_respaldo(TEL, sembrar=True)
    assert len(hist) == 3


async def test_sin_sembrar_no_se_escribe_en_redis(monkeypatch):
    """Los carriles sin lock (comprobante, confirmar pago) solo LEEN: sembrar sin lock podría
    duplicar el historial."""
    sembrado = []
    _falsear_postgres(monkeypatch, list(reversed(FILAS_REALES)))
    _falsear_redis(monkeypatch, [], sembrado=sembrado)
    await mem.historial_con_respaldo(TEL)
    assert sembrado == []


async def test_postgres_sin_nada_devuelve_vacio(monkeypatch):
    """Cliente nuevo de verdad: no hay nada que rescatar y el bot SÍ debe saludar como primera vez."""
    sembrado = []
    _falsear_postgres(monkeypatch, [])
    _falsear_redis(monkeypatch, [], sembrado=sembrado)
    assert await mem.historial_con_respaldo(TEL, sembrar=True) == []
    assert sembrado == [], "no se siembra una lista vacía"


# ── Los filtros de la consulta: que estén DE VERDAD en el SQL ──────────────────────

def _sql_de_la_consulta(monkeypatch):
    """Captura el SQL compilado para comprobar que los filtros existen. Un test que solo
    mirase filas falsas pasaría con los filtros borrados: las filas las pone el propio test."""
    capturado = {}

    class _SesionQueCompila(_Sesion):
        async def execute(self, stmt):
            capturado["sql"] = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            return _Resultado([])

    monkeypatch.setattr(
        mem, "get_session_factory", lambda: (lambda: _SesionQueCompila([], []))
    )
    return capturado


async def test_SIL8_los_globos_fallidos_quedan_FUERA(monkeypatch):
    """🔴 SIL-8. Postgres guarda los globos fallidos a propósito (rojo en el panel), pero el bot
    no puede recordar haber dicho lo que el cliente nunca recibió: si falló justo el globo con la
    cuenta y la cédula, lo daría por dicho y no lo repetiría nunca."""
    sql = _sql_de_la_consulta(monkeypatch)
    _falsear_redis(monkeypatch, [])
    await mem.historial_con_respaldo(TEL)
    assert "fallido" in sql["sql"]


async def test_no_se_filtra_por_enviado_porque_tiraria_los_entregados(monkeypatch):
    """El complemento del anterior, y el bug que casi se cuela: los mensajes que SÍ llegaron
    pasan a 'entregado'/'leido' cuando Meta avisa. Filtrar por `estado = 'enviado'` habría
    tirado la mayor parte del historial bueno (hoy en el taller: 29 'entregado' vs 25 'enviado')."""
    sql = _sql_de_la_consulta(monkeypatch)
    _falsear_redis(monkeypatch, [])
    await mem.historial_con_respaldo(TEL)
    assert "'enviado'" not in sql["sql"]


async def test_la_media_queda_FUERA(monkeypatch):
    """La media nunca entró al historial de Redis (decisión del 08-08). Meter las filas
    '(foto de X)' le enseñaría al bot un formato que su memoria viva no tiene."""
    sql = _sql_de_la_consulta(monkeypatch)
    _falsear_redis(monkeypatch, [])
    await mem.historial_con_respaldo(TEL)
    assert "'text'" in sql["sql"]


async def test_hay_ventana_de_dias(monkeypatch):
    """Sin ventana el bot desentierra un pedido de hace meses y lo trata como vivo."""
    sql = _sql_de_la_consulta(monkeypatch)
    _falsear_redis(monkeypatch, [])
    await mem.historial_con_respaldo(TEL)
    assert "created_at" in sql["sql"]


async def test_el_contenido_vacio_queda_fuera(monkeypatch):
    sql = _sql_de_la_consulta(monkeypatch)
    _falsear_redis(monkeypatch, [])
    await mem.historial_con_respaldo(TEL)
    assert "contenido" in sql["sql"]


# ══════════════════════════════════════════════════════════════════════════════════
# `sembrar_historial` DE VERDAD (sin mockearla)
#
# 🔴 Estos tres tests los pidió la REVERSIÓN, no el diseño: al anular la guarda anti-duplicado
# de `sembrar_historial` la suite entera siguió VERDE, porque todos los tests de arriba mockean
# esa función y su lógica real no se ejecutaba nunca. Un test que no puede ponerse rojo no
# prueba nada (L4/L15).
# ══════════════════════════════════════════════════════════════════════════════════

class _RedisFalso:
    """Lo justo de Redis para esta función: una lista, `exists`, y un pipeline que se acumula."""

    def __init__(self, ya_hay=None):
        self.datos = list(ya_hay or [])
        self.ltrim_pedido = None
        self.expire_pedido = None
        self._cola = []

    async def exists(self, _clave):
        return 1 if self.datos else 0

    def pipeline(self, transaction=False):  # noqa: ARG002 — la firma real la lleva
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def rpush(self, _clave, *vals):
        self._cola.append(("rpush", vals))

    def ltrim(self, _clave, ini, fin):
        self._cola.append(("ltrim", (ini, fin)))

    def expire(self, _clave, ttl):
        self._cola.append(("expire", ttl))

    async def execute(self):
        for op, arg in self._cola:
            if op == "rpush":
                self.datos.extend(arg)
            elif op == "ltrim":
                self.ltrim_pedido = arg
            elif op == "expire":
                self.expire_pedido = arg
        self._cola = []


async def test_sembrar_escribe_recorta_y_pone_TTL(monkeypatch):
    from app.services import redis_client as rcm

    falso = _RedisFalso()
    monkeypatch.setattr(rcm, "_client", lambda: falso)
    await rcm.sembrar_historial(TEL, [{"role": "user", "content": "hola"}])
    assert len(falso.datos) == 1
    # Lo sembrado NO es un ciudadano de segunda: envejece y se recorta igual que lo vivo.
    assert falso.ltrim_pedido == (-rcm.MAX_TURNOS_HISTORIAL, -1)
    assert falso.expire_pedido == rcm.settings.conversacion_ttl


async def test_sembrar_NO_PISA_lo_que_ya_hay(monkeypatch):
    """La guarda anti-duplicado. Sembrar encima metería los mismos mensajes dos veces en la
    memoria del agente — peor que no sembrar, porque el bot leería el turno duplicado."""
    from app.services import redis_client as rcm

    falso = _RedisFalso(ya_hay=['{"role": "user", "content": "ya estaba"}'])
    monkeypatch.setattr(rcm, "_client", lambda: falso)
    await rcm.sembrar_historial(TEL, [{"role": "user", "content": "nuevo"}])
    assert falso.datos == ['{"role": "user", "content": "ya estaba"}']


async def test_sembrar_una_lista_vacia_no_toca_redis(monkeypatch):
    from app.services import redis_client as rcm

    falso = _RedisFalso()
    monkeypatch.setattr(rcm, "_client", lambda: falso)
    await rcm.sembrar_historial(TEL, [])
    assert falso.datos == []
    assert falso.expire_pedido is None


# ══════════════════════════════════════════════════════════════════════════════════
# EL ORDEN DEL HILO EN EL PANEL (`_guardar_en_panel(ts_usuario=...)`)
#
# Bug del mismo incidente del 08-18: la media se escribe DURANTE el turno (al enviarla) y el
# resto al FINAL, así que en el hilo —que ordena por `created_at`— la foto salía ANTES de la
# pregunta que la provocó. La dueña leía el turno al revés.
# ══════════════════════════════════════════════════════════════════════════════════

class _SesionQueApunta:
    """Recoge los objetos `Mensaje` que se añaden, para mirarles el `created_at`."""

    def __init__(self, añadidos):
        self.añadidos = añadidos

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _stmt):
        class _R:
            def scalar_one_or_none(self):
                return None
        return _R()

    def add(self, obj):
        self.añadidos.append(obj)

    async def commit(self):
        return None


async def _guardar_con(monkeypatch, ts):
    from app.workers import tasks as tk

    añadidos = []
    # 🔴 Se parchea `app.services.db`, NO el namespace de `tasks`: `_guardar_en_panel` re-importa
    # `get_session_factory` DENTRO de la función, y ese import local sombrea el global del módulo.
    # Parchear `tk` dejaba pasar la llamada a Postgres de verdad (los tests se iban a buscar
    # localhost:5432 y fallaban por conexión, no por la aserción).
    monkeypatch.setattr(
        "app.services.db.get_session_factory", lambda: (lambda: _SesionQueApunta(añadidos))
    )

    async def _ok(fn, *_a, **_k):
        await fn()
        return True

    monkeypatch.setattr(tk, "_escribir_en_panel", _ok)
    await tk._guardar_en_panel(
        TEL, "Enova", "De queso de cabra. Cuanto es?",
        [{"texto": "son $8", "estado": "enviado"}], ts_usuario=ts,
    )
    return [o for o in añadidos if getattr(o, "rol", None) == "user"]


async def test_el_mensaje_del_cliente_lleva_la_hora_en_que_ESCRIBIO(monkeypatch):
    from datetime import UTC, datetime

    cuando = datetime(2026, 8, 18, 15, 4, 1, tzinfo=UTC)
    filas = await _guardar_con(monkeypatch, cuando)
    assert filas and filas[0].created_at == cuando, (
        "sin esto la pregunta se fecha al cerrar el turno y la foto (enviada antes) la adelanta"
    )


async def test_sin_ts_se_deja_el_default_de_siempre(monkeypatch):
    """Los carriles que no pasan `ts_usuario` (comprobante, confirmar pago) no cambian de
    comportamiento: el `default=now_utc` del modelo sigue mandando. Aditivo."""
    filas = await _guardar_con(monkeypatch, None)
    assert filas and filas[0].created_at is None  # lo rellena el default en el INSERT
