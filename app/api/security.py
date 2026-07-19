"""Autenticación del dashboard: hash de contraseñas y tokens JWT."""
from datetime import UTC, datetime, timedelta

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from app.config import get_settings

settings = get_settings()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/login")

ALGORITHM = "HS256"
TOKEN_HORAS = 12


def hash_password(password: str) -> str:
    # bcrypt admite máximo 72 bytes; truncamos por seguridad
    pw = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    pw = plain.encode("utf-8")[:72]
    try:
        return bcrypt.checkpw(pw, hashed.encode("utf-8"))
    except ValueError:
        return False


def crear_token(email: str) -> str:
    expira = datetime.now(UTC) + timedelta(hours=TOKEN_HORAS)
    return jwt.encode({"sub": email, "exp": expira}, settings.jwt_secret, algorithm=ALGORITHM)


def usuario_actual(token: str = Depends(oauth2_scheme)) -> str:
    cred_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Sesión inválida o expirada",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if email is None:
            raise cred_error
        return email
    except JWTError:
        # `from None`: el 401 ES la respuesta prevista, no un fallo secundario. Encadenar
        # el JWTError solo mete las tripas de la librería en el traceback de un login malo.
        raise cred_error from None


# ─── ROLES (migración 024): proveedora (Enova) vs dueña (la clienta) ──────────────────

async def leer_rol(email: str) -> str:
    """El rol de un usuario, LEÍDO DE LA BASE — no del token.

    🔴 Y ES UNA DECISIÓN, NO UN DESCUIDO. Meter el rol como claim del JWT sería más rápido (una
    consulta menos), pero trae dos problemas feos:

      1. **Los tokens ya emitidos no lo llevan.** El día que esto se despliegue, todo el mundo
         tiene un token viejo SIN el claim. Si la puerta exigiera el claim, la proveedora se
         quedaría FUERA de sus propias palancas hasta que el token caduque (12h).
      2. **Un cambio de rol no surtiría efecto hasta la próxima sesión.** Si le quitas el rol a
         alguien, seguiría entrando durante horas con el token que ya tiene en la mano.

    Leyéndolo de la BD, el rol es la VERDAD DE AHORA. Cuesta una consulta por request en un panel
    de dos usuarios: es gratis.

    Si el usuario no existe (borrado con la sesión abierta), devuelve 'duena' — el rol de MENOS
    privilegio. Fail-closed: ante la duda, la puerta se cierra.
    """
    from sqlalchemy import select

    from app.models import Usuario
    from app.services.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        rol = (
            await session.execute(select(Usuario.rol).where(Usuario.email == email))
        ).scalars().first()
    return rol or "duena"


async def proveedora_actual(email: str = Depends(usuario_actual)) -> str:
    """Puerta de las palancas de la PROVEEDORA (Enova). La dueña recibe 403.

    Qué protege: el selector de modelo de IA (CLAUDE.md §5: *"palanca de PROVEEDOR, no de la
    clienta"*) y, desde la fase 4, el interruptor de las herramientas del agente. La dueña no
    debería poder apagarle tools a su propio bot sin querer.
    """
    if await leer_rol(email) != "proveedora":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esto solo lo puede tocar la proveedora (Enova).",
        )
    return email
