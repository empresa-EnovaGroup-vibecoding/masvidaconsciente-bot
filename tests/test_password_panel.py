"""CAMBIAR Y RESTABLECER LA CONTRASEÑA DEL PANEL (lo pidió Maired, 2026-09-06).

Hasta hoy nadie podía cambiar una contraseña: ni la propia ni la de otro; la única vía era tocar
ADMIN_PASSWORD en el servidor. Dos puertas nuevas:
  · `PATCH /usuarios/me/password` — la dueña cambia la SUYA (exige la actual);
  · `PATCH /usuarios/{id}/password` — la proveedora le pone una nueva a quien la olvidó.
Y UNA frontera a propósito: la cuenta principal (ADMIN_EMAIL) no se cambia desde el panel —
`_crear_admin` la re-sincroniza con el servidor en cada arranque (la red anti-bloqueo de Enova);
cambiarla aquí sería una mentira que el próximo deploy revertiría en silencio.
"""
import pytest
from fastapi import HTTPException

from app.api import router as api
from app.api.security import hash_password, verify_password

ADMIN = "admin@masvidaconsciente.com"  # el default de ADMIN_EMAIL en config.py
DUENA = "duena@sunegocio.com"


class _Usuario:
    def __init__(self, id_, email, password):
        self.id = id_
        self.email = email
        self.password_hash = hash_password(password)
        self.rol = "duena"


class _Sesion:
    def __init__(self, usuario):
        self._u = usuario
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _q):
        class _R:
            def __init__(s, u):
                s._u = u

            def scalar_one_or_none(s):
                return s._u
        return _R(self._u)

    async def get(self, modelo, pk):
        return self._u

    async def commit(self):
        self.commits += 1


def _con(monkeypatch, usuario):
    ses = _Sesion(usuario)
    monkeypatch.setattr(api, "get_session_factory", lambda: (lambda: ses))
    return ses


# ══ La dueña cambia la suya ══

async def test_la_duena_cambia_su_contrasena_con_la_actual_correcta(monkeypatch):
    u = _Usuario(2, DUENA, "vieja-clave-123")
    ses = _con(monkeypatch, u)
    r = await api.cambiar_mi_password(
        api.PasswordPropiaIn(actual="vieja-clave-123", nueva="nueva-clave-456"), email=DUENA
    )
    assert r == {"ok": True} and ses.commits == 1
    assert verify_password("nueva-clave-456", u.password_hash)
    assert not verify_password("vieja-clave-123", u.password_hash)


async def test_con_la_actual_equivocada_no_cambia_nada(monkeypatch):
    """Un token robado de una sesión abierta no basta para cerrarle la puerta a la persona real."""
    u = _Usuario(2, DUENA, "vieja-clave-123")
    ses = _con(monkeypatch, u)
    with pytest.raises(HTTPException) as exc:
        await api.cambiar_mi_password(
            api.PasswordPropiaIn(actual="adivinada", nueva="nueva-clave-456"), email=DUENA
        )
    assert exc.value.status_code == 401
    assert ses.commits == 0 and verify_password("vieja-clave-123", u.password_hash)


async def test_la_cuenta_principal_no_se_cambia_desde_el_panel(monkeypatch):
    """🔒 La frontera: `_crear_admin` la re-sincroniza con ADMIN_PASSWORD en cada arranque."""
    ses = _con(monkeypatch, _Usuario(1, ADMIN, "EnovaMasvida20261865"))
    with pytest.raises(HTTPException) as exc:
        await api.cambiar_mi_password(
            api.PasswordPropiaIn(actual="EnovaMasvida20261865", nueva="otra-clave-789"), email=ADMIN
        )
    assert exc.value.status_code == 400 and "servidor" in exc.value.detail
    assert ses.commits == 0


async def test_la_nueva_debe_ser_distinta(monkeypatch):
    _con(monkeypatch, _Usuario(2, DUENA, "misma-clave-123"))
    with pytest.raises(HTTPException) as exc:
        await api.cambiar_mi_password(
            api.PasswordPropiaIn(actual="misma-clave-123", nueva="misma-clave-123"), email=DUENA
        )
    assert exc.value.status_code == 400


def test_la_politica_minima_vive_en_el_esquema():
    """Menos de 8 caracteres no llega ni al endpoint (Pydantic), igual que al crear usuarios."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        api.PasswordPropiaIn(actual="x", nueva="corta")
    with pytest.raises(ValidationError):
        api.PasswordAjenaIn(nueva="corta")


# ══ La proveedora restablece la de quien la olvidó ══

async def test_la_proveedora_restablece_la_clave_de_la_duena(monkeypatch):
    u = _Usuario(2, DUENA, "la-olvide-999")
    ses = _con(monkeypatch, u)
    r = await api.restablecer_password(2, api.PasswordAjenaIn(nueva="clave-nueva-2026"), _="enova")
    assert r == {"ok": True, "id": 2} and ses.commits == 1
    assert verify_password("clave-nueva-2026", u.password_hash)


async def test_restablecer_la_principal_tambien_se_niega(monkeypatch):
    ses = _con(monkeypatch, _Usuario(1, ADMIN, "EnovaMasvida20261865"))
    with pytest.raises(HTTPException) as exc:
        await api.restablecer_password(1, api.PasswordAjenaIn(nueva="otra-clave-789"), _="enova")
    assert exc.value.status_code == 400 and ses.commits == 0


async def test_restablecer_a_un_usuario_inexistente_da_404(monkeypatch):
    _con(monkeypatch, None)
    with pytest.raises(HTTPException) as exc:
        await api.restablecer_password(99, api.PasswordAjenaIn(nueva="clave-nueva-2026"), _="enova")
    assert exc.value.status_code == 404


# ══ El orden de las rutas: "me" no es un entero ══

def test_la_ruta_me_va_antes_que_la_de_id():
    """FastAPI resuelve en orden de declaración: si `/usuarios/{usuario_id}/password` fuera
    primero, "me" fallaría la conversión a int y respondería 422 en vez de llegar a su ruta."""
    rutas = [getattr(r, "path", "") for r in api.router.routes]  # traen el prefijo /api
    i_me = next(i for i, p in enumerate(rutas) if p.endswith("/usuarios/me/password"))
    i_id = next(i for i, p in enumerate(rutas) if p.endswith("/usuarios/{usuario_id}/password"))
    assert i_me < i_id
