from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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


@lru_cache
def get_settings() -> Settings:
    return Settings()
