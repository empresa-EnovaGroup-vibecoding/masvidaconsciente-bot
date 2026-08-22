"""EL TEXTO SALE PRIMERO, LA MEDIA DESPUÉS.

EL CASO REAL (lo reportó Erwin el 2026-08-21, viendo un turno del bot):

    *"Una persona real responde breve. Y saluda primero antes de enviar imágenes.
      Pero en este caso saluda después de enviar varias imágenes."*

Y no era un despiste del modelo: era **estructural**. Reproducido antes de tocar nada, el orden
en que le llegaban los mensajes a la clienta era

    1. IMAGEN · 2. IMAGEN · 3. IMAGEN · 4. TEXTO ("Hola, Ana, buenas noches 💚") · 5. TEXTO

porque `enviar_fotos_producto` llamaba a Meta DENTRO de `responder()` (tools.py) y el texto lo
mandaba `tasks.py` DESPUÉS. Los documentos de las 42 conversaciones reales muestran que
Whuilianny hace exactamente lo contrario, sin una excepción: **anuncia y después muestra.**

    [00:54] "Hola carlos buenas noches bendiciones."
    [00:54] "Por aquí te dejo nuestro catálogo. Por aquí a la orden."
    [00:54] (documento)

⚠️ La mitad de este archivo son los casos que NO deben disparar: la cola cerrada (los carriles
que no mandan texto después tienen que seguir enviando en el momento), la cola vacía, un envío
que falla, y abrir dos veces. Una cola que se trague una foto es peor que el bug que arregla.
"""

import pytest

from app.services import cola_media
from app.workers import tasks

# ══════════════════════════════════════════════════════════════════════════════════
#  LA PIEZA: la cola
# ══════════════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _cola_limpia():
    """Cada test arranca con la cola CERRADA. Sin esto un test que abre y no cierra
    contamina a los siguientes (y el `ContextVar` sobrevive entre tests del mismo hilo)."""
    cola_media.cerrar()
    yield
    cola_media.cerrar()


def test_cerrada_no_encola_y_quien_llama_envia_el_mismo():
    """🔴 EL CASO QUE NO DEBE DISPARAR, y el más importante de todos.

    Sin cola abierta, `encolar` devuelve False y quien llama envía en el momento — igual que
    siempre. Es lo que mantiene intactos los carriles que NO mandan texto detrás: el worker de
    visión y los avisos a la dueña. Si esto devolviera True, esas fotos no saldrían NUNCA.
    """
    assert cola_media.activa() is False
    assert cola_media.encolar("una foto", _nunca) is False
    assert cola_media.cuantos() == 0


def test_abierta_si_encola():
    cola_media.abrir()
    assert cola_media.activa() is True
    assert cola_media.encolar("una foto", _nunca) is True
    assert cola_media.cuantos() == 1


@pytest.mark.asyncio
async def test_vaciar_manda_en_orden():
    salidas = []
    cola_media.abrir()
    for n in ("foto 1", "foto 2", "foto 3"):
        cola_media.encolar(n, _apunta(salidas, n))
    assert await cola_media.vaciar() == 3
    assert salidas == ["foto 1", "foto 2", "foto 3"]
    assert cola_media.cuantos() == 0, "la cola tiene que quedar vacía tras vaciarla"


@pytest.mark.asyncio
async def test_vaciar_una_cola_vacia_no_hace_nada():
    """NO DISPARA: sin nada encolado, vaciar es un no-op (el turno sin fotos, el 90%)."""
    cola_media.abrir()
    assert await cola_media.vaciar() == 0


@pytest.mark.asyncio
async def test_un_envio_que_falla_no_se_lleva_a_los_demas():
    """Si Meta rechaza la foto 1, la 2 y la 3 se intentan igual — mismo criterio que el bucle
    original de `enviar_fotos_producto`. La media es un empujón de venta: jamás tumba el turno."""
    salidas = []

    async def _revienta():
        raise RuntimeError("Meta dijo 400")

    cola_media.abrir()
    cola_media.encolar("la que falla", _revienta)
    cola_media.encolar("foto 2", _apunta(salidas, "foto 2"))
    cola_media.encolar("foto 3", _apunta(salidas, "foto 3"))
    assert await cola_media.vaciar() == 2
    assert salidas == ["foto 2", "foto 3"]


@pytest.mark.asyncio
async def test_descartar_no_manda_nada():
    """La dueña tomó el chat: la media se TIRA. Antes de la cola ya había salido, y el cliente
    recibía fotos huérfanas encima del chat que una persona acababa de tomar."""
    salidas = []
    cola_media.abrir()
    cola_media.encolar("foto", _apunta(salidas, "foto"))
    assert cola_media.descartar("la dueña tomó el chat") == 1
    assert salidas == [], "no puede salir NADA tras descartar"
    assert await cola_media.vaciar() == 0


def test_abrir_dos_veces_respeta_la_de_fuera():
    """NO DISPARA: un carril anidado no puede robarle la cola al de fuera — el de fuera es el
    único que sabe cuándo sale el texto. Si `abrir()` reseteara la lista, lo ya encolado se
    perdería en silencio."""
    cola_media.abrir()
    cola_media.encolar("foto de antes", _nunca)
    cola_media.abrir()  # alguien de dentro vuelve a abrir
    assert cola_media.cuantos() == 1, "lo encolado antes NO se puede perder"


@pytest.mark.asyncio
async def test_cerrar_con_cosas_dentro_no_las_manda(caplog):
    """NO DISPARA: cerrar sin vaciar es un BUG del que llama. Se avisa fuerte y NO se envía:
    mandarlo desde `cerrar()` sería mandarlo fuera de orden, que es justo el bug de origen."""
    salidas = []
    cola_media.abrir()
    cola_media.encolar("foto", _apunta(salidas, "foto"))
    cola_media.cerrar()
    assert salidas == []
    assert any("sin vaciar ni descartar" in r.message for r in caplog.records)


# ══════════════════════════════════════════════════════════════════════════════════
#  EL CARRIL: `_pensar_y_enviar` — que es donde vive el ORDEN de verdad
# ══════════════════════════════════════════════════════════════════════════════════
#
# 🔴 Probar la PIEZA no es probar el CARRIL (L22, y en este repo pasó CUATRO veces en dos días).
# Los tests de arriba prueban que la cola ordena; estos prueban que `tasks.py` la USA — que es
# lo que se puede romper sin que ningún test de arriba se ponga rojo.

@pytest.fixture
def carril(monkeypatch):
    """Monta `_pensar_y_enviar` con Meta y Redis de mentira, y devuelve la LÍNEA DE TIEMPO real:
    un solo `list` donde el texto y la media apuntan en el orden en que se enviaron."""
    linea: list[str] = []

    async def _enviar_texto(telefono, parte, **kw):
        linea.append(f"TEXTO: {parte}")
        return {"messages": [{"id": f"wamid.{len(linea)}"}]}

    async def _nada(*a, **kw):
        return None

    async def _no_lo_tomo(telefono):
        return False

    monkeypatch.setattr(tasks, "enviar_texto", _enviar_texto)
    monkeypatch.setattr(tasks, "marcar_mensaje_propio", _nada)
    monkeypatch.setattr(tasks, "_lo_paso_una_persona", _no_lo_tomo)
    monkeypatch.setattr(tasks, "_proteger_afirmacion_de_pago", lambda r: r)
    return linea


def _responder_que_manda_fotos(texto: str, fotos: list[str], linea: list[str]):
    """Un `responder()` de mentira que hace lo que hace el de verdad: mientras piensa, la tool
    `enviar_fotos_producto` ENCOLA la media (tools.py) y él devuelve solo el texto."""
    async def _responder(telefono, entrada, historial, nombre, **kw):
        for f in fotos:
            cola_media.encolar(f, _apunta(linea, f"IMAGEN: {f}"))
        return texto

    return _responder


@pytest.mark.asyncio
async def test_el_saludo_le_llega_antes_que_las_fotos(carril, monkeypatch):
    """🔴 EL BUG QUE REPORTÓ ERWIN, exactamente como lo describió.

    Antes: IMAGEN · IMAGEN · IMAGEN · "Hola, Ana, buenas noches". Ahora el saludo va primero.
    """
    monkeypatch.setattr(
        tasks, "responder",
        _responder_que_manda_fotos(
            "Hola, Ana, buenas noches 💚\n\nSi tenemos Empanadas de platano, vienen 8 por paquete. Cuantas quieres?",
            ["empanada #1", "empanada #2", "empanada #3"],
            carril,
        ),
    )
    partes, _ = await tasks._pensar_y_enviar("584121112233", "hola, tienen empanadas?", [], "Ana")

    assert len(partes) == 2
    assert carril[0].startswith("TEXTO"), f"lo PRIMERO tiene que ser texto, y fue: {carril[0]}"
    assert "Hola, Ana, buenas noches" in carril[0]
    # Y todas las imágenes, después de todo el texto.
    primera_imagen = next(i for i, e in enumerate(carril) if e.startswith("IMAGEN"))
    ultimo_texto = max(i for i, e in enumerate(carril) if e.startswith("TEXTO"))
    assert primera_imagen > ultimo_texto, f"la media se colό entre el texto: {carril}"


@pytest.mark.asyncio
async def test_el_orden_del_catalogo_de_whuilianny(carril, monkeypatch):
    """El patrón exacto de CLI-051: saluda, anuncia el catálogo, y ENTONCES el archivo."""
    monkeypatch.setattr(
        tasks, "responder",
        _responder_que_manda_fotos(
            "Hola Carlos, buenas noches, bendiciones\n\nPor aqui te dejo nuestro catalogo. Por aqui a la orden",
            ["catálogo en PDF"],
            carril,
        ),
    )
    await tasks._pensar_y_enviar("584121112233", "buenas, que tienen?", [], "Carlos")

    assert carril == [
        "TEXTO: Hola Carlos, buenas noches, bendiciones",
        "TEXTO: Por aqui te dejo nuestro catalogo. Por aqui a la orden",
        "IMAGEN: catálogo en PDF",
    ]


@pytest.mark.asyncio
async def test_cada_foto_sale_con_LO_SUYO_no_con_lo_de_la_ultima(carril, monkeypatch):
    """🔴 LA TRAMPA DE LA CLOSURE. Si el envío se construye DENTRO del `for` de
    `enviar_fotos_producto`, captura la VARIABLE del bucle y no su valor: al vaciarse la cola,
    las tres fotos saldrían con la url y el caption de la ÚLTIMA. Por eso existe `_un_envio`.
    Este test se pondría rojo con ese error, que no rompe ningún otro."""
    monkeypatch.setattr(
        tasks, "responder",
        _responder_que_manda_fotos("ahi te dejo las fotos", ["yuca", "plátano"], carril),
    )
    await tasks._pensar_y_enviar("584121112233", "fotos?", [], None)

    assert [e for e in carril if e.startswith("IMAGEN")] == ["IMAGEN: yuca", "IMAGEN: plátano"]


@pytest.mark.asyncio
async def test_si_la_duena_tomo_el_chat_no_sale_ni_texto_ni_foto(carril, monkeypatch):
    """NO DISPARA — y es el bug que se arregló de regalo. `_enviar_en_partes` devuelve [] y
    antes de la cola las fotos YA habían salido: el cliente recibía 3 imágenes huérfanas encima
    del chat que una persona acababa de tomar."""
    async def _si_lo_tomo(telefono):
        return True

    monkeypatch.setattr(tasks, "_lo_paso_una_persona", _si_lo_tomo)
    monkeypatch.setattr(
        tasks, "responder",
        _responder_que_manda_fotos("te dejo las fotos", ["foto 1", "foto 2"], carril),
    )
    partes, _ = await tasks._pensar_y_enviar("584121112233", "fotos?", [], None)

    assert partes == []
    assert carril == [], f"no puede salir NADA si la dueña tomó el chat, y salió: {carril}"


@pytest.mark.asyncio
async def test_la_cola_queda_cerrada_al_terminar(carril, monkeypatch):
    """NO DISPARA: el `finally` tiene que cerrar siempre. Si la cola quedara abierta, el
    SIGUIENTE turno del mismo worker encolaría fotos que nadie va a vaciar — y no saldrían."""
    monkeypatch.setattr(
        tasks, "responder", _responder_que_manda_fotos("listo", ["foto"], carril)
    )
    await tasks._pensar_y_enviar("584121112233", "hola", [], None)
    assert cola_media.activa() is False


@pytest.mark.asyncio
async def test_aunque_responder_reviente_la_cola_queda_cerrada(carril, monkeypatch):
    """NO DISPARA: si `responder()` lanza, el `finally` cierra igual. Sin esto, un turno con
    error dejaría la cola abierta y envenenaría los turnos siguientes del worker."""
    async def _revienta(*a, **kw):
        raise RuntimeError("sin saldo en OpenRouter")

    monkeypatch.setattr(tasks, "responder", _revienta)
    with pytest.raises(RuntimeError):
        await tasks._pensar_y_enviar("584121112233", "hola", [], None)
    assert cola_media.activa() is False


# ══════════════════════════════════════════════════════════════════════════════════
#  LA PIEZA QUE SE ROMPE SOLA: `_envio_de_un_archivo` (la trampa de la closure)
# ══════════════════════════════════════════════════════════════════════════════════
#
# 🔴 ESTOS TESTS EXISTEN PORQUE FALTABAN. La reversión "construir la closure DENTRO del for"
# salió VERDE con todo lo de arriba en su sitio: los tests del carril usan un `responder` de
# mentira que encola a mano, así que NUNCA tocaban el factory real de `tools.py`. El factory se
# sacó a nivel de módulo para poder alcanzarlo desde aquí.

@pytest.mark.asyncio
async def test_cada_archivo_conserva_SU_url_y_SU_caption(monkeypatch):
    """La trampa del late binding, contra el código REAL de `tools.py`.

    Se construyen los tres envíos en un bucle (como hace `enviar_fotos_producto`) y se ejecutan
    DESPUÉS, ya terminado el bucle — que es exactamente cuando la cola los suelta. Con una
    closure que capture la variable, los tres saldrían con la url y el caption del último.
    """
    from app.agent import tools

    enviados = []

    async def _enviar_imagen(telefono, url, cap):
        enviados.append((url, cap))
        return {"messages": [{"id": "wamid.x"}]}

    async def _nada(**kw):
        return None

    monkeypatch.setattr(tools, "enviar_imagen", _enviar_imagen)
    monkeypatch.setattr(tools, "_guardar_media_saliente", _nada)

    archivos = [("http://r2/yuca.jpg", "Empanadas — yuca"),
                ("http://r2/platano.jpg", "Empanadas — plátano"),
                ("http://r2/keto.jpg", "Empanadas — keto")]
    envios = []
    for url, cap in archivos:  # el bucle: aquí nacía el bug
        envios.append(tools._envio_de_un_archivo(
            telefono="584121112233", producto="Empanadas", url=url, cap=cap,
            es_video=False, etiqueta=cap.split("— ")[-1],
        ))
    for envio in envios:  # y aquí se ejecutan, con el bucle ya cerrado
        await envio()

    assert enviados == archivos, (
        "cada archivo tiene que salir con LO SUYO; si salen tres veces el último, "
        "la closure capturó la variable del bucle en vez de su valor"
    )


@pytest.mark.asyncio
async def test_el_video_va_por_la_puerta_del_video(monkeypatch):
    """NO DISPARA por el lado de la imagen: `es_video` tiene que elegir `enviar_video`.
    Mandar un mp4 por `enviar_imagen` es un 400 de Meta."""
    from app.agent import tools

    por_donde = []

    async def _img(telefono, url, cap):
        por_donde.append("imagen")
        return {}

    async def _vid(telefono, url, cap):
        por_donde.append("video")
        return {}

    async def _nada(**kw):
        return None

    monkeypatch.setattr(tools, "enviar_imagen", _img)
    monkeypatch.setattr(tools, "enviar_video", _vid)
    monkeypatch.setattr(tools, "_guardar_media_saliente", _nada)

    await tools._envio_de_un_archivo(
        telefono="5841", producto="Kombucha", url="u", cap="c", es_video=True, etiqueta="",
    )()
    await tools._envio_de_un_archivo(
        telefono="5841", producto="Kombucha", url="u", cap="c", es_video=False, etiqueta="",
    )()
    assert por_donde == ["video", "imagen"]


# ── ayudantes ────────────────────────────────────────────────────────────────────

async def _nunca():
    raise AssertionError("este envío NO debía ejecutarse")


def _apunta(destino: list, etiqueta: str):
    async def _hacerlo():
        destino.append(etiqueta)

    return _hacerlo
