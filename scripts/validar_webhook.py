"""Valida el webhook simulando exactamente lo que envía Meta.
Ejecutar: .venv/Scripts/python.exe scripts/validar_webhook.py
"""
import hashlib
import hmac
import json

from fastapi.testclient import TestClient

import app.webhook.router as router_mod
from app.config import get_settings
from app.main import app

settings = get_settings()
client = TestClient(app)


def firmar(raw: bytes) -> str:
    return "sha256=" + hmac.new(settings.meta_app_secret.encode(), raw, hashlib.sha256).hexdigest()


def test_verificacion_ok():
    r = client.get(
        "/webhook/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": settings.meta_verify_token,
            "hub.challenge": "desafio-12345",
        },
    )
    assert r.status_code == 200, r.status_code
    assert r.text == "desafio-12345", r.text
    print("OK  verificacion responde el challenge")


def test_verificacion_token_malo():
    r = client.get(
        "/webhook/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "malo", "hub.challenge": "x"},
    )
    assert r.status_code == 403, r.status_code
    print("OK  verificacion rechaza token incorrecto (403)")


def test_mensaje_firmado():
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "100526692613101",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"display_phone_number": "584247047595", "phone_number_id": "000000000000000"},
                    "contacts": [{"profile": {"name": "Maria"}, "wa_id": "584140000000"}],
                    "messages": [{
                        "from": "584140000000",
                        "id": "wamid.TEST123",
                        "timestamp": "1700000000",
                        "type": "text",
                        "text": {"body": "Hola, quiero ver el catalogo"},
                    }],
                },
            }],
        }],
    }
    # Sustituimos el encolado real (Redis/Celery) por un mock que captura el mensaje
    capturado = []

    async def fake_encolar(mensaje):
        capturado.append(mensaje)
        return "ok"

    router_mod._encolar_mensaje = fake_encolar

    raw = json.dumps(payload).encode()
    r = client.post(
        "/webhook/whatsapp",
        content=raw,
        headers={"x-hub-signature-256": firmar(raw), "content-type": "application/json"},
    )
    assert r.status_code == 200, r.status_code
    assert r.json() == {"status": "ok"}, r.json()
    assert capturado, "no se encoló el mensaje"
    assert capturado[0]["telefono"] == "584140000000", capturado[0]
    assert capturado[0]["texto"] == "Hola, quiero ver el catalogo", capturado[0]
    print("OK  mensaje de texto con firma valida -> extrae y encola correctamente")


def test_firma_invalida():
    raw = b'{"fake": true}'
    r = client.post(
        "/webhook/whatsapp",
        content=raw,
        headers={"x-hub-signature-256": "sha256=firmafalsa", "content-type": "application/json"},
    )
    assert r.status_code == 401, r.status_code
    print("OK  firma invalida -> 401 (rechazado)")


def test_status_update_ignorado():
    # Meta tambien manda status updates (entregado/leido) al mismo webhook
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "statuses": [{"id": "wamid.X", "status": "delivered"}],
        }}]}],
    }
    raw = json.dumps(payload).encode()
    r = client.post(
        "/webhook/whatsapp",
        content=raw,
        headers={"x-hub-signature-256": firmar(raw), "content-type": "application/json"},
    )
    assert r.status_code == 200, r.status_code
    assert r.json() == {"status": "ignored"}, r.json()
    print("OK  status update (no mensaje) -> ignorado")


if __name__ == "__main__":
    test_verificacion_ok()
    test_verificacion_token_malo()
    test_mensaje_firmado()
    test_firma_invalida()
    test_status_update_ignorado()
    print("\nTODO OK - el webhook recibe y valida como Meta espera")
