from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Valores que NO se aceptan como secretos: placeholders inseguros conocidos.
# Si jwt_secret o admin_password caen aqui, el proceso no arranca.
_SECRETOS_INSEGUROS = {"", "cambia-esto-en-produccion", "masvida2026", "changeme"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # WhatsApp (Meta Cloud API)
    meta_phone_number_id: str = ""
    meta_waba_id: str = ""
    meta_access_token: str = ""
    meta_verify_token: str = ""
    meta_app_secret: str = ""

    # Infraestructura
    database_url: str = "postgresql+asyncpg://masvida:password@localhost:5432/masvidaconsciente"
    redis_url: str = "redis://localhost:6379/0"

    # Motor de IA
    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemini-2.5-flash"
    openrouter_model_fallback: str = "openai/gpt-4.1"

    # Comportamiento
    buffer_segundos: int = 15
    conversacion_ttl: int = 86400
    max_iteraciones_agente: int = 6

    # Negocio
    negocio_nombre: str = "masvidaconsciente"
    negocio_ubicacion: str = "Cabudare, Venezuela"

    # Cobro / Aviso a la duena.
    # dueno_telefono tambien vive en la tabla `configuracion` (editable sin redeploy);
    # esta variable de entorno funciona como semilla/fallback.
    dueno_telefono: str = ""

    # Tasa BCV (conversion USD -> Bs). La fuente real se consulta en services/tasa.py.
    tasa_api_url: str = ""
    tasa_api_key: str = ""
    tasa_ttl: int = 3600           # segundos de cache de la tasa en Redis
    tasa_manual_default: str = ""  # tasa de respaldo si la API y la config fallan

    # Comprobantes de pago (archivos en el volumen del VPS).
    comprobantes_dir: str = "/data/comprobantes"

    # Dashboard (login) — SIN defaults inseguros: se exigen al arranque.
    jwt_secret: str = ""
    admin_email: str = "admin@masvidaconsciente.com"
    admin_password: str = ""
    # Origen(es) permitido(s) para CORS (dominio del dashboard). Vacio = cualquiera.
    dashboard_origin: str = ""

    @model_validator(mode="after")
    def _exigir_secretos_seguros(self) -> "Settings":
        """Falla al arranque, con un mensaje claro, si los secretos no son seguros.

        Cierra el hueco de seguridad: antes el admin nacia con una contrasena
        publica ('masvida2026') y el JWT se firmaba con un secreto conocido, asi
        que cualquiera podria iniciar sesion y confirmar pagos.
        """
        if self.jwt_secret in _SECRETOS_INSEGUROS:
            raise ValueError(
                "JWT_SECRET no esta configurado o usa un valor inseguro. "
                "Define JWT_SECRET con una cadena larga y aleatoria (minimo 32 caracteres) "
                "en las variables de entorno (Coolify)."
            )
        if len(self.jwt_secret) < 32:
            raise ValueError(
                "JWT_SECRET es demasiado corto: usa al menos 32 caracteres aleatorios."
            )
        if self.admin_password in _SECRETOS_INSEGUROS:
            raise ValueError(
                "ADMIN_PASSWORD no esta configurado o usa un valor inseguro. "
                "Define ADMIN_PASSWORD con una contrasena fuerte en las variables de entorno (Coolify)."
            )
        if len(self.admin_password) < 8:
            raise ValueError(
                "ADMIN_PASSWORD es demasiado corto: usa al menos 8 caracteres."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
