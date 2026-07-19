"""Arranque de los tests.

⚠️ EL ORDEN DE ESTE FICHERO IMPORTA. `app/config.py` tiene un `@model_validator` que
HACE FALLAR EL IMPORT si `JWT_SECRET` (≥32 chars) o `ADMIN_PASSWORD` (≥8) no están en el
entorno. Es una buena defensa en producción, pero significa que cualquier test que importe
`app.*` revienta al importar si el entorno viene vacío.

Por eso el entorno se rellena AQUÍ, a nivel de módulo: pytest carga los `conftest.py`
ANTES que los módulos de test, así que para cuando `test_*.py` haga `from app...` las
variables ya están puestas.

Los valores son de MENTIRA a propósito: ningún test de esta carpeta toca la BD, ni Redis,
ni OpenRouter, ni WhatsApp. Los que sí lo hacen son los BANCOS (`scripts/probar_*.py`), que
corren contra un contenedor vivo DESPUÉS de desplegar. Aquí solo vive lo que es puro y
rápido — lo que puede correr en el CI ANTES de desplegar.
"""

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# La raíz del repo en sys.path: así `import app...` y `import scripts...` funcionan igual
# que cuando los bancos corren con PYTHONPATH=. dentro del contenedor.
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

# Secretos de mentira, pero que PASAN el validador de config.py (≥32 y ≥8 caracteres).
os.environ.setdefault("JWT_SECRET", "clave-de-pruebas-solo-para-pytest-no-es-un-secreto-real")
os.environ.setdefault("ADMIN_PASSWORD", "pytest-password")
# Nadie se conecta a estas URLs en los tests puros; están para que Settings valide.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("OPENROUTER_API_KEY", "sk-or-v1-de-mentira-para-pytest")
