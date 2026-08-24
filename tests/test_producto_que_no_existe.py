"""UN CALCE ESPURIO ES PEOR QUE NINGUNO — la auditoría forense del 2026-08-23.

**El caso, medido contra la base real.** La plantilla de negocio de Maired ofrece productos que el
catálogo NO tiene: *hogaza*, *rústicos* y *opciones veganas*. O sea que va a haber clientes que los
pidan por su nombre. Lo que contestaba el bot:

    "tienes hogaza?"    → **Arepas Andinas**
    "tienes rústicos?"  → **Yogurt Kéfirado**

Y no era el buscador siendo generoso: era el buscador siendo **falsamente preciso**. Las cifras:

    'hogaza'   similitud con el NOMBRE 0.000 · con la DESCRIPCIÓN 0.429  → Arepas Andinas
    'rusticos' similitud con el NOMBRE 0.000 · con la DESCRIPCIÓN 0.444  → Yogurt Kéfirado

Cero parecido con el nombre de nada. El calce salía de una descripción LARGA de ingredientes
('hogaza'↔'harina', 'rusticos'↔'probióticos'): cuantas más palabras tiene una descripción, más
fácil es que `word_similarity` encuentre algo.

🔴 **Y AQUÍ ESTÁ LO IMPORTANTE, QUE NO ES "ENCUENTRA DE MÁS".** Con CERO calces el buscador cae al
escalón bueno y la nota que recibe el modelo dice *"calzan VARIOS: nómbrale los TIPOS y pregúntale
de cuál quiere"* — que es exactamente la respuesta correcta a "tienes hogaza?" (*"eso no lo tengo,
mira lo que sí hay"*). Es lo que ya hacía bien con `pizza` y `sushi`. Con UN calce espurio la nota
cambia a *"Calza UN solo producto: preséntalo"*, y el bot responde **en tono de certeza** con un
producto que no tiene nada que ver. **El falso positivo secuestra el camino que funcionaba.**

⚠️ Por eso el arreglo NO es subir el umbral general: los typos viven de él (`'kombuncha'` → Kombucha
calza 0.583 **por el nombre**, y ese camino no se toca). Es darle a la DESCRIPCIÓN su propio piso,
más alto, porque el ruido vive ahí. Los calces legítimos por descripción están muy por encima:

    'bebidas' → Kéfir  0.750     ← lo protege `probar_buscador` desde julio
    'limon'   → tortas 1.000
    ── el piso nuevo: 0.6 ───────
    'rusticos' 0.444 · 'hogaza' 0.429 · 'limon'→Caldo de Huesos 0.333

Es **L63 por la otra puerta**: allí "encontrar de más es gratis" era falso con una restricción
alimentaria; aquí es falso cuando el cliente pide **por su nombre** algo que el negocio anuncia y
no tiene.
"""
from __future__ import annotations

import inspect


def test_el_umbral_de_la_descripcion_es_mas_alto_que_el_del_nombre():
    """El arreglo, fijado en la firma: el ruido vive en la descripción, así que ahí el piso sube.
    Si alguien lo iguala al del nombre, vuelve el 'hogaza → Arepas Andinas'."""
    import inspect

    from app.agent.tools import _buscar_productos_difuso

    firma = inspect.signature(_buscar_productos_difuso)
    assert "umbral_descripcion" in firma.parameters, (
        "desapareció el umbral propio de la descripción: vuelve el calce espurio"
    )
    por_defecto = firma.parameters["umbral_descripcion"].default
    del_nombre = firma.parameters["umbral"].default
    assert por_defecto > del_nombre, (
        f"el piso de la descripción ({por_defecto}) tiene que ser MÁS ALTO que el del nombre "
        f"({del_nombre}): el ruido de trigramas vive en las descripciones largas"
    )
    assert por_defecto >= 0.5, "por debajo de 0.5 vuelve a entrar el ruido medido (0.429 / 0.444)"


def test_el_sql_aplica_el_umbral_alto_SOLO_a_la_descripcion():
    """Que el parámetro exista no basta: tiene que llegar al WHERE de la descripción y NO al del
    nombre (si tocara el nombre, moriría el typo 'kombuncha', que calza 0.583)."""
    import inspect

    from app.agent.tools import _buscar_productos_difuso

    codigo = inspect.getsource(_buscar_productos_difuso)
    cuerpo = "\n".join(
        ln for ln in codigo.splitlines()
        if not ln.lstrip().startswith("#") and not ln.lstrip().startswith("'''")
    )
    assert ":umbral_desc" in cuerpo, "el umbral nuevo no llega al SQL"
    # la condición del NOMBRE sigue con el umbral de siempre
    assert "unaccent(lower(nombre))) >= :umbral" in cuerpo, (
        "el nombre dejó de usar su propio umbral: los typos se van a caer"
    )


def test_las_dos_notas_que_el_bug_confundia_siguen_existiendo():
    """🔴 El corazón del bug, comprobable sin BD: son las NOTAS las que cambian la conducta del
    modelo. La de UN calce le ordena *presentarlo* (certeza); la de VARIOS le ordena *nombrar los
    tipos y preguntar* (que es la respuesta correcta a algo que no existe). El arreglo consiste en
    que un producto inexistente caiga en la segunda, como ya hacía `pizza`.

    ⚠️ Los casos con base de datos —'hogaza', 'rusticos' y los typos que no se pueden romper— los
    corre `probar_buscador` (`./banco_local.sh probar_buscador`), que sí tiene Postgres con
    `pg_trgm`. Aquí solo se fija que las dos notas no se fusionen ni se renombren.
    """
    from app.agent import tools as T

    fuente = inspect.getsource(T)
    assert "Calza UN solo producto" in fuente, "cambió el texto de la nota: revisa probar_buscador"
    assert "Calzan VARIOS productos" in fuente, "cambió el texto de la nota: revisa probar_buscador"
