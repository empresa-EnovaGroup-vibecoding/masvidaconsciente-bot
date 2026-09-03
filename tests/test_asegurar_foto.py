"""LA RED DE LA FOTO — ver vende, y el modelo describe en vez de mostrar.

EL CASO REAL (smoke de 7 turnos contra el bot real, 2026-08-08, corrido DOS veces): CERO fotos
de producto en toda la conversación, con una clienta que llegó a decir *"ok esa quiero"*. La
regla de FOTOS/VIDEO del prompt (enorme, con 🔥 y "ÚSALA PROACTIVA") existe desde hace semanas
— y el modelo la ignora. Por eso `_asegurar_foto` es CÓDIGO, familia de `_asegurar_catalogo`:
cuando el turno queda enfocado en UN producto y no salió ninguna media, la foto la manda el
código por la misma puerta que usa el modelo (`enviar_fotos_producto`, que ya trae las guardas
de simulador y relevo — aquí no se duplican).

⚠️ La mitad de este archivo son los casos que NO deben disparar: el cliente todavía entre
varios productos, el producto ya mostrado, la media ya enviada, el saludo, la herramienta
apagada. Una red que bombardea fotos es spam que le baja la calidad al número — frenar de más
aquí no rompe nada (la foto es un extra), pero disparar de más sí.
"""

import pytest

from app.agent import agent as ag
from app.agent.agent import _asegurar_foto, _es_charla_pura
from app.agent.tools import (
    _buscar_producto,
    _productos_nombrados_en,
    etiqueta_del_cliente,
    ver_catalogo,
)

NOMBRES = ["Empanadas", "Empanadas Keto", "Pan de Sándwich", "Quesillo", "Galletas New York"]

# El nombre REAL del taller: UN producto, dos versiones, dos fotos etiquetadas. El bot jamás lo
# dice entero — confirma "las Empanadas de masa de plátano" — y la primera versión de esta red
# era CIEGA a eso (hueco encontrado por Erwin con el bot real, 2026-08-08).
COMPUESTO = "Empanadas de masa de yuca o de masa de plátano"


# ══════════════════════════════════════════════════════════════════════════════════
# LA PIEZA: qué productos nombra un texto (semántica exacto-primero, la del bug $12/$14)
# ══════════════════════════════════════════════════════════════════════════════════

def test_un_solo_producto_nombrado():
    frase = "Buena elección 💚 el Quesillo es cremosito y rinde full."
    assert _productos_nombrados_en(frase, NOMBRES) == ["Quesillo"]


def test_el_mas_especifico_gana_su_trozo_de_texto():
    """'Empanadas Keto' NO cuenta también como 'Empanadas' — la lección del bug $12/$14:
    el nombre exacto manda y el más específico se queda con su trozo."""
    frase = "Las Empanadas Keto vienen 4 por paquete."
    assert _productos_nombrados_en(frase, NOMBRES) == ["Empanadas Keto"]


def test_tolera_el_singular_del_bot():
    """El bot escribe "la empanada keto" y el producto se llama 'Empanadas Keto'."""
    frase = "te recomiendo la empanada keto, es la mas pedida"
    assert _productos_nombrados_en(frase, NOMBRES) == ["Empanadas Keto"]


def test_dos_productos_son_dos_menciones():
    """"Empanadas y Empanadas Keto" ofrece DOS opciones: cada una tiene su trozo de texto."""
    frase = "tenemos Empanadas y también Empanadas Keto"
    assert sorted(_productos_nombrados_en(frase, NOMBRES)) == ["Empanadas", "Empanadas Keto"]


def test_palabra_completa_jamas_substring():
    """'Pan' no puede calzar dentro de em-PAN-adas (el bug del buscador viejo, otra vez)."""
    frase = "esas empanadas están buenísimas"
    assert _productos_nombrados_en(frase, ["Pan", "Empanadas"]) == ["Empanadas"]


def test_sin_menciones_lista_vacia():
    assert _productos_nombrados_en("no tenemos eso, mira el catálogo 💚", NOMBRES) == []
    assert _productos_nombrados_en("", NOMBRES) == []


def test_el_caso_real_del_taller_nombre_compuesto():
    """El turno EXACTO en que la foto no salió: el bot confirma una VERSIÓN del nombre
    compuesto ("las Empanadas de masa de plátano") y eso tiene que resolver al producto."""
    frase = "Listo. Las Empanadas de masa de plátano vienen en paquete de 8 unidades. ¿Cuántos paquetes quieres y de qué relleno?"
    assert _productos_nombrados_en(frase, [COMPUESTO, "Pan Keto", "Quesillo"]) == [COMPUESTO]


def test_las_dos_versiones_del_mismo_producto_no_son_ambiguedad():
    """El primer turno real: el bot ofrece las DOS masas. Son EL MISMO producto — una sola
    mención, no dos (ambigüedad es solo entre productos DISTINTOS). Que ahí no salga foto lo
    decide el guard del "¿cuál…?", no un falso empate."""
    frase = (
        "Tengo Empanadas de masa de plátano y Empanadas de masa de yuca — ambas son "
        "saludables y sin gluten. ¿De cuál prefieres?"
    )
    assert _productos_nombrados_en(frase, [COMPUESTO, "Pan Keto"]) == [COMPUESTO]


def test_la_cabeza_sola_tambien_resuelve_el_compuesto():
    """"las empanadas" a secas = el producto compuesto, cuando nadie más reclama ese nombre."""
    assert _productos_nombrados_en("Perfecto, las empanadas entonces 💚", [COMPUESTO, "Quesillo"]) == [COMPUESTO]


def test_forma_en_colision_entre_productos_distintos_se_descarta():
    """Si además del compuesto existiera un producto "Empanadas" a secas, "las empanadas" la
    reclamarían LOS DOS: la forma se descarta entera. Mejor ninguna foto que la equivocada
    (la doctrina del bug $12/$14)."""
    assert _productos_nombrados_en("Perfecto, las empanadas entonces 💚", ["Empanadas", COMPUESTO]) == []


# El catálogo REAL tiene un producto llamado exactamente ``CHOCOLATE`` además de tortas
# cuyo sabor puede ser chocolate. También tiene ``Kéfir de Leche...`` y ``Yogurt
# Kéfirado``: el primero se pide naturalmente como "kéfir", sin recitar el título entero.
# Estos casos prueban la IDENTIDAD del producto, no una lista manual de excepciones.
CATALOGO_NOMBRES_PARCIALES = [
    "Torta baja en carbohidratos",
    "Tortas keto",
    "Untable de Chocolate",
    "CHOCOLATE",
    "Kéfir de Leche de cabra de libre pastoreo",
    "Yogurt Kéfirado",
]


def test_chocolate_como_sabor_no_se_convierte_en_el_producto_chocolate():
    """El texto que destapó el bug: hay DOS tortas; ``chocolate`` es un sabor, no una
    tercera oferta. Si cuenta como producto, el tope anti-spam ve 3 y apaga todas las fotos."""
    frase = (
        "Tenemos dos tipos de tortas. Torta baja en carbohidratos, en sabores como limón, "
        "zanahoria, naranja, piña, vainilla, marmoleada, manzana canela y cambur. Disponible "
        "en 250g, 500g y 1kg. Tortas keto, en sabores limón, almendras, chocolate y pistacho. "
        "También en 250g, 500g y 1kg. Cuál te provoca?"
    )
    assert sorted(_productos_nombrados_en(frase, CATALOGO_NOMBRES_PARCIALES)) == sorted([
        "Torta baja en carbohidratos", "Tortas keto",
    ])


def test_chocolate_a_secas_si_es_el_producto_chocolate():
    """Sin un contexto de sabor/ingrediente, el título exacto manda."""
    assert _productos_nombrados_en(
        "Sí, tenemos chocolate", CATALOGO_NOMBRES_PARCIALES
    ) == ["CHOCOLATE"]


def test_el_titulo_mas_largo_separa_untable_de_chocolate():
    """``Untable de Chocolate`` es su propio producto; no se parte en dos identidades."""
    assert _productos_nombrados_en(
        "Te recomiendo el untable de chocolate", CATALOGO_NOMBRES_PARCIALES
    ) == ["Untable de Chocolate"]


def test_kefir_a_secas_resuelve_el_titulo_que_empieza_por_kefir():
    """``kéfir`` es una palabra completa del título del Kéfir de Leche; ``kéfirado``
    es otra palabra y pertenece al yogurt. Una no puede calzar como prefijo de la otra."""
    assert _productos_nombrados_en(
        "¿Tienes kéfir?", CATALOGO_NOMBRES_PARCIALES
    ) == ["Kéfir de Leche de cabra de libre pastoreo"]


def test_yogurt_de_kefir_resuelve_el_yogurt_no_el_kefir_de_leche():
    assert _productos_nombrados_en(
        "Quiero el yogurt de kéfir", CATALOGO_NOMBRES_PARCIALES
    ) == ["Yogurt Kéfirado"]


class _ResultadoProductos:
    def __init__(self, productos):
        self._productos = productos

    def scalars(self):
        return self

    def all(self):
        return self._productos


class _SesionProductos:
    def __init__(self, productos):
        self._productos = productos

    async def execute(self, _consulta):
        return _ResultadoProductos(self._productos)


class _Producto:
    def __init__(self, id_, nombre, descripcion="", categoria="dulceria"):
        self.id = id_
        self.nombre = nombre
        self.descripcion = descripcion
        self.categoria = categoria
        self.disponible = True


def _catalogo_colisiones(*, sabores_en_descripcion=False):
    torta_1 = "Harina de almendra y coco"
    torta_2 = "Harina de almendra"
    if sabores_en_descripcion:
        # El doble unitario no modela ProductoVariante; ``ver_catalogo`` recibe esos sabores
        # como texto extra en producción. El banco real prueba ese cableado contra Postgres.
        torta_1 += ". Sabores: limón y chocolate"
        torta_2 += ". Sabores: almendra y chocolate"
    return [
        # En el catálogo vivo los sabores están en las VARIANTES. El carril del dinero solo
        # mira esta descripción: si el test pusiera "chocolate" aquí escondería la caída al
        # difuso que llegó a devolver Untable de Chocolate para "torta de chocolate".
        _Producto(1, "Torta baja en carbohidratos", torta_1),
        _Producto(2, "Tortas keto", torta_2),
        _Producto(3, "Untable de Chocolate", "Chocolate Dubai y almendras"),
        _Producto(4, "CHOCOLATE", "Cacao casi puro"),
        _Producto(5, "Kéfir de Leche de cabra de libre pastoreo", "Bebida fermentada"),
        _Producto(6, "Yogurt Kéfirado", "Yogurt con cultivos de kéfir"),
    ]


@pytest.mark.parametrize(("pedido", "esperado"), [
    ("kéfir", "Kéfir de Leche de cabra de libre pastoreo"),
    ("tienes kéfir de leche", "Kéfir de Leche de cabra de libre pastoreo"),
    ("quiero yogurt de kéfir", "Yogurt Kéfirado"),
    ("quiero chocolate", "CHOCOLATE"),
    ("quiero el untable de chocolate", "Untable de Chocolate"),
])
async def test_el_carril_estricto_resuelve_titulos_parciales_sin_confundirlos(pedido, esperado):
    producto = await _buscar_producto(_SesionProductos(_catalogo_colisiones()), pedido)
    assert producto is not None
    assert producto.nombre == esperado


async def test_torta_de_chocolate_no_se_cobra_como_el_producto_chocolate():
    """Hay dos tortas que pueden llevar ese sabor: el resultado correcto es preguntar cuál,
    nunca escoger el producto independiente ``CHOCOLATE`` por encontrar esa palabra dentro."""
    producto = await _buscar_producto(
        _SesionProductos(_catalogo_colisiones()), "quiero una torta de chocolate"
    )
    assert producto is None


async def test_tortas_es_una_familia_y_el_cobro_pregunta_cual():
    producto = await _buscar_producto(
        _SesionProductos(_catalogo_colisiones()), "envíame por favor las tortas que tienes"
    )
    assert producto is None


class _SesionCatalogo:
    """Primera consulta = productos; las siguientes = variantes (vacías para esta prueba)."""

    def __init__(self, productos):
        self._productos = productos
        self._consultas = 0

    async def execute(self, _consulta):
        self._consultas += 1
        return _ResultadoProductos(self._productos if self._consultas == 1 else [])


@pytest.mark.parametrize(("busqueda", "esperados"), [
    ("kéfir", ["Kéfir de Leche de cabra de libre pastoreo"]),
    ("kéfir de leche", ["Kéfir de Leche de cabra de libre pastoreo"]),
    ("yogurt de kéfir", ["Yogurt Kéfirado"]),
    ("chocolate", ["CHOCOLATE"]),
    ("untable de chocolate", ["Untable de Chocolate"]),
    ("envíame por favor las tortas que tienes", [
        "Torta baja en carbohidratos", "Tortas keto",
    ]),
    ("torta de chocolate", ["Torta baja en carbohidratos", "Tortas keto"]),
])
async def test_catalogo_separa_identidad_de_producto_y_atributos(busqueda, esperados):
    resultado = await ver_catalogo(
        _SesionCatalogo(_catalogo_colisiones(sabores_en_descripcion=True)),
        "__prueba__",
        busqueda=busqueda,
    )
    assert [p["nombre"] for p in resultado["productos"]] == esperados


async def test_una_categoria_directa_no_se_reduce_a_los_titulos_que_se_le_parecen():
    productos = [
        _Producto(1, "Premezclas", categoria="harinas"),
        _Producto(2, "Harina de Almendra", categoria="harinas"),
        _Producto(3, "Harina de Merey", categoria="harinas"),
        _Producto(4, "Harina de Yuca", categoria="otro"),
    ]
    resultado = await ver_catalogo(_SesionCatalogo(productos), "__prueba__", busqueda="harinas")
    assert [p["nombre"] for p in resultado["productos"]] == [
        "Premezclas", "Harina de Almendra", "Harina de Merey",
    ]


@pytest.mark.parametrize(("mensaje", "esperada"), [
    ("de platano", "platano"),                    # el turno real de Erwin
    ("la de yuca porfa 🙏", "yuca"),              # con relleno alrededor: solo el token distintivo
    ("las empanadas", None),                      # la cabeza no elige versión
    ("mejor la de yuca no la de platano", None),  # nombró las dos: van las generales
    ("", None),
])
def test_etiqueta_del_cliente_en_nombre_compuesto(mensaje: str, esperada: str | None):
    assert etiqueta_del_cliente(COMPUESTO, mensaje) == esperada


def test_etiqueta_solo_existe_en_nombres_compuestos():
    """En un producto simple no hay versiones que elegir: nunca se filtra nada."""
    assert etiqueta_del_cliente("Pan Keto", "de platano") is None


@pytest.mark.parametrize(("mensaje", "es_charla"), [
    ("hola buenas tardes", True),
    ("gracias!! chao", True),
    ("👍", True),
    ("", True),
    ("ok esa quiero", False),      # el turno 4 del smoke: AHÍ la foto tenía que salir
    ("quiero el quesillo", False),
])
def test_charla_pura(mensaje: str, es_charla: bool):
    assert _es_charla_pura(mensaje) is es_charla


# ══════════════════════════════════════════════════════════════════════════════════
# LA RED, pieza a pieza (con las dos consultas de BD falseadas)
# ══════════════════════════════════════════════════════════════════════════════════

async def _correr_red(
    monkeypatch,
    *,
    texto: str = "Buena elección 💚 el Quesillo es cremosito y rinde full. te lo preparo?",
    mensaje: str = "ok esa quiero",
    puede_fotos: bool = True,
    hubo_media: bool = False,
    enfocado: str | list | None = "Quesillo",
    enfocado_cliente: str | list | None = "__igual__",
    ya_mostrada: bool = False,
    resultado_tool: dict | Exception | None = None,
):
    """Corre `_asegurar_foto` y devuelve (tools_llamadas, veces_que_se_resolvio_producto)."""
    llamadas: list[tuple[str, dict]] = []
    resoluciones: list[str] = []

    def _como_lista(v):
        if v is None:
            return []
        return list(v) if isinstance(v, list) else [v]

    async def _enfocado(t, maximo=2):
        """El doble de `productos_enfocados`. Devuelve LISTA (la red pasó a plural el 08-21) y
        distingue si le preguntan por el TEXTO DEL BOT o por el MENSAJE DEL CLIENTE — la red
        consulta los dos, y sin distinguirlos no se puede probar la ceguera que se arregló."""
        resoluciones.append(t)
        lista = (_como_lista(enfocado_cliente)
                 if (t == mensaje and enfocado_cliente != "__igual__")
                 else _como_lista(enfocado))
        # 🔴 El doble RESPETA el tope, como la pieza real: por encima devuelve VACÍO (no recorta,
        # que mostrar 2 de 5 sería elegir por el cliente). Sin esto, bajar
        # `_MAX_FOTOS_POR_TURNO` a 1 no rompía ningún test — R44 salía verde.
        return [] if len(lista) > maximo else lista

    async def _mostrada(telefono, nombre):
        return ya_mostrada

    async def ejecutar(nombre, args, telefono):
        llamadas.append((nombre, args))
        if isinstance(resultado_tool, Exception):
            raise resultado_tool
        return resultado_tool if resultado_tool is not None else {"enviadas": 2, "producto": enfocado}

    monkeypatch.setattr(ag, "productos_enfocados", _enfocado)
    monkeypatch.setattr(ag, "media_ya_mostrada", _mostrada)
    await _asegurar_foto(
        texto, "584240000000", mensaje, ejecutar,
        puede_fotos=puede_fotos, hubo_media=hubo_media,
    )
    return llamadas, resoluciones


async def test_dispara_y_manda_la_foto_del_producto_exacto(monkeypatch):
    """EL CASO DEL SMOKE: turno enfocado en UN producto, cero media → la foto la manda el
    código, con el nombre EXACTO resuelto (jamás uno parecido)."""
    llamadas, _ = await _correr_red(monkeypatch)
    assert llamadas == [("enviar_fotos_producto", {"nombre": "Quesillo", "maximo": 1})]


async def test_cuando_el_cliente_dijo_la_version_la_etiqueta_viaja(monkeypatch):
    """EL CASO DE ERWIN: producto compuesto, el cliente eligió "de platano" → la llamada lleva
    `etiqueta` y la herramienta manda la foto que la dueña nombró así, jamás la de la otra
    masa. Sin versión elegida no se filtra nada (el caso de arriba: sin `etiqueta`)."""
    llamadas, _ = await _correr_red(
        monkeypatch,
        texto="Listo. Las Empanadas de masa de plátano vienen en paquete de 8 unidades. ¿Cuántos paquetes quieres y de qué relleno?",
        mensaje="de platano",
        enfocado=COMPUESTO,
    )
    assert llamadas == [("enviar_fotos_producto", {"nombre": COMPUESTO, "maximo": 1, "etiqueta": "platano"})]


async def test_con_la_tool_apagada_la_red_no_existe(monkeypatch):
    """Apagada en la config de herramientas ⇒ ni siquiera se intenta resolver el producto."""
    llamadas, resoluciones = await _correr_red(monkeypatch, puede_fotos=False)
    assert llamadas == []
    assert resoluciones == [], "con la tool apagada no hay nada que resolver"


async def test_si_ya_salio_media_este_turno_no_bombardea(monkeypatch):
    """El modelo ya mandó fotos o el catálogo: sumarle más media es spam."""
    llamadas, resoluciones = await _correr_red(monkeypatch, hubo_media=True)
    assert llamadas == []
    assert resoluciones == []


async def test_el_mismo_producto_no_se_muestra_dos_veces(monkeypatch):
    """El candado de la conversación: si ya se le mostró (tabla mensajes), no se repite."""
    llamadas, _ = await _correr_red(monkeypatch, ya_mostrada=True)
    assert llamadas == []


async def test_ambiguo_o_sin_producto_no_dispara(monkeypatch):
    """El texto no nombra UN único producto resoluble: sin certeza, ninguna foto."""
    llamadas, _ = await _correr_red(
        monkeypatch,
        texto="tenemos empanadas de yuca y de plátano, las dos quedan brutales",
        enfocado=None,
    )
    assert llamadas == []


async def test_saludo_o_small_talk_no_dispara(monkeypatch):
    llamadas, resoluciones = await _correr_red(
        monkeypatch,
        mensaje="hola buenas tardes",
        texto="Hola Ana, buenas tardes 💚 el Quesillo está recién hecho hoy",
    )
    assert llamadas == []
    assert resoluciones == []


async def test_si_pidio_el_catalogo_ese_turno_no_es_de_fotos(monkeypatch):
    llamadas, resoluciones = await _correr_red(monkeypatch, mensaje="mandame el catalogo porfa")
    assert llamadas == []
    assert resoluciones == []


async def test_con_TRES_o_mas_productos_no_se_manda_nada(monkeypatch):
    """El cliente sigue entre varios: mandarle 3 fotos es spam y quema el número con Meta.

    🔴 ESTE TEST CAMBIÓ DE MECANISMO EL 2026-08-21, no de intención. Antes comprobaba que la
    guarda `_OFRECE_OPCIONES` (`\bcual\b`) cortara la red ANTES de resolver nada — y esa guarda se
    quitó porque apagaba la foto también cuando el producto YA estaba elegido y el bot preguntaba
    por el RELLENO ("Tenemos de carne mechada, pollo o queso de cabra. ¿Cuál prefieres?" → cero
    fotos en el turno de más interés). Quien decide ahora es el TOPE de productos, que es la señal
    de verdad: 3+ nombrados ⇒ `productos_enfocados` devuelve lista vacía ⇒ la red no dispara.
    Lo que se protege sigue siendo lo mismo: NO bombardear."""
    llamadas, _ = await _correr_red(
        monkeypatch,
        texto="tengo pan de sándwich, de hamburguesa y keto 😊 cuál prefieres?",
        enfocado=None,   # lo que devuelve el tope con 3+ nombrados
    )
    assert llamadas == []


async def test_el_TOPE_de_verdad_con_tres_productos_en_el_texto(monkeypatch):
    """El de arriba usa el doble; este ejercita `productos_enfocados` REAL contra un catálogo
    falso — si no, quitar el tope dejaría la suite en verde (la lección de R36/R29/R17)."""
    from app.agent import tools as tl

    class _Prod:
        def __init__(self, id_, nombre, categoria="dulceria"):
            self.id = id_
            self.nombre = nombre
            self.categoria = categoria
            self.disponible = True

    catalogo = [
        _Prod(1, "Pan de Sándwich", "panaderia"),
        _Prod(2, "Pan de Hamburguesa", "panaderia"),
        _Prod(3, "Pan Keto", "panaderia"),
        _Prod(4, "Quesillo"),
        _Prod(5, "Torta baja en carbohidratos"),
        _Prod(6, "Tortas keto"),
        _Prod(7, "Premezclas", "harinas"),
        _Prod(8, "Harina de Almendra", "harinas"),
        _Prod(9, "Harina de Merey", "harinas"),
    ]

    class _R:
        def scalars(self):
            return self

        def all(self):
            return catalogo

    class _S:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return False

        async def execute(self, _q):
            return _R()

    async def _resolver(_session, mencion):
        """`_buscar_producto` de verdad hace SU propia consulta; aquí solo interesa el TOPE."""
        return _Prod(99, mencion)

    monkeypatch.setattr(tl, "get_session_factory", lambda: (lambda: _S()))
    monkeypatch.setattr(tl, "_buscar_producto", _resolver)

    # tres productos nombrados ⇒ por encima del tope ⇒ nada (corta ANTES de resolver)
    assert await tl.productos_enfocados(
        "tengo Pan de Sándwich, Pan de Hamburguesa y Pan Keto", 2
    ) == []
    # y con DOS sí resuelve — para eso se cambió
    assert len(await tl.productos_enfocados("tengo Pan Keto y Quesillo", 2)) == 2
    # con UNO, uno (el caso mayoritario no cambia)
    assert len(await tl.productos_enfocados("te recomiendo el Quesillo", 2)) == 1
    # una familia que tiene exactamente DOS productos muestra una foto de cada uno
    assert await tl.productos_enfocados(
        "envíame por favor las tortas que tienes", 2
    ) == ["Torta baja en carbohidratos", "Tortas keto"]
    # categoría de tres: no elige dos por el cliente ni manda una galería parcial
    assert await tl.productos_enfocados("muéstrame las harinas", 2) == []


async def test_si_no_hay_fotos_cargadas_no_pasa_nada(monkeypatch):
    """La tool dice "no hay fotos": el texto ya salió con la verdad y aquí no se toca nada.
    (No revienta, no reintenta, no promete.)"""
    llamadas, _ = await _correr_red(
        monkeypatch,
        resultado_tool={"enviadas": 0, "nota": "'Quesillo' no tiene fotos ni videos cargados."},
    )
    assert llamadas == [("enviar_fotos_producto", {"nombre": "Quesillo", "maximo": 1})]


async def test_si_la_tool_revienta_el_turno_sigue_intacto(monkeypatch):
    """La foto es un empujón de venta: una excepción suya JAMÁS puede tumbar un turno bueno."""
    llamadas, _ = await _correr_red(monkeypatch, resultado_tool=RuntimeError("Meta caída"))
    assert llamadas == [("enviar_fotos_producto", {"nombre": "Quesillo", "maximo": 1})]  # lo intentó y siguió


# ══════════════════════════════════════════════════════════════════════════════════
# POR LA PUERTA REAL (`responder`, modo uno): la red está CONECTADA
# ══════════════════════════════════════════════════════════════════════════════════

ACTIVAS = frozenset({
    "ver_catalogo", "info_producto", "enviar_catalogo", "pedir_ayuda", "enviar_fotos_producto",
})
HISTORIAL = [{"role": "assistant", "content": "hola 💚 dime, en que te puedo ayudar?"}]
TEXTO_ENFOCADO = "Buena elección 💚 el Quesillo es cremosito y rinde full. lo quieres para el sabado?"


@pytest.fixture(autouse=True)
def modo_uno(monkeypatch):
    """Solo se falsean las lecturas de configuración (molde de test_modo_dos.py)."""

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


async def _correr_turno(
    monkeypatch, *, activas=None, ya_mostrada=False, enfocado="Quesillo",
    mensaje="ok esa quiero",
):
    """Un turno real de modo uno cuyo texto final queda enfocado en el Quesillo."""
    llamadas: list[tuple[str, dict]] = []

    if activas is not None:
        activas_set = frozenset(activas)

        async def _activas():
            return activas_set

        monkeypatch.setattr(ag, "leer_tools_activas", _activas)

    async def _enfocado(texto, maximo=2):
        return [] if enfocado is None else [enfocado]

    async def _mostrada(telefono, nombre):
        return ya_mostrada

    monkeypatch.setattr(ag, "productos_enfocados", _enfocado)
    monkeypatch.setattr(ag, "media_ya_mostrada", _mostrada)

    async def llm(messages, tools, model):
        return {"choices": [{"message": {"role": "assistant", "content": TEXTO_ENFOCADO}}]}

    async def ejecutar(nombre, args, telefono):
        llamadas.append((nombre, args))
        return {"enviadas": 2, "producto": "Quesillo"}

    salida = await ag.responder(
        "584240000000", mensaje, list(HISTORIAL), "Ana",
        llm=llm, ejecutar=ejecutar,
    )
    return salida, llamadas


async def test_por_la_puerta_real_la_foto_sale_y_el_texto_no_se_toca(monkeypatch):
    salida, llamadas = await _correr_turno(monkeypatch)
    assert ("enviar_fotos_producto", {"nombre": "Quesillo", "maximo": 1}) in llamadas
    assert salida == TEXTO_ENFOCADO, "la red suma una foto: el texto JAMÁS se toca"


async def test_por_la_puerta_real_la_etiqueta_del_compuesto_viaja(monkeypatch):
    """El flujo completo del caso de Erwin: cliente "de platano", producto compuesto → la
    llamada sale con `etiqueta` para que la foto sea la de ESA masa."""
    _, llamadas = await _correr_turno(monkeypatch, enfocado=COMPUESTO, mensaje="de platano")
    assert ("enviar_fotos_producto", {"nombre": COMPUESTO, "maximo": 1, "etiqueta": "platano"}) in llamadas


async def test_por_la_puerta_real_apagada_no_existe(monkeypatch):
    """Con `enviar_fotos_producto` fuera de la config, la red no existe ese turno."""
    salida, llamadas = await _correr_turno(
        monkeypatch,
        activas={"ver_catalogo", "info_producto", "enviar_catalogo", "pedir_ayuda"},
    )
    assert not any(n == "enviar_fotos_producto" for n, _ in llamadas)
    assert salida == TEXTO_ENFOCADO


async def test_por_la_puerta_real_no_repite_el_producto(monkeypatch):
    salida, llamadas = await _correr_turno(monkeypatch, ya_mostrada=True)
    assert not any(n == "enviar_fotos_producto" for n, _ in llamadas)
    assert salida == TEXTO_ENFOCADO


# ══════════════════════════════════════════════════════════════════════════════════
# 🔴 UN INGREDIENTE NO ES UNA OFERTA (bug medido el 2026-08-21 con el smoke de asesoría)
#
# El bot cerró con "Galletas New York, vienen con harina de almendra y coco" — y "Harina de
# Almendra" ES un producto del catálogo. `producto_enfocado` veía DOS menciones, creía que el
# cliente seguía eligiendo, y la RED DE LA FOTO no disparaba: 0 fotos en 5 turnos con el producto
# ya elegido. Las dos redes se peleaban: la del PITCH obliga a decir ingredientes, y eso apagaba
# la de la foto — cuanto mejor vendía, menos fotos mandaba.
# ══════════════════════════════════════════════════════════════════════════════════

CATALOGO_INSUMOS = ["Galletas New York", "Harina de Almendra", "Harina de Yuca", "Quesillo"]


@pytest.mark.parametrize(("texto", "esperado"), [
    # El caso REAL del smoke: un solo producto ofrecido, el resto son ingredientes
    ("Listo, 1 paquete de Galletas New York, vienen con harina de almendra y coco",
     ["Galletas New York"]),
    ("El Quesillo lleva harina de yuca y azúcar de coco", ["Quesillo"]),
    ("Las Galletas New York están hechas con harina de almendra", ["Galletas New York"]),
    ("Galletas New York, endulzadas con azúcar de coco", ["Galletas New York"]),
    # …y cuando SÍ se ofrece el insumo, sigue contando (una aparición fuera de contexto basta)
    ("Tenemos Harina de Almendra y Harina de Yuca", ["Harina de Almendra", "Harina de Yuca"]),
    ("Quieres las Galletas New York o la Harina de Almendra?",
     ["Galletas New York", "Harina de Almendra"]),
    # el control: un texto sin ingredientes no cambia de comportamiento
    ("Te recomiendo el Quesillo", ["Quesillo"]),
])
def test_un_ingrediente_no_cuenta_como_producto_ofrecido(texto, esperado):
    from app.agent.tools import _productos_nombrados_en

    assert sorted(_productos_nombrados_en(texto, CATALOGO_INSUMOS)) == sorted(esperado)


def test_el_texto_del_smoke_deja_UN_solo_foco():
    """La consecuencia que importa: con una sola mención, `producto_enfocado` puede resolver y la
    red de la foto vuelve a disparar."""
    from app.agent.tools import _productos_nombrados_en

    real = ("Listo, 1 paquete de Galletas New York, vienen con harina de almendra y coco, "
            "endulzadas con azúcar de coco, y duran 2 semanas. Perfectas para compartir.")
    assert len(_productos_nombrados_en(real, CATALOGO_INSUMOS)) == 1


@pytest.mark.parametrize("texto", [
    # 🔴 R34 lo pidió: con un ARTÍCULO en medio, el marcador queda a DOS palabras del producto.
    # Mirando solo una atrás, "la"/"el"/"su" tapaba el "con" y el ingrediente volvía a contar
    # como oferta — es decir, la red de la foto se apagaba igual.
    "Las Galletas New York vienen con la harina de almendra que usamos siempre",
    "El Quesillo lleva su harina de yuca artesanal",
    "Galletas New York hechas con esa harina de almendra",
])
def test_un_articulo_en_medio_no_devuelve_el_bug(texto):
    from app.agent.tools import _productos_nombrados_en

    assert _productos_nombrados_en(texto, CATALOGO_INSUMOS) == ["Galletas New York"] or \
           _productos_nombrados_en(texto, CATALOGO_INSUMOS) == ["Quesillo"], (
        f"volvió a ver dos productos: {_productos_nombrados_en(texto, CATALOGO_INSUMOS)}"
    )


# ══════════════════════════════════════════════════════════════════════════════════
# 🔴 LAS TRES CEGUERAS QUE SE ARREGLARON EL 2026-08-21 — probadas DESDE LA RED
#
# Cinco reversiones (R41, R42, R44, R45, R46) salieron VERDES con los tests de las piezas: se
# podía deshacer el arreglo entero y la suite no se enteraba. Es la CUARTA vez en el día que pasa
# lo mismo (R17, R29, R36): probar la pieza no es probar el carril.
# ══════════════════════════════════════════════════════════════════════════════════

async def test_con_un_CUAL_en_el_texto_pero_producto_claro_SI_manda_la_foto(monkeypatch):
    """🔴 R41. El turno REAL que reportó Erwin: el producto ya está elegido y el bot pregunta por
    el RELLENO. Antes, cualquier "cuál" apagaba la red y no salía ninguna foto."""
    llamadas, _ = await _correr_red(
        monkeypatch,
        texto="Tenemos de carne mechada, pollo o queso de cabra. Cuál prefieres?",
        mensaje="me gustaría saber si tienen empanadas de plátano",
        enfocado=None,                                  # el bot NO nombra el producto…
        enfocado_cliente="Empanadas de masa de plátano",  # …pero el cliente sí
    )
    assert llamadas == [("enviar_fotos_producto", {"nombre": "Empanadas de masa de plátano", "maximo": 1})], (
        f"no mandó la foto con el producto ya elegido: {llamadas}"
    )


async def test_si_el_bot_no_nombra_el_producto_se_mira_al_CLIENTE(monkeypatch):
    """🔴 R42. El bot no repite el nombre cuando ya se sobreentiende: eso dejaba la red ciega."""
    llamadas, resoluciones = await _correr_red(
        monkeypatch,
        texto="Vienen en paquete de 8 unidades. Cuántos quieres?",
        mensaje="quiero las empanadas de plátano",
        enfocado=None,
        enfocado_cliente="Empanadas de masa de plátano",
    )
    assert len(llamadas) == 1
    # y se consultó PRIMERO el texto del bot, y solo después el del cliente
    assert resoluciones == ["Vienen en paquete de 8 unidades. Cuántos quieres?",
                            "quiero las empanadas de plátano"]


async def test_con_DOS_opciones_manda_LAS_DOS_fotos(monkeypatch):
    """🔴 R44/R45. Lo pidió Erwin: a puro texto no engancha. Con dos opciones, las dos fotos."""
    llamadas, _ = await _correr_red(
        monkeypatch,
        texto="Tenemos Empanadas de masa de plátano y Empanadas Horneadas. Cuál te llama más?",
        enfocado=["Empanadas de masa de plátano", "Empanadas Horneadas"],
    )
    assert [a["nombre"] for _, a in llamadas] == [
        "Empanadas de masa de plátano", "Empanadas Horneadas",
    ], f"no mandó las dos: {llamadas}"


async def test_un_STRING_suelto_no_se_convierte_en_una_llamada_POR_LETRA(monkeypatch):
    """🔴 R46. Si la pieza devolviera un string, `for nombre in nombres` iteraría sus CARACTERES:
    45 llamadas a WhatsApp por un error de tipo. Pasó de verdad con un doble mal escrito."""
    from app.agent import agent as agente

    llamadas: list = []

    async def _string(_t, maximo=2):
        return "Quesillo"          # ← un str, no una lista

    async def _no_mostrada(_tel, _n):
        return False

    async def ejecutar(nombre, args, telefono):
        llamadas.append(args["nombre"])
        return {"enviadas": 1}

    monkeypatch.setattr(agente, "productos_enfocados", _string)
    monkeypatch.setattr(agente, "media_ya_mostrada", _no_mostrada)
    await _asegurar_foto(
        "Te recomiendo el Quesillo", "584240000000", "algo dulce", ejecutar,
        puede_fotos=True, hubo_media=False,
    )
    assert llamadas == ["Quesillo"], f"iteró caracteres: {llamadas[:6]}…"


async def test_de_dos_productos_la_ya_mostrada_se_cae_pero_la_otra_SALE(monkeypatch):
    """El filtro de repetidas no puede matar la foto que sí falta (antes un `return` seco lo hacía)."""
    from app.agent import agent as agente

    llamadas: list = []

    async def _dos(_t, maximo=2):
        return ["Quesillo", "Empanadas Horneadas"]

    async def _ya_vista(_tel, nombre):
        return nombre == "Quesillo"      # el primero ya se mostró

    async def ejecutar(nombre, args, telefono):
        llamadas.append(args["nombre"])
        return {"enviadas": 1}

    monkeypatch.setattr(agente, "productos_enfocados", _dos)
    monkeypatch.setattr(agente, "media_ya_mostrada", _ya_vista)
    await _asegurar_foto(
        "Tenemos Quesillo y Empanadas Horneadas", "584240000000", "que tienes", ejecutar,
        puede_fotos=True, hubo_media=False,
    )
    assert llamadas == ["Empanadas Horneadas"]


async def test_con_DOS_productos_manda_UN_archivo_de_cada_uno(monkeypatch):
    """🔴 Medido con tráfico REAL el 2026-08-21: al mandar dos productos salieron **5 archivos
    seguidos** por WhatsApp (la tool manda hasta 3 por producto, y 2×3 = 6). Eso es el bombardeo
    que la regla quería evitar, y arriesga la calidad del número con Meta. Con varios productos va
    UNO de cada uno; con un solo producto siguen los 3 de siempre (ahí es enseñarlo, no spam)."""
    from app.agent import agent as agente

    llamadas: list = []

    async def _dos(_t, maximo=2):
        return ["Tortas keto", "Torta baja en carbohidratos"]

    async def _no_mostrada(_tel, _n):
        return False

    async def ejecutar(nombre, args, telefono):
        llamadas.append((args["nombre"], args.get("maximo")))
        return {"enviadas": 1}

    monkeypatch.setattr(agente, "productos_enfocados", _dos)
    monkeypatch.setattr(agente, "media_ya_mostrada", _no_mostrada)
    await _asegurar_foto(
        "Tenemos Tortas keto y Torta baja en carbohidratos", "584240000000", "tienes tortas?",
        ejecutar, puede_fotos=True, hubo_media=False,
    )
    assert [m for _, m in llamadas] == [1, 1], f"mandó de más: {llamadas}"


async def test_con_UN_solo_producto_va_UNA_la_principal(monkeypatch):
    """🔴 EL CONTRATO DEL PROACTIVO (decisión de producto 2026-09-03, Maired tras su prueba en
    vivo): el empuje que nadie pidió muestra LA CARA del producto — una foto, la principal de
    la 036 (o la primera si no hay marcada) —, no una galería de 3. "Ver más ángulos" no
    desaparece: cuando el cliente PIDE ver, la llamada va por el modelo y
    `_ejecutar_con_guardas` deja hasta 3 (test en test_foto_principal.py). Hasta hoy este test
    fijaba maximo==3 ("los TRES de siempre"); cambió CON la conducta, a propósito."""
    llamadas, _ = await _correr_red(monkeypatch, enfocado="Quesillo")
    assert llamadas[0][1].get("maximo") == 1


# ── El tope de archivos, en la TOOL y en el SCHEMA (R50 y R51 lo pidieron) ─────────

def test_el_schema_declara_maximo_o_el_filtro_lo_TIRARÍA():
    """🔴 R51. `ejecutar_tool` recorta los args a lo que el schema declara (`_solo_lo_declarado`,
    el arreglo de seguridad del 08-21). Si `maximo` no está en el schema, la red lo pasa… y el
    filtro lo descarta en silencio: volverían los 3 archivos por producto sin que nada fallara."""
    from app.agent.tools import _PARAMS_DECLARADOS, _solo_lo_declarado

    assert "maximo" in _PARAMS_DECLARADOS["enviar_fotos_producto"]
    limpio = _solo_lo_declarado("enviar_fotos_producto", {"nombre": "Quesillo", "maximo": 1})
    assert limpio.get("maximo") == 1, "el filtro se comió el tope"


def test_la_tool_RECORTA_por_maximo_y_no_por_un_3_fijo():
    """🔴 R50. Los tests de la red mockean `ejecutar`, así que el cuerpo de la tool nunca corre:
    devolver `medios[:3]` a pelo dejaba la suite entera en verde y volvían los 6 archivos.

    Montar los dobles de la tool completa (BD + R2 + Meta + hilo) daba un test frágil que se
    saltaba solo ante cualquier borde — o sea, uno que puede pasar sin probar nada, que es
    exactamente lo que este banco existe para evitar. Se comprueba el CONTRATO en su lugar: que
    los dos puntos donde la tool recorta la lista usen el parámetro y no una constante."""
    import inspect

    from app.agent import tools as tl

    fuente = inspect.getsource(tl.enviar_fotos_producto)
    assert "medios[:maximo]" in fuente, "la tool volvió a recortar con un número fijo"
    assert "medios[:3]" not in fuente, "queda un [:3] a pelo: el tope no se respetaría"
    # y el parámetro existe con el default de siempre
    assert inspect.signature(tl.enviar_fotos_producto).parameters["maximo"].default == 3
