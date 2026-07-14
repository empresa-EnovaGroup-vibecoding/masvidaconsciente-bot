"""LA HOJA DE HECHOS — lo único VERDADERO que la Voz puede decir.

El agente se parte en dos (fase 5):

  · **OPERADOR** — tiene las 12 herramientas. Busca, registra, cobra. NO le escribe al cliente.
  · **VOZ** — recibe esta hoja y el historial. Escribe el mensaje. NO tiene herramientas, NO ve
    el catálogo, NO ve los datos bancarios.

🔴 LA HOJA NO LA ESCRIBE EL MODELO. LA ESCRIBE EL CÓDIGO.

Y esa es LA decisión de todo el diseño. Si el Operador emitiera la hoja como un JSON —una tool
tipo `entregar_hechos({...})`— podría **mentir dentro de la hoja**, y habríamos movido la mentira
una capa más abajo, con una capa más de prompt pidiéndole que no mienta. Es exactamente el error
que este repo ya documentó tres veces: *"lo que vive en el texto se rompe; el prompt sugiere, el
código impide"*.

Aquí el CÓDIGO anota lo que las herramientas DEVOLVIERON. El Operador solo aporta el `encargo`
(qué decirle al cliente), y ese encargo se VALIDA contra los hechos duros antes de pasarlo.

═══ LA HOJA **ES** LA LISTA BLANCA DEL DINERO ═══

Hoy la red del dinero se alimenta así (agent.py):

    usd_ok, bs_ok = autorizados_por_moneda(estable, dinamico, mensaje_usuario)
                                           ↑ el PROMPT ENTERO: catálogo, zonas, conocimiento…

Esa línea es la razón por la que el bot le pudo decir **"$23"** a una clienta real: el 23 era
**el `id_para_pedir` de una variante**, y estaba en el prompt. La red se endureció para exigir
marca de dinero, pero la lista blanca **sigue siendo todos los precios del catálogo**.

Con la hoja:

    usd_ok, bs_ok, totales, datos = hoja.listas_blancas()
                                    ↑ SOLO lo que las herramientas devolvieron EN ESTE TURNO

La lista blanca colapsa de *"todo número con marca de dinero en 16.400 tokens"* a *"los 2-5 montos
que devolvieron las tools"*. **El bug del `id_para_pedir` se vuelve imposible por construcción.**

═══ EL VOCABULARIO ES CERRADO ═══

Lo que entra en la hoja, la Voz lo repite. Por eso el render tiene PROHIBIDO usar `herramienta`,
`sistema`, `base de datos`, `registrar_pedido`, `id_para_pedir`, `variante_id`, `null` o
`"ok": false` — y `_suena_a_sistema` (la red de la voz) es su test: si dispara, es que la hoja
está mal escrita.

Corolario: **el JSON crudo de las tools NO se vuelca aquí.** Sus campos `nota` son instrucciones
PARA EL OPERADOR y están llenos de vocabulario de sistema. Lo que sí entra son los strings que el
código YA construye para que un humano los lea: `resumen` y `resumen_cobro`.
"""
from dataclasses import dataclass, field


def _fmt(x) -> str:
    """Un número como lo escribiría una persona: '$14' o '$14.50'."""
    f = float(x)
    return f"{f:g}"


@dataclass
class HojaDeHechos:
    """Lo que ES VERDAD en este turno. Todo lo demás, la Voz no lo puede decir."""

    # ── HECHOS DUROS — los escribe el CÓDIGO, desde lo que devolvieron las herramientas ──
    montos_usd: set[float] = field(default_factory=set)
    montos_bs: set[float] = field(default_factory=set)
    # Los TOTALES solo los pone `registrar_pedido` / `generar_datos_pago`. El catálogo autoriza
    # precios SUELTOS, no sumas: sin esta distinción, "$20 + $5 = $25" se colaba porque $25 es
    # el precio del Pan Keto y la red lo daba por bueno.
    totales_usd: set[float] = field(default_factory=set)
    datos_ok: set[str] = field(default_factory=set)   # cédulas, cuentas, correos que dio una tool
    bloques: list[str] = field(default_factory=list)  # textos YA ARMADOS, legibles por un humano

    pedido_id: int | None = None
    fotos_enviadas: int = 0
    catalogo_enviado: bool = False
    escalado: bool = False          # ¿se llamó a pedir_ayuda en este turno?
    pago_registrado: bool = False   # ¿registrar_comprobante dio ok? → autoriza "recibí tu pago"

    # ── EL ENCARGO — lo escribe el OPERADOR, y se VALIDA contra los hechos de arriba ──
    encargo: str = ""

    # ── Anotación ────────────────────────────────────────────────────────────────────

    def anotar_tool(self, nombre: str, resultado) -> None:
        """Lo que devolvió una herramienta pasa a ser VERDAD. Centraliza lo que hoy está
        desparramado por el bucle de `responder`: es el MISMO código, movido de sitio."""
        import json

        from app.agent.agent import _datos_sensibles, autorizados_por_moneda

        if not isinstance(resultado, dict):
            return

        crudo = json.dumps(resultado, ensure_ascii=False)
        u, b = autorizados_por_moneda(crudo)
        self.montos_usd |= u
        self.montos_bs |= b
        self.datos_ok |= _datos_sensibles(crudo)

        # Flags (los mismos que hoy desarman las redes en el bucle).
        if nombre == "registrar_pedido" and resultado.get("ok"):
            self.pedido_id = resultado.get("pedido_id") or self.pedido_id
            self.totales_usd |= u   # SOLO aquí y en generar_datos_pago nace un TOTAL
        if nombre == "generar_datos_pago" and not resultado.get("error"):
            self.totales_usd |= u
        if nombre == "registrar_comprobante" and resultado.get("ok"):
            self.pago_registrado = True
        if nombre == "enviar_catalogo" and resultado.get("ok"):
            self.catalogo_enviado = True
        if nombre == "enviar_fotos_producto":
            self.fotos_enviadas += int(resultado.get("enviadas") or 0)
        if nombre == "pedir_ayuda":
            self.escalado = True

        bloque = _renderizar(nombre, resultado)
        if bloque:
            self.bloques.append(bloque)

    # ── Lectura ──────────────────────────────────────────────────────────────────────

    def listas_blancas(self) -> tuple[set[float], set[float], set[float], set[str]]:
        """(dólares, bolívares, TOTALES en dólares, datos sensibles) — lo ÚNICO decible."""
        return self.montos_usd, self.montos_bs, self.totales_usd, self.datos_ok

    def render(self, pregunta: str) -> str:
        """El briefing que ve la Voz. En UN solo turno `user` — igual que `redactar_mensaje`
        (Anthropic exige alternancia y el historial ya termina en `assistant`)."""
        partes = [
            "[SISTEMA — instrucción interna, NO es un mensaje del cliente]",
            "",
            "EL CLIENTE ESCRIBIÓ:",
            f"«{pregunta.strip()}»",
            "",
        ]
        if self.bloques:
            partes += [
                "LO QUE ES VERDAD (no puedes decir NINGUNA cifra ni dato que no esté aquí):",
                *[f"· {b}" for b in self.bloques],
                "",
            ]
        else:
            partes += [
                "NO consultaste nada: no tienes ningún dato nuevo que darle. Responde solo con lo "
                "que ya sabes de la conversación, sin inventar precios, productos ni datos.",
                "",
            ]
        partes += [
            "LO QUE HAY QUE DECIRLE:",
            self.encargo.strip() or "Responde con naturalidad a lo que preguntó.",
            "",
            "Escribe SOLO el mensaje de WhatsApp para el cliente, en tu voz de siempre. Sin "
            "comillas, sin explicar nada, sin mencionar este aviso.",
        ]
        return "\n".join(partes)


# ── LOS RENDERIZADORES: de lo que devuelve una tool a algo que un humano puede leer ─────
#
# 🔴 El campo `nota` de las tools NO ENTRA NUNCA. Son instrucciones para el OPERADOR ("NO sueltes
# un folleto", "usa el id_para_pedir de ESE tamaño") y están llenas de vocabulario de sistema. Si
# se colaran, la Voz las repetiría y `_suena_a_sistema` dispararía — con razón.

def _renderizar(nombre: str, r: dict) -> str:
    if r.get("error"):
        return ""  # un error de herramienta no es un HECHO que contarle al cliente

    if nombre == "ver_catalogo":
        prods = r.get("productos") or []
        if not prods:
            return ""
        if len(prods) == 1:
            p = prods[0]
            de = f" — {p['de_que_es']}" if p.get("de_que_es") else ""
            precio = p.get("precio_texto")
            pr = f" ({precio})" if precio else ""
            return f"Tienes {p['nombre']}{de}{pr}"
        nombres = ", ".join(p["nombre"] for p in prods[:8])
        cola = "…" if len(prods) > 8 else ""
        return f"Productos que tienes y calzan con lo que pidió: {nombres}{cola}"

    if nombre == "info_producto":
        p = r.get("producto") or r
        trozos = [str(p.get("nombre") or "")]
        for campo, etiqueta in (
            ("de_que_es", ""), ("descripcion", ""), ("precio_texto", "precio"),
            ("duracion", "dura"), ("se_congela", "se congela"),
            ("apto_diabeticos", "apto para diabéticos"),
        ):
            v = p.get(campo)
            if v:
                trozos.append(f"{etiqueta}: {v}" if etiqueta else str(v))
        return " · ".join(t for t in trozos if t)

    if nombre == "registrar_pedido":
        # `resumen` ya viene ARMADO para que un humano lo lea (líneas, total, fecha en palabras).
        return f"Su pedido quedó registrado:\n  {r.get('resumen', '').strip()}" if r.get("resumen") else ""

    if nombre == "generar_datos_pago":
        # `resumen_cobro` trae los bolívares con la tasa del día, ya calculados por el código.
        partes = []
        if r.get("resumen_cobro"):
            partes.append(f"Para cobrarle: {r['resumen_cobro'].strip()}")
        metodos = r.get("metodos_de_pago")
        if metodos:
            partes.append(
                "Datos de pago que SÍ le puedes dar (cópialos TAL CUAL, y SOLO los del método "
                f"que él elija): {metodos}"
            )
        return "\n  ".join(partes)

    if nombre == "registrar_comprobante":
        return "Recibiste su comprobante. La dueña lo revisa en su banco." if r.get("ok") else ""

    if nombre == "enviar_fotos_producto":
        n = int(r.get("enviadas") or 0)
        if n:
            return f"Le ENVIASTE {n} foto(s)/video(s) por WhatsApp: ya las tiene."
        return "NO se pudo enviar ninguna foto: NO le digas que se las mandaste."

    if nombre == "enviar_catalogo":
        return "Le ENVIASTE el catálogo en PDF por WhatsApp: ya lo tiene." if r.get("ok") else ""

    if nombre == "pedir_ayuda":
        return "Avisaste a la dueña: ella entra al chat enseguida. Díselo con tus palabras."

    if nombre == "buscar_info":
        temas = r.get("resultados") or r.get("temas") or []
        if isinstance(temas, list) and temas:
            return "Lo que sabes de eso: " + " · ".join(
                str(t.get("contenido") or t.get("titulo") or t) for t in temas[:3]
            )
        return ""

    if nombre == "info_negocio":
        return " · ".join(f"{k}: {v}" for k, v in r.items() if v and k != "nota")

    if nombre == "ver_pedidos_cliente":
        pedidos = r.get("pedidos") or []
        return f"Sus pedidos anteriores: {len(pedidos)}" if pedidos else ""

    if nombre == "recordar_cliente":
        return ""  # guardar un dato no es un hecho que contarle al cliente

    return ""
