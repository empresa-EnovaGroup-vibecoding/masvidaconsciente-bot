import hashlib
import hmac


def verificar_firma(app_secret: str, raw_body: bytes, signature_header: str | None) -> bool:
    """Valida el header X-Hub-Signature-256 que envía Meta en cada webhook.

    Sin esta validación, cualquiera podría enviar mensajes falsos al bot.
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    esperado = "sha256=" + hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, signature_header)
