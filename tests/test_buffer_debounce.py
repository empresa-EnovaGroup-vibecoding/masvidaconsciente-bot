"""EL DEBOUNCE DEL BUFFER — la ráfaga del cliente se contesta UNA vez, no a trozos.

🔴 EL CASO REAL que lo motivó (taller, 2026-08-08, tel …9792; logs del worker + tabla `mensajes`):
la ventana de 15s estaba anclada al PRIMER mensaje y no se reiniciaba, así que una tarea vieja
barría los mensajes recién llegados.

    22:25:40  "Como son las que tienes? Variada."   → tarea para 22:25:55
    22:25:47  "Tienes tortas?"                      → tarea para 22:26:02
    22:25:55  la 1ª tarea vacía el buffer: consolida esos DOS ✅   (mensajes.id 4009)
    22:25:57  "De chocolate"
    22:26:02  la 2ª tarea se lo lleva con solo 5s de espera ❌     (id 4013 + respuesta 4014)

Tres mensajes en ráfaga, DOS respuestas: el cliente ve al bot contestándole a trozos.

⚠️ EL EQUILIBRIO ES TODO, igual que en la red del bucle: esperar de MENOS deja el bug; esperar de
MÁS es peor, porque el cliente que solo escribió "hola" se queda mirando el chat. Por eso la mitad
de este archivo son los casos que **NO** deben esperar — incluido el que jamás puede fallar: el
cliente que escribe sin parar (el TOPE) y el buffer sin marca de tiempo (procesar YA).

Sin un solo `sleep`: el reloj (`rc._ahora`) se inyecta y la línea de tiempo se simula entera.
"""

import json

import pytest

from app.config import get_settings
from app.services import redis_client as rc
from app.workers import tasks

settings = get_settings()

TEL = "584264399792"  # el teléfono real del incidente
NOMBRE = "Clienta"

# Los instantes del incidente, en segundos desde las 22:25:00 (para que se lean igual que arriba).
T_MSG1, T_MSG2, T_MSG3 = 40.0, 47.0, 57.0


# ══════════════════════════════════════════════════════════════════════════════════
# Andamios: un reloj inyectable y el mínimo de Redis que toca el buffer
# ══════════════════════════════════════════════════════════════════════════════════

class Reloj:
    """El tiempo, a mano. Nada de `sleep`: los tests corren en microsegundos."""

    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class PipelineDeMentira:
    """Encola los comandos y los ejecuta en orden, como MULTI/EXEC."""

    def __init__(self, servidor):
        self.servidor = servidor
        self.cola: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def __getattr__(self, nombre):
        def encolar(*args, **kwargs):
            self.cola.append((nombre, args, kwargs))
            return self
        return encolar

    async def execute(self):
        resultados = []
        for nombre, args, kwargs in self.cola:
            resultados.append(await getattr(self.servidor, nombre)(*args, **kwargs))
        self.cola.clear()
        return resultados


class RedisDeMentira:
    """Lo justo de Redis para que corra el código REAL de `redis_client` (listas, hashes, TTL).

    Que corra el código de verdad es el punto: si mañana alguien le quita las marcas a
    `agregar_a_buffer`, estos tests se ponen rojos. Contra un doble que reimplemente las marcas,
    no se pondrían.
    """

    def __init__(self):
        self.listas: dict[str, list[str]] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.cadenas: dict[str, str] = {}

    def pipeline(self, transaction: bool = True):
        return PipelineDeMentira(self)

    async def rpush(self, clave, valor):
        self.listas.setdefault(clave, []).append(valor)
        return len(self.listas[clave])

    async def lrange(self, clave, ini, fin):
        return list(self.listas.get(clave, []))

    async def ltrim(self, clave, ini, fin):
        return True

    async def expire(self, clave, ttl):
        return True

    async def hsetnx(self, clave, campo, valor):
        h = self.hashes.setdefault(clave, {})
        if campo in h:
            return 0
        h[campo] = str(valor)
        return 1

    async def hset(self, clave, campo, valor):
        self.hashes.setdefault(clave, {})[campo] = str(valor)
        return 1

    async def hmget(self, clave, campos):
        h = self.hashes.get(clave, {})
        return [h.get(c) for c in campos]

    async def set(self, clave, valor, nx=False, ex=None):
        if nx and clave in self.cadenas:
            return None
        self.cadenas[clave] = valor
        return True

    async def get(self, clave):
        return self.cadenas.get(clave)

    async def delete(self, *claves):
        n = 0
        for c in claves:
            n += int(any(d.pop(c, None) is not None for d in (self.listas, self.hashes, self.cadenas)))
        return n


class Timeline:
    """La línea de tiempo REAL: mensajes que entran por el webhook y tareas que vencen.

    El webhook (`_encolar_mensaje`) hace exactamente esto por cada mensaje: `agregar_a_buffer` +
    una tarea a `+buffer_segundos`. Las reprogramaciones del debounce entran por el mismo sitio
    (`apply_async` está pinchado), así que lo que se simula aquí es el sistema entero.
    """

    def __init__(self, reloj: Reloj):
        self.reloj = reloj
        self.tareas: list[tuple[float, str, str | None]] = []
        self.reprogramaciones: list[tuple[float, float]] = []  # (instante, countdown)
        self.veredictos: list[str] = []

    def apply_async(self, args=(), countdown=0, **_):
        telefono = args[0] if args else TEL
        nombre = args[1] if len(args) > 1 else None
        self.tareas.append((self.reloj.t + countdown, telefono, nombre))
        self.reprogramaciones.append((self.reloj.t, countdown))

    async def correr(self, mensajes: list[tuple[float, str]]) -> None:
        entrantes = sorted(mensajes)
        vueltas = 0
        while entrantes or self.tareas:
            vueltas += 1
            assert vueltas < 200, "bucle infinito de reprogramaciones (eso sería el bug al revés)"
            prox_msg = entrantes[0][0] if entrantes else float("inf")
            prox_tarea = min(t[0] for t in self.tareas) if self.tareas else float("inf")
            if prox_msg <= prox_tarea:
                instante, texto = entrantes.pop(0)
                self.reloj.t = instante
                await rc.agregar_a_buffer(TEL, texto)
                self.tareas.append((instante + settings.buffer_segundos, TEL, NOMBRE))
            else:
                i = min(range(len(self.tareas)), key=lambda k: self.tareas[k][0])
                instante, telefono, nombre = self.tareas.pop(i)
                self.reloj.t = instante
                self.veredictos.append(await tasks._procesar(telefono, nombre))


@pytest.fixture
def reloj(monkeypatch):
    r = Reloj()
    monkeypatch.setattr(rc, "_ahora", r)
    return r


@pytest.fixture
def servidor(monkeypatch):
    s = RedisDeMentira()
    monkeypatch.setattr(rc, "_client", lambda: s)
    return s


@pytest.fixture
def turno(monkeypatch, reloj, servidor):
    """`_procesar` con los bordes pinchados (BD, Meta y OpenRouter) y el resto REAL."""
    registro = {"respuestas": [], "guardados_entrantes": [], "revienta": False}

    async def _si(*_a, **_k):
        return True

    async def _no(*_a, **_k):
        return False

    async def _responder(telefono, texto, historial, nombre):
        if registro["revienta"]:
            raise RuntimeError("OpenRouter sin saldo")
        registro["respuestas"].append(texto)
        return f"respuesta a: {texto}"

    async def _enviar(telefono, texto):
        return [{"texto": texto, "wa_message_id": "wamid.1", "estado": "enviado", "error": None}]

    async def _guardar_entrante(telefono, nombre, texto):
        registro["guardados_entrantes"].append(texto)
        return True

    async def _nada(*_a, **_k):
        return None

    monkeypatch.setattr(tasks, "_bot_activo", _si)
    monkeypatch.setattr(tasks, "_cliente_pausado", _no)
    monkeypatch.setattr(tasks, "_numero_permitido", _si)
    monkeypatch.setattr(tasks, "responder", _responder)
    monkeypatch.setattr(tasks, "_enviar_en_partes", _enviar)
    monkeypatch.setattr(tasks, "_guardar_en_panel", _si)
    monkeypatch.setattr(tasks, "_guardar_entrante", _guardar_entrante)
    monkeypatch.setattr(tasks, "_avisar_turno_a_medias", _nada)
    monkeypatch.setattr(tasks, "_avisar_turno_perdido", _nada)

    linea = Timeline(reloj)
    monkeypatch.setattr(tasks.procesar_buffer, "apply_async", linea.apply_async)
    registro["linea"] = linea
    return registro


# ══════════════════════════════════════════════════════════════════════════════════
# 1. LA CUENTA (`_espera_restante`): pura, sin Redis y sin reloj de verdad
# ══════════════════════════════════════════════════════════════════════════════════

def test_el_tercer_mensaje_de_las_2225_YA_no_se_barre():
    """EL BUG, en una línea. 22:25:57 entra "De chocolate" y a las 22:26:02 vence la tarea vieja:
    con 5s de silencio faltan 10 para contestar — antes se lo llevaba con esos 5s."""
    assert tasks._espera_restante(primero=T_MSG3, ultimo=T_MSG3, ahora=62.0) == 10.0


def test_cada_mensaje_nuevo_REINICIA_la_ventana():
    """El corazón del debounce: la cuenta va contra el ÚLTIMO, no contra el primero."""
    # Buffer abierto a las :40, último mensaje a las :57, la tarea del primero vence a las :55.
    assert tasks._espera_restante(primero=T_MSG1, ultimo=T_MSG2, ahora=55.0) == 7.0


def test_el_silencio_completo_NO_espera():
    assert tasks._espera_restante(primero=0.0, ultimo=0.0, ahora=15.0) == 0.0


def test_justo_en_el_borde_NO_espera():
    """Exactamente `buffer_segundos` de silencio ya es silencio. Ni un milisegundo más."""
    assert tasks._espera_restante(primero=0.0, ultimo=1.0, ahora=1.0 + settings.buffer_segundos) == 0.0


def test_pasado_de_largo_NO_espera():
    """La tarea que vence tarde (worker atascado) contesta ya; no se pone a esperar negativos."""
    assert tasks._espera_restante(primero=0.0, ultimo=0.0, ahora=40.0) == 0.0


def test_el_TOPE_manda_aunque_el_cliente_siga_escribiendo():
    """ANTI-INANICIÓN: quien escribe sin parar no puede quedarse sin respuesta jamás."""
    ahora = settings.buffer_max_segundos + 5
    assert tasks._espera_restante(primero=0.0, ultimo=ahora - 1, ahora=ahora) == 0.0


def test_la_espera_se_RECORTA_para_no_pisar_el_tope():
    """A 8s del tope no se esperan 15: se esperan 8 y se contesta en el tope clavado."""
    ahora = settings.buffer_max_segundos - 8
    assert tasks._espera_restante(primero=0.0, ultimo=ahora, ahora=ahora) == 8.0


def test_una_marca_del_FUTURO_no_cuelga_el_mensaje():
    """Relojes torcidos entre contenedores: un dato raro puede hacer esperar, nunca colgar."""
    espera = tasks._espera_restante(primero=0.0, ultimo=1_000.0, ahora=5.0)
    assert 0.0 <= espera <= settings.buffer_segundos


# ══════════════════════════════════════════════════════════════════════════════════
# 2. LAS MARCAS EN REDIS (código real de `redis_client` contra el Redis de mentira)
# ══════════════════════════════════════════════════════════════════════════════════

async def test_agregar_a_buffer_deja_las_dos_marcas(reloj, servidor):
    reloj.t = 100.0
    await rc.agregar_a_buffer(TEL, "hola")
    assert await rc.marcas_de_buffer(TEL) == (100.0, 100.0)


async def test_el_segundo_mensaje_pisa_ULTIMO_pero_no_PRIMERO(reloj, servidor):
    """`primero` es el ancla del tope; `ultimo` es la ventana que se reinicia."""
    reloj.t = 100.0
    await rc.agregar_a_buffer(TEL, "hola")
    reloj.t = 107.0
    await rc.agregar_a_buffer(TEL, "tienes tortas?")
    assert await rc.marcas_de_buffer(TEL) == (100.0, 107.0)


async def test_vaciar_buffer_se_lleva_las_marcas(reloj, servidor):
    """Una marca que sobrevive al vaciado haría esperar de más al SIGUIENTE mensaje."""
    reloj.t = 100.0
    await rc.agregar_a_buffer(TEL, "hola")
    assert await rc.vaciar_buffer(TEL) == ["hola"]
    assert await rc.marcas_de_buffer(TEL) is None


async def test_buffer_SIN_marca_se_procesa_YA(reloj, servidor):
    """Buffer de antes del despliegue (o Redis reiniciado): jamás colgar por un dato ausente."""
    await servidor.rpush(f"buffer:{TEL}", "quedé esperando desde ayer")
    assert await rc.marcas_de_buffer(TEL) is None
    assert await tasks._espera_del_buffer(TEL) == 0.0


async def test_marca_a_MEDIAS_se_procesa_YA(reloj, servidor):
    """Hash con `primero` y sin `ultimo`: dato roto ⇒ se contesta, no se espera."""
    await servidor.hset(f"buffer_ts:{TEL}", "primero", 100.0)
    reloj.t = 101.0
    assert await rc.marcas_de_buffer(TEL) is None
    assert await tasks._espera_del_buffer(TEL) == 0.0


# ══════════════════════════════════════════════════════════════════════════════════
# 3. EL TURNO ENTERO (`_procesar`) sobre la línea de tiempo del incidente
# ══════════════════════════════════════════════════════════════════════════════════

async def test_LA_RAFAGA_DE_LAS_2225_SE_CONTESTA_UNA_SOLA_VEZ(turno):
    """El test que da nombre al arreglo: 3 mensajes en ráfaga ⇒ UN turno, UNA respuesta."""
    await turno["linea"].correr([
        (T_MSG1, "Como son las que tienes? Variada."),
        (T_MSG2, "Tienes tortas?"),
        (T_MSG3, "De chocolate"),
    ])
    assert turno["respuestas"] == [
        "Como son las que tienes? Variada.\nTienes tortas?\nDe chocolate"
    ], "la ráfaga tenía que consolidarse en UN solo turno"


async def test_la_tarea_vieja_NO_barre_el_mensaje_recien_llegado(turno, reloj, servidor):
    """22:26:02, tarea vencida, "De chocolate" tiene 5s: se espera y el buffer queda INTACTO."""
    reloj.t = T_MSG3
    await rc.agregar_a_buffer(TEL, "De chocolate")
    reloj.t = 62.0
    assert await tasks._procesar(TEL, NOMBRE) == "esperando"
    assert servidor.listas[f"buffer:{TEL}"] == ["De chocolate"], "el buffer NO se toca al esperar"
    assert turno["respuestas"] == []


async def test_esperando_SUELTA_EL_LOCK(turno, reloj, servidor):
    """Si el camino nuevo se quedara el lock, la siguiente tarea vería 'ocupado' contra nadie."""
    reloj.t = T_MSG3
    await rc.agregar_a_buffer(TEL, "De chocolate")
    reloj.t = 62.0
    assert await tasks._procesar(TEL, NOMBRE) == "esperando"
    assert f"lock:{TEL}" not in servidor.cadenas
    assert await rc.adquirir_lock(TEL) is True


async def test_esperando_reprograma_SOLO_lo_que_falta(turno, reloj, servidor):
    """Reprograma a 10s (lo que falta), no a los 15 de siempre ni a los 20 del 'ocupado'."""
    reloj.t = T_MSG3
    await rc.agregar_a_buffer(TEL, "De chocolate")
    reloj.t = 62.0
    await tasks._procesar(TEL, NOMBRE)
    assert [c for _, c in turno["linea"].reprogramaciones] == [10.0]


async def test_un_mensaje_SOLO_se_contesta_a_los_15s_y_sin_reprogramar(turno):
    """El caso normal (el 90% del tráfico) no puede frenarse ni un segundo de más."""
    await turno["linea"].correr([(T_MSG1, "hola buenas")])
    assert turno["respuestas"] == ["hola buenas"]
    assert turno["linea"].reprogramaciones == [], "un mensaje suelto no reprograma nada"
    assert "esperando" not in turno["linea"].veredictos


async def test_dos_rafagas_SEPARADAS_son_dos_turnos(turno):
    """No es 'juntarlo todo': dos conversaciones separadas por minutos siguen siendo dos."""
    await turno["linea"].correr([
        (0.0, "hola"),
        (1.0, "buenas tardes"),
        (300.0, "sigo ahí?"),
    ])
    assert turno["respuestas"] == ["hola\nbuenas tardes", "sigo ahí?"]


async def test_el_TOPE_corta_la_espera_del_cliente_que_no_para(turno):
    """Cliente escribiendo cada 10s sin parar: el tope obliga a contestarle igual."""
    mensajes = [(float(i * 10), f"mensaje {i}") for i in range(12)]  # 0s … 110s
    await turno["linea"].correr(mensajes)
    assert turno["respuestas"], "🔴 INANICIÓN: el cliente escribió 12 veces y nadie le contestó"
    # El primer turno sale por el TOPE, no por el silencio: arranca en el mensaje 0 y se corta
    # dentro de la ventana del tope (no espera a que el cliente se calle).
    primero = turno["respuestas"][0]
    assert primero.startswith("mensaje 0")
    assert primero.count("\n") + 1 <= settings.buffer_max_segundos / 10 + 1


async def test_ningun_mensaje_se_queda_sin_contestar(turno):
    """La cuenta que importa: todo lo que escribió el cliente aparece en ALGÚN turno."""
    mensajes = [(float(i * 7), f"mensaje {i}") for i in range(9)]
    await turno["linea"].correr(mensajes)
    juntos = "\n".join(turno["respuestas"])
    for _, texto in mensajes:
        assert texto in juntos, f"se perdió {texto!r}"


# ══════════════════════════════════════════════════════════════════════════════════
# 4. LO QUE NO SE PUEDE ROMPER: SIL-1 (ocupado) y SIL-10 (el turno se anota antes)
# ══════════════════════════════════════════════════════════════════════════════════

async def test_OCUPADO_sigue_siendo_ocupado(turno, reloj, servidor):
    """SIL-1: con el lock tomado por otro turno se REENCOLA. No es el camino nuevo."""
    reloj.t = T_MSG3
    await rc.agregar_a_buffer(TEL, "De chocolate")
    await rc.adquirir_lock(TEL)  # otro turno en curso
    reloj.t = 62.0
    assert await tasks._procesar(TEL, NOMBRE) == "ocupado"
    assert turno["linea"].reprogramaciones == [], "'ocupado' lo reencola la tarea, no `_procesar`"
    assert servidor.listas[f"buffer:{TEL}"] == ["De chocolate"]


async def test_OCUPADO_manda_sobre_ESPERANDO(turno, reloj, servidor):
    """El orden importa: primero el lock (SIL-1), después el debounce."""
    reloj.t = 100.0
    await rc.agregar_a_buffer(TEL, "escribiendo ahora mismo")
    await rc.adquirir_lock(TEL)
    reloj.t = 101.0  # 1s de silencio: el debounce SÍ querría esperar
    assert await tasks._procesar(TEL, NOMBRE) == "ocupado"


def test_la_tarea_reencola_el_OCUPADO_con_su_reintento(monkeypatch):
    """SIL-1 de punta a punta: 'ocupado' gasta un reintento de los 8, como siempre."""
    reintentos = []

    async def _ocupado(*_a):
        return "ocupado"

    monkeypatch.setattr(tasks, "_procesar", _ocupado)
    monkeypatch.setattr(tasks.procesar_buffer, "retry", lambda **kw: reintentos.append(kw))
    tasks.procesar_buffer(TEL, NOMBRE)
    assert reintentos == [{"countdown": tasks._ESPERA_BUFFER}]


def test_la_tarea_NO_gasta_reintentos_esperando(monkeypatch):
    """'esperando' ya se reprogramó sola. Si pasara por `self.retry` agotaría el presupuesto del
    'ocupado' y dispararía la falsa alarma de `_avisar_turno_perdido`."""
    reintentos = []

    async def _esperando(*_a):
        return "esperando"

    monkeypatch.setattr(tasks, "_procesar", _esperando)
    monkeypatch.setattr(tasks.procesar_buffer, "retry", lambda **kw: reintentos.append(kw))
    tasks.procesar_buffer(TEL, NOMBRE)
    assert reintentos == []


async def test_SIL10_el_turno_del_cliente_se_anota_aunque_el_modelo_reviente(turno, reloj, servidor):
    """El rescate del `except` sigue igual: si `responder()` explota, lo que escribió el cliente
    queda en Postgres — y es el texto CONSOLIDADO, no un trozo."""
    turno["revienta"] = True
    reloj.t = 100.0
    await rc.agregar_a_buffer(TEL, "hola")
    await rc.agregar_a_buffer(TEL, "quiero 2 tortas")
    reloj.t = 100.0 + settings.buffer_segundos
    assert await tasks._procesar(TEL, NOMBRE) == "error"
    assert turno["guardados_entrantes"] == ["hola\nquiero 2 tortas"]


async def test_SIL10_el_historial_del_cliente_se_guarda_antes_de_pensar(turno, reloj, servidor):
    """La otra mitad de SIL-10: el turno entra en el historial de Redis antes del modelo."""
    reloj.t = 100.0
    await rc.agregar_a_buffer(TEL, "hola")
    reloj.t = 100.0 + settings.buffer_segundos
    await tasks._procesar(TEL, NOMBRE)
    guardado = [json.loads(f) for f in servidor.listas[f"hist:{TEL}"]]
    assert guardado[0] == {"role": "user", "content": "hola"}


async def test_buffer_vacio_no_llama_al_modelo(turno, reloj, servidor):
    """Tarea duplicada sobre un buffer ya vaciado: 'vacio', sin gastar un token."""
    reloj.t = 100.0
    assert await tasks._procesar(TEL, NOMBRE) == "vacio"
    assert turno["respuestas"] == []
