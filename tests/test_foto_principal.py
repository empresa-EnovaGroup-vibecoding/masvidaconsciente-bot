"""LA FOTO PRINCIPAL — la cara del producto (migración 036, decisión de producto 2026-09-03).

LO QUE CAMBIÓ, pedido por Maired tras su prueba en vivo (con la recomendación revisada de
comercio): el envío PROACTIVO de fotos muestra UNA — la principal que marcó la dueña (o la
primera del producto si no hay marcada) — en vez de una galería de hasta 3. "Ver más" no
desaparece: cuando el cliente PIDE ver, se mantienen los hasta 3 de siempre (y `reenviar`).

Las piezas y dónde se prueban:
  · la RED proactiva manda maximo=1 → tests actualizados en test_asegurar_foto.py y
    test_etiqueta_recordada.py (cambiaron CON la conducta, a propósito);
  · la GUARDA del modelo (`_ejecutar_con_guardas`) rellena maximo=1 cuando nadie pidió ver → aquí;
  · el ENDPOINT del panel que marca la principal (PATCH /media/{id}/principal) → aquí;
  · la columna y el índice de la 036 quedan REGISTRADOS en los bancos de drift → aquí;
  · que la principal ENCABECE el ORDER BY se prueba aquí en el SQL real y también contra
    Postgres en scripts/probar_media.py (con ROLLBACK).
"""
import pytest

from app.agent import agent as ag
from app.api import router as api
from app.models import ProductoMedia

TEL = "584240000000"


# ══════════════════════════════════════════════════════════════════════════════
# 1) LA GUARDA DEL MODELO: proactivo = 1 (la principal) · pidió ver = hasta 3
# ══════════════════════════════════════════════════════════════════════════════

async def _guardas(mensaje, args=None):
    visto: list = []

    async def ejecutar(nombre, args, telefono):
        visto.append(dict(args))
        return {"enviadas": 1}

    await ag._ejecutar_con_guardas(
        ejecutar, "enviar_fotos_producto", dict(args or {"nombre": "Quesillo"}),
        TEL, mensaje, None,
    )
    return visto[0]


async def test_proactivo_del_modelo_rellena_maximo_1():
    """🔴 EL CORAZÓN: el modelo se enfocó en un producto y llamó la tool SIN pedir el cliente
    ver fotos y SIN decidir un maximo — la política la pone el código: UNA, la principal."""
    args = await _guardas("quiero el quesillo")
    assert args.get("maximo") == 1
    assert "reenviar" not in args


async def test_si_el_cliente_PIDE_ver_quedan_los_3_de_siempre():
    """Quien pide ver quiere ver bien: sin maximo inyectado (la tool usa su default de 3),
    y con `reenviar` encendido (la válvula de siempre, rama fotos-con-memoria)."""
    args = await _guardas("mándame las fotos del quesillo porfa")
    assert args.get("maximo") == 3, "pidió ver: la política autoriza hasta 3"
    assert args.get("reenviar") is True


@pytest.mark.parametrize(
    "mensaje",
    [
        "muéstramela otra vez",
        "¿cómo se ve?",
        "quiero verlas",
        "puedo ver el quesillo?",
        "enséñame el quesillo",
    ],
)
async def test_formas_naturales_de_pedir_ver_usan_la_misma_politica(mensaje):
    """La regla de negocio entiende cómo habla una persona, no solo la palabra `foto`."""
    args = await _guardas(mensaje)
    assert args.get("reenviar") is True
    assert args.get("maximo") == 3


async def test_reenviar_explicito_del_modelo_tambien_autoriza_hasta_3():
    """Si el modelo ya entendió el reenvío, el detector de texto no puede recortarlo a una."""
    args = await _guardas(
        "otra vez por favor", args={"nombre": "Quesillo", "reenviar": True}
    )
    assert args.get("reenviar") is True
    assert args.get("maximo") == 3


async def test_el_maximo_explicito_del_modelo_se_respeta():
    """El prompt sugiere y el código rellena lo que quedó en blanco — no le tuerce la mano:
    si el modelo decidió maximo=2 (p. ej. va a enseñar dos productos), se respeta."""
    args = await _guardas("quiero el quesillo", args={"nombre": "Quesillo", "maximo": 2})
    assert args.get("maximo") == 2


async def test_la_guarda_no_toca_otras_tools():
    visto: list = []

    async def ejecutar(nombre, args, telefono):
        visto.append(dict(args))
        return {"ok": True}

    await ag._ejecutar_con_guardas(
        ejecutar, "enviar_catalogo", {}, TEL, "quiero el quesillo", None
    )
    assert "maximo" not in visto[0]


# ══════════════════════════════════════════════════════════════════════════════
# 2) EL ENDPOINT DEL PANEL: PATCH /media/{id}/principal
# ══════════════════════════════════════════════════════════════════════════════

class _Media:
    def __init__(
        self, id_, producto_id=7, tipo="imagen", es_principal=False,
        *, clave="productos/foto.jpg", etiqueta=None,
    ):
        self.id = id_
        self.producto_id = producto_id
        self.tipo = tipo
        self.es_principal = es_principal
        self.clave = clave
        self.etiqueta = etiqueta


class _Sesion:
    """Doble mínimo: `get` devuelve la fila preparada; `execute` (el UPDATE que apaga la
    principal anterior) y `commit` quedan registrados para las aserciones."""

    def __init__(self, fila, *, filas=None, error_commit=None):
        self._fila = fila
        self._filas = list(filas or [])
        self.statements = []
        self.commits = 0
        self.rollbacks = 0
        self.error_commit = error_commit

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, modelo, pk):
        return self._fila

    async def execute(self, q):
        self.statements.append(q)

        class _Resultado:
            def __init__(self, filas):
                self.filas = filas

            def scalars(self):
                return self

            def all(self):
                return list(self.filas)

        return _Resultado(self._filas)

    async def commit(self):
        self.commits += 1
        if self.error_commit is not None:
            raise self.error_commit

    async def rollback(self):
        self.rollbacks += 1


def _con_sesion(monkeypatch, fila):
    ses = _Sesion(fila)
    monkeypatch.setattr(api, "get_session_factory", lambda: (lambda: ses))
    return ses


async def test_marcar_principal_enciende_esta_y_apaga_la_anterior(monkeypatch):
    foto = _Media(31, tipo="imagen", es_principal=False)
    ses = _con_sesion(monkeypatch, foto)
    r = await api.marcar_media_principal(31, _="prueba@masvida.local")
    assert r == {"ok": True}
    assert foto.es_principal is True
    assert len(ses.statements) == 1
    sql = str(ses.statements[0])
    params = ses.statements[0].compile().params
    assert "producto_media.producto_id" in sql and "producto_media.es_principal" in sql
    assert 7 in params.values(), "solo puede apagar la principal de ESTE producto"
    assert False in params.values(), "la principal anterior tiene que quedar apagada"
    assert ses.commits == 1, "una sola transacción: apagar y encender juntos"


async def test_una_carrera_al_marcar_principal_devuelve_409_y_rollback(monkeypatch):
    from fastapi import HTTPException
    from sqlalchemy.exc import IntegrityError

    error = IntegrityError("UPDATE producto_media", {}, RuntimeError("indice unico"))
    ses = _Sesion(_Media(31), error_commit=error)
    monkeypatch.setattr(api, "get_session_factory", lambda: (lambda: ses))
    with pytest.raises(HTTPException) as exc:
        await api.marcar_media_principal(31, _="prueba@masvida.local")
    assert exc.value.status_code == 409
    assert ses.rollbacks == 1


async def test_un_video_no_puede_ser_la_principal(monkeypatch):
    """La principal es la CARA del producto (miniatura incluida): una imagen, no un video."""
    from fastapi import HTTPException

    _con_sesion(monkeypatch, _Media(9, tipo="video"))
    with pytest.raises(HTTPException) as exc:
        await api.marcar_media_principal(9, _="prueba@masvida.local")
    assert exc.value.status_code == 400
    assert "video" in exc.value.detail


async def test_una_foto_que_no_existe_da_404(monkeypatch):
    from fastapi import HTTPException

    _con_sesion(monkeypatch, None)
    with pytest.raises(HTTPException) as exc:
        await api.marcar_media_principal(999, _="prueba@masvida.local")
    assert exc.value.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# 3) EL CABLEADO DE LA 036 (que nadie pueda "olvidarse" de una pieza en silencio)
# ══════════════════════════════════════════════════════════════════════════════

def test_el_modelo_declara_es_principal_con_default_false():
    """`probar_drift` compara models.py contra el esquema real: la columna vive en LOS DOS
    sitios o el banco grita. Y nace FALSE: nada cambia hasta que la dueña marque."""
    campo = ProductoMedia.__table__.columns["es_principal"]
    assert campo.default.arg is False


def test_la_036_quedo_registrada_en_el_banco_de_migraciones():
    """La práctica de la casa (029, 022): columna nueva → fila en COLUMNAS y el índice en
    INDICES_VIVOS de probar_migraciones.py. Si alguien la borra de ahí, esto la delata.
    Se lee la FUENTE (no se importa: el banco corre `asyncio.run` al importarse — toca BD)."""
    import pathlib

    fuente = (
        pathlib.Path(__file__).resolve().parent.parent / "scripts" / "probar_migraciones.py"
    ).read_text(encoding="utf-8")
    assert '"es_principal"' in fuente, "la columna de la 036 no está registrada en COLUMNAS"
    assert "ux_media_principal_por_producto" in fuente, "el índice de la 036 no está en INDICES_VIVOS"


async def test_listar_media_expone_es_principal_y_ordena_por_ella(monkeypatch):
    """Ejecuta el endpoint: prueba el contrato consumido por el panel y el ORDER BY real."""
    foto = _Media(31, es_principal=True, clave="productos/principal.jpg", etiqueta="frente")
    ses = _Sesion(None, filas=[foto])
    monkeypatch.setattr(api, "get_session_factory", lambda: (lambda: ses))
    monkeypatch.setattr(
        "app.services.r2.url_publica", lambda clave: f"https://r2.local/{clave}"
    )

    respuesta = await api.listar_media(7, _="prueba@masvida.local")
    assert respuesta == [{
        "id": 31,
        "tipo": "imagen",
        "url": "https://r2.local/productos/principal.jpg",
        "etiqueta": "frente",
        "es_principal": True,
    }]
    sql = str(ses.statements[0])
    assert "producto_media.es_principal DESC" in sql
    assert "producto_media.orden" in sql and "producto_media.id" in sql


# ══════════════════════════════════════════════════════════════════════════════
# 4) EL "OTRA VEZ" CON VARIEDAD: las fotos NO vistas primero (pedido de Maired, 3-sep)
# ══════════════════════════════════════════════════════════════════════════════

class _Foto:
    def __init__(self, clave):
        self.clave = clave


@pytest.fixture()
def _url_es_la_clave(monkeypatch):
    """En estos tests la URL pública ES la clave: la identidad queda determinista sin R2."""
    from app.services import r2

    monkeypatch.setattr(r2, "url_publica", lambda clave: clave)


def test_al_reenviar_las_no_vistas_van_primero(_url_es_la_clave):
    """🔴 EL CASO DE MAIRED: 'muéstramela otra vez' con 5 fotos cargadas — repetirle la misma
    de entrada aburre; otro ángulo ayuda a comparar. La vista baja, las nuevas suben, y el
    orden entre las nuevas se conserva (sort estable: la principal sigue mandando entre ellas)."""
    from app.agent.tools import _para_reenvio_primero_las_no_vistas

    a, b, c = _Foto("a.jpg"), _Foto("b.jpg"), _Foto("c.jpg")
    orden = _para_reenvio_primero_las_no_vistas([a, b, c], {"a.jpg"}, None)
    assert [m.clave for m in orden] == ["b.jpg", "c.jpg", "a.jpg"]


def test_con_todo_visto_el_reenvio_queda_identico(_url_es_la_clave):
    """Pidió repetir y se le repite: con todas vistas no hay nada que variar."""
    from app.agent.tools import _para_reenvio_primero_las_no_vistas

    a, b = _Foto("a.jpg"), _Foto("b.jpg")
    orden = _para_reenvio_primero_las_no_vistas([a, b], {"a.jpg", "b.jpg"}, None)
    assert [m.clave for m in orden] == ["a.jpg", "b.jpg"]


def test_una_version_pedida_jamas_pierde_su_puesto(_url_es_la_clave):
    """'La de plátano otra vez': lo PEDIDO manda sobre la variedad — con etiqueta no se
    reordena nada (la de plátano ya va primera por `_elegir_medios`, aunque esté vista)."""
    from app.agent.tools import _para_reenvio_primero_las_no_vistas

    platano, neutra = _Foto("platano.jpg"), _Foto("neutra.jpg")
    orden = _para_reenvio_primero_las_no_vistas([platano, neutra], {"platano.jpg"}, "platano")
    assert [m.clave for m in orden] == ["platano.jpg", "neutra.jpg"]


def test_un_tamano_pedido_por_variante_id_jamas_pierde_su_puesto(_url_es_la_clave):
    """La variante es tan específica como la etiqueta: variedad no puede desplazarla."""
    from app.agent.tools import _para_reenvio_primero_las_no_vistas

    tamano, neutra = _Foto("700ml.jpg"), _Foto("neutra.jpg")
    orden = _para_reenvio_primero_las_no_vistas(
        [tamano, neutra], {"700ml.jpg"}, None, variante_id=44
    )
    assert [m.clave for m in orden] == ["700ml.jpg", "neutra.jpg"]


async def test_fallo_de_memoria_fina_hace_rollback_y_no_envenena_la_tool():
    from app.agent.tools import _urls_de_media_ya_enviadas

    class _SesionRota:
        def __init__(self):
            self.rollbacks = 0

        async def execute(self, _q):
            raise RuntimeError("postgres abortó la sentencia")

        async def rollback(self):
            self.rollbacks += 1

    ses = _SesionRota()
    assert await _urls_de_media_ya_enviadas(ses, TEL) == set()
    assert ses.rollbacks == 1


def test_sin_memoria_el_orden_de_siempre(_url_es_la_clave):
    from app.agent.tools import _para_reenvio_primero_las_no_vistas

    a, b = _Foto("a.jpg"), _Foto("b.jpg")
    assert [m.clave for m in _para_reenvio_primero_las_no_vistas([a, b], set(), None)] == [
        "a.jpg", "b.jpg",
    ]
