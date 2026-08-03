"""LA RED DEL RELEVO NO PUEDE CAERSE EN SILENCIO — banco del arreglo SIL-2.

🔴 EL CASO (auditoría 2026-08-02). `ejecutar_tool` atrapaba TODA excepción y devolvía
`{"error": str(e)}` **sin una sola línea de log**, y el bucle de `responder` encendía
`pidio_ayuda = True` en cuanto veía el NOMBRE de la tool, sin mirar el resultado. Si
`pedir_ayuda` reventaba a mitad (timeout de BD, FK, pool agotado):
  · no había Intervencion — la bandeja de la dueña, vacía,
  · no salía el WhatsApp de aviso,
  · el chat NO quedaba pausado,
  · el bot igual le decía al cliente "eso te lo confirmo enseguida",
  · y en el log NO había NADA que buscar.
El cliente esperaba para siempre y nadie se enteraba. Es la semana de mensajes mudos.

Lo que este banco vigila, pieza por pieza:
  1. `ejecutar_tool` deja testigo, y con NIVEL distinto según lo que se rompió (SIL-2.1).
  2. `_avisar_intervencion` no puede tumbar `pedir_ayuda` DESPUÉS del commit (SIL-2.7) —
     de eso depende que su `{"ok": True}` sea fiable, que es lo que el resto se cree.
  3. La HOJA mira el resultado, no el nombre (SIL-2.3).
  4. El turno entero con Postgres caído: se reintenta, salta el ÚLTIMO TESTIGO, y NO se
     martillea la base 5 veces por turno (SIL-2.2 + SIL-2.4 + §8 de la revisión).
  5. El camino bueno no cambia: una llamada y cero avisos de emergencia.
  6. El ÚLTIMO TESTIGO: a quién avisa, cuándo se calla (SIL-2.6).
  7. El candado: ninguna escalada llama a la tool del relevo a pelo.
  8. Una tool que revienta en el bucle deja rastro, en modo uno y en modo dos (SIL-2.8/2.9).

No se manda un solo WhatsApp: el modelo, la sesión de BD y Meta van todos mockeados, y el
teléfono empieza por "__" (el candado del simulador que ya usan enviar_catalogo y las fotos).
"""
import asyncio
import inspect
import logging
import re
import sys
from types import SimpleNamespace

from sqlalchemy.exc import OperationalError

from app.agent import agent as ag
from app.agent import tools as tl
from app.agent.hoja import HojaDeHechos
from app.agent.tools import ejecutar_tool
from app.services import dueno as dn

TEL = "__prueba_relevo__"
HISTORIAL = [
    {"role": "user", "content": "hola"},
    {"role": "assistant", "content": "¡Hola! 💚"},
]
fallos: list[str] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"   {'[OK ]' if ok else '[MAL]'} {nombre}{('  → ' + detalle) if detalle and not ok else ''}")
    if not ok:
        fallos.append(nombre)


class _Cazalogs(logging.Handler):
    """El log ES el testigo: si no queda escrito, el fallo es invisible. Aquí se comprueba
    que queda escrito, con QUÉ nivel y con qué contexto (tool + teléfono)."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)

    def tiene(self, nivel: int, *trozos: str) -> bool:
        return any(
            r.levelno == nivel and all(t in r.getMessage() for t in trozos)
            for r in self.records
        )


def _pool_agotado(sql: str) -> OperationalError:
    """El error REAL del incidente, no un Exception genérico."""
    return OperationalError(sql, {}, RuntimeError("QueuePool limit of size 5 reached"))


class _Resultado:
    """Lo mínimo que `pedir_ayuda` le pide a un `session.execute`."""

    def scalar_one_or_none(self):
        return None

    def scalars(self):
        return self

    def all(self):
        return []


class _SesionRota:
    """La BD que ya no responde: el pool agotado del incidente real."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        raise _pool_agotado("SELECT 1")

    async def commit(self):
        raise _pool_agotado("COMMIT")

    def add(self, *a, **k):
        pass


class _SesionQueMuereTrasElCommit:
    """El caso de SIL-2.7: la fila de la bandeja YA está comiteada y la base se cae justo
    después, en las lecturas que `_avisar_intervencion` tenía FUERA de su try."""

    def __init__(self) -> None:
        self.comiteado = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        if self.comiteado:
            raise _pool_agotado("SELECT configuracion")
        return _Resultado()

    async def commit(self):
        self.comiteado = True

    def add(self, *a, **k):
        pass


def _tool_call(cid: str, nombre: str, args: str = "{}") -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": nombre, "arguments": args}}
    ]}}]}


def _texto(contenido: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": contenido}}]}


def _guion(*turnos: dict):
    """Un `llm` falso que va soltando los turnos en orden."""
    it = iter(turnos)

    async def _llm(messages, tools, modelo):
        return next(it)

    return _llm


async def main() -> None:  # noqa: C901 — es un banco: son escenarios, no lógica
    caza = _Cazalogs()
    for nombre in ("app.agent.tools", "app.agent.agent", "app.agent.hoja"):
        lg = logging.getLogger(nombre)
        lg.addHandler(caza)
        lg.setLevel(logging.DEBUG)

    print("\n1) `ejecutar_tool` YA NO ES MUDO")
    caza.records.clear()
    r = await ejecutar_tool(
        "pedir_ayuda", {"motivo": "no_se", "detalle": "envíos a Caracas"}, TEL,
        session_factory=_SesionRota,
    )
    check("devuelve {'error': …} (el modelo tiene que poder recuperarse)",
          isinstance(r, dict) and "error" in r and not r.get("ok"), repr(r))
    check("🔴 y AHORA deja un ERROR con la tool y el teléfono",
          caza.tiene(logging.ERROR, "pedir_ayuda", TEL))

    caza.records.clear()
    await ejecutar_tool("ver_catalogo", {}, TEL, session_factory=_SesionRota)
    check("una tool NO crítica deja WARNING, no ERROR (el nivel distingue)",
          caza.tiene(logging.WARNING, "ver_catalogo")
          and not caza.tiene(logging.ERROR, "ver_catalogo"))

    caza.records.clear()
    r = await ejecutar_tool("tool_que_no_existe", {}, TEL, session_factory=_SesionRota)
    check("una tool que el modelo se INVENTA también deja rastro",
          "error" in r and caza.tiene(logging.WARNING, "tool_que_no_existe"))

    print("\n2) EL AVISO A LA DUEÑA NO PUEDE MATAR A `pedir_ayuda` (SIL-2.7)")
    caza.records.clear()
    r = await ejecutar_tool(
        "pedir_ayuda", {"motivo": "no_se", "detalle": "la base muere tras el commit"}, TEL,
        session_factory=_SesionQueMuereTrasElCommit,
    )
    check("🔴 la fila YA está comiteada ⇒ `pedir_ayuda` devuelve ok, no revienta",
          isinstance(r, dict) and r.get("ok") is True, repr(r))
    check("   ...y el fallo del WhatsApp queda escrito, no se lo traga nadie",
          caza.tiene(logging.ERROR, "no se pudo avisar por WhatsApp"))

    print("\n3) LA HOJA MIRA EL RESULTADO, NO EL NOMBRE (SIL-2.3)")
    caza.records.clear()
    h = HojaDeHechos()
    h.anotar_tool("pedir_ayuda", {"error": "pool agotado"})
    check("🔴 pedir_ayuda que FALLA NO enciende `escalado`", h.escalado is False)
    check("   ...y el error deja testigo aunque NO entre en la hoja",
          caza.tiene(logging.WARNING, "pedir_ayuda", "pool agotado"))
    h.anotar_tool("pedir_ayuda", {"ok": True, "nota": "listo"})
    check("pedir_ayuda que va bien SÍ lo enciende", h.escalado is True)

    print("\n4) EL TURNO COMPLETO CON LA BASE CAÍDA: el sistema SE ENTERA")
    caza.records.clear()
    llamadas: list[str] = []
    testigos: list[tuple] = []

    async def _ejecutar_roto(nombre, args, telefono):
        llamadas.append(nombre)
        if nombre == "pedir_ayuda":
            return {"error": "QueuePool limit of size 5 reached"}
        return {"ok": True}

    async def _testigo(telefono, motivo, detalle):
        testigos.append((telefono, motivo))
        return True

    original, ag.avisar_relevo_caido = ag.avisar_relevo_caido, _testigo
    try:
        texto = await ag.responder(
            TEL, "¿hacen envíos a Caracas?", HISTORIAL, "Rosa",
            llm=_guion(
                _tool_call("c1", "pedir_ayuda", '{"motivo": "no_se", "detalle": "envíos a Caracas"}'),
                _texto("Déjame verificar eso y te confirmo enseguida 💚"),
            ),
            ejecutar=_ejecutar_roto,
        )
    finally:
        ag.avisar_relevo_caido = original

    check("se REINTENTA (una sesión nueva suele arreglar un timeout)",
          llamadas.count("pedir_ayuda") >= 2, str(llamadas))
    check("🔴 y si tampoco, salta EL ÚLTIMO TESTIGO (WhatsApp fuera de la base)",
          bool(testigos), str(testigos))
    check("queda un ERROR 'RELEVO CAÍDO' en el log", caza.tiene(logging.ERROR, "RELEVO CAÍDO"))
    check("el turno NO se cae: el cliente recibe algo", bool((texto or "").strip()))
    # §8 de la revisión: con Postgres caído, la red del relevo del final NO puede volver a
    # abrir dos sesiones más ni mandar un segundo WhatsApp. Un turno cuesta 2 intentos y 1
    # aviso — no 5 aperturas y 2 WhatsApps por cliente cada pocos segundos.
    check("🔴 NO se martillea una base muerta: 1 llamada del modelo + 2 reintentos, y basta",
          llamadas.count("pedir_ayuda") <= 3, str(llamadas))
    check("   ...y UN solo aviso de emergencia por turno", len(testigos) == 1, str(testigos))

    llamadas.clear()
    ok = await ag._escalar(_ejecutar_roto, TEL, "no_se", "x", ya_fallo=True)
    check("con `ya_fallo` la escalada ni toca la base", ok is False and not llamadas, str(llamadas))

    print("\n5) EL CAMINO BUENO NO CAMBIA (no avisar de más)")
    llamadas.clear()
    testigos.clear()

    async def _ejecutar_ok(nombre, args, telefono):
        llamadas.append(nombre)
        return {"ok": True, "nota": "listo"}

    original, ag.avisar_relevo_caido = ag.avisar_relevo_caido, _testigo
    try:
        await ag.responder(
            TEL, "¿hacen envíos a Caracas?", HISTORIAL, "Rosa",
            llm=_guion(
                _tool_call("c1", "pedir_ayuda", '{"motivo": "no_se", "detalle": "envíos a Caracas"}'),
                _texto("Déjame verificar eso y te confirmo enseguida 💚"),
            ),
            ejecutar=_ejecutar_ok,
        )
    finally:
        ag.avisar_relevo_caido = original

    check("UNA sola llamada a pedir_ayuda", llamadas.count("pedir_ayuda") == 1, str(llamadas))
    check("CERO avisos de emergencia", not testigos, str(testigos))

    print("\n5b) EL SEXTO `return RESPUESTA_SEGURA`: las ITERACIONES AGOTADAS también avisan")
    # 🔴 La única grieta que el BARREDOR no tapa: el texto SALE y se guarda con rol='assistant',
    # así que el SQL del vigilante da ese chat por respondido. Si aquí no se escala, el cliente
    # se queda con "dame un momentito" y NADIE tiene el encargo de contestarle.
    llamadas.clear()
    turnos = tuple(
        _tool_call(f"i{n}", "ver_catalogo")
        for n in range(ag.settings.max_iteraciones_agente)
    )
    texto = await ag.responder(
        TEL, "¿me mandas el catálogo?", HISTORIAL, "Rosa",
        llm=_guion(*turnos), ejecutar=_ejecutar_ok,
    )
    check("el cliente recibe la respuesta segura (eso no cambia)",
          texto == ag.RESPUESTA_SEGURA, repr(texto))
    check("🔴 y AHORA la dueña se entera: se escala UNA vez",
          llamadas.count("pedir_ayuda") == 1, str(llamadas))

    llamadas.clear()
    turnos = (
        _tool_call("a1", "pedir_ayuda", '{"motivo": "no_se", "detalle": "envíos"}'),
    ) + tuple(
        _tool_call(f"i{n}", "ver_catalogo")
        for n in range(ag.settings.max_iteraciones_agente - 1)
    )
    await ag.responder(
        TEL, "¿hacen envíos a Caracas?", HISTORIAL, "Rosa",
        llm=_guion(*turnos), ejecutar=_ejecutar_ok,
    )
    check("y NO se duplica si el modelo ya había escalado bien dentro del bucle",
          llamadas.count("pedir_ayuda") == 1, str(llamadas))

    print("\n6) EL ÚLTIMO TESTIGO: a quién avisa y cuántas veces (SIL-2.6)")
    enviados: list[tuple] = []
    memoria: dict[str, str] = {}

    async def _enviar_falso(destino, cuerpo, **_):
        # `**_`: `avisar_relevo_caido` fuerza el portón (`forzar=True`) porque su aviso no tiene
        # bandeja donde caer. Si el doble no acepta la bandera, el TypeError se lo traga el
        # `except` de la función y el banco se pone rojo por el motivo equivocado.
        enviados.append((destino, cuerpo))
        return {"ok": True}

    async def _get_cache(clave):
        return memoria.get(clave)

    async def _set_cache(clave, valor, ttl):
        memoria[clave] = valor

    # 🔴 EL ESPÍA VA AHORA SOBRE `services/dueno.py`, NO SOBRE `tools`. Desde el arreglo del
    # cerebro partido, `avisar_relevo_caido` no resuelve el número por su cuenta: se lo pide al
    # resolvedor ÚNICO en modo `sin_base` (Postgres está caído — por eso existe el testigo). Si
    # el espía se quedara en `tl`, el banco tocaría el Redis DE VERDAD y leería el número real
    # del taller. `tl.get_cache`/`tl.set_cache` se siguen espiando: son los del candado
    # antiinundación, que sí vive en `tools`.
    orig = (tl.enviar_texto, tl.get_cache, tl.set_cache, dn.get_cache, dn.get_settings)
    tl.enviar_texto, tl.get_cache, tl.set_cache = _enviar_falso, _get_cache, _set_cache
    dn.get_cache = _get_cache
    dn.get_settings = lambda: SimpleNamespace(dueno_telefono="584140009999")
    dn._memo_valor, dn._memo_hasta = "", 0.0  # el memo del proceso no puede tapar al espía
    try:
        salio = await tl.avisar_relevo_caido(TEL, "no_se", "prueba")
        check("un teléfono de prueba (__) NO manda WhatsApp", salio is False and not enviados)

        real = "584141234567"
        salio = await tl.avisar_relevo_caido(real, "no_se", "envíos a Caracas")
        check("con un chat real, el aviso SÍ sale y va a la DUEÑA (nunca al cliente)",
              salio is True and len(enviados) == 1 and enviados[0][0] == "584140009999",
              str(enviados))
        check("   ...y el mensaje dice que la bandeja NO tiene la fila",
              "NO tiene esta fila" in enviados[0][1])

        salio = await tl.avisar_relevo_caido(real, "no_se", "otra vez")
        check("🔴 ANTI-INUNDACIÓN: el segundo aviso del MISMO chat no se reenvía",
              salio is False and len(enviados) == 1, str(len(enviados)))

        # Sin DUENO_TELEFONO en el entorno tira de la copia que dejó la base cuando SÍ respondía.
        dn.get_settings = lambda: SimpleNamespace(dueno_telefono="")
        memoria[tl._CLAVE_DUENO] = "584140008888"
        salio = await tl.avisar_relevo_caido("584149999999", "no_se", "sin entorno")
        check("sin DUENO_TELEFONO, usa la copia de Redis (por eso existe la copia)",
              salio is True and enviados[-1][0] == "584140008888", str(enviados[-1:]))
        check("   ...y la copia vive con el prefijo `cache:` de la casa",
              tl._CLAVE_DUENO.startswith("cache:"), tl._CLAVE_DUENO)
    finally:
        tl.enviar_texto, tl.get_cache, tl.set_cache, dn.get_cache, dn.get_settings = orig
        dn._memo_valor, dn._memo_hasta = "", 0.0

    print("\n7) EL CANDADO: ninguna escalada llama a la tool del relevo a pelo")
    # 🔴 Se cuenta lo QUE IMPORTA, no cuántas veces se escala. Añadir escaladas nuevas es sano
    # (SIL-11 añade dos) y este candado las deja pasar mientras usen `_escalar`. Lo que NO puede
    # volver a existir es un `await ejecutar("pedir_ayuda", …)` suelto cuyo retorno nadie mira:
    # ese fue EXACTAMENTE el bug. Por eso el umbral no es un número de llamadas, es "cero sueltas".
    patron = re.compile(r'ejecutar\(\s*"pedir_ayuda"')
    sueltas = (
        len(patron.findall(inspect.getsource(ag)))
        - len(patron.findall(inspect.getsource(ag._escalar)))
    )
    check("todas las escaladas de agent.py pasan por `_escalar`", sueltas == 0,
          f"sueltas={sueltas}")
    check("`_escalar` vive a nivel de MÓDULO (el modo uno y el modo dos comparten la red)",
          inspect.iscoroutinefunction(getattr(ag, "_escalar", None)))
    check("   ...y devuelve si LO LOGRÓ (bool), no None",
          "-> bool" in inspect.getsource(ag._escalar).split("\n")[2]
          or "-> bool" in "".join(inspect.getsource(ag._escalar).split("\n")[:4]))

    print("\n8) UNA TOOL QUE REVIENTA EN EL BUCLE DEJA RASTRO (SIL-2.8 / SIL-2.9)")
    caza.records.clear()

    async def _ejecutar_deadlock(nombre, args, telefono):
        if nombre == "registrar_pedido":
            return {"error": "deadlock detected"}
        return {"ok": True, "nota": "listo"}

    await ag.responder(
        TEL, "quiero 2 paquetes", HISTORIAL, "Rosa",
        llm=_guion(
            _tool_call("c1", "registrar_pedido", '{"variante_id": 1, "cantidad": 2}'),
            _texto("¿Me confirmas la dirección? 💚"),
        ),
        ejecutar=_ejecutar_deadlock,
    )
    check("🔴 el error del carril del DINERO deja un ERROR con la tool y el teléfono",
          caza.tiene(logging.ERROR, "registrar_pedido", TEL, "deadlock"))

    # El mismo agujero por el camino del modo dos (hoy tras la bandera `agente_modo`).
    caza.records.clear()

    async def _modo_dos():
        return "dos", "modelo/operador", "modelo/voz"

    async def _voz_falsa(messages, modelo):
        return "¿Me confirmas la dirección? 💚"

    orig_modo, ag.leer_config_agente = ag.leer_config_agente, _modo_dos
    try:
        await ag.responder(
            TEL, "quiero 2 paquetes", HISTORIAL, "Rosa",
            llm=_guion(
                _tool_call("c1", "registrar_pedido", '{"variante_id": 1, "cantidad": 2}'),
                _texto("dile que confirme la dirección"),
            ),
            voz=_voz_falsa, ejecutar=_ejecutar_deadlock,
        )
    finally:
        ag.leer_config_agente = orig_modo

    check("y por el camino del Operador también (antes se descartaba DOS veces)",
          caza.tiene(logging.ERROR, "Operador", "registrar_pedido"))
    check("   ...la HOJA además avisa de que ese resultado no entra en ella",
          caza.tiene(logging.WARNING, "HOJA:", "registrar_pedido"))

    print()
    if fallos:
        print(f"🔴 {len(fallos)} FALLO(S): {', '.join(fallos)}")
        sys.exit(1)
    print("✅ LA RED DEL RELEVO YA NO SE CAE EN SILENCIO")


if __name__ == "__main__":
    asyncio.run(main())
