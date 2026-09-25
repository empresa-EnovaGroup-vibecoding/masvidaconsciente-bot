"""🗂️ EL EXPEDIENTE DE LA VENTA — el extractor (PR3, SESIONES (37)).

Lo que Whuilianny hace A MANO conversando con el cliente (toma un pedido, confirma un pago con un
"Listo", acuerda una entrega por nota de voz) quedaba solo como texto en `mensajes`: ningún dato lo
recogía, y el bot entraba ciego después de ella (en 6 de las 7 conversaciones reales donde entró,
chocó). Aquí ese texto se vuelve DATO con procedencia:

  1. `ventanas_owner` agrupa sus mensajes consecutivos (hasta que habla el cliente o pasan 5 min).
  2. `interpretar_duena` le pide a un modelo BARATO que PROPONGA eventos tipados copiando literales
     (nombre, cantidad, total, fecha). El modelo no ve precios ni ids: solo nombres del catálogo.
  3. `validar` los resuelve el CÓDIGO contra el catálogo, el precio de hoy, el calendario y las
     franjas, y dicta un veredicto: `escribe` (inequívoco), `propuesta` (dudoso: lo confirma una
     persona con un toque en la Bandeja) o `descarta` (no consta o no es de la venta).
  4. `procesar_ventana` aplica lo inequívoco (pedido claro, entrega clara) cuando `expediente_escritura`
     está en `auto` (el default desde el 24-sep) y deja como PROPUESTA (`intervenciones.propuesta`,
     motivo `propuesta_expediente`) todo lo dudoso y TODO pago.
  5. `aplicar_propuesta` es la ÚNICA puerta de escritura: la usan el toque humano del panel y el
     modo `auto`. Todo lo escrito lleva `origen='dueña'`, el mensaje de evidencia, la confianza y la hora.

🔴 Reglas que no se negocian: un PAGO es SIEMPRE propuesta (`pagado` solo nace de un toque humano,
CLAUDE.md §3) · un dato dudoso jamás se escribe · la evidencia tiene que constar literalmente en lo
que ella dijo · el extractor no le habla a nadie (ni al cliente ni a la dueña).
"""
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError
from sqlalchemy import select

from app.agent.contratos_atencion import (
    TEMAS_CONFIRMABLES,
    Contexto,
    EventoDuena,
    ExtraccionDuena,
    ItemPropuesto,
    PropuestaExpediente,
)
from app.agent.resolver_atencion import consta, fecha_del_cliente, normalizar
from app.agent.tools import _matchear_franja, _pedido_igual_reciente
from app.models import (
    ESTADOS_ACORDADOS,
    ORIGEN_DUENA,
    Conocimiento,
    Intervencion,
    Pago,
    Pedido,
    now_utc,
)

logger = logging.getLogger(__name__)

MOTIVO_PROPUESTA = "propuesta_expediente"
HUECO_VENTANA_MIN = 5
# `expediente_escritura`: off (no extrae) · propuestas (TODO a la Bandeja) · auto (lo inequívoco de
# pedido/entrega se escribe solo; pagos y lo dudoso siguen siendo propuesta).
# 🔴 El default pasó a `auto` el 24-sep noche (SESIONES (41), decisión de Maired): Whuilianny no va a
# estar en el panel confirmando tarjetas, así que lo que ella dice CLARO se anota solo y lo dudoso queda
# como pregunta que, si nadie toca, el bot simplemente no da por hecho. La palanca `propuestas` sigue
# disponible en Configuración (solo Enova) si el extractor se equivoca. Antes de PRODUCCIÓN, el replay
# sobre las conversaciones reales tiene que dar ≥0,98 de precisión en lo escrito (puerta G6).
ESCRITURA_DEFAULT = "auto"
ESCRITURAS = ("off", "propuestas", "auto")
UMBRAL_AUTO = 0.8
MARCA_VOZ = "🎤"

INSTRUCCION = """Lees SOLO mensajes que la dueña del negocio (Whuilianny) le escribió o le dijo por nota
de voz a un cliente. Tu trabajo: detectar si en esos mensajes ella HIZO algo de la venta y describirlo
con UNA llamada a proponer_eventos_duena. No escribes respuestas ni hablas con nadie.
Tipos: pedido_tomado (ella anota o confirma qué lleva el cliente, con o sin total); pago_confirmado
(ella dice que el pago llegó, quedó listo o lo recibió); entrega_acordada (día, momento del día o
lugar de entrega o retiro); entregado (ella dice que YA lo entregó, ya se lo llevó o ya lo recibió el
cliente); precio_especial (un precio, descuento, regalo o cortesía distinto al normal); cancelado (se
cancela o se quita algo ya acordado); respuesta_general (explica algo del producto o del negocio:
ingredientes, alérgenos, conservación, envíos nacionales, políticas); nada (charla, saludos,
bendiciones, preguntas suyas, cosas fuera de la venta).
Reglas: en evidencia copia LITERAL el trozo exacto de ella que sostiene cada evento. nombre_literal y
cantidad_literal se copian tal cual están (no deduzcas cantidades ni completes nombres con el
catálogo). total_literal y monto_literal se copian con su moneda si la dijo. fecha_texto copia
"mañana", "el sábado" o la fecha tal cual; nunca calcules. momento_texto copia "en la tarde", "a las
10". Si ella se corrige dentro de la ventana, vale SOLO lo último. No inventes nada que no esté
escrito. Si dudas, usa nada. Sin texto, cifras ni explicaciones fuera del contrato.
FORMATO EXACTO de la llamada (cada elemento de eventos es un OBJETO con sus campos, nunca una palabra
sola; todos los valores van entre comillas): {"eventos": [{"tipo": "pedido_tomado", "items":
[{"nombre_literal": "quesillos", "cantidad_literal": "2"}], "total_literal": "16$", "evidencia":
"Te anoté 2 quesillos son 16$"}, {"tipo": "entrega_acordada", "fecha_texto": "mañana",
"momento_texto": "en la tarde", "evidencia": "te lo llevo mañana en la tarde"}]}"""

# Los campos de texto y su tope: un modelo barato puede desbordarlos; se recortan ANTES de validar
# (una evidencia recortada sigue constando: es un prefijo literal).
_TOPES = {
    "nombre_literal": 120, "cantidad_literal": 40, "total_literal": 40, "monto_literal": 40,
    "metodo": 60, "fecha_texto": 60, "momento_texto": 60, "lugar_texto": 200, "tema": 40,
    "contenido": 400, "evidencia": 300,
}


# ══════════════════════════════════════════════════════════════════════════════════
#  1) LAS VENTANAS: sus mensajes consecutivos, hasta que habla el cliente
# ══════════════════════════════════════════════════════════════════════════════════

@dataclass
class Ventana:
    owner: list[dict] = field(default_factory=list)
    ultimo_cliente: str = ""

    @property
    def texto(self) -> str:
        return "\n".join(str(m.get("contenido") or "") for m in self.owner)

    @property
    def ultimo_id(self) -> int | None:
        return self.owner[-1].get("id") if self.owner else None

    @property
    def evidencia_mensaje(self) -> dict | None:
        return self.owner[-1] if self.owner else None


def _es_placeholder(texto) -> bool:
    t = str(texto or "").strip()
    return t.startswith("[") and t.endswith("]")


def ventanas_owner(mensajes, hueco_min: int = HUECO_VENTANA_MIN) -> list[Ventana]:
    """Función PURA (la usa también el arnés de replay): agrupa los mensajes `owner` consecutivos.

    Corta la ventana cuando habla el cliente o cuando pasan más de `hueco_min` minutos entre dos
    mensajes de ella. Los placeholders ("[nota de voz]" sin transcribir, "[foto]") no aportan y se
    saltan. Los mensajes del bot no cortan (con ella en el chat, el bot está pausado).
    """
    ventanas: list[Ventana] = []
    actual: Ventana | None = None
    ultimo_cliente = ""
    for m in sorted(mensajes, key=lambda x: x.get("id") or 0):
        rol = m.get("rol")
        if rol == "user":
            if actual and actual.owner:
                ventanas.append(actual)
            actual = None
            ultimo_cliente = str(m.get("contenido") or "")
            continue
        if rol != "owner" or _es_placeholder(m.get("contenido")):
            continue
        if actual and actual.owner:
            previo = actual.owner[-1].get("created_at")
            ahora = m.get("created_at")
            if previo and ahora and (ahora - previo) > timedelta(minutes=hueco_min):
                ventanas.append(actual)
                actual = None
        if actual is None:
            actual = Ventana(ultimo_cliente=ultimo_cliente)
        actual.owner.append(m)
    if actual and actual.owner:
        ventanas.append(actual)
    return ventanas


# ══════════════════════════════════════════════════════════════════════════════════
#  2) EL INTÉRPRETE: un modelo barato que solo copia literales
# ══════════════════════════════════════════════════════════════════════════════════

def indice_para_extractor(ctx: Contexto) -> dict:
    """Solo NOMBRES: sin ids, sin precios, sin cuentas. El modelo copia; el código resuelve."""
    return {
        "productos": [
            {"nombre": p["nombre"], "presentaciones": [v["presentacion"] for v in p["variantes"].values()]}
            for p in ctx.productos.values()
        ],
        "zonas": [z["nombre"] for z in ctx.zonas.values()],
        "metodos_de_pago": list(ctx.metodos),
        "temas": list(TEMAS_CONFIRMABLES),
    }


def _hora_vet(created_at) -> str:
    if not created_at:
        return ""
    return (created_at - timedelta(hours=4)).strftime("%H:%M")


def _mensajes_para_modelo(ventana: Ventana, ctx: Contexto) -> list[dict]:
    lineas = []
    for m in ventana.owner:
        contenido = str(m.get("contenido") or "")
        marca = "voz" if contenido.startswith(MARCA_VOZ) else "texto"
        lineas.append(f"[{_hora_vet(m.get('created_at'))} {marca}] {contenido}")
    return [
        {"role": "system", "content": INSTRUCCION + "\nCatálogo (solo nombres):\n"
         + json.dumps(indice_para_extractor(ctx), ensure_ascii=False)},
        {"role": "user", "content": (
            "Último mensaje del cliente: " + (ventana.ultimo_cliente or "(ninguno)")
            + "\n\nMensajes de la dueña:\n" + "\n".join(lineas)
        )},
    ]


def esquema_sin_refs(esquema: dict) -> dict:
    """El esquema de la herramienta, con cada `$ref` sustituido por su definición (sin `$defs`).

    La primera noche en pruebas (22-sep) Flash-Lite devolvió `{"eventos": ["pedido_tomado"]}` —
    una lista de PALABRAS en vez de objetos—: se perdió con las referencias internas que genera
    pydantic para los modelos anidados. Inline, el esquema se lee de arriba abajo."""
    defs = esquema.get("$defs", {})

    def _resolver(nodo):
        if isinstance(nodo, dict):
            if "$ref" in nodo:
                return _resolver(defs[nodo["$ref"].rsplit("/", 1)[-1]])
            return {k: _resolver(v) for k, v in nodo.items() if k != "$defs"}
        if isinstance(nodo, list):
            return [_resolver(x) for x in nodo]
        return nodo

    return _resolver(esquema)


def _recortar(d: dict) -> dict:
    return {k: (v[: _TOPES[k]] if k in _TOPES and isinstance(v, str) else v) for k, v in d.items()}


def normalizar_argumentos(texto_json: str) -> dict:
    """Lo que devolvió el modelo, listo para el contrato: strings recortadas a su tope. Una lista
    de eventos con PALABRAS SUELTAS (el fallo de Flash-Lite) se rechaza aquí, con nombre, para que
    el llamador reintente con el respaldo en vez de tragarse una extracción vacía."""
    datos = json.loads(texto_json)
    if not isinstance(datos, dict):
        raise ValueError("la propuesta no es un objeto")
    eventos = datos.get("eventos", [])
    if not isinstance(eventos, list):
        raise ValueError("eventos no es una lista")
    if any(not isinstance(e, dict) for e in eventos):
        raise ValueError("eventos trae palabras sueltas en vez de objetos")
    limpios = []
    for e in eventos:
        e = _recortar(e)
        if isinstance(e.get("items"), list):
            e["items"] = [_recortar(i) if isinstance(i, dict) else i for i in e["items"]]
        limpios.append(e)
    return {**datos, "eventos": limpios}


def _leer_extraccion(datos: dict) -> ExtraccionDuena:
    salida = datos["choices"][0]["message"]
    llamadas = salida.get("tool_calls") or []
    if len(llamadas) != 1 or llamadas[0]["function"]["name"] != "proponer_eventos_duena":
        raise ValueError("El extractor no devolvió una única propuesta")
    normalizado = normalizar_argumentos(llamadas[0]["function"]["arguments"])
    return ExtraccionDuena.model_validate_json(json.dumps(normalizado, ensure_ascii=False))


async def interpretar_duena(
    ventana: Ventana, ctx: Contexto, llm, modelo: str, *, modelo_respaldo: str | None = None,
) -> ExtraccionDuena:
    """Una llamada con el modelo barato; si su respuesta no cumple el contrato, UNA más con el
    respaldo. Si tampoco, ValueError (el llamador descarta la ventana sin escribir nada)."""
    herramienta = {"type": "function", "function": {
        "name": "proponer_eventos_duena",
        "description": "Eventos de la venta que la dueña hizo a mano. Propuesta sin efectos.",
        "parameters": esquema_sin_refs(ExtraccionDuena.model_json_schema()),
    }}
    mensajes = _mensajes_para_modelo(ventana, ctx)
    intentos = [modelo] + ([modelo_respaldo] if modelo_respaldo and modelo_respaldo != modelo else [])
    ultimo: Exception | None = None
    for n, m in enumerate(intentos):
        datos = await llm(mensajes, [herramienta], m)
        try:
            return _leer_extraccion(datos)
        except (ValidationError, ValueError, KeyError, TypeError) as e:
            ultimo = e
            logger.warning(
                "Expediente: %s no devolvió un contrato válido (%s)%s", m, str(e)[:160],
                " — se reintenta con el respaldo" if n + 1 < len(intentos) else "",
            )
    raise ValueError(f"El extractor no devolvió un contrato válido: {str(ultimo)[:200]}")


# ══════════════════════════════════════════════════════════════════════════════════
#  3) EL VALIDADOR: el código resuelve y dicta el veredicto
# ══════════════════════════════════════════════════════════════════════════════════

@dataclass
class Veredicto:
    accion: str  # escribe | propuesta | descarta
    motivo: str = ""
    confianza: float = 0.0
    propuesta: PropuestaExpediente | None = None


_STOP = {"el", "la", "de", "los", "las", "un", "una", "unos", "unas", "del", "al", "y", "con"}
_PALABRAS_NUM = {"un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
                 "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "doce": 12, "media docena": 6,
                 "docena": 12}


def _singular(palabra: str) -> str:
    return palabra[:-1] if len(palabra) > 3 and palabra.endswith("s") else palabra


def _palabras(texto: str) -> set[str]:
    return {_singular(w) for w in re.findall(r"\w+", normalizar(texto))} - _STOP


def producto_por_nombre(ctx: Contexto, nombre_literal: str):
    """(producto | None, ambiguo). Exacto → singular/plural → todas las palabras del literal están
    en UN solo nombre del catálogo. Dos candidatos = ambiguo (jamás se adivina: propuesta)."""
    objetivo = normalizar(nombre_literal)
    if not objetivo:
        return None, False
    productos = list(ctx.productos.values())
    for p in productos:
        if normalizar(p["nombre"]) == objetivo:
            return p, False
    obj_sg = " ".join(_singular(w) for w in objetivo.split())
    exactos = [p for p in productos if " ".join(_singular(w) for w in normalizar(p["nombre"]).split()) == obj_sg]
    if len(exactos) == 1:
        return exactos[0], False
    palabras = _palabras(nombre_literal)
    if not palabras:
        return None, False
    candidatos = [p for p in productos if palabras <= _palabras(p["nombre"])]
    if len(candidatos) == 1:
        return candidatos[0], False
    return None, len(candidatos) > 1


def variante_para(producto: dict, nombre_literal: str, texto_ventana: str):
    """La presentación: si el producto tiene una sola, esa; si no, la que ella nombró (en el ítem o
    en la ventana). Dos o ninguna ⇒ None (propuesta)."""
    variantes = list(producto["variantes"].values())
    if len(variantes) == 1:
        return variantes[0]
    dichas = [v for v in variantes if v.get("presentacion") and (
        consta(v["presentacion"], nombre_literal) or consta(v["presentacion"], texto_ventana))]
    return dichas[0] if len(dichas) == 1 else None


def parsear_cantidad(texto: str) -> int | None:
    t = normalizar(texto)
    if not t:
        return None
    if t in _PALABRAS_NUM:
        return _PALABRAS_NUM[t]
    m = re.search(r"\b(\d{1,3})\b", t)
    if m:
        n = int(m.group(1))
        return n if 1 <= n <= 100 else None
    for palabra, n in _PALABRAS_NUM.items():
        if re.search(rf"\b{palabra}\b", t):
            return n
    return None


def parsear_monto(texto: str) -> tuple[Decimal | None, str]:
    """('28$', '$28', '28 dólares', '1.500 bs', '28,50') → (Decimal, '$' | 'Bs' | '')."""
    t = normalizar(texto)
    if not t:
        return None, ""
    moneda = ""
    if "$" in t or "usd" in t or "dolar" in t or "verde" in t:
        moneda = "$"
    elif "bs" in t or "bolivar" in t:
        moneda = "Bs"
    m = re.search(r"(\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,](\d{1,2}))?", t)
    if not m:
        return None, moneda
    entero = re.sub(r"[.,]", "", m.group(1))
    dec = m.group(2) or "0"
    try:
        return Decimal(f"{entero}.{dec}"), moneda
    except InvalidOperation:
        return None, moneda


def _base(ev: EventoDuena, telefono: str, evidencia_id, pedido: dict | None) -> PropuestaExpediente:
    return PropuestaExpediente(
        tipo=ev.tipo, telefono=telefono, evidencia=ev.evidencia, evidencia_mensaje_id=evidencia_id,
        pedido_id=pedido["id"] if pedido else None,
    )


def _pedido_del_pago(
    pedido: dict | None, pedidos: list[dict] | None, monto: Decimal | None,
) -> tuple[dict | None, str]:
    """💰 PR6c: ¿a qué pedido va este pago? Devuelve (pedido elegido, nota para la Bandeja).

    Candidatos = los que NO están cancelados y NO tienen pago confirmado, del más nuevo al más viejo.
    Si dijo un monto y UN solo candidato tiene ese total → ese. Un solo candidato → ese. Varios sin
    pista → el más reciente, pero se anota "hay N sin pagar" para que se vea. Ninguno → el más reciente
    de la lista (al aplicar, el candado dirá "ya tiene un pago confirmado", que es lo correcto).

    Con `pedidos=None` cae a `pedido` (el de hoy): el comportamiento no cambia para quien no pase lista."""
    lista = pedidos if pedidos is not None else ([pedido] if pedido else [])
    candidatos = [
        p for p in lista
        if p and p.get("estado") != "cancelado" and p.get("pago_pendiente") != "confirmado"
    ]
    if not candidatos:
        return (lista[0] if lista else pedido), ("todos sus pedidos ya tienen pago" if lista else "")
    if monto is not None:
        cuadran = [
            p for p in candidatos
            if p.get("total") is not None and abs(Decimal(str(p["total"])) - monto) <= Decimal("0.01")
        ]
        if len(cuadran) == 1:
            return cuadran[0], ""
    if len(candidatos) == 1:
        return candidatos[0], ""
    return candidatos[0], f"hay {len(candidatos)} pedidos sin pagar; se propone el más reciente (#{candidatos[0]['id']})"


def validar(
    ev: EventoDuena, texto_ventana: str, ctx: Contexto, *, telefono: str, hoy: date,
    franjas: list[str], pedido: dict | None, evidencia_id: int | None,
    pedidos: list[dict] | None = None,
) -> Veredicto:
    """Función PURA (sin BD): el código dicta escribe / propuesta / descarta. `pedido` = el pedido
    abierto del cliente según `cargar_contexto` (o None); `pedidos` = la lista (PR6c) para elegir a
    cuál va un pago cuando hay varios; si no viene, se comporta como antes."""
    if ev.tipo == "nada":
        return Veredicto("descarta", "nada que anotar")
    if not consta(ev.evidencia, texto_ventana):
        return Veredicto("descarta", "la evidencia no consta en lo que ella dijo")
    p = _base(ev, telefono, evidencia_id, pedido)

    if ev.tipo == "pedido_tomado":
        if not ev.items:
            return Veredicto("descarta", "pedido sin ítems")
        confianza = 1.0
        motivos = []
        total = Decimal("0")
        for it in ev.items:
            prod, ambiguo = producto_por_nombre(ctx, it.nombre_literal)
            if prod is None:
                return Veredicto("propuesta", f"'{it.nombre_literal}' {'calza con varios productos' if ambiguo else 'no está en el catálogo'}", 0.0, p)
            var = variante_para(prod, it.nombre_literal, texto_ventana)
            if var is None:
                return Veredicto("propuesta", f"'{prod['nombre']}' tiene varias presentaciones y no dijo cuál", 0.0, p)
            cantidad = parsear_cantidad(it.cantidad_literal)
            if cantidad is None:
                cantidad, confianza = 1, min(confianza, UMBRAL_AUTO)
                motivos.append(f"cantidad de '{prod['nombre']}' asumida en 1")
            precio = var.get("precio")
            p.items.append(ItemPropuesto(
                producto_id=prod["id"], variante_id=var["id"], nombre=prod["nombre"],
                presentacion=var.get("presentacion") or "", cantidad=cantidad,
                precio_unitario=float(precio) if precio is not None else None,
            ))
            if precio is None:
                return Veredicto("propuesta", f"'{prod['nombre']}' no tiene precio cargado hoy", 0.0, p)
            total += Decimal(str(precio)) * cantidad
        p.total = float(total)
        dicho, moneda = parsear_monto(ev.total_literal)
        p.moneda = moneda
        if dicho is not None and moneda != "Bs" and abs(dicho - total) > Decimal("0.01"):
            p.total = float(dicho)  # lo que ELLA pactó manda, pero lo confirma una persona
            return Veredicto("propuesta", f"el total que dijo ({dicho}) no cuadra con el catálogo ({total})", 0.0, p)
        if dicho is None:
            confianza = min(confianza, UMBRAL_AUTO)
            motivos.append("sin total dicho")
        p.confianza = confianza
        return Veredicto("escribe" if confianza >= UMBRAL_AUTO else "propuesta", "; ".join(motivos) or "pedido claro", confianza, p)

    if ev.tipo == "pago_confirmado":
        monto, moneda = parsear_monto(ev.monto_literal)
        # A qué pedido va (PR6c). Un monto en Bs no se compara contra un total en $.
        elegido, nota = _pedido_del_pago(pedido, pedidos, monto if moneda != "Bs" else None)
        p.pedido_id = elegido["id"] if elegido else None
        if monto is None and elegido and elegido.get("total") is not None:
            monto, moneda = Decimal(str(elegido["total"])), "$"
        p.monto = float(monto) if monto is not None else None
        p.moneda, p.metodo = moneda, ev.metodo.strip()
        # 🔴 SIEMPRE propuesta: `pagado` solo nace de un toque humano (CLAUDE.md §3).
        return Veredicto("propuesta", "un pago lo confirma una persona" + (f"; {nota}" if nota else ""), 0.0, p)

    if ev.tipo == "entrega_acordada":
        fecha = fecha_del_cliente(ev.fecha_texto, hoy) if ev.fecha_texto.strip() else None
        franja = _matchear_franja(ev.momento_texto, franjas) if ev.momento_texto.strip() else None
        p.fecha = fecha.isoformat() if fecha else None
        p.franja, p.lugar = franja or "", ev.lugar_texto.strip()
        if not (p.fecha or p.franja or p.lugar):
            return Veredicto("descarta", "entrega sin fecha, momento ni lugar reconocibles")
        dudas = []
        if ev.fecha_texto.strip() and fecha is None:
            dudas.append(f"no entendí la fecha '{ev.fecha_texto}'")
        if ev.momento_texto.strip() and franja is None:
            dudas.append(f"'{ev.momento_texto}' no cae en una franja del negocio")
        if pedido is None:
            dudas.append("no hay un pedido abierto al que anotarla")
        elif pedido.get("fecha") and p.fecha and pedido["fecha"] != p.fecha:
            dudas.append(f"el pedido ya tenía fecha {pedido['fecha']}")
        if dudas:
            return Veredicto("propuesta", "; ".join(dudas), 0.0, p)
        p.confianza = 0.9
        return Veredicto("escribe", "entrega clara sobre el pedido abierto", 0.9, p)

    if ev.tipo == "precio_especial":
        monto, moneda = parsear_monto(ev.monto_literal)
        if monto is None:
            return Veredicto("descarta", "precio especial sin monto")
        p.monto, p.moneda, p.contenido = float(monto), moneda, ev.contenido.strip()
        return Veredicto("propuesta", "un precio especial lo confirma una persona", 0.0, p)

    if ev.tipo == "cancelado":
        if pedido is None:
            return Veredicto("descarta", "no hay pedido que cancelar")
        p.contenido = ev.contenido.strip()
        return Veredicto("propuesta", "una cancelación la confirma una persona", 0.0, p)

    if ev.tipo == "entregado":
        # "Ya te lo entregué" cierra la venta (PR4, decisión de Maired 24-sep): como PROPUESTA. Sin pedido
        # al que pegarlo no hay nada que cerrar; sobre uno cancelado tampoco.
        if pedido is None or pedido.get("estado") == "cancelado":
            return Veredicto("descarta", "no hay pedido abierto que marcar como entregado")
        if pedido.get("estado") == "entregado":
            return Veredicto("descarta", f"el pedido #{pedido['id']} ya figura entregado")
        p.contenido = ev.contenido.strip()
        return Veredicto("propuesta", "una entrega hecha la confirma una persona", 0.0, p)

    if ev.tipo == "respuesta_general":
        if not ev.contenido.strip():
            return Veredicto("descarta", "respuesta sin contenido")
        p.tema = ev.tema.strip() if ev.tema.strip() in TEMAS_CONFIRMABLES else "politica"
        p.contenido = ev.contenido.strip()
        return Veredicto("propuesta", "una respuesta del negocio la confirma una persona", 0.0, p)

    return Veredicto("descarta", f"tipo sin regla: {ev.tipo}")


# ══════════════════════════════════════════════════════════════════════════════════
#  4) LA PROPUESTA: cómo se le cuenta a una persona
# ══════════════════════════════════════════════════════════════════════════════════

def _fmt(monto, moneda) -> str:
    if monto is None:
        return ""
    d = Decimal(str(monto))
    numero = f"{d:.2f}".rstrip("0").rstrip(".")
    return f"Bs {numero}" if moneda == "Bs" else f"${numero}"


def resumen_humano(p: PropuestaExpediente, *, tipo_mensaje: str = "text", hora: str = "") -> str:
    """El texto de la Bandeja: una pregunta con la evidencia y su procedencia."""
    via = "nota de voz" if tipo_mensaje == "audio" else "mensaje"
    cuando = f" ({via}{' del ' + hora if hora else ''})"
    if p.tipo == "pedido_tomado":
        items = ", ".join(f"{i.cantidad} × {i.nombre}{' ' + i.presentacion if i.presentacion else ''}" for i in p.items)
        total = f" por {_fmt(p.total, p.moneda or '$')}" if p.total is not None else ""
        return f"Parece que Whuilianny le tomó el pedido a mano: {items}{total} — ¿correcto?{cuando}"
    if p.tipo == "pago_confirmado":
        monto = f" de {_fmt(p.monto, p.moneda)}" if p.monto is not None else ""
        metodo = f" por {p.metodo}" if p.metodo else ""
        return f"Parece que Whuilianny confirmó el pago{monto}{metodo} de este cliente — ¿correcto?{cuando}"
    if p.tipo == "entrega_acordada":
        partes = [x for x in (p.fecha and f"el {p.fecha}", p.franja, p.lugar and f"en {p.lugar}") if x]
        return f"Parece que Whuilianny acordó la entrega {' '.join(partes)} — ¿correcto?{cuando}"
    if p.tipo == "precio_especial":
        return f"Parece que Whuilianny dio un precio especial: {_fmt(p.monto, p.moneda)} — ¿lo aplico a este pedido?{cuando}"
    if p.tipo == "cancelado":
        return f"Parece que Whuilianny canceló el pedido #{p.pedido_id} — ¿correcto?{cuando}"
    if p.tipo == "entregado":
        return f"Parece que Whuilianny ya entregó el pedido #{p.pedido_id} — ¿correcto?{cuando}"
    if p.tipo == "respuesta_general":
        return f"Whuilianny respondió algo del negocio ({p.tema}): «{p.contenido[:160]}» — ¿lo guardo como respuesta confirmada?{cuando}"
    return f"Propuesta del expediente ({p.tipo}){cuando}"


async def _propuesta_repetida(session, telefono: str, p: PropuestaExpediente) -> bool:
    """La misma propuesta (mismo tipo, misma evidencia) ya está pendiente: no se duplica."""
    pendientes = (await session.execute(
        select(Intervencion).where(
            Intervencion.cliente_telefono == telefono,
            Intervencion.estado == "pendiente",
            Intervencion.motivo == MOTIVO_PROPUESTA,
        )
    )).scalars().all()
    return any(
        (i.propuesta or {}).get("tipo") == p.tipo
        and (i.propuesta or {}).get("evidencia_mensaje_id") == p.evidencia_mensaje_id
        for i in pendientes
    )


async def procesar_ventana(
    factory, telefono: str, ventana: Ventana, ctx: Contexto, *, llm, modelo: str,
    escritura: str, franjas: list[str], hoy: date, modelo_respaldo: str | None = None,
) -> list[tuple[str, str]]:
    """Interpreta una ventana y deja propuestas (o escribe, solo en `auto`). Devuelve
    [(tipo, accion)] para el log y los tests. Nunca lanza: un fallo aquí no puede tumbar al worker."""
    try:
        extraccion = await interpretar_duena(ventana, ctx, llm, modelo, modelo_respaldo=modelo_respaldo)
    except (ValidationError, ValueError, KeyError, TypeError) as e:
        logger.warning("Expediente: el extractor no devolvió un contrato válido para %s: %s", telefono, e)
        return []
    evidencia = ventana.evidencia_mensaje or {}
    resultados: list[tuple[str, str]] = []
    for ev in extraccion.eventos:
        v = validar(
            ev, ventana.texto, ctx, telefono=telefono, hoy=hoy, franjas=franjas,
            pedido=ctx.pedido, pedidos=ctx.pedidos, evidencia_id=evidencia.get("id"),
        )
        if v.accion == "descarta" or v.propuesta is None:
            logger.info("Expediente %s: %s descartado (%s)", telefono, ev.tipo, v.motivo)
            resultados.append((ev.tipo, "descarta"))
            continue
        p = v.propuesta
        p.resumen = resumen_humano(
            p, tipo_mensaje=str(evidencia.get("tipo") or "text"),
            hora=(evidencia.get("created_at") - timedelta(hours=4)).strftime("%d-%b %H:%M") if evidencia.get("created_at") else "",
        )
        async with factory() as session:
            if v.accion == "escribe" and escritura == "auto":
                try:
                    await aplicar_propuesta(session, p.model_dump(), usuario="extractor")
                    await session.commit()
                    resultados.append((ev.tipo, "escribe"))
                    continue
                except Exception as e:  # noqa: BLE001 — si no se puede escribir, se PROPONE
                    await session.rollback()
                    logger.warning("Expediente %s: no se pudo escribir %s (%s); queda como propuesta", telefono, ev.tipo, e)
                    v.motivo = f"no se pudo aplicar solo: {e}"
            if await _propuesta_repetida(session, telefono, p):
                resultados.append((ev.tipo, "repetida"))
                continue
            session.add(Intervencion(
                cliente_telefono=telefono, motivo=MOTIVO_PROPUESTA,
                detalle=(p.resumen + (f"\n· {v.motivo}" if v.motivo and v.accion == "propuesta" else ""))[:1500],
                mensaje_cliente=(ventana.ultimo_cliente or None),
                propuesta=p.model_dump(),
            ))
            await session.commit()
            resultados.append((ev.tipo, "propuesta"))
    return resultados


# ══════════════════════════════════════════════════════════════════════════════════
#  5) LA ÚNICA PUERTA DE ESCRITURA (el toque humano y el modo `auto` pasan por aquí)
# ══════════════════════════════════════════════════════════════════════════════════

def _monto_decimal(valor) -> Decimal | None:
    return Decimal(str(valor)) if valor is not None else None


async def _pedido_de(session, p: PropuestaExpediente) -> Pedido:
    if p.pedido_id is None:
        raise ValueError("la propuesta no señala ningún pedido")
    pedido = await session.get(Pedido, p.pedido_id)
    if pedido is None or pedido.cliente_telefono != p.telefono:
        raise ValueError(f"el pedido #{p.pedido_id} no es de este cliente")
    return pedido


def _items_json(p: PropuestaExpediente) -> list[dict]:
    return [{
        "producto": i.nombre, "variante_id": i.variante_id, "cantidad": i.cantidad,
        "precio_unitario": i.precio_unitario, "presentacion": i.presentacion, "opciones": None,
    } for i in p.items]


async def _crear_pedido_duena(session, p: PropuestaExpediente, usuario: str) -> Pedido:
    if not p.items:
        raise ValueError("un pedido sin productos no existe")
    if any(i.precio_unitario is None for i in p.items) and p.total is None:
        raise ValueError("falta el precio de algún producto y no hay total pactado")
    # 🔒 EL MISMO CANDADO DE DUPLICADOS que frena al bot (`tools._pedido_igual_reciente`, la familia del
    # #2074/#2603) frena también al toque humano (PR4): ella repite el pedido en dos mensajes ("te anoté 2
    # quesillos" … "entonces son los 2 quesillos, 16$"), el extractor propone dos veces, y un segundo "Sí"
    # NO puede fabricar el pedido gemelo. Si la lectura falla, el candado se abstiene (vender > bloquear).
    repetido = await _pedido_igual_reciente(
        session, p.telefono, [{"variante_id": i.variante_id, "cantidad": i.cantidad} for i in p.items]
    )
    if repetido is not None:
        raise ValueError(
            f"ya existe el pedido #{repetido.id} con estos mismos productos (tomado hace menos de 24 h): "
            f"si es el mismo, descarta esta propuesta"
        )
    total = _monto_decimal(p.total) if p.total is not None else sum(
        Decimal(str(i.precio_unitario)) * i.cantidad for i in p.items
    )
    pedido = Pedido(
        cliente_telefono=p.telefono,
        # Nace 'confirmado', JAMÁS 'esperando_pago': eso dispararía el cobro del bot sobre una venta
        # que ella ya cerró a mano.
        estado="confirmado", items=_items_json(p), total=total,
        notas=f"Tomado a mano por Whuilianny ({usuario}): «{p.evidencia[:200]}»",
        origen="dueña", evidencia_mensaje_id=p.evidencia_mensaje_id,
        confianza=_monto_decimal(round(p.confianza, 2)), extraido_at=now_utc(),
    )
    session.add(pedido)
    await session.flush()
    return pedido


async def aplicar_propuesta(session, propuesta: dict, *, usuario: str) -> dict:
    """Ejecuta una propuesta YA CONFIRMADA (por un toque humano o por `auto`). Lanza ValueError con
    un texto legible si no se puede; quien llama decide (409 en el panel, propuesta en el worker).
    Todo lo escrito lleva `origen='dueña'` + evidencia + confianza + `extraido_at`."""
    p = PropuestaExpediente.model_validate(propuesta)
    ahora = now_utc()

    if p.tipo == "pedido_tomado":
        pedido = await _crear_pedido_duena(session, p, usuario)
        return {"tipo": p.tipo, "pedido_id": pedido.id}

    if p.tipo == "pago_confirmado":
        pedido = await _pedido_de(session, p) if p.pedido_id else await _crear_pedido_duena(session, p, usuario)
        if pedido.estado == "cancelado":
            raise ValueError(f"el pedido #{pedido.id} está cancelado")
        otro = (await session.execute(
            select(Pago.id).where(Pago.pedido_id == pedido.id, Pago.estado == "confirmado")
        )).scalars().first()
        if otro is not None:
            raise ValueError(f"el pedido #{pedido.id} ya tiene un pago confirmado (#{otro})")
        monto = _monto_decimal(p.monto) if p.monto is not None else (
            Decimal(str(pedido.total)) if pedido.total is not None else None)
        # `pagado` nace de ESTE toque humano (o de `auto` solo si Maired lo enciende): igual que
        # /confirmar, queda firmado quién lo aprobó.
        pago = Pago(
            pedido_id=pedido.id, metodo=(p.metodo.strip().lower().replace(" ", "_") or "a_mano"),
            monto_usd=monto if p.moneda != "Bs" else None, monto_bs=monto if p.moneda == "Bs" else None,
            monto_recibido=monto, estado="confirmado", confirmado_por=usuario,
            origen="dueña", evidencia_mensaje_id=p.evidencia_mensaje_id,
            confianza=_monto_decimal(round(p.confianza, 2)), extraido_at=ahora,
        )
        session.add(pago)
        pedido.estado = "pagado"
        pedido.updated_at = ahora
        await session.flush()
        return {"tipo": p.tipo, "pedido_id": pedido.id, "pago_id": pago.id}

    if p.tipo == "entrega_acordada":
        pedido = await _pedido_de(session, p)
        if p.fecha:
            pedido.entrega_fecha = date.fromisoformat(p.fecha)
        if p.franja:
            pedido.entrega_franja = p.franja
        if p.lugar:
            pedido.entrega_referencia = p.lugar[:300]
        pedido.updated_at = ahora
        return {"tipo": p.tipo, "pedido_id": pedido.id}

    if p.tipo == "precio_especial":
        pedido = await _pedido_de(session, p)
        if p.monto is None:
            raise ValueError("precio especial sin monto")
        if p.moneda == "Bs":
            raise ValueError("un precio especial en bolívares no se aplica solo: el total del pedido es en dólares")
        pedido.total = _monto_decimal(p.monto)
        nota = f"Precio especial según Whuilianny ({usuario}): {_fmt(p.monto, p.moneda)} — «{p.evidencia[:160]}»"
        pedido.notas = f"{pedido.notas}\n{nota}" if pedido.notas else nota
        pedido.updated_at = ahora
        try:
            from app.services import redis_client as rc
            await rc.borrar_cobro(p.telefono)  # el cobro en curso ya no vale con este total
        except Exception:  # noqa: BLE001
            logger.warning("No se pudo borrar el cobro en curso de %s tras el precio especial", p.telefono)
        return {"tipo": p.tipo, "pedido_id": pedido.id}

    if p.tipo == "cancelado":
        pedido = await _pedido_de(session, p)
        if pedido.estado in ("pagado", "entregado"):
            raise ValueError(f"el pedido #{pedido.id} está {pedido.estado}: cancelarlo lo decide el panel, no una propuesta")
        pedido.estado = "cancelado"
        pedido.updated_at = ahora
        return {"tipo": p.tipo, "pedido_id": pedido.id}

    if p.tipo == "entregado":
        pedido = await _pedido_de(session, p)
        if pedido.estado == "cancelado":
            raise ValueError(f"el pedido #{pedido.id} está cancelado: no se puede marcar entregado")
        if pedido.estado == "entregado":
            return {"tipo": p.tipo, "pedido_id": pedido.id}  # ya estaba: idempotente
        # Se cierra aunque el pago no esté registrado (ella cobra en la puerta muchas veces): el pago,
        # si llega como dato, sigue siendo SU propuesta aparte. Cerrado = el bot ya no lo toca ni lo cobra.
        pedido.estado = "entregado"
        pedido.updated_at = ahora
        return {"tipo": p.tipo, "pedido_id": pedido.id}

    if p.tipo == "respuesta_general":
        tema = p.tema if p.tema in TEMAS_CONFIRMABLES else "politica"
        if not p.contenido.strip():
            raise ValueError("respuesta sin contenido")
        embedding = None
        try:
            from app.services.embeddings import obtener_embedding
            embedding = await obtener_embedding(f"{tema}. {p.contenido}")
        except Exception:  # noqa: BLE001 — el embedding es una mejora; la respuesta vale igual
            embedding = None
        c = Conocimiento(
            categoria="faq", titulo=f"Respuesta de Whuilianny · {tema}", contenido=p.contenido.strip(),
            tema_confirmado=tema, producto_id=None, confirmado=True, activo=True, embedding=embedding,
        )
        session.add(c)
        await session.flush()
        return {"tipo": p.tipo, "conocimiento_id": c.id}

    raise ValueError(f"tipo de propuesta sin puerta: {p.tipo}")


def cerrar_propuesta(inter, *, usuario: str, resultado: str) -> None:
    """Marca una Intervencion `propuesta_expediente` como resuelta (firmada por quien la tocó).

    `resultado` es "aplicada" o "descartada". NO hace commit ni notifica: eso lo decide quien llama
    (los dos endpoints del panel y la respuesta del pago por WhatsApp). Se extrajo para que el cierre
    sea EL MISMO en los tres sitios y nadie se olvide de firmar quién y cuándo."""
    ahora = now_utc()
    inter.estado = "resuelta"
    inter.resuelta_at = ahora
    inter.aplicada_por = usuario
    inter.aplicada_at = ahora
    inter.propuesta = {**(inter.propuesta or {}), "resultado": resultado}


# 💰 PR6b: SÍ/NO claros; una respuesta ambigua NO aplica un pago (se queda como propuesta).
_SI_PALABRAS = frozenset({"si", "sii", "yes"})
_NO_PALABRAS = frozenset({"no"})
_SI_EMOJI = ("✅", "👍", "✔", "☑")
_NO_EMOJI = ("❌", "👎", "✖")


def interpretar_respuesta_duena(texto: str | None) -> tuple[str, int | None] | None:
    """¿La dueña respondió un SÍ o un NO claro a la pregunta de un pago? (PR6b).

    Devuelve `("si", id|None)` / `("no", id|None)` con el número opcional que puso al final
    (el `#id` de la propuesta, p. ej. "SÍ 3131"), o **None** si el mensaje no es una respuesta
    inequívoca. La regla es conservadora a propósito: aplicar un pago falso (marcar pagado lo que
    no lo está) es peor que dejar la propuesta en la Bandeja, así que ante la duda → None.
    """
    if not texto:
        return None
    base = normalizar(texto)  # minúsculas, sin acentos, espacios colapsados
    if not base:
        return None
    palabras = re.findall(r"[a-z]+", base)  # "sí," → "si": la puntuación no cuenta
    # "no sé" NO es un NO: es "no lo sé". Ambiguo → None.
    if palabras[:2] == ["no", "se"]:
        return None
    m = re.search(r"#?\s*(\d{1,7})", texto)
    num = int(m.group(1)) if m else None
    tiene_si = bool(_SI_PALABRAS & set(palabras)) or any(e in texto for e in _SI_EMOJI)
    tiene_no = bool(_NO_PALABRAS & set(palabras)) or any(e in texto for e in _NO_EMOJI)
    if tiene_si and not tiene_no:
        return ("si", num)
    if tiene_no and not tiene_si:
        return ("no", num)
    return None


# ══════════════════════════════════════════════════════════════════════════════════
#  6) LEER EL EXPEDIENTE (PR4): lo que el bot tiene que saber al entrar a un chat
# ══════════════════════════════════════════════════════════════════════════════════

def _items_breves(items) -> str:
    partes = []
    for it in items or []:
        if not isinstance(it, dict) or not it.get("producto"):
            continue
        cant = it.get("cantidad")
        partes.append(f"{cant}× {it['producto']}" if cant else str(it["producto"]))
    return " · ".join(partes)


async def leer_expediente(telefono: str) -> dict:
    """Resumen SIN DINERO de la venta que una persona del negocio ya llevó a mano con este cliente:
    `pedidos_a_mano` (los últimos 3 con origen dueña, no cancelados), `propuestas_pendientes` y
    `venta_cerrada_a_mano` (hay al menos un pedido de ella confirmado/pagado/entregado). Lo lee
    `_retomar` para no reabrir lo que ella cerró. Fallo ⇒ vacío: sin dato, el retomar sigue como antes."""
    from app.services.db import get_session_factory

    vacio = {"pedidos_a_mano": [], "propuestas_pendientes": 0, "venta_cerrada_a_mano": False, "hechos": ""}
    try:
        factory = get_session_factory()
        async with factory() as session:
            pedidos = (await session.execute(
                select(Pedido).where(
                    Pedido.cliente_telefono == telefono, Pedido.origen == ORIGEN_DUENA,
                    Pedido.estado != "cancelado",
                ).order_by(Pedido.created_at.desc()).limit(3)
            )).scalars().all()
            pagos: dict[int, str] = {}
            if pedidos:
                filas = (await session.execute(
                    select(Pago.pedido_id, Pago.estado).where(Pago.pedido_id.in_([p.id for p in pedidos]))
                    .order_by(Pago.created_at)
                )).all()
                pagos = {pid: est for pid, est in filas}
            pendientes = (await session.execute(
                select(Intervencion.id).where(
                    Intervencion.cliente_telefono == telefono, Intervencion.estado == "pendiente",
                    Intervencion.motivo == MOTIVO_PROPUESTA,
                )
            )).scalars().all()
    except Exception:  # noqa: BLE001 — leer el expediente nunca tumba al que retoma
        logger.exception("No se pudo leer el expediente de %s", telefono)
        return vacio
    resumen = []
    for p in pedidos:
        pago = pagos.get(p.id)
        estado_pago = "pago confirmado" if pago == "confirmado" else (
            "comprobante recibido, en revisión" if pago in ("reportado", "parcial") else "pago SIN registrar")
        if p.estado == "pagado":
            estado_pago = "pago confirmado"
        entrega = " ".join(x for x in (
            p.entrega_fecha and f"el {p.entrega_fecha.isoformat()}", (p.entrega_franja or "").strip() or None,
            (p.entrega_referencia or "").strip() and f"en {p.entrega_referencia.strip()}",
        ) if x) or "sin acordar"
        resumen.append(
            f"El negocio ya le tomó a mano el pedido #{p.id} ({p.estado}): {_items_breves(p.items) or 'ver detalle'}; "
            f"entrega {entrega}; {estado_pago}."
        )
    if pendientes:
        resumen.append(
            f"Hay {len(pendientes)} dato(s) de esta venta dichos a mano por el negocio y aún SIN confirmar: "
            "no los des por hechos ni los contradigas."
        )
    return {
        "pedidos_a_mano": [{"id": p.id, "estado": p.estado, "pago": pagos.get(p.id)} for p in pedidos],
        "propuestas_pendientes": len(pendientes),
        "venta_cerrada_a_mano": any(p.estado in (*ESTADOS_ACORDADOS, "pagado", "entregado") for p in pedidos),
        "hechos": " ".join(resumen),
    }


# ══════════════════════════════════════════════════════════════════════════════════
#  Configuración (leída por turno; cualquier fallo cae al default seguro)
# ══════════════════════════════════════════════════════════════════════════════════

async def leer_config_expediente() -> tuple[str, str]:
    """(escritura, modelo_extractor). Falla → ('propuestas', modelo por defecto): nunca `auto`."""
    from app.config import get_settings
    from app.models import Configuracion
    from app.services.db import get_session_factory

    settings = get_settings()
    modelo = settings.openrouter_model_extractor
    escritura = ESCRITURA_DEFAULT
    try:
        factory = get_session_factory()
        async with factory() as session:
            filas = dict((await session.execute(
                select(Configuracion.clave, Configuracion.valor).where(
                    Configuracion.clave.in_(("expediente_escritura", "modelo_extractor"))
                )
            )).all())
        e = (filas.get("expediente_escritura") or "").strip().lower()
        if e in ESCRITURAS:
            escritura = e
        m = (filas.get("modelo_extractor") or "").strip()
        if m:
            modelo = m
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo leer la configuración del expediente; se usan los defaults")
    return escritura, modelo
