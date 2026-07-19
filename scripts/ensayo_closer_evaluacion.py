"""Criterios duros y juez comercial para el ensayo del closer."""
from __future__ import annotations

import json
import re

import httpx

from app.agent.agent import _afirma_pedido_registrado, _datos_sensibles, _frase_prohibida
from app.models import Pedido
from ensayo_closer_dominio import Escenario, LlamadaTool, sin_acentos

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _resultado_cobro(llamadas: list[LlamadaTool]) -> dict[str, object] | None:
    for llamada in reversed(llamadas):
        if llamada.nombre == "generar_datos_pago" and llamada.resultado.get("ok"):
            return llamada.resultado
    return None


def _resultado_pedido(llamadas: list[LlamadaTool]) -> dict[str, object] | None:
    for llamada in reversed(llamadas):
        if llamada.nombre == "registrar_pedido" and llamada.resultado.get("ok"):
            return llamada.resultado
    return None


def _metodo_elegido(cobro: dict[str, object], nombre: str) -> dict[str, object] | None:
    metodos = cobro.get("metodos_de_pago")
    if not isinstance(metodos, list):
        return None
    buscado = sin_acentos(nombre)
    for metodo in metodos:
        if not isinstance(metodo, dict):
            continue
        disponible = sin_acentos(str(metodo.get("metodo") or ""))
        if buscado in disponible or disponible in buscado:
            return metodo
    return None


def _fallos_comunes(
    escenario: Escenario,
    respuestas: list[str],
    llamadas: list[LlamadaTool],
    pedidos: list[Pedido],
) -> list[str]:
    fallos: list[str] = []
    tools = [llamada.nombre for llamada in llamadas]
    for requerida in escenario.herramientas_requeridas:
        if requerida not in tools:
            fallos.append(f"no llamo a la herramienta requerida {requerida}")
    for prohibida in escenario.herramientas_prohibidas:
        if prohibida in tools:
            fallos.append(f"llamo indebidamente a {prohibida}")
    for nombre in ("registrar_comprobante", "pedir_ayuda"):
        if nombre in tools:
            fallos.append(f"llamo a {nombre} sin que el escenario lo justificara")
    for respuesta in respuestas:
        prohibida = _frase_prohibida(respuesta)
        if prohibida:
            fallos.append(f"frase prohibida entregada: {prohibida}")
    if not escenario.espera_pedido and pedidos:
        fallos.append("creo un pedido cuando el cliente no estaba comprando")
    if not pedidos and any(_afirma_pedido_registrado(r) for r in respuestas):
        fallos.append("afirmo que agendo pero no existe pedido en la BD")
    if escenario.producto_foto and not _foto_correcta(escenario.producto_foto, llamadas):
        fallos.append(f"pidio una foto distinta de {escenario.producto_foto}")
    return fallos


def _foto_correcta(producto: str, llamadas: list[LlamadaTool]) -> bool:
    esperado = sin_acentos(producto)
    fotos = [llamada for llamada in llamadas if llamada.nombre == "enviar_fotos_producto"]
    return any(
        esperado == sin_acentos(str(llamada.args.get("nombre") or ""))
        for llamada in fotos
    )


def _fallos_pedido(
    escenario: Escenario,
    texto: str,
    llamadas: list[LlamadaTool],
    pedidos: list[Pedido],
) -> list[str]:
    if not escenario.espera_pedido:
        return []
    if len(pedidos) != 1:
        return [f"se esperaba 1 pedido y hay {len(pedidos)}"]
    pedido = pedidos[0]
    ids = {int(item.get("variante_id") or 0) for item in (pedido.items or [])}
    fallos: list[str] = []
    if ids != {escenario.variante_id}:
        fallos.append(f"variantes incorrectas en el pedido: {sorted(ids)}")
    cantidades = [
        int(item.get("cantidad") or 0)
        for item in (pedido.items or [])
        if int(item.get("variante_id") or 0) == escenario.variante_id
    ]
    if cantidades != [1]:
        fallos.append(f"cantidad incorrecta para la variante elegida: {cantidades}")
    if pedido.zona_id != escenario.zona_id:
        fallos.append(f"zona incorrecta: {pedido.zona_id} != {escenario.zona_id}")
    if pedido.entrega_fecha != escenario.fecha:
        fallos.append(f"fecha incorrecta: {pedido.entrega_fecha} != {escenario.fecha}")
    if pedido.total is None or float(pedido.total) <= 0:
        fallos.append("pedido sin total positivo")
    tool = _resultado_pedido(llamadas)
    resumen = str((tool or {}).get("resumen") or "")
    if not resumen or resumen not in texto:
        fallos.append("no copio exactamente el recibo calculado por registrar_pedido")
    return fallos


def _fallos_cobro(
    escenario: Escenario,
    texto: str,
    llamadas: list[LlamadaTool],
    pedidos: list[Pedido],
) -> list[str]:
    if not escenario.espera_cobro:
        return []
    cobro = _resultado_cobro(llamadas)
    if cobro is None:
        return ["generar_datos_pago no devolvio un cobro valido"]
    fallos: list[str] = []
    tools = [llamada.nombre for llamada in llamadas]
    if not pedidos or pedidos[0].estado != "esperando_pago":
        fallos.append("el pedido no quedo en esperando_pago")
    if (
        "registrar_pedido" in tools
        and tools.index("registrar_pedido") > tools.index("generar_datos_pago")
    ):
        fallos.append("intento cobrar antes de registrar el pedido")
    resumen = str(cobro.get("resumen_cobro") or "")
    if resumen and resumen not in texto:
        fallos.append("no copio exactamente el resumen_cobro calculado por el codigo")
    elegido = _metodo_elegido(cobro, escenario.metodo or "")
    if elegido is None:
        fallos.append(f"no encontro el metodo elegido {escenario.metodo}")
    elif not _incluye_dato_pago(elegido, texto):
        fallos.append("no entrego ningun dato del metodo de pago elegido")
    return fallos


def _incluye_dato_pago(metodo: dict[str, object], texto: str) -> bool:
    campos = [
        str(valor)
        for clave, valor in metodo.items()
        if clave != "metodo"
        and valor
        and ("@" in str(valor) or len(re.sub(r"\D", "", str(valor))) >= 6)
    ]
    return not campos or any(valor in texto for valor in campos)


def evaluar_duro(
    escenario: Escenario,
    respuestas: list[str],
    llamadas: list[LlamadaTool],
    pedidos: list[Pedido],
) -> list[str]:
    texto = "\n".join(respuestas)
    fallos = _fallos_comunes(escenario, respuestas, llamadas, pedidos)
    if escenario.id == "datos_sin_pedido" and _datos_sensibles(texto):
        fallos.append("solto datos sensibles sin pedido ni herramienta de cobro")
    fallos.extend(_fallos_pedido(escenario, texto, llamadas, pedidos))
    fallos.extend(_fallos_cobro(escenario, texto, llamadas, pedidos))
    return fallos


def evaluar_advertencias(
    respuestas: list[str],
    llamadas: list[LlamadaTool],
) -> list[str]:
    advertencias: list[str] = []
    if any(
        llamada.nombre == "registrar_pedido"
        and "SIN CAMBIOS" in str(llamada.resultado.get("nota") or "")
        for llamada in llamadas
    ):
        advertencias.append("repitio registrar_pedido aunque el pedido no habia cambiado")
    texto = "\n".join(respuestas)
    if "**" in texto or re.search(r"(?m)^\s*[-*]\s+", texto):
        advertencias.append("uso negritas o listas aunque la voz pide texto plano")
    return advertencias


def redactar_para_juez(texto: str, llamadas: list[LlamadaTool]) -> str:
    limpio = texto
    for llamada in llamadas:
        if llamada.nombre != "generar_datos_pago":
            continue
        metodos = llamada.resultado.get("metodos_de_pago")
        if not isinstance(metodos, list):
            continue
        for metodo in metodos:
            if not isinstance(metodo, dict):
                continue
            for clave, valor in metodo.items():
                if clave != "metodo" and valor:
                    limpio = _redactar_valor(limpio, str(valor))
    return limpio


def _redactar_valor(texto: str, valor: str) -> str:
    digitos = re.sub(r"\D", "", valor)
    if len(digitos) >= 6:
        patron = r"\D*".join(re.escape(digito) for digito in digitos)
        texto = re.sub(patron, "[DATO_DE_PAGO]", texto)
    return re.sub(re.escape(valor), "[DATO_DE_PAGO]", texto, flags=re.I)


def _prompt_juez(escenario: Escenario, dialogo: str) -> str:
    return f"""Eres un evaluador de conversaciones de venta por WhatsApp.
Puntua de 0 a 5 cada criterio. No premies presion, discursos largos ni datos inventados.
El agente debe sonar calido, venezolano, con clase, breve y conducir el siguiente paso natural.
Objetivo del escenario: {escenario.objetivo}

CONVERSACION:
{dialogo}

Devuelve SOLO JSON:
{{"conduccion_al_cierre": 0, "tono_humano": 0, "manejo_del_momento": 0,
  "brevedad": 0, "presion_indebida": false, "observacion": "una frase"}}"""


async def juzgar(
    escenario: Escenario,
    dialogo: str,
    modelo: str,
    api_key: str,
) -> dict[str, object]:
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": modelo,
                    "messages": [{"role": "user", "content": _prompt_juez(escenario, dialogo)}],
                    "temperature": 0,
                },
            )
            response.raise_for_status()
            contenido = response.json()["choices"][0]["message"].get("content") or "{}"
        match = re.search(r"\{.*\}", contenido, re.S)
        valor = json.loads(match.group(0) if match else "{}")
        return valor if isinstance(valor, dict) else {"error": "el juez no devolvio un objeto"}
    except (httpx.HTTPError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return {"error": f"juez no disponible: {type(exc).__name__}"}
