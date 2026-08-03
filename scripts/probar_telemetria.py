"""BANCO: la telemetría cuenta bien y NO puede tumbar un turno.

Qué vigila, y por qué cada cosa:
1. LAS FIRMAS NO CAMBIARON. `_llamar_openrouter(messages, tools, model)` y
   `_pedir_redaccion(messages, modelo)` son INYECTABLES y media docena de bancos les pasan dobles.
   Si alguien "mejora" esto añadiéndoles un parámetro, se rompen esos bancos: aquí se caza antes.
2. EL `usage` REAL SE LEE ENTERO. Se le da a `_tomar_usage` la respuesta EXACTA que devolvió
   OpenRouter el 2026-08-03 (copiada tal cual, incluido `cost: 3.3e-05`) y se exige el costo con
   todos sus decimales, los tokens y el modelo REAL de la raíz.
3. SE CUENTA POR LLAMADA, NO POR GLOBO. Tres llamadas del mismo turno ⇒ tres filas con el MISMO
   `turno_id` y la suma exacta de los tres costos. Es la garantía de que nada se cuenta doble.
4. `abrir_turno` NO PISA. La nota de voz abre 'audio' y `responder` no lo convierte en 'charla'.
5. 🔴 SI LA BASE FALLA, EL TURNO SIGUE. Con la escritura reventando, `registrar` devuelve
   normalmente (no lanza) — que es la única propiedad innegociable de todo este arreglo.
6. 🔴 LA SONDA DEL MODELO NO SE DEJA EMPATAR. Es el assert que caza el `GROUP BY` que la revisión
   cruzada tuvo que quitar: con el modelo malo y el fallback con el MISMO número de llamadas,
   aquel SQL devolvía cualquiera de los dos y la sonda decía "ok" la mitad de las veces — justo en
   la avería que vino a cazar. Anclada al modelo que dice la configuración AHORA, el veredicto es
   el mismo las tres veces seguidas.

Escribe en la BD REAL del taller, así que LIMPIA AL PRINCIPIO Y AL FINAL (regla dura).
"""
import asyncio
import inspect
import sys
import time
from decimal import Decimal

from sqlalchemy import text

from app.agent.agent import _llamar_openrouter, _pedir_redaccion
from app.services import telemetria as T
from app.services.db import get_session_factory

TELEFONO = "__banco_telemetria__"
MODELO_MALO = "__banco__/modelo-que-no-existe"
MODELO_BUENO = "__banco__/modelo-de-respaldo"
fallos: list[str] = []

# La respuesta REAL de OpenRouter (anthropic/claude-haiku-4.5 por Amazon Bedrock), 2026-08-03.
RESPUESTA_REAL = {
    "model": "anthropic/claude-haiku-4.5",
    "provider": "Amazon Bedrock",
    "choices": [{"message": {"content": "OK"}}],
    "usage": {
        "prompt_tokens": 13,
        "completion_tokens": 4,
        "total_tokens": 17,
        "cost": 3.3e-05,
        "is_byok": False,
        "prompt_tokens_details": {"cached_tokens": 8, "cache_write_tokens": 0, "audio_tokens": 0},
        "cost_details": {"upstream_inference_cost": 3.3e-05},
        "completion_tokens_details": {"reasoning_tokens": 0},
    },
}


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"   {'[OK ]' if ok else '[MAL]'} {nombre}{('  → ' + detalle) if detalle and not ok else ''}")
    if not ok:
        fallos.append(nombre)


async def _limpiar() -> None:
    factory = get_session_factory()
    async with factory() as s:
        await s.execute(
            text("DELETE FROM llamadas_ia WHERE cliente_telefono = :t"), {"t": TELEFONO}
        )
        await s.commit()


async def _leer_modelo_configurado() -> str | None:
    """El valor CRUDO de `configuracion.modelo_ia` (None si no hay fila). Se guarda para
    devolverlo tal cual: este banco corre contra el taller de verdad."""
    factory = get_session_factory()
    async with factory() as s:
        return (
            await s.execute(
                text("SELECT valor FROM configuracion WHERE clave = :c"), {"c": "modelo_ia"}
            )
        ).scalar_one_or_none()


async def _poner_modelo_configurado(valor: str | None) -> None:
    factory = get_session_factory()
    async with factory() as s:
        if valor is None:
            await s.execute(
                text("DELETE FROM configuracion WHERE clave = :c"), {"c": "modelo_ia"}
            )
        else:
            await s.execute(
                text(
                    "INSERT INTO configuracion (clave, valor) VALUES (:c, :v) "
                    "ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor"
                ),
                {"c": "modelo_ia", "v": valor},
            )
        await s.commit()


async def main() -> None:
    await _limpiar()  # al PRINCIPIO: una corrida anterior que se cayó no puede falsear esta
    modelo_previo = await _leer_modelo_configurado()
    try:
        print("\n1) LAS FIRMAS INYECTABLES NO CAMBIARON")
        check(
            "_llamar_openrouter(messages, tools, model)",
            list(inspect.signature(_llamar_openrouter).parameters) == ["messages", "tools", "model"],
        )
        check(
            "_pedir_redaccion(messages, modelo)",
            list(inspect.signature(_pedir_redaccion).parameters) == ["messages", "modelo"],
        )

        print("\n2) EL BLOQUE `usage` REAL SE LEE ENTERO")
        u = T._tomar_usage(RESPUESTA_REAL)
        check("el modelo REAL sale de la raíz", u["modelo_real"] == "anthropic/claude-haiku-4.5")
        check("el proveedor se guarda", u["proveedor"] == "Amazon Bedrock")
        check("tokens de entrada/salida", (u["tokens_entrada"], u["tokens_salida"]) == (13, 4))
        check("los tokens CACHEADOS se ven", u["tokens_cache"] == 8)
        # 🔴 SE COMPARA EL VALOR, NUNCA SU REPRESENTACIÓN EN TEXTO. La primera versión de este
        # check pedía `str(...) == "3.3E-5"` y salía ROJA con el código CORRECTO: `Decimal` solo
        # imprime en notación científica cuando el exponente ajustado es menor que -6, así que
        # `Decimal("3.3e-05")` se escribe `0.000033`. Mismo número, otro texto. Es el mismo veneno
        # que el `"37" in dijo_todo` de TST-6: una aserción sobre la FORMA acusa de un bug que no
        # existe — y la que de verdad importa aquí es la aritmética.
        check(
            "el costo se lee exacto (por VALOR, no por cómo se escribe)",
            u["costo_usd"] == Decimal("0.000033"),
            str(u["costo_usd"]),
        )
        # 🔴 Y ESTE ES EL QUE JUSTIFICA `NUMERIC(14,10)`. Con la columna en `(12,8)` que traía el
        # diseño original, un costo por debajo de 8 decimales se guarda como 0.00000000 — que es
        # exactamente la mentira que `costo_usd NULL` estaba pensado para evitar: "no lo sé" y
        # "salió gratis" NO son lo mismo. Un embedding ya cuesta 6e-08 (justo al filo), así que
        # cualquier modelo más barato caía debajo. Se comprueba con un ida y vuelta REAL por la
        # base, no con la aritmética de Python: lo que trunca es la COLUMNA.
        centavo = Decimal("0.0000000060")
        async with get_session_factory()() as s:
            leido = (
                await s.execute(
                    text("SELECT CAST(:c AS NUMERIC(14,10))"), {"c": str(centavo)}
                )
            ).scalar_one()
        check(
            "un costo minúsculo NO se trunca a cero en la columna",
            leido == centavo and leido > 0,
            f"{leido!s} (esperado {centavo!s})",
        )
        check("sin `usage`, el costo es NULL (no cero)", T._tomar_usage({})["costo_usd"] is None)

        print("\n3) UNA FILA POR LLAMADA, TODAS DEL MISMO TURNO (no por globo)")
        turno = T.abrir_turno(TELEFONO, "charla")
        for _ in range(3):
            await T.registrar(
                paso="agente", modelo_pedido="anthropic/claude-haiku-4.5",
                t0=time.monotonic(), datos=RESPUESTA_REAL,
            )
        factory = get_session_factory()
        async with factory() as s:
            n, suma, carriles = (
                await s.execute(
                    text(
                        "SELECT count(*), coalesce(sum(costo_usd),0), count(DISTINCT carril) "
                        "FROM llamadas_ia WHERE turno_id = :t"
                    ),
                    {"t": turno},
                )
            ).first()
        check("3 llamadas ⇒ 3 filas", n == 3, f"salieron {n}")
        # NUMERIC(14,10): el costo del embedding (6e-08) cabe entero. Con (12,8) estaba al filo.
        check("la suma es exacta (3 × 0.000033)", str(suma) == "0.0000990000", str(suma))
        check("todas del mismo carril", carriles == 1)

        print("\n4) `abrir_turno` NO PISA UN TURNO ABIERTO")
        check("el mismo turno se conserva", T.abrir_turno(TELEFONO, "pago") == turno)

        print("\n5) 🔴 LA SONDA DEL MODELO NO SE DEJA EMPATAR (la corrección que la hace servir)")
        from app.services import salud as S

        # El escenario EXACTO de la avería: el id del panel está mal escrito, así que cada llamada
        # del principal falla y el fallback contesta. Mismo número de filas para los dos: con el
        # `GROUP BY modelo_pedido ORDER BY count(*) DESC LIMIT 1` de la primera versión, Postgres
        # devolvía cualquiera de los dos y la sonda decía "ok" la mitad de las veces.
        async with factory() as s:
            for _i in range(S._MINIMO_LLAMADAS):
                await s.execute(
                    text(
                        "INSERT INTO llamadas_ia "
                        "(cliente_telefono, carril, paso, modelo_pedido, ok) "
                        "VALUES (:t, 'charla', 'agente', :m, false)"
                    ),
                    {"t": TELEFONO, "m": MODELO_MALO},
                )
                await s.execute(
                    text(
                        "INSERT INTO llamadas_ia "
                        "(cliente_telefono, carril, paso, modelo_pedido, ok) "
                        "VALUES (:t, 'charla', 'agente', :m, true)"
                    ),
                    {"t": TELEFONO, "m": MODELO_BUENO},
                )
            await s.commit()

        await _poner_modelo_configurado(MODELO_MALO)
        veredictos = [(await S._modelo())["ok"] for _ in range(3)]
        check(
            "🔴 con el modelo configurado en rojo, la sonda lo canta las 3 veces (determinista)",
            veredictos == [False, False, False], str(veredictos),
        )
        detalle = await S._modelo()
        check(
            "y NO publica el id del modelo (/salud es público)",
            MODELO_MALO not in str(detalle), str(detalle)[:200],
        )

        await _poner_modelo_configurado(MODELO_BUENO)
        veredictos_ok = [(await S._modelo())["ok"] for _ in range(3)]
        check(
            "con el modelo configurado en verde, NO hay falso positivo (las 3 veces)",
            veredictos_ok == [True, True, True], str(veredictos_ok),
        )

        print("\n6) 🔴 SI LA BASE FALLA, EL TURNO SIGUE")
        original = T._escribir

        async def _revienta(_fila):
            raise RuntimeError("Postgres caído (simulado)")

        T._escribir = _revienta
        T._APAGADA_HASTA["t"] = 0.0
        try:
            await T.registrar(paso="agente", modelo_pedido="x", t0=time.monotonic())
            check("registrar() NO lanza con la base caída", True)
        except Exception as e:  # noqa: BLE001
            check("registrar() NO lanza con la base caída", False, repr(e))
        finally:
            T._escribir = original
        check("y se apaga sola para no penalizar el turno", T._APAGADA_HASTA["t"] > time.monotonic())
        T._APAGADA_HASTA["t"] = 0.0
    finally:
        # El taller queda EXACTAMENTE como estaba: las filas fuera y el modelo de la proveedora
        # devuelto a su sitio (si este banco se dejara `modelo_ia` puesto, el bot hablaría con un
        # id inventado hasta que alguien lo notara).
        await _poner_modelo_configurado(modelo_previo)
        await _limpiar()

    print()
    if fallos:
        print(f"   🔴 TELEMETRÍA: {len(fallos)} problema(s).")
        sys.exit(1)
    print("   ✅ La telemetría cuenta por llamada y no puede tumbar un turno")


if __name__ == "__main__":
    asyncio.run(main())
