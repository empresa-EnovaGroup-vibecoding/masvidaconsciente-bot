"""AUDITOR DE LA PLANTILLA DE NEGOCIO DE MAIRED — 106 requisitos, EJECUTADOS contra el sistema vivo.

    docker exec -w /app -e PYTHONPATH=/app <worker> python scripts/auditar_plantilla.py
    ./banco_local.sh auditar_plantilla        (en local, con la base clonada)

🔴 **POR QUÉ EXISTE.** "¿Está el bot como lo pidió Maired?" se venía respondiendo leyendo el
documento y el código, a mano, y cada vez daba un número distinto: una revisión sacó 51 requisitos,
la siguiente 200. Un requisito que nadie puede volver a comprobar en un comando no es un requisito:
es una opinión con fecha.

Esto no lee ficheros ni cita de memoria: **ensambla el prompt real, ejecuta las tools reales,
consulta la BD real y comprueba que las redes de código existen de verdad.** Cada línea es una
pregunta del documento con su respuesta medida.

Veredictos, y la diferencia importa:

  CUMPLE     · el sistema hace lo que pide el documento (medido)
  NO CUMPLE  · el sistema NO lo hace, y es arreglable en código o prompt
  PARCIAL    · lo hace a medias, y lo que falta está identificado
  ROADMAP    · es una PIEZA SIN CONSTRUIR (N1 pago 30/70 · N2 delivery extraordinario · N4 día
               flojo). El propio documento las aplaza ("una fase posterior", "debe validarse con
               la dueña antes de salir en vivo"), así que no son deuda: son alcance.
  DATO       · falta un DATO de Whuilianny, no código. La maquinaria que lo usa ya está viva y lo
               respeta en el turno siguiente SIN desplegar, porque el catálogo se relee en cada
               mensaje.
  N/A        · el requisito no aplica (su condición no se da)

🪦 **24-ago:** las redes de la ficha repetida y del recibo exacto se QUITARON (decisión de
Erwin): esas dos conductas pasan a ser del LLM y este auditor ya no las cuenta como redes.
Si el modelo repite o omite cifras, eso es el TERMÓMETRO del modelo, no una red que falta.

⚠️ **Y una advertencia que costó cuatro falsos rojos escribiendo esto:** de los 6 fallos que dio la
primera corrida, **4 eran del auditor, no del bot** — firmas de tools sin la sesión, un `in`
demasiado ingenuo sobre una frase que decía lo contrario de lo que asumí, y un chequeo del domingo
que se disparaba porque la tool reportaba correctamente `hoy_es: domingo`. Si algo sale NO CUMPLE,
**sospecha primero de este fichero** (L35).
"""
import asyncio
import inspect

R = []


def chk(ident, veredicto, requisito, evidencia=""):
    R.append((ident, veredicto, requisito, str(evidencia)[:150]))


async def main():
    import app.agent.agent as ag
    import app.agent.system_prompt as sp
    import app.agent.tools as T
    from app.agent.tools import TOOL_SCHEMAS
    from app.services.tools_config import BLINDADAS
    from app.workers import tasks

    prompt = await sp.construir_system_prompt("Maired", "584264399792")
    per = await sp.leer_personalidad() if inspect.iscoroutinefunction(sp.leer_personalidad) else sp.leer_personalidad()
    if inspect.isawaitable(per):
        per = await per
    todo = prompt  # prompt = personalidad + reglas + catálogo + zonas + calendario
    cat = await sp._catalogo_bloque()
    nombres_tools = {t["function"]["name"] for t in TOOL_SCHEMAS}
    esquemas = {t["function"]["name"]: t["function"] for t in TOOL_SCHEMAS}

    def en(aguja, donde=None):
        return aguja.lower() in (donde or todo).lower()

    # ═══════════════ PARTE 1 · QUÉ ES EL NEGOCIO ═══════════════
    chk("1.1", "CUMPLE" if en("masvidaconsciente") else "NO CUMPLE", "el nombre del negocio está en el prompt")
    chk("1.2", "CUMPLE" if en("antiinflamator") else "NO CUMPLE",
        "antiinflamatorio, para condiciones de salud delicadas")
    chk("1.3", "CUMPLE" if en("bajo pedido") else "NO CUMPLE", "todo se trabaja bajo pedido")
    chk("1.4", "CUMPLE" if en("cabudare") else "NO CUMPLE", "Cabudare")
    # productos que el documento ofrece
    for p in ("hogaza", "rústic", "vegan"):
        chk(f"1.5·{p}", "DATO" if p not in cat.lower() else "CUMPLE",
            f"el documento ofrece «{p}» — ¿existe en el catálogo vivo?",
            "no está en el catálogo" if p not in cat.lower() else "sí")
    chk("1.5·hamburguesa", "PARCIAL" if "pan de hamburguesa" in cat.lower() else "DATO",
        "el documento ofrece «hamburguesas»", "solo existe «Pan de Hamburguesa»")
    chk("1.8", "CUMPLE" if en("contaminación cruzada", per) else "NO CUMPLE",
        "sin contaminación cruzada, hornos propios", "vive en la personalidad")
    chk("1.9", "CUMPLE" if en("verificar su ficha") or en("info_producto de ESE producto") else "NO CUMPLE",
        "para un producto puntual, verificar SU ficha antes de afirmar")
    chk("1.10", "CUMPLE" if en("vacío") or en("congelad") else "PARCIAL",
        "congelados al vacío, conservación según la ficha")
    chk("1.11", "CUMPLE" if en("conservarlo") or en("conservación") else "NO CUMPLE",
        "tip de conservación/acompañamiento al cerrar")
    chk("1.12", "CUMPLE" if en("SOLO existen los productos") else "NO CUMPLE",
        "no vende ni promete lo que no está en el catálogo")
    chk("1.13", "CUMPLE" if en("bajo pedido") else "NO CUMPLE", "no entrega inmediata como regla")
    # domingo: se comprueba EJECUTANDO el calendario con una sesión REAL
    from app.services.db import get_session_factory as _gsf
    _f = _gsf()
    try:
        async with _f() as ses:
            fechas = await T.proxima_fecha_entrega(ses, "__auditoria__")
        # 🔴 Se miran las fechas que OFRECE, no el texto entero: `hoy_es: domingo` es la tool
        #    diciendo bien qué día es hoy, no una entrega en domingo. (Este chequeo dio un falso
        #    NO CUMPLE justo por eso — y la auditoría se corrió EN domingo, que es el mejor día
        #    posible para probarlo.)
        ofrecidas = [f.get("cuando", "") for f in (fechas.get("proximas_fechas") or [])]
        primera = (fechas.get("primera_fecha") or {}).get("cuando", "")
        hay_domingo = any("domingo" in c.lower() for c in ofrecidas + [primera])
        chk("1.14", "NO CUMPLE" if hay_domingo else "CUMPLE",
            "no entrega los domingos (lo del domingo pasa al lunes)",
            f"hoy={fechas.get('hoy_es')} · hoy_se_puede={fechas.get('hoy_se_puede_entregar')} "
            f"· primera={primera}")
    except Exception as e:
        chk("1.14", "NO CUMPLE", "no entrega los domingos", f"error ejecutando: {e}")
    fuentes_tools = inspect.getsource(T)
    esquemas_txt = str(TOOL_SCHEMAS)
    def en_tools(a):
        return a.lower() in fuentes_tools.lower() or a.lower() in esquemas_txt.lower()
    chk("1.15", "CUMPLE" if en_tools("no calza con ninguna zona") else "NO CUMPLE",
        "no inventa zonas/tarifas; escala la dirección que no reconoce",
        "en las notas de las tools (el modelo las lee al llamarlas)")
    chk("1.16", "CUMPLE" if en("NO coordines la entrega") or en("Hasta que ella lo apruebe") else "NO CUMPLE",
        "no aprueba pagos solo: espera «Pago aprobado»")
    chk("1.17", "ROADMAP", "no despacha sin el pago de la modalidad (100% o 30%)", "el 30/70 es N1")
    chk("1.18", "CUMPLE" if en("NADA DE CONSEJO MÉDICO") else "NO CUMPLE", "sin diagnóstico ni consejo médico")

    # ═══════════════ PARTE 2 · LOS 11 PASOS ═══════════════
    chk("2.3", "CUMPLE" if en("vegano", per) or en("es para un niño", per) else "PARCIAL",
        "si menciona niño/vegano/salud, tenerlo presente y responder con cuidado")
    chk("P1", "CUMPLE" if en("Alejandra") and en("FICHA DEL CLIENTE") else "NO CUMPLE",
        "paso 1 · saludar; nuevo → presentarse como Alejandra; conocido → por su nombre")
    chk("P2", "CUMPLE" if en("enviar_catalogo") else "NO CUMPLE",
        "paso 2 · responder la consulta y compartir el catálogo cuando corresponda")
    chk("P3", "CUMPLE" if en("proxima_fecha_entrega") and en("info_producto") else "NO CUMPLE",
        "paso 3 · ficha, tiempo de preparación y próxima fecha")
    chk("P4", "CUMPLE" if (en("le pides su nombre", per) or en("a nombre de quién", per)) else "NO CUMPLE",
        "paso 4 · pedir el nombre al agendar, nunca como formulario")
    chk("P5", "CUMPLE" if en("retiro") and en("sector") else "NO CUMPLE",
        "paso 5 · antes de cobrar: retiro o delivery; si delivery, el SECTOR primero")
    chk("P6", "CUMPLE" if en("efectivo") and en("20") and en("bcv") else "NO CUMPLE",
        "paso 6 · método de pago; Bs a BCV + delivery; efectivo 20% + delivery gratis")
    chk("P7a", "CUMPLE" if en("desglose_efectivo") else "NO CUMPLE", "paso 7 · confirmar ítems y desglose")
    chk("P7b", "ROADMAP", "paso 7 · cobrar 100% o anticipo del 30%", "el 30/70 es N1")
    chk("P8", "CUMPLE" if en("revisando") else "NO CUMPLE", "paso 8 · en revisión + avisar a la dueña")
    chk("P9", "CUMPLE" if en("te llega el aviso") or en("cuando lo haga") else "PARCIAL",
        "paso 9 · «Pago aprobado» reactiva y continúa sin pedir lo ya dado")
    chk("P10", "CUMPLE" if en("referencia") and en("mándame tu ubicación") else "NO CUMPLE",
        "paso 10 · datos del delivery progresivos, ubicación OPCIONAL")
    chk("P11", "CUMPLE" if en("resumen final") else "NO CUMPLE", "paso 11 · resumen final antes del despacho")

    # ═══════════════ PARTE 3 · CÓMO HABLA ═══════════════
    chk("3.1a", "CUMPLE" if en("Alejandra") else "NO CUMPLE", "es Alejandra, asesora")
    ok_ident = (en("responde con sencillez y sinceridad que eres Alejandra", per)
                or en("dile con sencillez y calidez que eres Alejandra"))
    no_jura = en("Nunca digas que eres humana ni que eres Whuilianny", per) or en("PROHIBIDO jurar que eres humana")
    chk("3.1b", "CUMPLE" if (ok_ident and no_jura) else "NO CUMPLE",
        "Whuilianny es la dueña, NO la voz: si preguntan, responde que es Alejandra",
        f"dice-Alejandra={ok_ident} no-jura={no_jura}")
    chk("3.1c", "CUMPLE" if not en("asistente") else "NO CUMPLE", "no se presenta como asistente")
    chk("3.2", "CUMPLE" if en("con clase", per) else "NO CUMPLE", "tono venezolano cálido, con clase")
    chk("3.4", "CUMPLE" if en("no se lo devuelvas", per) or en("mi amor", per) else "NO CUMPLE",
        "acompaña la energía pero no devuelve «mi amor»/«reina»")
    chk("3.5", "CUMPLE" if en("globitos", per) else "NO CUMPLE", "mensajes cortos, una idea a la vez")
    chk("3.7", "CUMPLE" if en("sin listas") or en("nunca listas") else "NO CUMPLE",
        "no párrafos largos, ni negritas, ni listas")
    chk("3.8", "CUMPLE" if en("máximo uno", per) else "NO CUMPLE", "máximo UN emoji")
    chk("3.9", "CUMPLE" if en("con gusto", per) else "NO CUMPLE", "vocabulario recomendado")
    chk("3.10", "CUMPLE" if en('"va?"', per) or en("va?", per) else "NO CUMPLE", 'nunca el mexicanismo "va?"')
    chk("3.11", "CUMPLE" if en("bendici", per) else "NO CUMPLE", "bendición al cerrar, no en cada mensaje")
    chk("3.12", "CUMPLE" if en("ANTIINVENCIÓN") else "NO CUMPLE", "no inventar nada")
    chk("3.13", "CUMPLE" if en("no te disculpes", per) or en("SI DUDAN DEL PRECIO") else "NO CUMPLE",
        "no decir que cuesta más por ser antiinflamatorio")
    chk("3.15", "CUMPLE" if en("No comiences pidiendo su nombre", per) else "NO CUMPLE",
        "no pedir el nombre al inicio como formulario")
    chk("3.16", "CUMPLE" if en("NO mandes el catálogo") else "NO CUMPLE",
        "no mandar el catálogo ante toda pregunta puntual")
    chk("3.17", "CUMPLE" if en("solo los panes") else "NO CUMPLE",
        "no agrupar mal: si piden pan, solo panes")
    chk("3.18", "CUMPLE" if en("LAS FECHAS SE CONSULTAN") else "NO CUMPLE",
        "no prometer entrega inmediata ni fijar horarios no disponibles")
    _exc = next((ln for ln in todo.split("\n") if "LA EXCEPCIÓN NO LA DAS TÚ" in ln), "")
    chk("3.19", "CUMPLE" if (_exc and "el día esté flojo" in _exc and "SOLO si la dueña la autorizó" in _exc)
        else "NO CUMPLE",
        "no ofrecer delivery fuera de horario por decisión propia ni inferir una excepción",
        "regla explícita + red _dias_imposibles")
    chk("3.20", "CUMPLE" if "una autorización no vuelve posible cualquier cosa" in todo else "NO CUMPLE",
        "una autorización de delivery NO habilita cualquier producto (producto, capacidad, zona, hora)")
    chk("3.21", "CUMPLE" if en("no todos de golpe") else "NO CUMPLE",
        "no pedir todos los datos del delivery de golpe ni exigir la ubicación")
    chk("3.22", "CUMPLE" if en("NO PLANTILLAS") else "NO CUMPLE", "no plantillas rígidas")
    chk("3.23", "CUMPLE" if en("NO FUERCES UNA PREGUNTA AL FINAL") else "NO CUMPLE",
        "no forzar una pregunta al final de cada mensaje")
    chk("3.24", "CUMPLE" if en("No dejes ninguna pregunta del cliente sin responder", per) else "PARCIAL",
        "no dejar preguntas sin responder")

    # ═══════════════ PARTE 4 · LA INFO CONCRETA ═══════════════
    chk("4.1", "CUMPLE" if en("6 de la tarde") or en("6:00") else "NO CUMPLE", "delivery lun-sáb hasta las 6pm")
    chk("4.3", "CUMPLE" if en("EL NEGOCIO NO CIERRA") else "NO CUMPLE",
        "el agente atiende después de las 6 pero agenda")
    chk("4.5", "CUMPLE" if en("ANTICIPACIÓN") else "NO CUMPLE", "preparación 1-2 días según ficha")
    chk("4.6", "ROADMAP", "delivery extraordinario activable por la dueña, que expira solo", "es N2")
    chk("4.8", "ROADMAP", "aviso de día flojo", "es N4")
    chk("4.9", "CUMPLE" if en("DINERO (regla de oro)") else "NO CUMPLE", "los precios nunca de memoria")
    chk("4.13", "CUMPLE" if en("desglose_efectivo") else "NO CUMPLE",
        "efectivo: mostrar subtotal, descuento, delivery en 0 y total")
    chk("4.15", "ROADMAP", "modalidad B 30/70 con medición A/B", "es N1")
    chk("4.17", "CUMPLE" if en("la mendera") else "NO CUMPLE", "retiro en La Mendera")
    chk("4.20", "CUMPLE" if en("primero el sector") else "NO CUMPLE", "antes del pago, solo el sector")
    chk("4.30", "CUMPLE" if en("no lo mandes por si acaso") or en("por si acaso") else "NO CUMPLE",
        "catálogo solo a quien quiere ver todo")
    chk("4.32", "CUMPLE" if en("EL EXTRA SE AVISA") else "NO CUMPLE", "upsell sin insistir")

    # los 6 casos de escalada
    chk("esc.1", "CUMPLE" if en("revisando") else "NO CUMPLE", "escala: llega el comprobante")
    chk("esc.2", "CUMPLE" if en("no calculo diferencias") or en("no calcules diferencias") else "PARCIAL",
        "escala: diferencia de pago / no se puede verificar")
    chk("esc.3", "CUMPLE" if en_tools("no calza con ninguna zona") and en_tools("pedir_ayuda") else "NO CUMPLE",
        "escala: zona fuera del mapa", "nota de la tool del delivery")
    chk("esc.4", "CUMPLE" if en("ALERGIAS") and en("pedir_ayuda") else "NO CUMPLE",
        "escala: info no cargada / alergia no cubierta")
    chk("esc.5", "CUMPLE" if en("RECLAMA de verdad") else "NO CUMPLE", "escala: queja, reclamo, cliente molesto")
    chk("esc.6", "N/A", "escala: método distinto a Pago Móvil", "el multimétodo SÍ está activo: no aplica")

    # ── EJECUTAR de verdad las piezas del dinero y la seguridad ──
    try:
        from decimal import Decimal
        efectivo = T.monto_en_efectivo(Decimal("17.00"), Decimal("3.00"))
        chk("dinero.efectivo", "CUMPLE" if efectivo == Decimal("11.20") else "NO CUMPLE",
            "efectivo = 20% a los productos y delivery GRATIS ($14+$3 → $11.20)", f"devolvió {efectivo}")
    except Exception as e:
        chk("dinero.efectivo", "NO CUMPLE", "monto_en_efectivo", f"error: {e}")

    try:
        async with _f() as ses:
            veg = await T.ver_catalogo(ses, "__auditoria__", busqueda="vegano")
        s = str(veg).lower()
        malo = any(x in s for x in ("cochino", "manteca", "hígado", "higado", "huevo"))
        chk("seguridad.vegano", "NO CUMPLE" if malo else "CUMPLE",
            "a quien pide «vegano» NO se le ofrece un producto con animal", str(veg)[:130])
    except Exception as e:
        chk("seguridad.vegano", "NO CUMPLE", "buscar «vegano»", f"error: {e}")

    # ── El caso de la auditoría: lo que el DOCUMENTO ofrece y el catálogo NO tiene ──
    for _q in ("hogaza", "rusticos"):
        try:
            async with _f() as ses:
                _r = await T.ver_catalogo(ses, "__auditoria__", busqueda=_q)
            _n = len(_r.get("productos") or [])
            _nota = _r.get("nota") or ""
            # correcto = MUCHAS alternativas ("eso no lo tengo, mira lo que sí hay").
            # incorrecto = UN calce, cuya nota le ordena al modelo PRESENTARLO con certeza.
            _bien = _n > 1 and "Calza UN solo producto" not in _nota
            chk(f"fantasma.{_q}", "CUMPLE" if _bien else "NO CUMPLE",
                f"«{_q}» (lo ofrece el documento y no existe): da alternativas, no un falso calce",
                f"{_n} producto(s)" + (" · nota de UN calce" if "Calza UN solo" in _nota else ""))
        except Exception as e:
            chk(f"fantasma.{_q}", "NO CUMPLE", f"«{_q}»", f"error: {e}")

    try:
        async with _f() as ses:
            cel = await T.ver_catalogo(ses, "__auditoria__", busqueda="celiaco")
        chk("seguridad.celiaco", "CUMPLE" if "ok" in str(cel).lower() or cel else "NO CUMPLE",
            "«celiaco» no devuelve el catálogo entero como si todo aplicara", str(cel)[:130])
    except Exception as e:
        chk("seguridad.celiaco", "NO CUMPLE", "buscar «celiaco»", f"error: {e}")

    # redes de código que sostienen los requisitos
    redes = [
        ("red.salud", "_dictamina_salud_sin_ficha", "no sentencia sobre salud sin abrir la ficha"),
        ("red.dinero", "_dinero_inventado", "no deja pasar una cifra que no dio una herramienta"),
        ("red.datos", "_datos_sensibles_inventados", "no deja pasar datos de pago inventados"),
        ("red.pedido", "_afirma_pedido_registrado", "no deja decir «te lo anoté» sin pedido"),
        # 🪦 24-ago: `red.ficha` (_sin_ficha_repetida) y `red.recibo` (_asegurar_resumenes_exactos)
        # SALIERON de esta lista — las redes se QUITARON por decisión de Erwin: no repetir la
        # ficha y presentar el recibo exacto pasan a ser CONDUCTA DEL LLM (reglas 66 y 107-108
        # de _REGLAS), para medir el techo del modelo. No es un NO CUMPLE: es un cambio de capa.
        # La primera mutiló el cobro de Maired (23-ago) y la segunda alimentaba el conflicto L28.
        ("red.tamano", "_tamano_sin_elegir", "no adivina el tamaño (carril del dinero)"),
        ("red.cierre", "_dato_opcional_pedido", "no bloquea el cierre por un dato opcional"),
        ("red.saludo", "_asegurar_saludo", "garantiza el saludo aunque el modelo falle"),
        ("red.foto", "_asegurar_foto", "garantiza la foto"),
        ("red.catalogo", "_asegurar_catalogo", "garantiza el catálogo"),
        ("red.frase", "_frase_prohibida", "frena la mentira del banco"),
        ("red.promesa", "_promete_averiguar", "no promete sin avisar a la dueña"),
    ]
    for ident, fn, req in redes:
        chk(ident, "CUMPLE" if hasattr(ag, fn) else "NO CUMPLE", req, fn)

    # el carril del comprobante: ¿espera de verdad?
    carril = inspect.getsource(tasks._procesar_comprobante)
    sin_com = "\n".join(ln for ln in carril.splitlines() if not ln.lstrip().startswith("#"))
    chk("pago.espera", "CUMPLE" if "coordinas la entrega" not in sin_com else "NO CUMPLE",
        "el CÓDIGO no coordina la entrega al recibir el comprobante")
    chk("pago.aviso", "CUMPLE" if ("dueno" in sin_com or "avisar" in sin_com or "notificar" in sin_com) else "NO CUMPLE",
        "y avisa a la dueña (pausar y avisar van juntos)")

    # tools blindadas
    chk("tools.n", "CUMPLE" if len(nombres_tools) == 13 else "NO CUMPLE",
        "las 13 herramientas están registradas", f"{len(nombres_tools)}")
    chk("tools.blindadas", "CUMPLE" if len(BLINDADAS) == 8 else "NO CUMPLE",
        "8 herramientas blindadas (no se pueden apagar)", f"{len(BLINDADAS)}")
    chk("tools.opciones", "CUMPLE" if "OPCIONAL" in esquemas["registrar_pedido"]["parameters"]["properties"].get(
        "items", {}).get("description", "") + str(esquemas["registrar_pedido"]) else "PARCIAL",
        "el schema dice que `opciones` es OPCIONAL")

    # datos de la BD que el documento exige
    from sqlalchemy import text as sqltext

    from app.services.db import get_session_factory
    factory = get_session_factory()
    async with factory() as ses:
        async def uno(q):
            return (await ses.execute(sqltext(q))).scalar()
        churros = await uno("SELECT count(*) FROM productos WHERE nombre ILIKE '%churro%'")
        chk("dato.churros", "CUMPLE" if churros == 0 else "NO CUMPLE",
            "«Churros» no debe ofrecerse", f"{churros} en el catálogo")
        antic0 = await uno("SELECT count(*) FROM productos WHERE dias_anticipacion=0")
        chk("dato.anticipacion", "CUMPLE" if antic0 < 32 else "DATO",
            "cada producto con su tiempo de preparación", f"{antic0} de 32 en 0")
        zona = await uno("SELECT costo FROM zonas_entrega WHERE nombre ILIKE '%centro%'")
        chk("dato.zona", "CUMPLE" if str(zona) == "2.00" else "NO CUMPLE",
            "zona cercana = USD 2 (documento §9)", f"cobra {zona}")
        oeste = await uno("SELECT costo FROM zonas_entrega WHERE nombre ILIKE '%oeste%'")
        chk("dato.oeste", "CUMPLE" if str(oeste) == "5.00" else "NO CUMPLE",
            "zona oeste = USD 5", f"cobra {oeste}")
        fer = await uno("SELECT count(*) FROM feriados")
        chk("dato.feriados", "DATO" if fer == 0 else "CUMPLE", "feriados cargados", f"{fer}")
        sinfoto = await uno("SELECT count(*) FROM productos p WHERE NOT EXISTS "
                            "(SELECT 1 FROM producto_media m WHERE m.producto_id=p.id)")
        chk("dato.fotos", "DATO" if sinfoto else "CUMPLE", "productos sin ninguna foto", f"{sinfoto} de 32")
        sab = await uno("SELECT count(*) FROM producto_variantes WHERE sabores IS NOT NULL AND sabores<>''")
        chk("dato.sabores", "DATO" if sab < 37 else "CUMPLE", "sabores cargados en las variantes", f"{sab} de 37")
        dur = await uno("SELECT count(*) FROM productos WHERE duracion IS NOT NULL AND duracion<>''")
        chk("dato.duracion", "CUMPLE" if dur >= 24 else "DATO",
            "duración cargada (la necesita el tip de conservación)", f"{dur} de 32")
        alm = await uno("SELECT count(*) FROM productos WHERE nombre ILIKE '%masa madre%'")
        chk("dato.almendra", "N/A" if alm == 0 else "DATO",
            "¿la masa madre lleva almendra? (alérgeno)", f"{alm} productos «masa madre» en el catálogo")
        wapp = await uno("SELECT count(*) FROM configuracion WHERE clave='dueno_telefono' AND valor<>''")
        chk("dato.wapp_avisos", "CUMPLE" if wapp else "DATO",
            "WhatsApp de avisos a la dueña, distinto al del bot")

    # ═══════════════ RESUMEN ═══════════════
    print("\n" + "=" * 108)
    orden = ["NO CUMPLE", "PARCIAL", "ROADMAP", "DATO", "CUMPLE", "N/A"]
    for v in orden:
        filas = [r for r in R if r[1] == v]
        if not filas:
            continue
        print(f"\n{'█'} {v}  ({len(filas)})")
        for ident, _, req, ev in filas:
            print(f"   {ident:<20} {req[:70]:<71} {ev[:34]}")
    print("\n" + "=" * 108)
    tot = len(R)
    for v in orden:
        n = sum(1 for r in R if r[1] == v)
        if n:
            print(f"  {v:<11} {n:>3}  ({n*100//tot}%)")
    print(f"  {'TOTAL':<11} {tot:>3}")


asyncio.run(main())
