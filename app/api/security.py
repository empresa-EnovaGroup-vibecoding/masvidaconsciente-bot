"""Autenticación del dashboard: hash de contraseñas y tokens JWT."""
from datetime import datetime, timedelta, timezone

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
    expira = datetime.now(timezone.utc) + timedelta(hours=TOKEN_HORAS)
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
        raise cred_error
