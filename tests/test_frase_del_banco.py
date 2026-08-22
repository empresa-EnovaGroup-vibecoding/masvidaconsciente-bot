"""EL BOT NO TIENE BANCO — y el 2026-08-22 dijo que había mirado la cuenta.

**El mensaje REAL** (fila 5601 de `mensajes`, taller, 1:46 pm, a un número de la lista blanca):

    🤖 "Enova, acabo de revisar y ese pago no me aparece en la cuenta. Verifica que lo
        mandaste a los datos exactos que te pasé del Pago Móvil y me reenvías la captura."

El fondo era legítimo —la visión leyó la captura y el beneficiario no era el de la dueña— pero
la frase es una mentira de las caras: **el bot no tiene acceso a ninguna cuenta**. Quien mira el
banco es Whuilianny, en el suyo. Está prohibido desde el primer día y aun así salió, por DOS
huecos a la vez:

1. **La red no la vio.** `_PROHIBIDO_SIEMPRE` exigía que "banco/cuenta" viniera PEGADO al verbo
   (`revisó en mi banco`), y aquí hay media frase en medio: *"revisar **y ese pago** no me
   aparece en la cuenta"*.
2. **La instrucción del sistema se lo ORDENABA.** El texto que dispara ese turno decía, literal,
   «dile con cariño que ese pago no te aparece **a tu cuenta**». El modelo obedeció.

Se arreglaron los dos, y el orden importa: la instrucción es el arreglo de fondo (ahora habla de
la CAPTURA), la red es el cinturón. *Una red que hace falta en el camino normal es una
instrucción mal escrita.*

⚠️ Lo que hace difícil esta red: el bot **sí debe poder decir** que está revisando el comprobante
—es su respuesta correcta al recibirlo—. Lo que separa la verdad de la mentira no es el verbo,
es el TIEMPO: *"lo estoy revisando"* es verdad, *"ya revisé"* es mentira. Por eso media parte de
este fichero son frases buenas que NO deben dispararla.
"""

import pytest

from app.agent.agent import frase_prohibida_siempre

# La frase exacta, copiada de la base de datos del taller.
LA_DEL_SABADO = (
    "Enova, acabo de revisar y ese pago no me aparece en la cuenta. Verifica que lo mandaste "
    "a los datos exactos que te pasé del Pago Móvil y me reenvías la captura para confirmar, "
    "con gusto."
)


def test_la_frase_real_del_22_de_agosto():
    assert frase_prohibida_siempre(LA_DEL_SABADO) is not None


@pytest.mark.parametrize("texto", [
    "acabo de revisar y ese pago no me aparece en la cuenta",
    "ya revisé y no me aparece nada",
    "Ya verifiqué en mi banco y no está",
    "recién chequeé tu pago",
    "Ya lo revisé, no llegó",
    "acabo de mirar y no aparece",
    # Las que ya cazaba antes: se comprueban para que un refactor no las pierda
    "Ya revisé en mi banco y no me aparece",
    "Mi banco ya me confirmó el pago",
])
def test_lo_que_debe_frenar(texto):
    assert frase_prohibida_siempre(texto) is not None, f"se escapó: {texto!r}"


@pytest.mark.parametrize("texto", [
    # 🔴 ESTAS SON LA RAZÓN DE QUE LA RED MIRE EL TIEMPO VERBAL. Son las respuestas CORRECTAS
    # del bot al recibir un comprobante: frenarlas lo dejaría mudo justo cuando alguien pagó.
    "Ya lo recibí, lo estoy revisando con calma y te confirmo enseguida.",
    "Ya me llegó tu comprobante. Déjame revisarlo y te confirmo.",
    "Listo, ya lo tengo. Lo reviso y te aviso en un momentito.",
    "Voy a revisar y te digo.",
    "Lo estoy verificando, te confirmo enseguida",
    # Y una venta normal, que no tiene nada que ver con el dinero
    "Ya te preparo tu pedido para mañana",
    "Ya te comparto los datos para el pago",
])
def test_lo_que_NO_debe_frenar(texto):
    assert frase_prohibida_siempre(texto) is None, (
        f"frenó un mensaje CORRECTO: {texto!r} — esta red no puede callar al bot cuando "
        "alguien acaba de pagar"
    )


def test_la_instruccion_del_sistema_ya_no_ordena_la_mentira():
    """El arreglo de fondo. Si alguien reescribe esa situación y vuelve a poner "tu cuenta",
    el bot volverá a decirlo — la red lo taparía, pero el mensaje correcto se pierde igual.

    ⚠️ DOS trampas del instrumento, las dos cazadas escribiéndolo (y las dos de la misma familia:
    buscar texto en el código fuente es frágil):
      1. Mirar el fuente entero encuentra la frase prohibida **en el comentario que documenta el
         bug**. Por eso se filtran los comentarios.
      2. El literal está PARTIDO entre dos líneas ("…HABLA DE LA " + "CAPTURA, NUNCA…"), así que
         buscarlo contiguo falla aunque esté. Por eso se normalizan espacios y saltos.
    """
    import inspect
    import re

    from app.workers import tasks

    sin_comentarios = "\n".join(
        ln for ln in inspect.getsource(tasks._procesar_comprobante).splitlines()
        if not ln.lstrip().startswith("#")
    )
    # Junta los literales partidos y colapsa los espacios: así el test mira lo que el modelo
    # va a LEER, no cómo quedó formateado el fichero.
    codigo = re.sub(r'"\s*\n\s*"', "", sin_comentarios)
    codigo = re.sub(r"\s+", " ", codigo)

    assert "no te aparece a tu cuenta" not in codigo, (
        "la instrucción volvió a ordenarle al bot hablar de 'tu cuenta' en vez de la captura"
    )
    assert "HABLA DE LA CAPTURA" in codigo, (
        "la instrucción dejó de decirle explícitamente que hable de la captura y no de la cuenta"
    )
