"""TANDA 2 — las dos redes: la del RELEVO (promesa sin aviso) y la de la HONESTIDAD."""
from app.agent.agent import _frase_prohibida, _promete_averiguar, _suena_a_sistema

# ── 1. LA RED DEL RELEVO: ¿detecta una promesa de averiguar? ──
PROMESAS = [
    # (texto, ¿es una promesa que exige avisar a la dueña?)
    ("Para envíos a otras ciudades, eso puntual te lo confirmo con la dueña.", True),   # el caso REAL
    ("Déjame verificar eso y te confirmo enseguida 💚", True),
    ("Permíteme consultarlo y te aviso", True),
    ("Ese precio lo verifico y te lo confirmo ya", True),
    ("Voy a preguntar y te digo", True),
    ("Dame un momentito, lo consulto con Whuilianny", True),
    # Lo que NO debe disparar la red (falsos positivos que romperían la venta):
    ("Perfecto, te confirmo el pedido: 2 paquetes de empanadas. Total: $28", False),
    ("Listo, te lo tengo para el lunes 💚", False),
    ("Son $14 el paquete de 8 unidades", False),
    ("¿Te confirmo entonces 2 paquetes?", False),
]

# ── 2. LA RED DE LA HONESTIDAD: frases que NO pueden salir jamás ──
PROHIBIDAS = [
    ("Acabo de revisar todo en mi banco y no me aparece tu pago", True),   # el caso REAL del ensayo
    ("Ya verifiqué en el banco, tu pago no llegó", True),
    ("Mi banco ya me confirmó el pago", True),
    ("Ya me llegó tu pago, gracias 💚", True),
    ("No me ha llegado ningún pago", True),
    ("Soy Whuilianny 💚 Sí, soy yo, una persona", True),
    ("No soy un bot, soy una persona real", True),
    # Lo que SÍ puede decir (no debe bloquearse):
    ("Recibí tu comprobante 💚 Whuilianny lo revisa en su banco y te confirma", False),
    ("Soy la asistente virtual de masvidaconsciente 💚 ¿Quieres que te pase con Whuilianny?", False),
    ("Cuando hagas el pago, me mandas la captura del comprobante", False),
    ("Te llegó bien el catálogo?", False),
]

# ── 3. LA RED DE LA VOZ: no hablar como un sistema ──
SISTEMA = [
    ("Lo que tengo cargado es entrega local: retiro en La Mendera o delivery", True),  # caso REAL
    ("No me trae información sobre descuento por cantidad", True),
    ("El sistema no me deja hacer eso", True),
    ("Lamento, no se pudo enviar la foto", True),
    ("Según mi sistema, ese producto está agotado", True),
    # Lo que SÍ suena a persona:
    ("Hacemos entrega en La Mendera o delivery por tu zona 💚", False),
    ("Ese producto se nos agotó, pero tengo estos otros", False),
    ("Te mando la foto ahorita", False),
]

fallos = 0
print("\n1) RED DEL RELEVO — 'si prometes averiguar, TIENES que avisarle a la dueña'")
for texto, esperado in PROMESAS:
    got = _promete_averiguar(texto)
    ok = got == esperado
    fallos += 0 if ok else 1
    marca = "[OK ]" if ok else "[MAL]"
    accion = "AVISA a la dueña" if got else "no avisa       "
    print(f"   {marca} {accion} | {texto[:62]}")

print("\n2) RED DE LA HONESTIDAD — 'hay frases que NO salen jamás'")
for texto, esperado in PROHIBIDAS:
    que = _frase_prohibida(texto)
    got = que is not None
    ok = got == esperado
    fallos += 0 if ok else 1
    marca = "[OK ]" if ok else "[MAL]"
    accion = "BLOQUEA" if got else "pasa   "
    extra = f"  ({que})" if que else ""
    print(f"   {marca} {accion} | {texto[:58]}{extra}")

print("\n3) RED DE LA VOZ — 'una vendedora no dice lo que tiene cargado'")
for texto, esperado in SISTEMA:
    got = _suena_a_sistema(texto)
    ok = got == esperado
    fallos += 0 if ok else 1
    marca = "[OK ]" if ok else "[MAL]"
    accion = "REESCRIBE" if got else "pasa     "
    print(f"   {marca} {accion} | {texto[:58]}")

print()
print("   ✅ LAS TRES REDES FUNCIONAN" if not fallos else f"   🔴 {fallos} CASO(S) MAL")
raise SystemExit(1 if fallos else 0)
