"""EL PROMPT COMPACTO — la auditoría de las TRES capas juntas (6-sep, lo exigió Maired).

"¿Por qué rama tras rama para arreglar cada cosa?" — porque cada arreglo miraba UNA capa. Esta vez
se bajó el prompt EXACTO que recibe el modelo en pruebas (personalidad + reglas + catálogo + zonas
+ dinámico + las 14 descripciones de herramientas: 67 KB) y se leyó entero. Los choques que
quedaban, y que este archivo deja clavados:

1. CATÁLOGO regla 5 decía "nómbrale SOLO los TIPOS… sin soltar los rellenos" cuando calzan VARIOS
   productos — 'galletas' calza con Galletas New York y Mini New York, así que la regla le
   ordenaba callar los rellenos. Justo el bug medido cuatro veces. Ahora: las OPCIONES sí se
   dicen (una vez si son las mismas); lo que no se recita es ingredientes y duración.
2. R102 (fotos) pedía un "pitch con el gancho: de qué es, cuánto dura…" → de ahí salía "de harina
   de almendra y coco, sin gluten" sin que nadie lo preguntara. Ahora: una línea con el producto,
   sus opciones y la pregunta.
3. R107 tenía de ejemplo "para cuándo te las preparo?" — una pregunta de FECHA a votación, que R142
   prohíbe. Ahora el ejemplo avanza al paso que sí toca (retiro o delivery).
4. R108 daba un orden de venta sin la referencia ni la franja (038). Ahora el orden es completo.
5. R135 tenía el resumen final (paso 11 de la plantilla de Maired: productos, modalidad, saldo,
   dirección o retiro, fecha/VENTANA de entrega, confirmación antes del despacho) pero sin la
   franja y sin aclarar que lo que se confirma es la ENTREGA, no el pago. Ojo: la primera versión
   de esta auditoría iba a BORRAR ese resumen por el "te cuadra así?" de GLM — y el test viejo
   (`test_el_resumen_final_antes_del_despacho_existe`) la frenó: el resumen es un requisito del
   negocio, no un tic del modelo. Lo que sobraba era reconfirmar el pago, no resumir la entrega.
   (7-sep: Maired lo vio EN VIVO y tachó el resumen final tras el pago; ver test_5.)
6. El schema de `registrar_pedido.entrega` aún decía "la hora la coordina la dueña después".
"""
from app.agent import system_prompt as sp
from app.agent.tools import TOOL_SCHEMAS


def _regla5() -> str:
    import inspect

    return inspect.getsource(sp._catalogo_bloque)


def test_1_las_opciones_se_dicen_aunque_calcen_varios_productos():
    src = _regla5()
    assert "sin soltar los rellenos" not in src, "era la orden de callar los rellenos"
    assert "esas son las OPCIONES (rellenos, sabores) y SÍ se dicen" in src
    # (las frases se cortan por el salto de línea del código fuente: se buscan trozos enteros)
    assert "comparten las mismas opciones, dilas UNA vez" in src
    assert "nómbrale TODOS los que" in src


def test_2_la_foto_va_con_una_linea_no_con_la_ficha():
    r = sp._REGLAS
    assert "pitch CORTO y en tus palabras, con el gancho REAL" not in r
    assert "Acompáñala con UNA línea, en tus palabras: el producto y sus opciones" in r
    assert "De qué está hecho, cuánto dura o si se congela NO van aquí" in r


def test_3_el_ejemplo_de_cierre_no_pone_la_fecha_a_votacion():
    r = sp._REGLAS
    assert "para cuándo te las preparo?" not in r
    assert "las retiras o te las llevo?" in r


def test_4_el_orden_de_la_venta_incluye_referencia_y_franja():
    r = sp._REGLAS
    i = r.index("El orden es: qué producto")
    orden = r[i:i + 400]
    for paso in ("retiro o delivery", "la fecha", "punto de referencia", "cobrar", "la franja"):
        assert paso in orden, paso
    assert orden.index("punto de referencia") < orden.index("cobrar") < orden.index("la franja")


def test_5_despues_del_pago_momento_de_entrega_y_cierre_sin_resumen():
    """(Actualizado 7-sep: Maired tachó el resumen final tras el pago — ver
    test_prompt_sin_contradicciones.test_tras_el_pago_no_hay_resumen_final_y_la_conversacion_muere.)"""
    r = sp._REGLAS
    linea = next(ln for ln in r.split("\n") if "CUANDO EL PAGO YA ESTÁ APROBADO" in ln)
    assert "ofrece los momentos de entrega y guarda el elegido (anotar_entrega)" in linea
    assert "resumen final" not in linea
    assert "NO reconfirmes el pago" in linea and "saldo pendiente" in linea
    assert "NO coordines la franja ni la hora" in r


def test_6_el_schema_de_registrar_pedido_habla_de_franja_no_de_hora():
    reg = next(t for t in TOOL_SCHEMAS if t["function"]["name"] == "registrar_pedido")
    desc = reg["function"]["parameters"]["properties"]["entrega"]["description"]
    assert "la coordina la dueña después" not in desc
    assert "anotar_entrega" in desc and "franja" in desc


def test_ninguna_regla_nueva_trae_signos_de_apertura():
    """R90 prohíbe '¿' y '¡' en lo que escribe el bot; sus propios ejemplos no pueden traerlos."""
    for trozo in (
        "las retiras o te las llevo?",
        "te cuadra así?",
        "cuánto?', 'cuántas trae?', 'se congela?'",
    ):
        assert "¿" not in trozo and "¡" not in trozo
    r = sp._REGLAS
    i = r.index("CUANDO EL PAGO YA ESTÁ APROBADO")
    assert "¿" not in r[i:i + 500]
