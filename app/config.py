import logging
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Valores que NO se aceptan como secretos: placeholders inseguros conocidos.
# Si jwt_secret o admin_password caen aqui, el proceso no arranca.
_SECRETOS_INSEGUROS = {"", "cambia-esto-en-produccion", "masvida2026", "changeme"}

# La proporcion de fabrica entre el tope y la ventana del buffer (60 / 15). Es la que se usa para
# REPARAR una configuracion imposible: conserva la intencion de quien la escribio (el tamaño de su
# ventana) en vez de imponerle los 60 s del default.
_PROPORCION_TOPE_BUFFER = 4


def url_publica_utilizable(url: str) -> bool:
    """True si `url` sirve para que Meta DESCARGUE la media del bot (el catalogo en PDF).

    Meta exige un enlace HTTPS publico. Vacia o sin `https://` => el envio moriria con
    `131053 Media upload error`, asi que aqui se corta antes: el unico requisito que se puede
    comprobar SIN salir a la red es el esquema. Un `https://` que apunta a un host MUERTO pasa
    este filtro (no hay forma de saberlo sin pedirlo) — de ESE caso avisa la telemetria del
    webhook cuando Meta reporta el `fallido` (`_avisar_media_no_entregada`).
    """
    return (url or "").strip().lower().startswith("https://")


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
    # Modelo conversacional. Es SEMILLA/fallback: el modelo real lo elige la
    # proveedora desde el panel (clave 'modelo_ia' en la tabla configuracion),
    # sin redeploy. Si la BD no tiene valor, se usa este.
    openrouter_model: str = "google/gemini-2.5-flash"
    openrouter_model_fallback: str = "openai/gpt-4.1"
    # Modelo para transcribir notas de VOZ. Va aparte y FIJO: solo modelos con
    # entrada de audio (Gemini) sirven aquí; Claude/GPT no aceptan audio. El
    # selector del panel cambia solo el conversacional, NUNCA este.
    openrouter_model_audio: str = "google/gemini-2.5-flash"
    # Modelo de EMBEDDINGS (búsqueda por significado del Conocimiento). Va por la
    # misma API/llave de OpenRouter (endpoint /embeddings). Multilingüe y barato.
    # Es una MEJORA: si falla, el bot cae a la búsqueda léxica (pg_trgm) y sigue.
    openrouter_model_embedding: str = "openai/text-embedding-3-small"

    # Almacenamiento de fotos/videos de productos en Cloudflare R2 (S3-compatible).
    # En la BD se guarda solo la RUTA del archivo; el archivo vive en R2. Si estas
    # variables faltan, la subida/envío de media se desactiva solo (no rompe el bot).
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""
    r2_public_url: str = ""

    # Comportamiento
    # Ventana del buffer: se contesta cuando el cliente lleva ESTOS segundos CALLADO (debounce:
    # cada mensaje nuevo la reinicia), no a los 15s del primero.
    buffer_segundos: int = 15
    # TOPE ANTI-INANICIÓN del buffer, medido desde el PRIMER mensaje del buffer actual. Un
    # cliente que escribe sin parar reiniciaría la ventana para siempre y se quedaría sin
    # respuesta: pasado este tope se le contesta aunque siga escribiendo.
    buffer_max_segundos: int = 60
    conversacion_ttl: int = 86400
    # 🔴 CUÁNTOS DÍAS ATRÁS se rescata la conversación de Postgres cuando el historial de Redis ya
    # expiró (`services/memoria.py`). `conversacion_ttl` son 24 h: sin este respaldo, una clienta
    # que pregunta hoy y decide en tres días le habla a un bot que no recuerda NADA — y encima con
    # cuatro redes de seguridad ciegas. Aquí NO se sube el TTL de Redis a propósito: eso solo
    # movería la frontera y engordaría la memoria viva de todos. 15 días cubre de sobra el patrón
    # real de compra sin desenterrar pedidos de hace meses como si estuvieran vivos.
    historial_respaldo_dias: int = 15
    # 🔴 Tras cuántas HORAS de silencio se le DEVUELVE el saludo a un cliente que saluda.
    # Lo pidió Maired el 2026-08-21: escribió "Buenas tardes, ¿cómo estás?" un día después de su
    # último mensaje y el bot no le devolvió las buenas tardes, porque la red del saludo solo
    # corría en el PRIMER contacto. 4 h: cubre el "vuelvo al día siguiente" y el "vuelvo por la
    # tarde" sin saludar dos veces dentro de la misma conversación (eso se lee como un bot).
    saludo_tras_horas: float = 4.0
    max_iteraciones_agente: int = 6
    # Anti-abuso / tope de gasto: maximo de mensajes por cliente al dia antes de
    # pausar las respuestas automaticas con el (y avisar a la duena). 0 = sin tope.
    limite_mensajes_cliente_dia: int = 80
    # LISTA BLANCA de pruebas: si NO esta vacia (ej. "573005690062,584121234567"), el bot
    # SOLO responde a esos numeros; a los demas les guarda el mensaje pero NO responde
    # (probar en produccion sin contestarle a clientes reales). Vacia = responde a todos.
    numeros_permitidos: str = ""

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

    # Catálogo en PDF (archivo en el volumen) + URL pública del bot para que Meta
    # pueda descargar el PDF al enviarlo al cliente.
    catalogo_dir: str = "/data/catalogo"
    # 🔴 SIN DEFAULT A PROPÓSITO: cada entorno define la SUYA (Coolify). Antes traía
    # hardcodeada la URL del TALLER (`https://api-masvida.enovagroup.tech`); cuando el
    # taller murió (1-sep) el link del catálogo apuntó a un dominio muerto en TODOS los
    # entornos, Meta no podía descargar el PDF (error 131053) y el envío moría en SILENCIO
    # — hizo falta una autopsia a la BD para verlo (SESIONES 2-sep (15)). Un default con la
    # URL de UN entorno es la enfermedad D3 en versión config: funciona de casualidad hasta
    # que ese entorno se cae. Vacía ⇒ el catálogo se degrada al texto (`enviar_catalogo`) y
    # el arranque avisa fuerte; jamás se manda un PDF que Meta no pueda descargar.
    public_base_url: str = ""

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

    @model_validator(mode="after")
    def _el_tope_del_buffer_por_encima_de_la_ventana(self) -> "Settings":
        """`buffer_max_segundos` TIENE que ser mayor que `buffer_segundos`, o no hay debounce.

        🔴 POR QUE. `_procesar` (workers/tasks.py) espera a que el cliente lleve `buffer_segundos`
        CALLADO, pero nunca mas alla de `primero + buffer_max_segundos`. Con el tope por DEBAJO de
        la ventana, la resta que recorta la espera sale negativa o cero SIEMPRE: el tope dispara en
        el primer intento, se contesta al instante y el debounce que se construyo el 2026-08-09
        queda ANULADO. Y no se nota: el bot responde —a trozos, como antes— sin un solo error.
        Hasta hoy esto solo estaba DOCUMENTADO en el comentario de los dos campos.

        ⚠️ SE REPARA, NO SE LANZA — y es la decision que hay que justificar, porque el otro camino
        parece el rigoroso:
          · Los dos `raise` de arriba son de SEGURIDAD: con una contraseña publica, un proceso que
            no arranca es lo SEGURO. Aqui es al reves. `Settings` se construye al importar
            `app.config`, o sea en el webhook, en el worker, en los bancos y en cada script: un
            `raise` por una perilla de AFINADO deja al negocio SIN VENDER — apaga el bot entero
            por un ajuste que solo empeora el ritmo de las respuestas. En esta casa se DEGRADA,
            nunca se bloquea la venta.
          · Y no queda silencioso, que es el criterio del encargo: sale un `logger.error` con el
            valor malo, el corregido y el porque. Sin `logging` configurado, el `lastResort` de
            Python igual lo escribe en stderr (nivel WARNING), asi que se ve en los logs del
            contenedor desde la primera linea del arranque.

        Se corrige a la PROPORCION de fabrica (x4) y no al default fijo de 60: si alguien puso una
        ventana de 30 s queria esperar mas, y forzarle 60 le rompe la intencion. El `+1` cubre el
        borde de `buffer_segundos = 0` (buffer desactivado a proposito), donde x4 seguiria dando 0.
        """
        if self.buffer_max_segundos <= self.buffer_segundos:
            reparado = max(
                self.buffer_segundos * _PROPORCION_TOPE_BUFFER, self.buffer_segundos + 1
            )
            logger.error(
                "CONFIG: BUFFER_MAX_SEGUNDOS (%s) no puede ser <= BUFFER_SEGUNDOS (%s): el tope "
                "dispararia siempre y el bot volveria a contestar a trozos (sin debounce). Se "
                "corrige a %s para arrancar igual; ajusta el valor en el entorno.",
                self.buffer_max_segundos, self.buffer_segundos, reparado,
            )
            self.buffer_max_segundos = reparado
        return self

    @model_validator(mode="after")
    def _avisar_si_falta_la_url_publica(self) -> "Settings":
        """Grita al arrancar si `public_base_url` no sirve — pero NO bloquea el arranque.

        Es la misma disyuntiva del validador de arriba, y se resuelve igual (por las mismas
        razones): los dos `raise` de `_exigir_secretos_seguros` son de SEGURIDAD —una contraseña
        pública ⇒ el proceso NO debe arrancar—. Esto NO lo es. La URL pública la usa UN solo
        sitio, el catálogo en PDF (`enviar_catalogo`): sin ella el bot SIGUE vendiendo, cobrando
        y atendiendo — el catálogo se degrada solo al de TEXTO. Apagar el webhook, el worker, los
        bancos y cada script por un PDF sería bloquear la venta por una pieza que se degrada sola.
        En esta casa se DEGRADA, nunca se bloquea la venta. *(Y un `raise` aquí reventaría hasta
        el import de los tests y los bancos, que tampoco definen esta URL — ver `tests/conftest.py`.)*

        Pero NO queda mudo, que es justo lo que costó la autopsia del 2-sep: sale un `logger.error`
        en la PRIMERA línea del arranque (el `lastResort` de Python lo escribe en stderr aunque no
        haya logging configurado), así se ve en los logs del contenedor antes de que nadie pida el
        catálogo.
        """
        if not url_publica_utilizable(self.public_base_url):
            logger.error(
                "CONFIG: PUBLIC_BASE_URL no está definida o no es HTTPS (%r). El catálogo en PDF "
                "se degradará al catálogo de TEXTO (Meta no podría descargar el PDF: error 131053). "
                "Define PUBLIC_BASE_URL con la URL pública HTTPS de ESTE entorno en las variables "
                "de entorno (Coolify).",
                self.public_base_url,
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
