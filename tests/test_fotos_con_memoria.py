"""LA MEMORIA DE LA HERRAMIENTA: un producto ya mostrado NO se reenvía — llame quien llame.

🔴 EL CASO REAL (Omaira Mendez, 2026-08-29, 4:39-4:50pm — la primera venta con el bot ABIERTO):
la clienta recibió las MISMAS fotos TRES veces en una sola venta. El de-duplicador existía, pero
vivía SOLO en la RED DE LA FOTO (`_asegurar_foto` filtra con `media_ya_mostrada` ANTES de
llamar); cuando el MODELO llama `enviar_fotos_producto` directo en el bucle —y Sonnet obedece
"ÚSALA PROACTIVA" cada turno—, ese camino no pasaba por ningún candado y reenviaba ciego.
El hueco existió siempre: los modelos viejos casi no llamaban la herramienta solos.

El arreglo es de la capa que EJECUTA (la herramienta), no del vigilante ni del modelo: la
garantía tiene que valer sea Sonnet, DeepSeek o GPT. La suite vieja jamás ejercitó este camino:
en todos los tests de `responder`, el `llm` falso devolvía SOLO texto, nunca `tool_calls` —
este archivo estrena ese molde.

⚠️ La mitad de este archivo son los casos que NO deben frenar: el reenvío pedido por el cliente
(`reenviar=True`, y el código lo enciende solo si pidió ver — `_ejecutar_con_guardas`), la
versión nueva de un producto compuesto (la de yuca no bloquea la de plátano), el interruptor
`fotos_memoria` en 'off', el fallo de BD (la herramienta envía: `si_falla=False`) y el
simulador del panel (su teléfono fijo acumularía filas y cada demo parecería rota).
Frenar de más rompe la venta.
"""
from types import SimpleNamespace

import pytest

from app.agent import agent as ag
from app.agent import hoja as hj
from app.agent import tools as tl
from app.services import cola_media

TEL = "584240000000"


# ══════════════════════════════════════════════════════════════════════════════════
# LA PIEZA: el candado DENTRO de `enviar_fotos_producto`
# ══════════════════════════════════════════════════════════════════════════════════

def _foto(mid: int, etiqueta=None, tipo="imagen"):
    return SimpleNamespace(
        id=mid, etiqueta=etiqueta, variante_id=None, tipo=tipo, clave=f"falsa/{mid}.jpg"
    )


class _Resultado:
    def __init__(self, filas):
        self._filas = filas

    def scalars(self):
        return self

    def all(self):
        return list(self._filas)

    def first(self):
        return self._filas[0] if self._filas else None


class _Sesion:
    """La BD justa para la herramienta: las medias del producto y el interruptor."""

    def __init__(self, medios, interruptor=None):
        self.medios = medios
        self.interruptor = interruptor  # el valor de la clave `fotos_memoria` (None = ausente)

    async def execute(self, consulta):
        if "configuracion" in str(consulta):
            return _Resultado([self.interruptor] if self.interruptor is not None else [])
        return _Resultado(self.medios)

    async def get(self, modelo, pk):
        return None


@pytest.fixture(autouse=True)
def _cola_limpia():
    """Cada test arranca con la cola cerrada y no deja nada dentro (cerrar avisa si no)."""
    yield
    cola_media.descartar("limpieza de test")
    cola_media.cerrar()


async def _correr_tool(
    monkeypatch, *, ya_mostrada=False, medios=None, interruptor=None,
    telefono=TEL, memoria_espia=None, **kwargs,
):
    """Ejecuta la herramienta REAL con la BD falseada y la cola ABIERTA (el turno normal)."""

    async def _prod(session, nombre):
        return SimpleNamespace(id=7, nombre="Quesillo")

    async def _relevo(session, telefono):
        return False

    async def _mostrada(telefono, nombre, etiqueta=None, *, si_falla=True):
        if memoria_espia is not None:
            memoria_espia.append((telefono, nombre, etiqueta, si_falla))
        return ya_mostrada

    async def _guardar(**kwargs):
        return None

    # 🔴 EN EL MÓDULO tools, NO en agent: los tests de la red parchean `ag.media_ya_mostrada`
    # y ese parche NO alcanza la llamada nueva desde dentro de la herramienta (lección medida).
    monkeypatch.setattr(tl, "_buscar_producto", _prod)
    monkeypatch.setattr(tl, "_la_duena_tomo_el_chat", _relevo)
    monkeypatch.setattr(tl, "media_ya_mostrada", _mostrada)
    monkeypatch.setattr(tl, "_guardar_media_saliente", _guardar)
    monkeypatch.setattr("app.services.r2.url_publica", lambda clave: f"https://r2.local/{clave}")

    cola_media.abrir()
    sesion = _Sesion(
        medios if medios is not None else [_foto(1), _foto(2), _foto(3)], interruptor
    )
    return await tl.enviar_fotos_producto(sesion, telefono, "quesillo", **kwargs)


async def test_el_producto_ya_mostrado_NO_se_reenvia(monkeypatch):
    """El candado del caso Omaira: la llamada directa del modelo ya no reenvía ciega."""
    r = await _correr_tool(monkeypatch, ya_mostrada=True)
    assert r.get("enviadas") == 0
    assert r.get("ya_mostrado") is True
    assert cola_media.cuantos() == 0, "no puede quedar NADA en la cola"


async def test_la_nota_le_ensena_la_valvula_al_modelo(monkeypatch):
    """La nota dirige el turno: no afirmar un envío nuevo, seguir la venta, y `reenviar`."""
    r = await _correr_tool(monkeypatch, ya_mostrada=True)
    assert "reenviar" in r["nota"]
    assert "sigue" in r["nota"].lower()


async def test_con_reenviar_SI_se_reenvia(monkeypatch):
    """La válvula prometida: el cliente la pidió otra vez ⇒ se reenvía aunque ya la vio."""
    r = await _correr_tool(monkeypatch, ya_mostrada=True, reenviar=True)
    assert r.get("enviadas") == 3
    assert not r.get("ya_mostrado")
    assert cola_media.cuantos() == 3


async def test_sin_memoria_previa_el_camino_feliz_queda_intacto(monkeypatch):
    """NO-disparo: al cliente nuevo no le cambia nada."""
    r = await _correr_tool(monkeypatch, ya_mostrada=False)
    assert r.get("enviadas") == 3
    assert cola_media.cuantos() == 3


async def test_la_memoria_pregunta_con_el_nombre_resuelto_y_la_etiqueta_elegida(monkeypatch):
    """El chequeo va con `prod.nombre` (el canónico que se escribe en el pie), la etiqueta que
    SALDRÍA, y `si_falla=False` (el lado seguro de la herramienta es ENVIAR)."""
    espia: list = []
    medios = [_foto(1, "base de yuca"), _foto(2, "base de plátano"), _foto(3)]
    await _correr_tool(
        monkeypatch, ya_mostrada=False, medios=medios, memoria_espia=espia,
        etiqueta="de yuca",
    )
    assert espia == [(TEL, "Quesillo", "base de yuca", False)]


async def test_ver_la_de_yuca_no_bloquea_la_de_platano(monkeypatch):
    """NO-disparo: versiones distintas son fotos distintas. La memoria pregunta por ESA
    etiqueta, así que un 'ya vio la de yuca' no puede callar la de plátano que acaba de pedir."""

    async def _mostrada(telefono, nombre, etiqueta=None, *, si_falla=True):
        return etiqueta == "base de yuca"  # solo la de yuca está en el historial

    monkeypatch.setattr(tl, "media_ya_mostrada", _mostrada)
    medios = [_foto(1, "base de yuca"), _foto(2, "base de plátano"), _foto(3)]

    async def _prod(session, nombre):
        return SimpleNamespace(id=7, nombre="Quesillo")

    async def _relevo(session, telefono):
        return False

    monkeypatch.setattr(tl, "_buscar_producto", _prod)
    monkeypatch.setattr(tl, "_la_duena_tomo_el_chat", _relevo)
    monkeypatch.setattr("app.services.r2.url_publica", lambda clave: f"https://r2.local/{clave}")
    cola_media.abrir()

    frenada = await tl.enviar_fotos_producto(_Sesion(medios), TEL, "quesillo", etiqueta="de yuca")
    permitida = await tl.enviar_fotos_producto(
        _Sesion(medios), TEL, "quesillo", etiqueta="de plátano"
    )
    assert frenada.get("ya_mostrado") is True
    assert permitida.get("enviadas", 0) > 0, "la versión nueva tiene que salir"


async def test_el_interruptor_off_apaga_la_memoria(monkeypatch):
    """NO-disparo: `fotos_memoria='off'` en `configuracion` vuelve a la conducta vieja."""
    r = await _correr_tool(monkeypatch, ya_mostrada=True, interruptor="off")
    assert r.get("enviadas") == 3


async def test_interruptor_ausente_o_con_basura_deja_la_garantia_puesta(monkeypatch):
    """El fail-safe del interruptor: solo un 'off' con todas sus letras la apaga."""
    r = await _correr_tool(monkeypatch, ya_mostrada=True, interruptor="cualquier cosa")
    assert r.get("ya_mostrado") is True


async def test_el_simulador_queda_exento(monkeypatch):
    """NO-disparo: el teléfono fijo del panel acumula filas para siempre — con el candado
    encima, la SEGUNDA demo de la dueña parecería rota (la confusión que ya se arregló una vez)."""
    r = await _correr_tool(monkeypatch, ya_mostrada=True, telefono="__simulador__ana")
    assert r.get("enviadas", 0) > 0
    assert "(SIMULADOR)" in r["nota"]


async def test_ante_fallo_de_bd_la_herramienta_ENVIA(monkeypatch):
    """El lado seguro cambia según quién llama: la red que EMPUJA se calla (True), pero la
    herramienta en plena venta envía (False). Un hipo de Postgres no deja al bot sin fotos."""

    def _reventada():
        raise RuntimeError("postgres tosiendo")

    monkeypatch.setattr(tl, "get_session_factory", _reventada)
    assert await tl.media_ya_mostrada(TEL, "Quesillo", si_falla=False) is False
    assert await tl.media_ya_mostrada(TEL, "Quesillo") is True  # la red sigue callándose


async def test_llamarla_dos_veces_en_el_MISMO_turno_no_duplica(monkeypatch):
    """El candado intra-turno: la fila de `mensajes` se escribe recién al VACIAR la cola, así
    que la BD no ve lo del propio turno — la cola sí. 2ª llamada ⇒ cero duplicados."""
    r1 = await _correr_tool(monkeypatch, ya_mostrada=False)
    assert r1.get("enviadas") == 3 and cola_media.cuantos() == 3
    # La memoria por BD sigue diciendo "no mostrada" (las filas aún no existen): aun así frena.
    r2 = await _correr_tool(monkeypatch, ya_mostrada=False)
    assert r2.get("enviadas") == 0
    assert r2.get("ya_mostrado") is True
    assert cola_media.cuantos() == 3, "la cola tiene que seguir con 3, no con 6"


async def test_intra_turno_los_archivos_NUEVOS_si_pasan(monkeypatch):
    """NO-disparo intra-turno: pidió la otra masa en el mismo turno ⇒ sale la foto nueva (la
    neutra, que ya estaba encolada, no se repite)."""
    medios = [_foto(1, "base de yuca"), _foto(2, "base de plátano"), _foto(3)]
    r1 = await _correr_tool(monkeypatch, ya_mostrada=False, medios=medios, etiqueta="de yuca")
    assert r1.get("enviadas") == 2  # la de yuca + la neutra
    r2 = await _correr_tool(monkeypatch, ya_mostrada=False, medios=medios, etiqueta="de plátano")
    assert r2.get("enviadas") == 1, "solo la de plátano: la neutra ya estaba en la cola"
    assert cola_media.cuantos() == 3


async def test_maximo_va_capado_a_3_en_codigo(monkeypatch):
    """El tope anti-spam es del CÓDIGO: un maximo=50 (o basura) alucinado no son 50 archivos."""
    medios = [_foto(i) for i in range(1, 6)]
    r = await _correr_tool(monkeypatch, medios=medios, maximo=50)
    assert r.get("enviadas") == 3
    r2 = await _correr_tool(monkeypatch, medios=medios, maximo="basura")
    assert r2.get("enviadas") == 0 and r2.get("ya_mostrado") is True  # mismos 3 del turno


# ══════════════════════════════════════════════════════════════════════════════════
# LA PIEZA: `cola_media.ya_encolada` y el schema (R51: lo no declarado se TIRA)
# ══════════════════════════════════════════════════════════════════════════════════

def test_ya_encolada_solo_ve_la_cola_abierta():
    assert cola_media.ya_encolada("Quesillo · media 1") is False  # cerrada ⇒ nunca frena

    async def _nada():
        return None

    cola_media.abrir()
    cola_media.encolar("Quesillo · media 1", _nada)
    assert cola_media.ya_encolada("Quesillo · media 1") is True
    assert cola_media.ya_encolada("Quesillo · media 2") is False


def test_el_schema_declara_reenviar_o_el_filtro_lo_TIRARIA():
    """🔴 R51: `_solo_lo_declarado` recorta a lo que el schema declara. Sin esto, el modelo
    pasa `reenviar=true`, el filtro lo tira en silencio, y el reenvío pedido no existe."""
    assert "reenviar" in tl._PARAMS_DECLARADOS["enviar_fotos_producto"]
    limpio = tl._solo_lo_declarado(
        "enviar_fotos_producto", {"nombre": "Quesillo", "reenviar": True}
    )
    assert limpio.get("reenviar") is True, "el filtro se comió la válvula"


# ══════════════════════════════════════════════════════════════════════════════════
# EL CABLEADO: `_ejecutar_con_guardas` enciende la válvula si el cliente pide VER
# ══════════════════════════════════════════════════════════════════════════════════

async def _guardas(mensaje, tool="enviar_fotos_producto", args=None):
    visto: list = []

    async def ejecutar(nombre, args, telefono):
        visto.append(dict(args))
        return {"enviadas": 1}

    await ag._ejecutar_con_guardas(
        ejecutar, tool, dict(args or {"nombre": "Quesillo"}), TEL, mensaje, None
    )
    return visto[0]


async def test_si_el_cliente_pide_ver_el_codigo_enciende_reenviar():
    """La garantía del reenvío tampoco depende del modelo: las palabras del cliente mandan."""
    args = await _guardas("mandame la foto otra vez porfa")
    assert args.get("reenviar") is True


async def test_la_pregunta_de_omaira_NO_enciende_reenviar():
    """NO-disparo (el caso literal): '¿cuánto salen?' no es pedir ver — el candado se queda."""
    args = await _guardas("cuanto salen las empanadas?")
    assert "reenviar" not in args


async def test_la_valvula_es_solo_de_la_tool_de_fotos():
    args = await _guardas("mandame la foto", tool="enviar_catalogo", args={})
    assert "reenviar" not in args


# ══════════════════════════════════════════════════════════════════════════════════
# POR LA PUERTA REAL (`responder`, modo uno): el MODELO llama la herramienta — el molde
# que la suite no tenía, y el camino exacto del bug de Omaira
# ══════════════════════════════════════════════════════════════════════════════════

ACTIVAS = frozenset({
    "ver_catalogo", "info_producto", "enviar_catalogo", "pedir_ayuda", "enviar_fotos_producto",
})
HISTORIAL = [{"role": "assistant", "content": "hola 💚 dime, en que te puedo ayudar?"}]


@pytest.fixture(autouse=True)
def modo_uno(monkeypatch):
    """Solo se falsean las lecturas de configuración (molde de test_asegurar_foto.py)."""

    async def _config():
        return "uno", "modelo/uno", "modelo/uno"

    async def _modelo():
        return "modelo/uno"

    async def _activas():
        return ACTIVAS

    async def _partes(nombre_cliente, telefono, *, activas=None, quien="uno"):
        return ("Eres Alejandra. CATÁLOGO (para ti): Quesillo — $8.00", "Hoy es viernes.")

    monkeypatch.setattr(ag, "leer_config_agente", _config)
    monkeypatch.setattr(ag, "leer_modelo_ia", _modelo)
    monkeypatch.setattr(ag, "leer_tools_activas", _activas)
    monkeypatch.setattr(ag, "construir_partes_prompt", _partes)
    return None


def _llm_que_llama_la_tool(textos: list[str]):
    """El molde nuevo: 1ª iteración `tool_calls` (la llamada proactiva del modelo), después los
    textos dados, en orden. Devuelve (llm, llamadas_al_llm)."""
    turnos: list[int] = []

    async def llm(messages, tools, model):
        turnos.append(1)
        if len(turnos) == 1:
            return {"choices": [{"message": {
                "role": "assistant", "content": None,
                "tool_calls": [{
                    "id": "tc1", "type": "function",
                    "function": {"name": "enviar_fotos_producto",
                                 "arguments": '{"nombre": "Quesillo"}'},
                }],
            }}]}
        return {"choices": [{"message": {
            "role": "assistant", "content": textos[min(len(turnos) - 2, len(textos) - 1)],
        }}]}

    return llm, turnos


def _ejecutar_real(monkeypatch, *, ya_mostrada):
    """`ejecutar` que DELEGA en la herramienta REAL (con la BD falseada): así el candado que se
    prueba es el de verdad, no un doble que devuelve lo que el test quiere oír."""
    llamadas: list[tuple[str, dict]] = []

    async def _prod(session, nombre):
        return SimpleNamespace(id=7, nombre="Quesillo")

    async def _relevo(session, telefono):
        return False

    async def _mostrada(telefono, nombre, etiqueta=None, *, si_falla=True):
        return ya_mostrada

    monkeypatch.setattr(tl, "_buscar_producto", _prod)
    monkeypatch.setattr(tl, "_la_duena_tomo_el_chat", _relevo)
    monkeypatch.setattr(tl, "media_ya_mostrada", _mostrada)
    monkeypatch.setattr("app.services.r2.url_publica", lambda clave: f"https://r2.local/{clave}")

    async def ejecutar(nombre, args, telefono):
        llamadas.append((nombre, dict(args)))
        if nombre != "enviar_fotos_producto":
            return {"ok": True}
        return await tl.enviar_fotos_producto(
            _Sesion([_foto(1), _foto(2), _foto(3)]), telefono, **args
        )

    return ejecutar, llamadas


async def test_puerta_real_el_turno_de_omaira_ya_no_reenvia(monkeypatch):
    """EL CASO ENTERO: pregunta de precio, el modelo llama la tool proactivo, el producto ya se
    mostró ⇒ cero fotos nuevas, y el turno sigue de lo más normal."""
    llm, _ = _llm_que_llama_la_tool(["El Quesillo es cremosito, mi amor 💚 te animas?"])
    ejecutar, llamadas = _ejecutar_real(monkeypatch, ya_mostrada=True)
    cola_media.abrir()
    salida = await ag.responder(
        TEL, "cuanto salen las empanadas?", list(HISTORIAL), "Omaira",
        llm=llm, ejecutar=ejecutar,
    )
    assert salida == "El Quesillo es cremosito, mi amor 💚 te animas?"
    assert cola_media.cuantos() == 0, "el caso Omaira: NINGUNA foto repetida en la cola"
    assert [n for n, _ in llamadas] == ["enviar_fotos_producto"]


async def test_puerta_real_si_lo_pide_otra_vez_las_fotos_salen(monkeypatch):
    """NO-disparo end-to-end: el cliente pidió verlas de nuevo ⇒ el código enciende `reenviar`
    en la MISMA llamada del modelo y las fotos salen."""
    llm, _ = _llm_que_llama_la_tool(["Claro mi amor, por aqui te las dejo de nuevo 💚"])
    ejecutar, llamadas = _ejecutar_real(monkeypatch, ya_mostrada=True)
    cola_media.abrir()
    salida = await ag.responder(
        TEL, "mandame la foto del quesillo otra vez", list(HISTORIAL), "Omaira",
        llm=llm, ejecutar=ejecutar,
    )
    assert salida == "Claro mi amor, por aqui te las dejo de nuevo 💚"
    assert llamadas[0][1].get("reenviar") is True, "el código encendió la válvula, no el modelo"
    assert cola_media.cuantos() == 3


async def test_puerta_real_referirse_a_la_historia_NO_es_envio_fantasma(monkeypatch):
    """La absolución: con el re-envío frenado, "ya te mandé las fotos" es VERDAD histórica.
    Sin `fotos_ya_mostradas`, la red fantasma ordenaba re-llamar (la tool se niega) y a la
    segunda vuelta escalaba a la dueña una falsa alarma — castigo por comportarse bien."""
    llm, turnos = _llm_que_llama_la_tool(
        ["Ya te mande las fotos del quesillo mas arriba, mi amor. Te animas?"]
    )
    ejecutar, _ = _ejecutar_real(monkeypatch, ya_mostrada=True)
    cola_media.abrir()
    salida = await ag.responder(
        TEL, "cuanto sale el quesillo?", list(HISTORIAL), "Omaira",
        llm=llm, ejecutar=ejecutar,
    )
    assert salida == "Ya te mande las fotos del quesillo mas arriba, mi amor. Te animas?"
    assert len(turnos) == 2, "sin regaño de la red fantasma: dos llamadas al modelo y listo"


async def test_puerta_real_la_red_fantasma_sigue_viva_para_la_mentira_de_verdad(monkeypatch):
    """El CONTROL de la absolución: si la tool NO envió y NO era 'ya mostrada' (falló de
    verdad), afirmar el envío se sigue frenando como siempre — la red no se desarmó."""
    llm, turnos = _llm_que_llama_la_tool([
        "Ya te mande las fotos del quesillo, mi amor.",   # mentira: no salió ninguna
        "Se me complico mandartelas ahorita 🙏 te cuento del quesillo mientras?",
    ])

    llamadas: list = []

    async def ejecutar(nombre, args, telefono):
        llamadas.append(nombre)
        return {"enviadas": 0, "nota": "no se pudieron enviar las fotos de 'Quesillo' ahora"}

    cola_media.abrir()
    salida = await ag.responder(
        TEL, "cuanto sale el quesillo?", list(HISTORIAL), "Omaira",
        llm=llm, ejecutar=ejecutar,
    )
    assert salida == "Se me complico mandartelas ahorita 🙏 te cuento del quesillo mientras?"
    assert len(turnos) == 3, "hubo regaño de la red fantasma y el modelo se corrigió"


# ══════════════════════════════════════════════════════════════════════════════════
# LA HOJA (modo dos, dormido pero coherente): la Voz no puede recibir un hecho falso
# ══════════════════════════════════════════════════════════════════════════════════

def test_la_hoja_le_dice_la_verdad_a_la_voz():
    bloque = hj._renderizar("enviar_fotos_producto", {"enviadas": 0, "ya_mostrado": True})
    assert "YA se le habían mostrado" in bloque
    assert "NO se pudo enviar" not in bloque, "ese es el hecho FALSO que se arregló"


def test_la_hoja_sin_ya_mostrado_sigue_diciendo_que_no_salio():
    """NO-disparo: el fallo real de envío se narra igual que siempre."""
    bloque = hj._renderizar("enviar_fotos_producto", {"enviadas": 0})
    assert "NO se pudo enviar" in bloque


def test_la_hoja_apunta_el_flag_para_la_red_de_la_voz():
    hoja = hj.HojaDeHechos()
    hoja.anotar_tool("enviar_fotos_producto", {"enviadas": 0, "ya_mostrado": True})
    assert hoja.fotos_ya_mostradas is True
    assert hoja.fotos_enviadas == 0
