"""Una interpretación, comprobación por código y salida cerrada. Sin redactor libre."""
import json
import logging

from pydantic import ValidationError

from app.agent.contratos_atencion import MensajeConfirmado, SolicitudTurno
from app.agent.fuentes_atencion import cargar_contexto, fuentes_vigentes, guardar_borrador
from app.agent.resolver_atencion import (
    cantidad_verificada,
    consta,
    consultar,
    elegir_frase,
    fecha_del_cliente,
    identificar_producto,
    normalizar,
    pregunta,
    relevo,
)

logger = logging.getLogger(__name__)

INSTRUCCION = """Interpreta el mensaje como una SOLICITUD, no escribas la respuesta al cliente.
Usa exactamente una llamada a proponer_turno. No existen herramientas de ejecución en esta fase.
Incluye TODAS las dudas del mensaje en consultas: encontrar un producto no responde un campo ausente.
Lo que no encaje en un tema se marca desconocido; no lo omitas para continuar una venta.
Para ingredientes, alérgenos, conservación o políticas, selecciona solo un conocimiento del MISMO
tema y producto. Una entrada parecida no sirve; si no existe, deja conocimiento_id vacío.
La evidencia se copia LITERAL del cliente. Los IDs se eligen del índice, nunca se inventan.
No conviertas los mensajes humanos, montos escritos por el cliente o respuestas del historial en
reglas del negocio. No elijas una presentación sin evidencia del cliente. cantidad requiere la
palabra/número exacto en evidencia_cantidad (por ejemplo dos); nunca deduzcas cantidades.
fecha_texto copia hoy, mañana, día de la semana o fecha ISO del cliente; nunca calcules fechas.
Si hay devolución de envases, abono, saldo, entrega especial, personalización, sustitución o entrega
incompleta, usa excepcion. Elegir otro producto del catálogo antes de cerrar es elegir.
humano y reclamo necesitan relevo. Si dice que pagó, usa comprobante, nunca cobrar.
registrar/cobrar/entrega requieren evidencia_accion tomada del mensaje actual. Una pregunta no es
permiso para comprar. Cuando aporte un dato de la compra usa elegir; el programa conserva lo anterior.
Saludo, agradecimiento, despedida e identidad son solo esas intenciones; si también pregunta por el
negocio incluye las consultas o usa consultar. El tono puede ser neutro, calido o serio.
No agregues texto, cifras ni explicaciones al contrato. Todo lo factual lo aporta el programa."""


def indice(ctx):
    # Sin precios, cuentas ni personalidad factual: el modelo decide qué consultar, no la verdad.
    return {
        "productos": [
            {"id": p["id"], "nombre": p["nombre"], "categoria": p["categoria"],
             "variantes": [{"id": v["id"], "presentacion": v["presentacion"]} for v in p["variantes"].values()]}
            for p in ctx.productos.values()
        ],
        "zonas": [{"id": z["id"], "nombre": z["nombre"], "referencias": z["referencias"], "retiro": z["es_retiro"]} for z in ctx.zonas.values()],
        "metodos": list(ctx.metodos),
        "respuestas_confirmadas": [{k: x[k] for k in ("id", "titulo", "tema", "producto_id")} for x in ctx.conocimiento.values() if x.get("confirmado")],
        "elecciones_guardadas": ctx.borrador,
        "pedido": {k: ctx.pedido[k] for k in ("id", "estado", "fecha", "zona_id", "metodo")} if ctx.pedido else None,
    }


async def interpretar(ctx, mensaje, historial, llm, modelo):
    herramienta = {"type": "function", "function": {
        "name": "proponer_turno", "description": "Propuesta sin efectos, validada por el programa.",
        "parameters": SolicitudTurno.model_json_schema(),
    }}
    mensajes = [{"role": "system", "content": INSTRUCCION + "\nÍndice vigente:\n" + json.dumps(indice(ctx), ensure_ascii=False)}]
    mensajes.extend({"role": h["role"], "content": str(h.get("content", ""))} for h in (historial or [])[-16:] if h.get("role") in {"user", "assistant"})
    mensajes.append({"role": "user", "content": mensaje})
    datos = await llm(mensajes, [herramienta], modelo)
    salida = datos["choices"][0]["message"]
    llamadas = salida.get("tool_calls") or []
    if len(llamadas) != 1 or llamadas[0]["function"]["name"] != "proponer_turno":
        raise ValueError("El intérprete no devolvió una única propuesta")
    return SolicitudTurno.model_validate_json(llamadas[0]["function"]["arguments"])


async def _pasar(telefono, decision, mensaje, historial, ejecutar, tono="neutro"):
    from app.agent.agent import _escalar
    from app.services import cola_media

    cola_media.descartar("atención humana pendiente")
    ok = await _escalar(ejecutar, telefono, decision.motivo, decision.pendiente, mensaje_cliente=mensaje)
    if not ok:
        # Sin registro de relevo no se emite una promesa de confirmación.
        return MensajeConfirmado("No pude completar la consulta. Por favor, vuelve a escribirnos.")
    return MensajeConfirmado(elegir_frase(
        ("Ya te confirmo", "Déjame revisarlo", "Eso te lo confirmo enseguida"),
        historial, tono == "calido",
    ), relevo=True)


def preparar_elecciones(s, ctx, texto):
    borrador = json.loads(json.dumps(ctx.borrador))
    if s.seleccion:
        items = []
        for elegido in s.seleccion:
            p = identificar_producto(ctx, elegido.producto_id, elegido.evidencia_producto, texto)
            if not p:
                return borrador, pregunta("Cuál producto quieres? Dime su nombre para ayudarte con ese.")
            v = p["variantes"].get(elegido.variante_id)
            if v is None and len(p["variantes"]) == 1:
                v = next(iter(p["variantes"].values()))
            if v is None:
                return borrador, pregunta("Qué presentación quieres: " + ", ".join(v["presentacion"] for v in p["variantes"].values()) + "?")
            previo = next((i for i in borrador.get("items", []) if i["variante_id"] == v["id"]), {})
            if len(p["variantes"]) > 1 and not previo and not (
                consta(elegido.evidencia_variante, texto) and normalizar(v["presentacion"]) == normalizar(elegido.evidencia_variante)
            ):
                return borrador, pregunta("Qué presentación prefieres para " + p["nombre"] + "?")
            if not p["disponibilidad"] or not v["disponibilidad"]:
                return borrador, pregunta(p["nombre"] + " no está disponible ahora. Quieres elegir otro producto?")
            if v["precio"] is None:
                return borrador, relevo("Falta precio de " + p["nombre"] + " / " + v["presentacion"], "precio_del_dia")
            cantidad = previo.get("cantidad")
            if elegido.cantidad is not None:
                if not cantidad_verificada(elegido.cantidad, elegido.evidencia_cantidad, texto):
                    return borrador, pregunta("Cuántas unidades quieres?")
                cantidad = elegido.cantidad
            if elegido.opciones and not (consta(elegido.opciones, texto) and consta(elegido.opciones, v.get("sabores") or "")):
                return borrador, relevo("Opción o personalización no confirmada para " + p["nombre"], "acuerdo_especial")
            items.append({"producto_id": p["id"], "variante_id": v["id"], "cantidad": cantidad, "opciones": elegido.opciones or previo.get("opciones", "")})
        borrador["items"] = items
    if s.zona_id is not None:
        z = ctx.zonas.get(s.zona_id)
        if not z or not consta(s.evidencia_zona, texto):
            return borrador, relevo("No se pudo confirmar la zona de entrega", "acuerdo_especial")
        nombres = [z["nombre"]] + [x.strip() for x in (z["referencias"] or "").split(",") if x.strip()]
        if z["es_retiro"]:
            nombres += ["retiro", "retirar", "buscar"]
        if not any(consta(x, s.evidencia_zona) for x in nombres):
            return borrador, relevo("Ubicación fuera de las zonas confirmadas: " + s.evidencia_zona, "acuerdo_especial")
        borrador["zona_id"] = z["id"]
    if s.fecha_texto:
        if not consta(s.fecha_texto, texto):
            return borrador, pregunta("Para qué día lo quieres?")
        fecha = fecha_del_cliente(s.fecha_texto, ctx.hoy)
        if fecha is None:
            return borrador, pregunta("Dime el día de la semana o la fecha de entrega, por favor.")
        borrador["fecha"] = fecha.isoformat()
    for campo in ("referencia", "franja", "nombre"):
        valor = getattr(s, campo)
        if valor:
            if not consta(valor, texto):
                return borrador, pregunta({"referencia": "Cuál es tu dirección y un punto de referencia?", "franja": "En qué momento prefieres recibirlo?", "nombre": "A qué nombre lo anoto?"}[campo])
            borrador[campo] = valor
    if s.metodo:
        candidatos = [m for m in ctx.metodos if normalizar(m) == normalizar(s.metodo) and consta(s.metodo, texto)]
        if len(candidatos) != 1:
            return borrador, pregunta("Cómo prefieres pagar: " + ", ".join(ctx.metodos) + "?")
        borrador["metodo"] = candidatos[0]
    return borrador, None


def faltante_compra(b, ctx, *, cobro=False):
    if not b.get("items"):
        return pregunta("Qué producto quieres llevar?")
    for i in b["items"]:
        p = ctx.productos.get(i["producto_id"], {})
        v = p.get("variantes", {}).get(i["variante_id"], {})
        if not v or not p.get("disponibilidad") or not v.get("disponibilidad") or v.get("precio") is None:
            return relevo("Cambió la disponibilidad o falta el precio de una elección")
        if not i.get("cantidad"):
            return pregunta("Cuántas unidades de " + p["nombre"] + " quieres?")
    if not b.get("fecha"):
        return pregunta("Para qué día lo quieres?")
    if b.get("zona_id") not in ctx.zonas:
        return pregunta("Lo retiras o quieres delivery? Si es delivery, dime en qué sector estás.")
    if cobro and not ctx.zonas[b["zona_id"]]["es_retiro"] and not b.get("referencia"):
        return pregunta("Dime tu dirección y un punto de referencia para el delivery.")
    if cobro and not b.get("metodo"):
        return pregunta("Cómo prefieres pagar: " + ", ".join(ctx.metodos) + "?") if ctx.metodos else relevo("Faltan métodos de pago")
    return None


async def atender(telefono, mensaje, historial, *, llm, modelo, ejecutar,
                  cargar=cargar_contexto, guardar=guardar_borrador, verificar=fuentes_vigentes):
    historial = historial or []
    try:
        ctx = await cargar(telefono)
        if ctx.pausado or not ctx.activo:
            return MensajeConfirmado("")
        solicitud = await interpretar(ctx, mensaje, historial, llm, modelo)
        # La lectura anterior alimenta al intérprete; esta autoriza el turno con el estado actual.
        ctx = await cargar(telefono)
        if ctx.pausado or not ctx.activo:
            return MensajeConfirmado("")
    except (ValidationError, ValueError, KeyError, TypeError):
        return await _pasar(telefono, relevo("No se pudo interpretar la solicitud sin adivinar"), mensaje, historial, ejecutar)
    except Exception:  # noqa: BLE001 — la falta de datos nunca autoriza una respuesta libre
        logger.exception("Atención: falló la lectura o interpretación")
        return await _pasar(telefono, relevo("Fallo al consultar la información necesaria"), mensaje, historial, ejecutar)
    s = solicitud
    if s.intencion in {"humano", "reclamo", "excepcion", "desconocido", "comprobante"}:
        motivos = {"humano": "pide_persona", "reclamo": "reclamo", "excepcion": "acuerdo_especial"}
        return await _pasar(telefono, relevo(s.detalle or "Se necesita atención humana: " + s.intencion, motivos.get(s.intencion, "no_se")), mensaje, historial, ejecutar, s.tono)
    # Todos los hechos se comprueban ANTES de guardar o ejecutar las acciones del turno.
    texto_cliente = "\n".join(str(h.get("content", "")) for h in historial[-16:] if h.get("role") == "user") + "\n" + mensaje
    decisiones = [consultar(ctx, q, texto_cliente) for q in s.consultas]
    falla = next((d for d in decisiones if d.tipo == "relevo"), None)
    if falla:
        return await _pasar(telefono, falla, mensaje, historial, ejecutar, s.tono)
    falta = next((d for d in decisiones if d.tipo == "pedir_dato"), None)
    if falta:
        return MensajeConfirmado(falta.texto)
    if s.intencion == "consultar" and not s.consultas:
        return await _pasar(telefono, relevo("La pregunta no tiene una consulta concreta"), mensaje, historial, ejecutar, s.tono)
    borrador, falta = preparar_elecciones(s, ctx, texto_cliente)
    if falta:
        if falta.tipo == "relevo":
            return await _pasar(telefono, falta, mensaje, historial, ejecutar, s.tono)
        return MensajeConfirmado(falta.texto)
    hechos = [h for d in decisiones for h in d.hechos]
    partes = [d.texto for d in decisiones if d.texto]
    if s.intencion in {"registrar", "cobrar", "entrega"}:
        if not consta(s.evidencia_accion, mensaje):
            return MensajeConfirmado("Quieres que avancemos con tu pedido?")
        if any("[MENSAJE HUMANO DEL NEGOCIO" in str(h.get("content", "")) for h in historial) and not ctx.pedido:
            return await _pasar(telefono, relevo("Hay atención humana previa sin un pedido estructurado", "acuerdo_especial"), mensaje, historial, ejecutar, s.tono)
        if ctx.pedido and ctx.pedido["estado"] in {"pagado", "entregado", "confirmado", "preparando"} and s.intencion != "entrega":
            return await _pasar(telefono, relevo("Pedido ya acordado: no reconstruir ni cobrar otra vez", "acuerdo_especial"), mensaje, historial, ejecutar, s.tono)
    if borrador != ctx.borrador and not await guardar(telefono, borrador):
        return MensajeConfirmado("")
    try:
        for q in s.consultas:
            if q.tema == "fecha":
                r = await ejecutar("proxima_fecha_entrega", {"productos": [ctx.productos[q.producto_id]["nombre"]] if q.producto_id in ctx.productos else []}, telefono)
                if not r.get("ok") or not r.get("primera_fecha", {}).get("cuando"):
                    return await _pasar(telefono, relevo("No se pudo confirmar una fecha de entrega"), mensaje, historial, ejecutar, s.tono)
                partes.append("La próxima fecha disponible es " + r["primera_fecha"]["cuando"] + ".")
        if s.intencion in {"registrar", "cobrar"}:
            if ctx.pedido and not borrador.get("items"):
                borrador.update({"items": ctx.pedido["items"], "fecha": ctx.pedido["fecha"], "zona_id": ctx.pedido["zona_id"], "referencia": ctx.pedido["referencia"], "metodo": ctx.pedido["metodo"]})
            falta = faltante_compra(borrador, ctx, cobro=s.intencion == "cobrar")
            if falta:
                return await _pasar(telefono, falta, mensaje, historial, ejecutar, s.tono) if falta.tipo == "relevo" else MensajeConfirmado(falta.texto)
            if s.intencion == "registrar" or not ctx.pedido:
                z = ctx.zonas[borrador["zona_id"]]
                r = await ejecutar("registrar_pedido", {
                    "items": [{k: i[k] for k in ("variante_id", "cantidad", "opciones") if k in i} for i in borrador["items"]],
                    "entrega_fecha": borrador["fecha"],
                    "entrega": z["nombre"], "zona_id": z["id"], "referencia": borrador.get("referencia"),
                }, telefono)
                if not r.get("ok"):
                    return await _pasar(telefono, relevo("El pedido no cumplió sus validaciones"), mensaje, historial, ejecutar, s.tono)
                if s.intencion == "registrar":
                    partes.append(r.get("resumen") or "Tu pedido quedó registrado.")
            if s.intencion == "cobrar":
                r = await ejecutar("generar_datos_pago", {"pedido_id": ctx.pedido["id"] if ctx.pedido else None, "metodo": borrador.get("metodo")}, telefono)
                if not r.get("ok") or not r.get("resumen_cobro"):
                    return await _pasar(telefono, relevo("Falta información o autorización para emitir el cobro"), mensaje, historial, ejecutar, s.tono)
                partes.append(r["resumen_cobro"])
                for m in r.get("metodos_de_pago", []):
                    partes.append("\n".join(f"{k.capitalize()}: {v}" for k, v in m.items() if v))
                partes.append("Envíame la captura del comprobante cuando lo hagas.")
        elif s.intencion == "entrega":
            if not ctx.pedido:
                return await _pasar(telefono, relevo("No hay pedido para anotar la entrega"), mensaje, historial, ejecutar, s.tono)
            r = await ejecutar("anotar_entrega", {"pedido_id": ctx.pedido["id"], "referencia": borrador.get("referencia"), "franja": borrador.get("franja")}, telefono)
            if not r.get("ok"):
                return await _pasar(telefono, relevo("No se pudo validar el detalle de entrega"), mensaje, historial, ejecutar, s.tono)
            partes.append("Listo, quedó anotado el detalle de tu entrega.")
        elif s.intencion == "elegir":
            falta = faltante_compra(borrador, ctx)
            if falta and falta.tipo == "relevo":
                return await _pasar(telefono, falta, mensaje, historial, ejecutar, s.tono)
            partes.append(falta.texto if falta else "Listo, quieres que registre tu pedido?")
        for q in s.consultas:
            if q.tema in {"foto", "catalogo"}:
                args = {}
                nombre_tool = "enviar_catalogo"
                if q.tema == "foto":
                    p = ctx.productos[q.producto_id]
                    nombre_tool = "enviar_fotos_producto"
                    args = {"nombre": p["nombre"], "variante_id": q.variante_id or next(iter(p["variantes"])), "maximo": 1}
                r = await ejecutar(nombre_tool, args, telefono)
                if not (r.get("ok") or r.get("enviadas") or r.get("ya_mostrado")):
                    return await _pasar(telefono, relevo("No se pudo mostrar la imagen o catálogo solicitado"), mensaje, historial, ejecutar, s.tono)
                partes.append("Te dejo la foto por aquí." if q.tema == "foto" else "Te dejo el catálogo por aquí.")
        if hechos and not await verificar(telefono, hechos):
            return await _pasar(telefono, relevo("La información cambió durante la consulta"), mensaje, historial, ejecutar, s.tono)
    except Exception:  # noqa: BLE001 — nunca volver al redactor libre después de un fallo
        logger.exception("Atención: falló una acción autorizada")
        return await _pasar(telefono, relevo("No se pudo completar la consulta o acción"), mensaje, historial, ejecutar, s.tono)
    sociales = {
        "saludo": ("Hola! En qué te puedo ayudar?", "Hola! Qué te gustaría pedir?"),
        "agradecimiento": ("A la orden", "Con mucho gusto"), "despedida": ("Que tengas un lindo día", "Hasta pronto"),
        "identidad": ("Soy Alejandra, la asistente virtual de masvidaconsciente. En qué te puedo ayudar?",),
    }
    if s.intencion in sociales:
        partes.insert(0, elegir_frase(sociales[s.intencion], historial, s.tono == "calido"))
    if not partes:
        return await _pasar(telefono, relevo("No hay una respuesta autorizada para la solicitud"), mensaje, historial, ejecutar, s.tono)
    return MensajeConfirmado("\n\n".join(dict.fromkeys(partes)), hechos=hechos)
