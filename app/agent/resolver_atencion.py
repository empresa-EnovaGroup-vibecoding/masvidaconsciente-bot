"""Decisiones y frases deterministas. No interpreta texto libre como una regla comercial."""
import re
import unicodedata
from datetime import date, timedelta

from app.agent.contratos_atencion import Consulta, Contexto, DecisionTurno
from app.agent.fuentes_atencion import hecho


def normalizar(texto):
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", str(texto or "").lower())
                            if not unicodedata.combining(c)).split())


def consta(evidencia, texto):
    return bool(evidencia and normalizar(evidencia) in normalizar(texto))


def elegir_frase(opciones, historial=(), calido=False):
    anteriores = {str(h.get("content", "")) for h in (historial or [])[-4:] if h.get("role") == "assistant"}
    for frase in opciones:
        if not any(frase in anterior for anterior in anteriores):
            return frase + (" 💚" if calido else "")
    return opciones[0] + (" 💚" if calido else "")


def relevo(detalle, motivo="no_se", producto=""):
    return DecisionTurno("relevo", motivo=motivo, pendiente=detalle, producto=producto)


def pregunta(texto):
    return DecisionTurno("pedir_dato", texto)


def identificar_producto(ctx, producto_id, evidencia, texto):
    p = ctx.productos.get(producto_id)
    if not p:
        return None
    # Una elección validada y persistida puede resolver "ese". Un número emitido por la IA no.
    if any(i.get("producto_id") == producto_id for i in ctx.borrador.get("items", [])):
        return p
    if not consta(evidencia, texto):
        return None
    palabras = set(re.findall(r"\w+", normalizar(evidencia))) - {"el", "la", "de", "los", "las", "un", "una"}
    candidatos = [x for x in ctx.productos.values() if palabras and palabras <= set(re.findall(r"\w+", normalizar(x["nombre"])))]
    if len(candidatos) == 1 and candidatos[0]["id"] == producto_id:
        return p
    return None


def consultar(ctx: Contexto, q: Consulta, texto: str) -> DecisionTurno:
    tema = q.tema
    if tema in {"productos", "catalogo"}:
        productos = [p for p in ctx.productos.values() if p["disponibilidad"]]
        if not productos:
            return relevo("No hay productos confirmados disponibles")
        hs = [hecho("producto", p["id"], "nombre", p["nombre"]) for p in productos]
        return DecisionTurno("responder", "Tenemos estas opciones:\n" + "\n".join(p["nombre"] for p in productos), hs)
    if tema == "ubicacion":
        valor = ctx.negocio.get("negocio_ubicacion")
        return (DecisionTurno("responder", str(valor), [hecho("negocio", "negocio", "negocio_ubicacion", valor)])
                if valor else relevo("Falta la ubicación confirmada del negocio"))
    if tema == "metodos_pago":
        return (DecisionTurno("responder", "Puedes pagar por " + ", ".join(ctx.metodos) + ".",
                              [hecho("metodos", "metodos", "titulos", list(ctx.metodos))])
                if ctx.metodos else relevo("No hay métodos de pago confirmados"))
    if tema == "estado_pedido":
        if not ctx.pedido:
            return relevo("No se encontró un pedido registrado para confirmar su estado")
        estados = {"pendiente": "Tu pedido está en preparación de la cuenta.", "esperando_pago": "Tu pedido está esperando el pago.",
                   "pagado": "Tu pago está aprobado.", "confirmado": "Tu pedido está confirmado.",
                   "preparando": "Estamos preparando tu pedido.", "entregado": "Tu pedido figura como entregado."}
        estado = ctx.pedido["estado"]
        if estado not in estados:
            return relevo("Estado del pedido no reconocido")
        return DecisionTurno("responder", estados[estado], [hecho("pedido", ctx.pedido["id"], "estado", estado)])
    if tema in {"ingredientes", "alergenos", "conservacion", "envio_nacional", "politica"}:
        k = ctx.conocimiento.get(q.conocimiento_id, {})
        if not k.get("confirmado") or k.get("tema") != tema or k.get("producto_id") != q.producto_id:
            producto = ctx.productos.get(q.producto_id, {}).get("nombre", "")
            return relevo(
                f"Falta respuesta confirmada para {tema}; producto {q.producto_id or 'negocio'}",
                producto=producto,
            )
        if q.producto_id and not identificar_producto(ctx, q.producto_id, q.evidencia, texto):
            return pregunta("Cuál producto quieres consultar?")
        if not k.get("contenido"):
            return relevo(f"Respuesta vacía: {tema}", producto=k.get("titulo", ""))
        # Dos entradas aprobadas distintas para el mismo tema/alcance son una contradicción.
        pares = [x for x in ctx.conocimiento.values() if x.get("confirmado") and x.get("tema") == tema and x.get("producto_id") == q.producto_id]
        if len({normalizar(x.get("contenido")) for x in pares}) != 1:
            return relevo(f"Respuestas contradictorias: {tema}", producto=k.get("titulo", ""))
        return DecisionTurno("responder", k["contenido"], [hecho("conocimiento", k["id"], "contenido", k["contenido"])])
    if tema == "desconocido":
        return relevo("Pregunta sin una fuente confirmada")
    if tema == "fecha":
        return DecisionTurno("responder")  # la resuelve el calendario, no un texto del modelo
    p = identificar_producto(ctx, q.producto_id, q.evidencia, texto)
    if p is None:
        return pregunta("Cuál producto quieres? Dime el nombre para ayudarte con ese.")
    hs = [hecho("producto", p["id"], "nombre", p["nombre"])]
    if tema in {"precio", "sabores", "foto"}:
        vs = p["variantes"]
        v = vs.get(q.variante_id) if q.variante_id else (next(iter(vs.values())) if len(vs) == 1 else None)
        if v is None:
            return pregunta("De cuál presentación? " + ", ".join(v["presentacion"] for v in vs.values()))
        if len(vs) > 1 and not consta(v["presentacion"], texto) and not any(i.get("variante_id") == v["id"] for i in ctx.borrador.get("items", [])):
            return pregunta("Qué presentación quieres: " + ", ".join(v["presentacion"] for v in vs.values()) + "?")
        hs.append(hecho("variante", v["id"], "presentacion", v["presentacion"]))
        if tema == "foto":
            return DecisionTurno("responder", "", hs)
        valor = v.get(tema)
        if valor is None or (isinstance(valor, str) and not valor.strip()):
            return relevo(
                f"Falta {tema} de {p['nombre']} / {v['presentacion']}",
                "precio_del_dia" if tema == "precio" else "no_se",
                p["nombre"],
            )
        hs.append(hecho("variante", v["id"], tema, valor))
        if tema == "precio":
            from app.agent.tools import _fmt_usd
            return DecisionTurno("responder", f"{p['nombre']} ({v['presentacion']}): {_fmt_usd(valor)}.", hs)
        return DecisionTurno("responder", f"Para {p['nombre']}: {valor}.", hs)
    if tema == "disponibilidad":
        valor = p[tema]
        return DecisionTurno("responder", f"{p['nombre']} {'está disponible' if valor else 'no está disponible ahora'}.", hs + [hecho("producto", p["id"], tema, valor)])
    valor = p.get(tema)
    if valor is None or not str(valor).strip():
        return relevo(f"Falta {tema} de {p['nombre']}", producto=p["nombre"])
    hs.append(hecho("producto", p["id"], tema, valor))
    etiquetas = {"duracion": "Duración", "se_congela": "Para congelarlo", "apto_diabeticos": "Información de la ficha", "descripcion": p["nombre"]}
    return DecisionTurno("responder", f"{etiquetas.get(tema, tema)}: {valor}", hs)


def fecha_del_cliente(texto: str, hoy: date):
    t = normalizar(texto).strip()
    if t in {"hoy", "para hoy"}:
        return hoy
    if t in {"manana", "para manana"}:
        return hoy + timedelta(days=1)
    if t in {"pasado manana", "para pasado manana"}:
        return hoy + timedelta(days=2)
    try:
        return date.fromisoformat(t)
    except ValueError:
        pass
    dias = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")
    for n, dia in enumerate(dias):
        if t in {dia, f"el {dia}", f"para el {dia}", f"para {dia}"}:
            return hoy + timedelta(days=(n - hoy.weekday()) % 7)
    return None


def cantidad_verificada(cantidad, evidencia, texto):
    if cantidad is None or not consta(evidencia, texto):
        return False
    palabras = {"un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
                "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10}
    e = normalizar(evidencia)
    # La evidencia de cantidad es SOLO el número del cliente, nunca una frase con otros números.
    return (e.isdigit() and int(e) == cantidad) or palabras.get(e) == cantidad
