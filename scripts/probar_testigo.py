"""El TESTIGO del webhook: mira y anota, nunca estorba."""
import logging
import sys

import app.webhook.router as R
from app.webhook.parser import extraer_mensaje
from app.webhook.router import _testigo

# El log de la app ya esta configurado: le engancho una salida propia para VER lo que anota.
h = logging.StreamHandler(sys.stdout)
h.setFormatter(logging.Formatter("   ANOTA -> %(message)s"))
R.logger.addHandler(h)
R.logger.setLevel(logging.INFO)
R.logger.propagate = False

print("\n1) mensaje normal de un cliente -> NO anota (ya se registra en otro lado)")
p1 = {"entry":[{"changes":[{"field":"messages","value":{"messaging_product":"whatsapp","metadata":{},"contacts":[{"profile":{"name":"Ana"}}],"messages":[{"id":"wamid.1","from":"58412","type":"text","text":{"body":"hola"}}]}}]}]}
_testigo(p1)
print("   (silencio = correcto) · y el camino normal sigue intacto:", extraer_mensaje(p1)["tipo"] == "text")

print("\n2) un ESTADO (entregado/leido/fallo) -> anota")
_testigo({"entry":[{"changes":[{"field":"messages","value":{"messaging_product":"whatsapp","statuses":[{"id":"wamid.2","status":"delivered"}]}}]}]})

print("\n3) un ECO (lo que la duena escribe desde SU CELULAR) -> anota DE QUIEN es")
_testigo({"entry":[{"changes":[{"field":"smb_message_echoes","value":{"messaging_product":"whatsapp","metadata":{},"message_echoes":[{"id":"wamid.3","from":"573132933806","to":"584121112233","type":"text","text":{"body":"hola"}}]}}]}]})

print("\n4) basura / payload roto -> NO explota")
_testigo({"nada":"que ver"}); _testigo({"entry":[]}); _testigo({})
print("   (no exploto)")
print("\n   OK: el testigo mira y anota; no cambia ni una respuesta del bot.")
