"""LOS CIMIENTOS DEL EXPEDIENTE DE LA VENTA (migración 040) — SESIONES (35).

El bot entra ciego después de la dueña: lo que ella hace a mano (toma un pedido, confirma un pago
con un "Listo", acuerda una entrega por nota de voz) queda solo como texto en `mensajes`, y en 6 de
las 7 conversaciones reales donde el bot entró después de ella, chocó (pidió un pago ya hecho, dijo
otro total, re-preguntó el pedido). El expediente es la memoria compartida que falta: cada dato de
la venta con QUIÉN lo puso, con qué mensaje como evidencia y cuándo.

Este PR abre las casillas y NO cambia ninguna conducta. Lo que estos tests fijan:
  · la 040 existe, y cada sentencia se puede correr dos veces (idempotente, sin `DO $$`);
  · los modelos declaran las mismas casillas que la migración abre;
  · la procedencia solo admite tres orígenes: bot, dueña, panel;
  · una PROPUESTA (`propuesta_expediente`) NO es un chat pausado: las seis guardas que impiden que
    el sistema la trate como si lo fuera — porque si un eco la cerrara, o el barredor, o
    `pedir_ayuda` le pisara el motivo, el dato se perdería en silencio y nadie lo confirmaría nunca.
"""
import inspect
import re
from pathlib import Path
from types import SimpleNamespace

from app import models
from app.agent import fuentes_atencion, tools
from app.api import router as api
from app.services import barredor
from app.webhook import router as webhook

RAIZ = Path(__file__).resolve().parents[1]
MIGRACION = RAIZ / "migrations" / "040_expediente.sql"
CASILLAS_VENTA = {"origen", "evidencia_mensaje_id", "confianza", "extraido_at"}
CASILLAS_PROPUESTA = {"propuesta", "aplicada_por", "aplicada_at"}


def _sentencias() -> list[str]:
    """Las sentencias tal como las verá `init_db._statements` (mismo partidor ingenuo)."""
    lineas = [
        ln for ln in MIGRACION.read_text(encoding="utf-8").splitlines()
        if not ln.strip().startswith("--")
    ]
    return [s.strip() for s in "\n".join(lineas).split(";") if s.strip()]


# ══════════════════════════════════════════════════════════════════════════════════
#  LA MIGRACIÓN
# ══════════════════════════════════════════════════════════════════════════════════

def test_la_040_existe_y_cada_sentencia_se_puede_correr_dos_veces():
    sents = _sentencias()
    assert len(sents) >= 17, "la 040 quedó a medias"
    for s in sents:
        assert "DO $$" not in s, "init_db parte por ';': un bloque DO lo rompería"
        idempotente = (
            "IF NOT EXISTS" in s
            or "DROP CONSTRAINT IF EXISTS" in s
            or "ADD CONSTRAINT" in s  # va SIEMPRE precedida de su DROP IF EXISTS (abajo)
        )
        assert idempotente, f"esta sentencia reventaría la segunda vez: {s[:80]!r}"
    for i, s in enumerate(sents):
        if "ADD CONSTRAINT" in s:
            nombre = re.search(r"ADD CONSTRAINT (\w+)", s).group(1)
            assert f"DROP CONSTRAINT IF EXISTS {nombre}" in sents[i - 1], (
                f"{nombre} se agrega sin soltar el anterior: la 040 no sería idempotente"
            )


def test_la_procedencia_solo_admite_tres_origenes():
    """bot (lo hizo el bot), dueña (lo dijo Whuilianny a mano), panel (un clic). Ni uno más:
    un origen libre volvería a ser texto suelto, que es justo lo que el expediente viene a matar."""
    sql = MIGRACION.read_text(encoding="utf-8")
    assert sql.count("CHECK (origen IN ('bot','dueña','panel'))") == 2  # pedidos y pagos
    for modelo, nombre in ((models.Pedido, "ck_pedido_origen"), (models.Pago, "ck_pago_origen")):
        ck = next(c for c in modelo.__table__.constraints if c.name == nombre)
        assert "'bot','dueña','panel'" in str(ck.sqltext)


def test_los_modelos_declaran_las_mismas_casillas_que_abre_la_migracion():
    sql = MIGRACION.read_text(encoding="utf-8")
    for modelo in (models.Pedido, models.Pago):
        cols = modelo.__table__.columns
        assert CASILLAS_VENTA <= set(cols.keys()), modelo.__name__
        assert cols["origen"].default.arg == "bot", "todo lo que ya existe es del bot"
        assert cols["evidencia_mensaje_id"].nullable and cols["confianza"].nullable
    assert CASILLAS_PROPUESTA <= set(models.Intervencion.__table__.columns.keys())
    for col in CASILLAS_VENTA | CASILLAS_PROPUESTA:
        assert col in sql, f"el modelo declara `{col}` pero la 040 no la abre"
    for indice in ("idx_intervenciones_propuestas", "idx_mensajes_owner_por_id"):
        assert indice in sql


def test_la_evidencia_apunta_al_mensaje_exacto_de_la_duena():
    """De aquí sale "según Whuilianny, nota de voz del 21-sep 10:32": si el mensaje se borra, el
    dato queda sin evidencia pero NO se borra (SET NULL), porque la venta sí ocurrió."""
    for modelo in (models.Pedido, models.Pago):
        fk = next(iter(modelo.__table__.columns["evidencia_mensaje_id"].foreign_keys))
        assert fk.column.table.name == "mensajes" and fk.ondelete == "SET NULL"


# ══════════════════════════════════════════════════════════════════════════════════
#  UNA PROPUESTA NO ES UN CHAT PAUSADO — las seis guardas
# ══════════════════════════════════════════════════════════════════════════════════
#
# El motivo `propuesta_expediente` es una PREGUNTA con dos botones ("Sí, es correcto" / "No"), no un
# problema que atender ni un freno del bot. Todo lo que hoy trata a un aviso pendiente como "la dueña
# tiene este chat" o "algo espera respuesta" tiene que dejarla pasar de largo.

def test_una_propuesta_esta_declarada_como_informativa_y_el_modelo_no_puede_crearla():
    assert "propuesta_expediente" in models.MOTIVOS_INFORMATIVOS
    # El modelo NO la crea por `pedir_ayuda`: no está en su lista (un motivo desconocido cae a no_se)
    assert "propuesta_expediente" not in tools._MOTIVO_TITULO
    assert "propuesta_expediente" not in tools._MOTIVOS_DE_PAUSA
    # …pero la bandeja sí sabe pintarla, y con una pregunta, no con una alarma.
    assert "confirmas" in api._MOTIVO_TEXTO["propuesta_expediente"]


# Los cableados, fijados en la fuente (patrón R50): si alguien "limpia" una de estas líneas creyendo
# que sobra, el test lo ve antes que la dueña.

def test_el_eco_de_la_duena_no_cierra_una_propuesta():
    """Que ella escriba de nuevo no confirma que su "Listo" fue un pago."""
    assert "MOTIVOS_INFORMATIVOS" in inspect.getsource(webhook._procesar_eco)


def test_el_mensaje_desde_el_panel_no_cierra_una_propuesta():
    assert "MOTIVOS_INFORMATIVOS" in inspect.getsource(api.responder_como_dueña)


def test_el_barredor_no_cierra_una_propuesta():
    fuente = inspect.getsource(barredor.cerrar_avisos_ya_atendidos)
    assert "NOT IN ('chat_tomado', 'propuesta_expediente')" in fuente
    # y sigue respetando el botón de devolver, como antes
    assert "chat_tomado" in fuente


def test_pedir_ayuda_no_enriquece_ni_pisa_una_propuesta():
    """Con una propuesta pendiente, una escalada real del bot crea SU aviso; no se cuelga de la
    propuesta ni le cambia el motivo (se perdería la pregunta y nadie la confirmaría)."""
    assert "MOTIVOS_INFORMATIVOS" in inspect.getsource(tools.pedir_ayuda)


def test_el_acuse_no_se_lo_lleva_una_propuesta():
    """`tomar_acuse` reserva el "Ya te confirmo" en la intervención del RELEVO, no en la propuesta
    que casualmente sea la más nueva."""
    assert "MOTIVOS_INFORMATIVOS" in inspect.getsource(fuentes_atencion.tomar_acuse)


async def test_resolver_una_propuesta_no_reactiva_al_bot(monkeypatch):
    """La guarda con conducta observable: "resolver" una propuesta la cierra y NADA MÁS. No mira al
    cliente, no despausa, no dispara el retomar — el chat queda exactamente como estaba."""
    inter = SimpleNamespace(
        id=5, motivo="propuesta_expediente", cliente_telefono="584240000000",
        estado="pendiente", resuelta_at=None,
    )

    class _Sesion:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, modelo, pk):
            assert modelo is models.Intervencion and pk == 5
            return inter

        async def execute(self, _q):
            raise AssertionError("no debe ir a buscar al cliente: una propuesta no pausó nada")

        async def commit(self):
            return None

    monkeypatch.setattr(api, "get_session_factory", lambda: (lambda: _Sesion()))
    monkeypatch.setattr(
        api, "_disparar_retomar",
        lambda *a: (_ for _ in ()).throw(AssertionError("no hay nada que retomar")),
    )

    r = await api.resolver_intervencion(5, reactivar=True, _="prueba@masvida.local")

    assert r == {"ok": True, "bot_reactivado": False}
    assert inter.estado == "resuelta" and inter.resuelta_at is not None
