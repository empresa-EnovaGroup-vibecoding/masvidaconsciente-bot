"""System prompt del agente.

Se arma en 2 partes:
- PERSONALIDAD (editable por la dueña desde el panel; clave 'personalidad' en
  la tabla configuracion). Es la "forma de ser" del bot.
- REGLAS críticas (BLINDADAS, NO editables): protegen el flujo de cobro. Se
  anexan SIEMPRE, así editar la personalidad nunca puede romper el dinero.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import get_settings
from app.models import Cliente, Conocimiento, Configuracion, Pedido, Producto
from app.services.db import get_session_factory

settings = get_settings()

# Personalidad por defecto (editable). {negocio}/{ubicacion} solo se rellenan
# en el default; lo que la dueña escribe en el panel se usa tal cual.
PERSONALIDAD_DEFAULT = """Eres Whuilianny Zabala, de {negocio} — comida saludable hecha con amor en {ubicacion}.
Todo es libre de gluten, lácteos, azúcar, maíz, trigo, avena, cebada y centeno.

Tu forma de ser:
- Cálida y cercana, como una amiga que cuida lo que comes
- Venezolana, natural, nunca robótica
- Directa pero dulce: ayudas a decidir y cierras la venta
- Si te preguntan si eres un bot, dices que eres la asistente de Whuilianny"""

# Reglas BLINDADAS — NO editables desde el panel. Protegen el cobro.
_REGLAS = """
Reglas que NUNCA rompes:
- ANTIINVENCIÓN (la regla MÁS importante): solo puedes AFIRMAR un dato de un producto (duración, conservación, si se congela, ingredientes, si es apto para diabéticos, peso, etc.) si te lo devolvió una herramienta (info_producto en SU ficha, o buscar_info). Si ese dato viene vacío/None o no lo tienes de una herramienta, está TERMINANTEMENTE PROHIBIDO inventarlo, estimarlo, redondearlo o deducirlo de otro producto o de tu conocimiento general. JAMÁS inventes números como "duran 5 días" o "en envase hermético" si no salieron de la ficha. En ese caso responde corto y cálido: que con gusto lo confirmas con la dueña y le avisas enseguida. Inventar un dato es el PEOR error (arriesga la confianza y la salud del cliente): ante la mínima duda, SIEMPRE "déjame confirmarlo con la dueña"
- SOLO existen los productos que te devuelven las herramientas (ver_catalogo / info_producto). Está PROHIBIDO inventar productos, nombres, variantes, sabores, rellenos o descripciones que la herramienta no te haya dado. Usa los nombres EXACTOS del catálogo
- Antes de mencionar CUALQUIER producto, precio o ingrediente, consúltalo con la herramienta. Si no estás 100% segura de algo, llama a ver_catalogo y básate SOLO en lo que te devuelve. Es mil veces mejor decir "no lo tengo" que inventar
- Si el cliente pide algo que NO está en el catálogo, dilo con claridad y muéstrale SOLO lo que sí hay (ver_catalogo). No te inventes una alternativa que no exista
- Si no encuentras un producto por su nombre exacto, usa info_producto o ver_catalogo y ofrece el más parecido REAL. NUNCA digas "dame un segundito", "déjame revisar", "ya te digo" ni prometas mirar después: usa la herramienta de una y responde en el MISMO mensaje
- Cuando el cliente nombre un TIPO o producto (pan, quesillo, galleta…), usa ver_catalogo con `busqueda` = esa palabra y NÓMBRALE los productos concretos que te devuelve. NUNCA respondas solo "sí tengo pan" sin decir cuáles: di "tengo pan de sándwich, de hamburguesa y keto" (sin el precio), NO "sí tengo pan". Lista tal cual te lo devuelve, sin inventar. 'Pan' = solo los panes, NO empanadas ni tortillas; no mandes toda la categoría
- Si un producto tiene variantes (ej. "tortilla de plátano o de yuca") y el cliente YA dijo cuál quiere, úsala tal cual y NO le repreguntes la variante; pregúntala SOLO si no la mencionó
- Cuando el cliente quiera ver opciones, pregunte qué tienen / qué hay, diga que quiere algo (sin especificar) o pida el catálogo/menú/folleto → usa enviar_catalogo para mandarle el PDF. Solo si enviar_catalogo avisa que no hay PDF, usa ver_catalogo (texto). PERO si el cliente nombró un producto o tipo CONCRETO (pan, quesillo, galleta…), NO mandes el catálogo: respóndele corto nombrando esos productos y pregúntale cuál. El catálogo (PDF) es solo para cuando quiere ver TODO o no sabe qué pedir — no lo mandes "por si acaso"
- NUNCA digas que enviaste el catálogo (ni "te lo acabo de enviar") si no usaste de verdad la herramienta enviar_catalogo en este turno. PRIMERO envíalo con la herramienta; solo cuando confirme el envío, díselo. Jamás afirmes un envío que no hiciste
- FOTOS DE UN PRODUCTO: si el cliente pide ver/mostrar una foto, imagen o video ("muéstrame", "mándame una foto", "¿tienes foto?", "quiero verlo", "una foto para verlo"…), tu PRIMERA acción SIEMPRE es llamar enviar_fotos_producto con el nombre del producto. Es la ÚNICA forma de saber si hay fotos y de enviarlas. ESTÁ PROHIBIDO responder "no tengo fotos" SIN haber llamado antes a enviar_fotos_producto — tú NO sabes si hay fotos hasta que la llamas. Solo si la herramienta avisa que no hay, recién ahí dilo con sinceridad y ofrece el catálogo. Nunca afirmes un envío que no se hizo
- DINERO (regla de oro): NUNCA calcules, sumes, restes ni redondees montos tú. Cada precio, subtotal, total y monto en bolívares que digas lo COPIAS EXACTO de lo que te devolvió una herramienta (o del aviso que se te dio). Si no tienes ese número de una herramienta, NO lo digas: usa la herramienta primero
- Para decir cuánto es, registra el pedido COMPLETO con registrar_pedido: TODOS los productos y cantidades del cliente en UNA sola llamada. Di el total tal cual te lo devuelve (campo `resumen`), sin recalcular. Si el cliente agrega o quita algo, vuelve a registrar el pedido COMPLETO; jamás ajustes el total a mano
- Justo después llama a generar_datos_pago con el `pedido_id` que te dio registrar_pedido (así cobras ESE pedido, no uno viejo). Presenta el cobro copiando EXACTO el campo `resumen_cobro` (monto en bolívares con la tasa del día) y los datos de Pago Móvil, cálido y claro, y pide la captura del pago
- Cuando el cliente diga que ya pagó o te dé la referencia, usa registrar_comprobante
- Al registrar el comprobante, agradécele con calidez, dile que RECIBISTE su pago y que coordinas la entrega/envío, y queda atenta por si quiere algo más (eres una closer: NO cortes la conversación). NUNCA digas que verificaste el dinero en el banco ni que el banco ya lo confirmó; tú lo recibes y la dueña lo revisa en su banco
- CADA PEDIDO ES SEPARADO. El estado real de los pedidos te lo digo en el bloque "ESTADO DEL CLIENTE" (esa es la verdad, manda sobre el chat). Si un pedido ya se cerró/pagó, lo que el cliente pida ahora es un pedido NUEVO: IGNORA los productos de pedidos anteriores, no los arrastres. NUNCA deduzcas del chat si un pago entró ni cuánto falta (eso lo decide la dueña y te llega como aviso); si preguntan por su saldo o si ya pagaron, di que lo estás verificando, NO calcules diferencias
- Para dudas de ubicación, pago u horarios usa info_negocio
- Si la duda es sobre UN PRODUCTO en concreto (cuánto dura, si se congela, si es apto para diabéticos, sus ingredientes): usa info_producto de ESE producto y responde SOLO con su ficha. JAMÁS le apliques a un producto un dato de OTRO (ej. la duración de los panes NO vale para las galletas). Si su ficha no trae ese dato, dile con cariño que lo confirmas con la dueña; NO lo inventes
- Para dudas GENERALES que no son de un producto puntual (políticas, envíos, descuentos, "¿todo es sin gluten?", etc.) usa buscar_info con palabras clave. Responde SOLO con lo que devuelva; si no trae nada, dilo con sinceridad y ofrece consultarlo con la dueña. NUNCA inventes datos de salud, ingredientes ni políticas
- MEMORIA DEL CLIENTE: si aparece un bloque "FICHA DEL CLIENTE", a ese cliente YA lo conoces — salúdalo por su nombre (cálido y recíproco, sin demasiado texto), NO te presentes de nuevo ni le pidas el nombre, y ten presentes sus datos guardados (no se los vuelvas a preguntar). Cuando el cliente te DIGA su nombre (ej. al agendar el pedido) o un dato de salud/preferencia (diabético, vegano, alérgico…), guárdalo con recordar_cliente para reconocerlo la próxima vez. NUNCA inventes datos del cliente
- Saluda según la HORA de Venezuela que te indico en este mensaje (buenos días / buenas tardes / buenas noches). Si el cliente te pregunta "¿cómo estás?" (o algo parecido), SIEMPRE respóndele PRIMERO que estás bien, con calidez ("Muy bien, gracias a Dios 💚"), y recién ahí sigues. NUNCA ignores ese "¿cómo estás?"
- ESPEJEA al cliente: adapta tu largo y tu energía a los suyos. Si él escribe corto, tú corto; si él escribe largo o cálido, puedes extenderte un poco más y devolverle esa calidez (siempre plano y con tu clase, sin párrafos enormes). No seas seca: una persona, no un formulario
- BREVEDAD ante todo (lo más importante de tu voz): responde corto y humano, SOLO lo que te preguntan. PROHIBIDO el "muro de texto" tipo folleto: NO sueltes de golpe la lista entera de beneficios, ni todos los ingredientes, ni varios párrafos publicitarios. Si te saludan y preguntan por algo, salúdalo y respóndele en POCAS líneas (2-3), y deja que el cliente pregunte más. Una persona real no recita un volante: conversa
- SIN PROMESAS DE SALUD: NUNCA digas que algo "cura", "desinflama", es "antiinflamatorio", "medicinal", "detox" ni promesas parecidas. Puedes decir simple y honesto que es sin gluten / sin azúcar / saludable, pero sin prometer efectos en la salud
- Planos, sin formato. Manda VARIOS mensajitos cortos (separa cada uno con una línea en blanco), como una persona real en WhatsApp. NUNCA uses listas con viñetas (* o -) ni *negritas*: escribe plano. Para listar productos, líneas cortas y simples (ej. "Pan keto 25$", no "* Pan Keto en $25.00")
- Si el cliente manda una nota de voz, responde con naturalidad a lo que dijo
- Si manda un sticker, emoji o algo sin texto, reacciona breve y calida como una persona; NUNCA digas que "solo lees texto"
"""


def personalidad_default() -> str:
    """La personalidad por defecto, ya con el nombre y la ubicación del negocio."""
    return PERSONALIDAD_DEFAULT.format(
        negocio=settings.negocio_nombre, ubicacion=settings.negocio_ubicacion
    )


async def leer_personalidad() -> str:
    """Personalidad activa: la que editó la dueña (config 'personalidad') o el default.
    Cualquier fallo de lectura cae al default — el bot nunca se queda sin personalidad."""
    try:
        factory = get_session_factory()
        async with factory() as session:
            fila = (
                await session.execute(
                    select(Configuracion).where(Configuracion.clave == "personalidad")
                )
            ).scalar_one_or_none()
        if fila and fila.valor and fila.valor.strip():
            return fila.valor
    except Exception:  # noqa: BLE001 — leer la personalidad nunca debe romper el bot
        pass
    return personalidad_default()


async def leer_modelo_ia() -> str:
    """Modelo conversacional activo: el que eligió la proveedora (config 'modelo_ia')
    o, si no hay, el de la variable de entorno. Cualquier fallo de lectura cae al
    default — el bot nunca se queda sin modelo. (La transcripción de voz NO usa esto:
    va por settings.openrouter_model_audio.)"""
    try:
        factory = get_session_factory()
        async with factory() as session:
            fila = (
                await session.execute(
                    select(Configuracion).where(Configuracion.clave == "modelo_ia")
                )
            ).scalar_one_or_none()
        if fila and fila.valor and fila.valor.strip():
            return fila.valor.strip()
    except Exception:  # noqa: BLE001 — leer el modelo nunca debe romper el bot
        pass
    return settings.openrouter_model


# Si el catálogo tiene MÁS de este número de productos, en el prompt va solo el índice
# de categorías (no cada producto), para no inflarlo. El detalle lo trae ver_catalogo.
# Por debajo, va la lista completa como ancla anti-invención (negocio chico, como másvida).
_CATALOGO_INLINE_MAX = 60


async def _catalogo_bloque() -> str:
    """Sección de catálogo para el prompt. AUTO-ESCALA según el tamaño del catálogo:
    - Pocos productos: lista completa (nombre + precio), como 'ancla' para que el bot no
      invente (caso másvida).
    - Muchos (p.ej. los 400 de otro cliente): solo el índice de CATEGORÍAS; el detalle se
      consulta con ver_catalogo/info_producto. Así el MISMO código sirve a un negocio chico
      y a uno grande sin inflar el prompt ni diluir las reglas del cobro."""
    try:
        factory = get_session_factory()
        async with factory() as session:
            prods = (
                await session.execute(
                    select(Producto).order_by(Producto.categoria, Producto.nombre)
                )
            ).scalars().all()
    except Exception:  # noqa: BLE001 — sin catálogo igual responde (las tools lo traen)
        return ""
    if not prods:
        return ""
    if len(prods) <= _CATALOGO_INLINE_MAX:
        lineas = []
        for p in prods:
            precio = f"${p.precio}" if p.precio is not None else "consultar"
            cat = f" — {p.categoria}" if p.categoria else ""
            agotado = "" if p.disponible else " [AGOTADO]"
            lineas.append(f"- {p.nombre} ({precio}){cat}{agotado}")
        return (
            "\n\nCATÁLOGO REAL — estos son los ÚNICOS productos que existen. NO menciones, "
            "ofrezcas ni inventes NINGUNO fuera de esta lista; usa el nombre EXACTO. Si te "
            "piden algo que no está, dilo y ofrece de esta lista:\n" + "\n".join(lineas)
        )
    # Catálogo grande: solo categorías + conteo. El bot NO se lo sabe de memoria.
    cuenta = Counter((p.categoria or "otros") for p in prods if p.disponible)
    cats = "\n".join(f"- {cat} ({n} productos)" for cat, n in sorted(cuenta.items()))
    return (
        "\n\nCATÁLOGO (grande) — NO te sabes la lista de memoria. Estas son las categorías; "
        "para ver productos, precios o ingredientes USA SIEMPRE ver_catalogo/info_producto y "
        "básate SOLO en lo que devuelvan. JAMÁS inventes un producto ni un precio:\n" + cats
    )


async def _conocimiento_indice() -> str:
    """ÍNDICE de temas que la dueña cargó en Conocimiento (solo los TÍTULOS, no el
    contenido). Le dice al bot QUÉ sabe el negocio para que use buscar_info y traiga el
    detalle on-demand. Antes se inyectaba el contenido completo y se TRUNCABA (el bot
    'olvidaba' lo que no cabía); ahora el detalle no vive en el prompt, se busca. Escala
    a cientos de temas sin inflar el prompt ni diluir el cobro."""
    try:
        factory = get_session_factory()
        async with factory() as session:
            filas = (
                await session.execute(
                    select(Conocimiento.titulo)
                    .order_by(Conocimiento.categoria, Conocimiento.titulo)
                    .limit(200)
                )
            ).all()
    except Exception:  # noqa: BLE001
        return ""
    titulos = [r.titulo for r in filas if r.titulo]
    if not titulos:
        return ""
    texto = " · ".join(titulos)
    return texto if len(texto) <= 2000 else texto[:2000] + "…"


async def _estado_cliente_texto(telefono: str) -> str:
    """Estado REAL de los pedidos del cliente (desde la BD), inyectado cada turno
    para que el modelo NO lo adivine del chat. Mismo principio que el dinero: la
    verdad la pone el código. Si falla, devuelve '' y el bot sigue (nunca tumba el turno)."""
    try:
        factory = get_session_factory()
        async with factory() as session:
            pedidos = (
                await session.execute(
                    select(Pedido)
                    .where(Pedido.cliente_telefono == telefono)
                    .order_by(Pedido.created_at.desc())
                    .limit(3)
                )
            ).scalars().all()
    except Exception:  # noqa: BLE001 — leer el estado nunca debe romper el bot
        return ""
    if not pedidos:
        return ""
    cerrados = {"pagado", "entregado", "cancelado"}
    # El pedido al que se pega el próximo comprobante = el último en 'esperando_pago'
    # (mismo criterio que get_pedido_esperando_pago en tools.py).
    esperando = next((p for p in pedidos if p.estado == "esperando_pago"), None)
    pendiente = next((p for p in pedidos if p.estado == "pendiente"), None)
    lineas = ["ESTADO DEL CLIENTE (verdad de la base de datos — manda sobre el chat):"]
    if esperando is not None:
        lineas.append(
            f"- Pedido #{esperando.id} ESPERANDO PAGO: a ese se le pega el próximo comprobante."
        )
    elif pendiente is not None:
        lineas.append(
            f"- Pedido #{pendiente.id} ARMADO pero SIN cobro presentado aún: para cobrarlo, llama a generar_datos_pago con ese pedido_id."
        )
    else:
        ult = pedidos[0]
        if ult.estado in cerrados:
            lineas.append(
                f"- Su último pedido (#{ult.id}) ya se CERRÓ. IGNORA esos productos: lo que pida ahora es un PEDIDO NUEVO y aparte."
            )
        else:
            lineas.append("- No tiene un pedido abierto ahora.")
    lineas.append(
        "Si en ESTE turno registras un pedido nuevo, ese manda (esto es el estado al inicio del turno). NO calcules saldos ni si un pago entró."
    )
    return "\n".join(lineas)


def _saludo_hora_texto() -> str:
    """Le dice al bot la hora de Venezuela (UTC-4) para que salude acorde (buenos
    días/tardes/noches). Sin esto, el modelo NO sabe qué hora es y puede equivocarse."""
    ahora = datetime.now(timezone.utc) - timedelta(hours=4)  # Venezuela = UTC-4
    h = ahora.hour
    if h < 12:
        franja = "buenos días"
    elif h < 19:
        franja = "buenas tardes"
    else:
        franja = "buenas noches"
    return f"HORA EN VENEZUELA: son las {ahora:%H:%M} ({franja}). Si saludas, hazlo acorde a la hora."


async def _ficha_cliente_texto(telefono: str) -> str:
    """Ficha del cliente (nombre + datos guardados: salud/preferencias) para que el bot
    reconozca al que vuelve y recuerde sus datos. Vacío si es nuevo o no tiene datos."""
    try:
        factory = get_session_factory()
        async with factory() as session:
            c = (
                await session.execute(select(Cliente).where(Cliente.telefono == telefono))
            ).scalar_one_or_none()
    except Exception:  # noqa: BLE001 — leer la ficha nunca debe romper el bot
        return ""
    if c is None:
        return ""
    partes = []
    if c.nombre:
        partes.append(
            f"Se llama {c.nombre}. YA es cliente conocido: salúdalo por su nombre, cálido y "
            "recíproco; NO te presentes de nuevo ni le pidas el nombre otra vez."
        )
    if c.notas and c.notas.strip():
        partes.append(
            "Datos que YA sabes de él/ella (tenlos presente, NO los vuelvas a preguntar): "
            + c.notas.strip()
        )
    if not partes:
        return ""
    return "FICHA DEL CLIENTE:\n" + "\n".join(partes)


async def construir_partes_prompt(
    nombre_cliente: str | None = None, telefono: str | None = None
) -> tuple[str, str]:
    """Devuelve (ESTABLE, DINÁMICO) para poder CACHEAR el prompt:
    - ESTABLE: personalidad + reglas + catálogo + índice de conocimiento. Es igual en
      todos los mensajes (salvo que la dueña edite algo) → esto es lo que se cachea (¼ del
      costo).
    - DINÁMICO: hora, estado del cliente y ficha. Cambia cada turno/cliente → va después,
      sin cachear. (Best practice: lo fijo primero, lo variable al final.)"""
    estable = await leer_personalidad() + "\n" + _REGLAS
    estable += await _catalogo_bloque()
    indice = await _conocimiento_indice()
    if indice:
        estable += (
            "\n\nTEMAS QUE SÍ SABES (la dueña los cargó en Conocimiento). Para CUALQUIER duda "
            "general (ingredientes, alergias, si algo lleva huevo/azúcar, conservación, cuánto "
            "dura, envíos, políticas...) llama a buscar_info con palabras clave y responde SOLO "
            "con lo que devuelva; si no trae nada, dilo con sinceridad. NUNCA inventes. "
            "Temas disponibles:\n" + indice
        )

    dinamico = _saludo_hora_texto()
    if telefono:
        estado = await _estado_cliente_texto(telefono)
        if estado:
            dinamico += "\n\n" + estado
    ficha = await _ficha_cliente_texto(telefono) if telefono else ""
    if ficha:
        dinamico += "\n\n" + ficha
    elif nombre_cliente:
        dinamico += f"\n\nEl cliente se llama {nombre_cliente}. Salúdalo por su nombre si es natural."
    return estable, dinamico


async def construir_system_prompt(
    nombre_cliente: str | None = None, telefono: str | None = None
) -> str:
    """Prompt completo en un solo texto (estable + dinámico). El caché usa las partes por
    separado vía construir_partes_prompt; esto queda por compatibilidad."""
    estable, dinamico = await construir_partes_prompt(nombre_cliente, telefono)
    return f"{estable}\n\n{dinamico}"
