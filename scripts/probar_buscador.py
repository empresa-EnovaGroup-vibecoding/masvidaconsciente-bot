"""EL BUSCADOR DEL CATÁLOGO: el bot NO puede negar lo que sí vende.

🔴 POR QUÉ EXISTE (auditoría 2026-07-14, verificado ejecutando el filtro real contra los 31
productos vivos):

`ver_catalogo` es la ÚNICA puerta por la que el modelo puede saber de qué es cada producto.
Su filtro exigía que **CADA** palabra de la consulta fuese prefijo de alguna palabra del
nombre o la descripción. Si UNA sola fallaba, devolvía CERO. Y cuando devolvía cero, el
código le ORDENABA al bot:

    "no tienes ningún producto que calce con 'X'; dile con sinceridad que de eso no tienes"

Combinado con la regla ANTIINVENCIÓN del prompt (la que se llama a sí misma "la MÁS
importante"), el bot obedecía al pie de la letra. **El bot no desobedecía: obedecía un bug.**

De 19 consultas normales de cliente, SEIS terminaban con el bot negando cosas que el negocio
SÍ vende:

  · "pan sin gluten"      → CERO. Y TODO el negocio es sin gluten. Ninguna de las 31
                            descripciones menciona la palabra "gluten": vive en la personalidad.
  · "bebidas"             → CERO. Vende Kombucha, Kéfir, Yogurt Kéfirado.
  · "postres"             → CERO. Vende Quesillo, Ponquesitos, Galletas, Tortas, Chocolate.
  · "algo para diabéticos"→ CERO. 24 de 31 productos tienen el campo `apto_diabeticos` lleno.
  · "desayuno" / "snacks" → CERO.

Las tres causas, todas de código: el filtro era un **AND** (una palabra mala tira todo a
cero); la **categoría no era buscable** (aunque existan `harinas`, `dulceria`, `panaderia`…);
y el **plural rompía** (`_singular()` existía, pero solo se usaba en el carril del COBRO).

⚠️ LO QUE ESTE BANCO TAMBIÉN PROTEGE (y es igual de importante): que arreglar la asesoría
NO afloje el COBRO. `_coincide_texto` lo comparten `ver_catalogo` y `_buscar_producto` (el
camino del dinero), y su rigidez es LOAD-BEARING allí: si 'pan' calzara con la categoría
'panaderia', `_buscar_producto('pan')` traería las **Empanadas Keto** (categoria=panaderia)
y el bot podría COBRAR el producto equivocado — el bug del 2026-07-11 ($12 vs $14).
Por eso la parte 3 de abajo comprueba que el carril del dinero sigue siendo ESTRICTO.
"""
import asyncio
import sys

from app.agent.tools import _buscar_producto, productos_enfocados, ver_catalogo
from app.services.db import get_session_factory

TEL = "__prueba_buscador__"

fallos: list[str] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"   {'[OK ]' if ok else '[MAL]'} {nombre}{('  → ' + detalle) if detalle and not ok else ''}")
    if not ok:
        fallos.append(nombre)


# ── 1. LO QUE UN CLIENTE ESCRIBE DE VERDAD ──
# (consulta, al menos un producto que TIENE que aparecer)
# El "debe_estar" es lo que hace este banco honesto: no basta con devolver ALGO — tiene que
# devolver lo CORRECTO. Sin eso, "devolver el catálogo entero siempre" pasaría el test.
CONSULTAS = [
    # 🔴 LAS SEIS QUE DEVOLVÍAN CERO (el bug):
    ("pan sin gluten", "Pan"),          # todo el negocio es sin gluten
    ("bebidas", "Kombucha"),
    ("postres", "Quesillo"),
    ("algo para diabeticos", None),     # 24 de 31 lo son: basta con que traiga algo
    ("desayuno", None),
    ("snacks", None),
    # Las que ya funcionaban (no se pueden romper):
    ("pan", "Pan"),
    ("panes", "Pan"),
    # 🔴 LOS QUE LA PLANTILLA DE MAIRED OFRECE Y EL CATÁLOGO **NO TIENE** (medido 2026-08-23).
    #    No van aquí para que "encuentren algo": van para que encuentren MUCHOS. Un producto
    #    inexistente tiene que caer en el camino de `pizza`/`sushi` —"eso no lo tengo, mira lo
    #    que sí hay"— y NO en el de UN calce, que le ordena al modelo presentarlo con certeza.
    #    Antes del arreglo: 'hogaza' → Arepas Andinas (1) y 'rusticos' → Yogurt Kéfirado (1),
    #    los dos por ruido de trigramas contra descripciones largas de ingredientes.
    ("hogaza", None),
    ("rusticos", None),
    ("harinas", "Harina"),
    ("empanadas", "Empanada"),
    ("galletas", "Galleta"),
    ("kombucha", "Kombucha"),
    ("tortas", "Torta"),
    ("torta keto", "Torta"),
    ("chocolate", "chocolate"),
    ("dulces", None),
    ("queso", None),
    ("yuca", None),
    ("platano", None),
    # Typos (la difusa tiene que seguir salvándolos):
    ("galetas", "Galleta"),
    ("kombuncha", "Kombucha"),
]

# ── IDENTIDAD DEL TÍTULO VS. ATRIBUTOS ──
# La coincidencia por prefijo convertía ``kefir`` en ``kefirado``; y una palabra que es a la
# vez producto y sabor (CHOCOLATE) podía secuestrar ``torta de chocolate``. Aquí no basta con
# que aparezca "algo relacionado": el conjunto exacto es el contrato.
IDENTIDAD = [
    ("kefir", ["Kéfir de Leche de cabra de libre pastoreo"]),
    ("kefir de leche", ["Kéfir de Leche de cabra de libre pastoreo"]),
    ("yogurt de kefir", ["Yogurt Kéfirado"]),
    ("chocolate", ["CHOCOLATE"]),
    ("untable de chocolate", ["Untable de Chocolate"]),
    ("enviame por favor las tortas que tienes", [
        "Tortas keto", "Torta baja en carbohidratos",
    ]),
]

# ── 3. EL CARRIL DEL DINERO SIGUE ESTRICTO ──
# (lo que se busca, qué producto DEBE salir o None si es ambiguo/no existe)
# 🔴 'pan' TIENE que ser ambiguo (hay 4 panes con precios distintos) ⇒ None ⇒ el bot PREGUNTA.
#    Si esto devolviera un producto, el bot cobraría uno al azar.
# 🔴 'pan' JAMÁS puede traer una Empanada (aunque su categoría sea 'panaderia').
DINERO = [
    ("Empanadas Keto", "Empanadas Keto"),
    ("Kombucha", "Kombucha"),
    # 'Empanadas' a secas: hay TRES productos que empiezan así (de yuca/plátano, Keto,
    # Horneadas) y NINGUNO se llama exactamente "Empanadas" ⇒ ambiguo ⇒ None ⇒ el bot PREGUNTA.
    # Si esto devolviera un producto, estaría cobrando uno al azar. Ese fue el bug de julio.
    ("Empanadas", None),
    ("pan", None),        # ambiguo: 4 panes con precios distintos ⇒ preguntar, jamás adivinar
    ("postres", None),    # NO es un producto: el cobro no puede inventarse uno
    # 🔴 EL CENTINELA DE LA REGRESIÓN. La asesoría necesita que 'bebidas' encuentre el Kéfir
    # (su descripción dice "Bebida láctea fermentada"). Al encender la búsqueda por DESCRIPCIÓN
    # en la difusa —que los DOS carriles comparten— el COBRO empezó a devolver el Kéfir para
    # 'bebidas': el bot podía cobrar un producto porque la palabra salía en su descripción.
    # Verificado contra `master`: allí da None. Por eso `con_descripcion` es opt-in y el cobro
    # NO la enciende. Este caso es el que lo vigila: si alguien la enciende, esto se pone rojo.
    ("bebidas", None),
    # Títulos abreviados: la identidad se conserva sin pedirle al cliente que recite la ficha.
    ("kefir", "Kéfir de Leche de cabra de libre pastoreo"),
    ("kefir de leche", "Kéfir de Leche de cabra de libre pastoreo"),
    ("yogurt de kefir", "Yogurt Kéfirado"),
    ("quiero chocolate", "CHOCOLATE"),
    ("quiero el untable de chocolate", "Untable de Chocolate"),
    # ``chocolate`` aquí es SABOR de una torta. Hay varias tortas posibles: preguntar cuál,
    # nunca cobrar el producto independiente CHOCOLATE.
    ("torta de chocolate", None),
    ("enviame por favor las tortas que tienes", None),
]


async def main() -> None:
    factory = get_session_factory()
    async with factory() as session:
        print("\n1) LO QUE EL CLIENTE ESCRIBE — el bot NO puede negar lo que sí vende")
        for consulta, debe_estar in CONSULTAS:
            r = await ver_catalogo(session, TEL, busqueda=consulta)
            prods = r.get("productos") or []
            nombres = [p["nombre"] for p in prods]
            if not prods:
                check(f"'{consulta}' devuelve algo", False, "CERO productos → el bot dirá 'de eso no tengo'")
                continue
            # 🔴 CALCE POR PALABRA, NO POR SUBCADENA. La primera versión usaba `in`, y
            # `"pan" in "empanadas keto"` es True: si el buscador devolviera EMPANADAS para
            # "panes", este banco habría salido VERDE. Es el mismo veneno del bug original
            # ('pan' calzaba por substring con em-PAN-adas) — servido en el test.
            def _calza(nombre: str, palabra: str) -> bool:
                return any(
                    w.lower().startswith(palabra.lower())
                    for w in nombre.replace("-", " ").split()
                )

            if debe_estar and not any(_calza(n, debe_estar) for n in nombres):
                check(
                    f"'{consulta}' trae {debe_estar!r}",
                    False,
                    f"devolvió {len(prods)} pero ninguno es {debe_estar!r}: {nombres[:4]}",
                )
                continue
            check(f"'{consulta}' → {len(prods)} producto(s)", True)

        # 🔴 EL BUG ORIGINAL, EN EL CARRIL DE LA ASESORÍA: 'pan' NO puede traer em-PAN-adas.
        for q in ("pan", "panes"):
            r = await ver_catalogo(session, TEL, busqueda=q)
            malos = [
                p["nombre"] for p in (r.get("productos") or [])
                if "empanada" in p["nombre"].lower()
            ]
            check(f"'{q}' NUNCA trae una empanada", not malos, str(malos))
        for consulta, categoria, imprescindible in (
            ("harinas", "harinas", "Premezclas"),
            ("dulces", "dulceria", "Quesillo"),
        ):
            r = await ver_catalogo(session, TEL, busqueda=consulta)
            prods = r.get("productos") or []
            check(
                f"'{consulta}' devuelve la CATEGORÍA completa",
                bool(prods)
                and all(p["categoria"] == categoria for p in prods)
                and imprescindible in [p["nombre"] for p in prods],
                f"devolvió {[(p['nombre'], p['categoria']) for p in prods]}",
            )

        print("\n1b) EL TÍTULO, EL APODO Y EL ATRIBUTO NO SE CONFUNDEN")
        for consulta, esperados in IDENTIDAD:
            r = await ver_catalogo(session, TEL, busqueda=consulta)
            nombres = [p["nombre"] for p in (r.get("productos") or [])]
            check(
                f"'{consulta}' resuelve exactamente {esperados}",
                nombres == esperados,
                f"devolvió {nombres}",
            )
        for consulta in ("torta de chocolate", "torta sabor chocolate"):
            r = await ver_catalogo(session, TEL, busqueda=consulta)
            nombres = [p["nombre"] for p in (r.get("productos") or [])]
            check(
                f"'{consulta}': chocolate es SABOR, no el producto CHOCOLATE",
                bool(nombres) and "CHOCOLATE" not in nombres
                and all("torta" in n.lower() or "ponqué" in n.lower() for n in nombres),
                f"devolvió {nombres}",
            )

        # La misma identidad gobierna la RED DE LA FOTO. Este es el texto REAL que se envió en
        # el chat del 3-sep: antes detectaba también el producto CHOCOLATE y, al creer que eran
        # tres, el tope anti-spam apagaba las dos fotos de torta.
        respuesta_real_tortas = (
            "Tenemos dos tipos de tortas. Torta baja en carbohidratos, en sabores como limón, "
            "zanahoria, naranja, piña, vainilla, marmoleada, manzana canela y cambur. Disponible "
            "en 250g, 500g y 1kg. Tortas keto, en sabores limón, almendras, chocolate y pistacho. "
            "También en 250g, 500g y 1kg. Cuál te provoca?"
        )
        focos = await productos_enfocados(respuesta_real_tortas, 2)
        check(
            "red de foto: la respuesta real tiene DOS tortas, no el producto CHOCOLATE",
            focos == ["Torta baja en carbohidratos", "Tortas keto"],
            f"detectó {focos}",
        )
        focos = await productos_enfocados("¿Tienes kéfir?", 2)
        check(
            "red de foto: 'kéfir' es el Kéfir de Leche, no Yogurt Kéfirado",
            focos == ["Kéfir de Leche de cabra de libre pastoreo"],
            f"detectó {focos}",
        )
        focos = await productos_enfocados("envíame por favor las tortas que tienes", 2)
        check(
            "red de foto: pedir 'las tortas' enfoca las DOS tortas del catálogo",
            focos == ["Tortas keto", "Torta baja en carbohidratos"],
            f"detectó {focos}",
        )
        focos = await productos_enfocados("muéstrame las harinas", 2)
        check(
            "red de foto: una categoría de TRES no enseña solo dos elegidas al azar",
            focos == [],
            f"detectó {focos}",
        )

        print("\n2) LA NOTA NUNCA ORDENA NEGAR EL CATÁLOGO")
        for consulta in ("bebidas", "postres", "pan sin gluten", "pizza", "sushi",
                         "hogaza", "rusticos"):
            r = await ver_catalogo(session, TEL, busqueda=consulta)
            nota = (r.get("nota") or "").lower()
            # "pizza"/"sushi" NO existen: el bot SÍ debe poder decir que eso no lo tiene —
            # pero NUNCA con la lista vacía. Tiene que ofrecerle alternativas REALES.
            prods = r.get("productos") or []
            check(
                f"'{consulta}': la nota no deja al bot sin nada que ofrecer",
                bool(prods),
                "productos=[] ⇒ el bot corta la venta",
            )
            # 🔴 Un producto que NO existe no puede salir con UN solo calce: esa nota le dice
            #    al modelo "preséntalo", y presenta un producto que no tiene nada que ver.
            if consulta in ("pizza", "sushi", "hogaza", "rusticos"):
                check(
                    f"'{consulta}' (no existe): ofrece ALTERNATIVAS, no un falso calce único",
                    len(prods) > 1,
                    f"devolvió {len(prods)}: {[p['nombre'] for p in prods][:3]}",
                )
            if consulta in ("bebidas", "postres", "pan sin gluten"):
                check(
                    f"'{consulta}': la nota NO le ordena decir 'no tengo'",
                    "de eso no tienes" not in nota,
                    f"nota: {nota[:70]}",
                )

        print("\n3) EL CARRIL DEL DINERO SIGUE ESTRICTO (arreglar la asesoría no puede aflojar el cobro)")
        for pedido, esperado in DINERO:
            p = await _buscar_producto(session, pedido)
            got = p.nombre if p else None
            ok = (got == esperado) if esperado else (p is None)
            check(
                f"_buscar_producto({pedido!r}) → {esperado if esperado else 'None (pregunta)'}",
                ok,
                f"devolvió {got!r}",
            )
        # El bug de las Empanadas, explícito: 'pan' jamás puede traer una empanada.
        p = await _buscar_producto(session, "pan")
        check(
            "'pan' NUNCA cobra una Empanada (el bug de $12 vs $14)",
            p is None or "empanada" not in p.nombre.lower(),
            f"¡devolvió {p.nombre if p else None}!",
        )

    print()
    if fallos:
        print(f"   🔴 {len(fallos)} FALLO(S). El bot está negando productos que sí vende.")
        sys.exit(1)
    print("   ✅ EL BUSCADOR ENCUENTRA LO QUE EL CLIENTE PIDE, Y EL COBRO SIGUE ESTRICTO")


if __name__ == "__main__":
    asyncio.run(main())
