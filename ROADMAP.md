# 🗺️ ROADMAP = lo que FALTA por hacer (másvida)

> **Visión:** másvida es una plataforma de **ventas y cobro por WhatsApp** para marcas pequeñas de productos saludables: un agente IA **oficial de Meta** que atiende, cobra en bolívares con tasa BCV y le deja a la dueña el **control total** desde un panel simple — diseñada para **replicarse cliente por cliente** (la base del negocio Tech Provider de Maired).

**Cómo leer las prioridades:** 🟢 Ahora · 🟡 Pronto · ⚪ Futuro · *(esfuerzo: bajo/medio/alto)*

> 📌 Este documento = **lo que FALTA**. Lo que YA está hecho vive en **SESIONES** (el diario). Cómo habla el bot vive en el **BRIEF**.

---

## ✅ Ya tienes funcionando (resumen — el detalle está en SESIONES)

Las **FASES 0 a 3 ya están hechas y desplegadas**:
- **Ventas:** agotados · catálogo y precios editables · catálogo en PDF · tasa BCV con margen/candado · pago que no calza.
- **Clientes:** panel · ficha con historial · notas internas.
- **Control del bot:** encender/apagar · datos editables · personalidad editable · simulador · pausar por chat · mensajes editables · conocimiento (FAQ).
- **Otros:** reporte de ventas · tope de gasto / anti-abuso.
- **Unificación del taller (18-jul):** buscador nuevo · multimedia visible en el chat interno · roles · herramientas configurables · selector de modelos · arquitectura opcional de dos agentes · **17 bancos automáticos**.

---

## 🔨 EN QUÉ ESTAMOS AHORA (lo siguiente, en orden)

> 📍 **Pestaña NUEVA de Claude: empieza leyendo este bloque + la última entrada de SESIONES.** Ahí está el estado REAL (no asumir de memoria vieja).
>
> 🏭 **Desde el 1-sep (noche) existe el ENTORNO DE PRUEBAS de Enova** (VPS propio del socio,
> número de la agencia +57 313 2933806): el reemplazo del taller para probar por WhatsApp REAL
> sin tocar a la clienta. URLs y estado: `ESTADO.md` bloque 🏭 · historia: SESIONES 1-sep (14).
> El panel de pruebas ya tiene su dominio propio: `panel-masvida.enovagroup.tech` (2-sep).

### 🚀 AHORA MISMO (16-sep): REGLA DEL DELIVERY Y MODELO MÁS ECONÓMICO

> 🧭 **22-sep — PLAN VIGENTE: "atención confirmada + Voz" (modo `confirmado`)** — SESIONES (33) y
> `~/.claude/plans/…crystalline-sprout.md`. El código decide QUÉ (capa de hechos de Codex), la Voz decide CÓMO
> (modo DOS de agosto), el código verifica. Absorbe el punto 3 de abajo: los modelos baratos entran donde el
> peor caso es un reintento, nunca una mentira. **E0 hecho** (rama `atencion-confirmada-voz`): modo cableado y
> APAGADO, suite 1105/0, producción sigue en `uno`. Siguen E1 (Voz sobre la hoja de hechos) → E2 (redes de
> nombres/fechas) → E3 (pagos) → E4 (medir con el arnés, jueces Claude, puertas G1-G5) → E5 (pruebas + una
> conversación de Maired) → E6 (prod por config). Dos preguntas de negocio pendientes en SESIONES (33):
> sobrepago = ¿saldo a favor?; BD caída = ¿callar o seguir?

1. **Cerrar la regla del delivery.** Whuilianny confirmó la fórmula: productos + delivery y después
   20% de descuento al total cuando se paga en dólares. El cambio de código conserva las cotizaciones
   que ya se dieron a clientas y debe pasar por PR, CI y despliegue manual.
2. **Terminar la auditoría de atención real.** El corpus local ya contiene 313 conversaciones y las
   notas de voz recuperables. No se repite la corrida de 42 agentes: se continúa desde los resultados
   y la auditoría ya escrita, separando clientas de proveedores, familia y repartidores.
3. **Bajar el costo sin empeorar a Alejandra.** Primero se construye la evaluación con casos reales y
   fallos duros; después se prueban pocos modelos finalistas con un presupuesto pequeño y controlado.
   No se cambia Sonnet por intuición ni se gasta el saldo de producción en una barrida masiva.
4. **Abrir gradualmente.** Mantener lista blanca, leer las conversaciones piloto y ampliar solo cuando
   el cobro, la entrega y el relevo humano estén estables.

La organización documental del mentor ya quedó aplicada en el PR #56: `FUNCIONES.md`,
`ONBOARDING.md` y la poda de historia. Eso permite entender el sistema y reduce contexto de trabajo;
no reduce directamente los tokens que consume el bot de WhatsApp.

### 🧵 EL TRABAJO EN CURSO: "que no repregunte lo que la clienta YA dijo" (abierto el 2026-08-31)

**La clase de bug, nombrada:** *LA VENTANA SIN ESTADO*. Entre que el cliente dice un dato y
que una herramienta lo guarda en una casilla de la BD, ese dato vive SOLO como chat crudo — y
en esa ventana, cualquier resultado FRESCO de herramienta que traiga las opciones reabre la
elección y el modelo repregunta. **Verificado con los ojos** (SSH al taller, entradas 3 y 4 de
SESIONES del 31-ago): la elección SÍ le llegaba al modelo y SÍ existía la regla en el prompt —
repreguntó igual. **El dato fresco le gana al chat viejo; solo el ESTADO le gana al dato
fresco.** De ahí la doctrina aplicada: cada dato de la venta necesita su casilla, su línea de
estado y (rama D) su vigilante.

**El test que decide todo:** *¿ese dato tiene casilla?* Si no la tiene, es repreguntable.

> 🎯 **ESTADO AL 1-SEP: EL PLAN A→D ESTÁ COMPLETO, FUSIONADO Y DESPLEGADO AL TALLER.** Los PRs
> #10, #11 (B método de pago) · #12 (guardias) · #13 (C hilo a tamaño/sabor) · #14 (D el
> vigilante) están TODOS en `master`. Maired probó en vivo B y C y le gustaron. Detalle de cada
> una en SESIONES 1-sep (1)-(4). 🔬 Y la autopsia del "borré el chat y el bot recuerda la
> entrega": NO era bug — el pedido abierto sobrevive al borrado del chat por diseño (correcto en
> producción); Maired borró los pedidos de prueba y quedó en cero. Ver SESIONES 1-sep (5).
> **El trabajo de "que no repregunte" está CERRADO.** Lo siguiente es la meta grande: la entrega.

**HECHO y fusionado el 31-ago** (PRs #5→#8, todos desplegados al taller):
- **#5 fotos con memoria** · **#6 EL HILO DE LA VENTA** (`hilo_de_la_venta` en tools.py: destila
  del historial la VERSIÓN elegida —masa yuca/plátano— y `responder()` la inyecta como HECHO en
  la parte DINÁMICA del prompt) · **#7 pie de foto limpio** · **#8 EL PEDIDO COMPLETO COMO
  ESTADO** (`_items_sin_dinero` + "Lo que LLEVA el pedido #X" + "Entrega YA ACORDADA" en
  `_estado_cliente_texto`, SIN cifras de dinero a propósito — la red del dinero lee ese texto;
  más 8 guardias de hilo en las notas de las herramientas que "tentaban").

### ⚪ Lo que NO se puede prometer (dicho a Maired, 31-ago)

Con B+C+D las repreguntas de datos del negocio quedan entre "casi nunca" y "frenadas antes de
salir". El **"nunca jamás con cualquier palabra" NO existe**: frases 100% libres ("lo de la otra
vez", "como el mes pasado") no se convierten en dato sin adivinar — y adivinar fabricó el pedido
duplicado #2074. La vía es registrar temprano + estado + vigilante, no un segundo NLU.

**Cómo se hace una autopsia en el taller (método, verificado el 31-ago).** 🔴 Este repositorio
es PÚBLICO: los nombres de contenedor, usuarios y claves NO se escriben aquí — se leen del
servidor en el momento (`docker ps`, y la clave de Redis sale de
`docker exec <bot> sh -c 'echo $REDIS_URL'`). Los pasos que cerraron el caso del 31-ago:
1. Ubicar el chat por su texto en la tabla `mensajes` (o por `clientes.nombre`).
2. `LRANGE hist:<telefono>` en Redis = **la lista EXACTA que recibió el modelo** (TTL 24h: se
   pudre rápido, es lo primero que hay que sacar). Es la prueba reina de "¿lo tenía delante?".
3. `grep 'MEMORIA RESCATADA'` en los logs del bot y del worker: si NO aparece, el contexto vino
   de Redis vivo y no del respaldo de Postgres (que filtra).
4. `SELECT items … FROM pedidos` → verificar el cobro **en la BD, no en el texto** (regla de oro
   del repo, CLAUDE.md §8).
5. `llamadas_ia` NO guarda los messages, solo métricas — sirve para tokens y costo, no para
   reconstruir el prompt. *(Medido: 96% del prompt viaja cacheado; $1,99 en 7 días de pruebas.)*

---

# 🎯 QUÉ SIGNIFICA "TERMINADO" (la meta — todo lo demás es camino)

> **PROPUESTA del 2026-08-21 — Maired debe confirmarla o corregirla.** Un proyecto sin definición
> de "terminado" no termina nunca: la lista de pendientes crece más rápido de lo que se achica.
> **Cuando estas casillas estén marcadas, la v1 ESTÁ TERMINADA** y lo que siga es mejora, no deuda.

- [x] 1. Código de agosto de Erwin unificado en GitHub (hecho 21-ago: 32 commits del bot + 6 del panel).
- [x] 2. **Despliegue reconectado y AUTOMÁTICO (hecho 22-ago).** Coolify reconectado a `master` y construyendo desde GitHub; y un push a `master` **despliega el taller solo**, con la CI (`ruff`/`compileall`/`pytest`) como puerta: si sale rojo, no se despliega. Producción sigue SOLO a mano (en un push el destino se fuerza a `taller`). Cómo funciona hoy está en `ESTADO.md` § "cómo se despliega".
- [x] 3. Producción actualizada a la última versión — CON respaldo previo de BD + personalidad de netcup. *(Hecho el 5-sep con `89aea1d` y repetido el 6-sep con `42d37de`, con respaldo previo cada vez; ⏳ toca re-promover `f60c3f7` — ver ESTADO "Última verificación".)*
- [x] 4. Los **27 bancos en verde en producción** (no solo en el taller). *(27/27 en producción el 5-sep y el 6-sep; se re-corren tras cada promoción, paso 3 de la liturgia.)* *(Eran 17 cuando se escribió esta casilla; hoy son 27 — y 24 de ellos corren en LOCAL antes de desplegar con `./banco_local.sh`.)*
- [ ] 5. Pruebas de humo con el número real: saludo · catálogo · fotos · pedido · datos de pago · comprobante · delivery — **verificado en la BD, no en el chat**.
- [ ] 6. La lista blanca se quita (o se amplía por grupos) y **el bot atiende clientas reales**.
- [ ] 7. La dueña atiende desde la bandeja del panel y el bot escala cuando no sabe.
- [ ] 8. Saldo de IA recargado + respaldo automático corriendo también en el taller (D4).
- [ ] 9. **ENTREGA formal a la clienta:** recorrido del panel juntas y **cierre del acuerdo comercial**. Esta casilla convierte a masvida en el primer caso del portafolio de Enova.

## 📋 LO QUE PIDE LA PLANTILLA DE NEGOCIO DE MAIRED (2026-08-22) — lo que falta construir

> La plantilla que llenó Maired es la especificación de negocio más completa que ha tenido el
> proyecto. **La mayor parte ya estaba construida o se aplicó el 22-ago** (ver `SESIONES.md`
> 08-22 (5)): el descuento del efectivo con delivery gratis, la voz, las alergias por ficha, el
> calendario consultable. Lo que sigue son las piezas GRANDES que pide y que no existen.
>
> 🔴 **No se creó el `project.md` que pide su último paso, a propósito.** Ese paso asume un bot
> que se construye desde cero; másvida ya tiene su cerebro de tres capas. Un documento más sería
> una cuarta copia de la verdad que el bot no lee — la enfermedad D3. El contenido se ruteó a las
> capas que sí se ejecutan.

| # | Qué pide | Tamaño | Nota |
|---|---|---|---|
| **N1** | 💵 **Pago dividido 30/70** — 30% para confirmar, 70% contra entrega, con la modalidad asignada POR SISTEMA (no la elige el cliente) y medición A/B contra el pago completo | **alto** | Toca el carril del dinero entero: `Pago`, el panel, la validación del comprobante y el estado del pedido. La plantilla lo llama "la prueba" y pide medir pago, abandono, venta completada y pedidos no recibidos. **Necesita migración.** |
| **N2** | 🚚 **Delivery extraordinario** — la dueña lo activa en el panel con fecha, hora límite, zonas, productos y capacidad; **expira solo** y el bot NUNCA lo activa por su cuenta | medio-alto | Encaja limpio sobre el calendario que ya existe (`proxima_fecha_entrega` lo leería). La plantilla insiste dos veces en que una autorización de delivery **no** significa que cualquier producto pueda prepararse hoy: hay que verificar producto, capacidad, zona y hora. |
| **N3** | 🗺️ **Mapa de zonas por sector** — hoy son 3 zonas planas; pide sectores reconocidos y **escalar a la dueña** la dirección que no calce | medio | ✅ El dato que había que resolver antes **ya está resuelto** (23-ago): la zona cercana pasó a **$2** en `zonas_entrega` (Barquisimeto centro $2.00 · oeste $5.00 · retiro $0), como pide la plantilla. |
| **N4** | 📊 **Aviso de día flojo** — que el sistema detecte ventas bajas y le SUGIERA a la dueña activar una extensión | bajo-medio | La propia plantilla lo pone en "una fase posterior" y exige **aprobación de ella** antes de ofrecérselo a nadie. Depende de N2. |
| **N5** | 🧾 **Resumen final antes del despacho** (su paso 11) | bajo | ⏸️ **Atado a N6.** Su paso 11 ocurre "después de «Pago aprobado»", así que dónde va exactamente depende de si el bot espera o no. Se hace junto con N6, no antes. *(La otra mitad de N5 —los tips de conservación al cerrar— ✅ **ya está hecha**: 24 de 32 fichas traen `duracion`, y la regla no deja inventar el tip si falta.)* |
| **N6** | ⏸️ **Que el bot ESPERE el clic de «Pago aprobado»** antes de coordinar la entrega (sus pasos 8-9) | medio | 🔴 **Es un cambio de diseño, no un ajuste**: hoy el bot registra el comprobante y **sigue** la venta (`CLAUDE.md` §3). Tiene un costo real — si la dueña tarda dos horas en aprobar, el cliente pasa dos horas mudo después de haber pagado. **Decisión de Maired antes de tocarlo.** |

### 🔴 Y lo que la plantilla necesita de DATOS (es de Whuilianny, no de código)

✅ **`dias_anticipacion` YA ESTÁ CARGADO** (23-ago, tomado del documento): 16 productos en 0
(congelados y envasados), 12 en 1 y 4 en 2 (los que se hornean). Verificado en la BD el 23-ago.
El bot lo respeta **sin desplegar nada**, porque el catálogo se relee en cada mensaje.
⚠️ **Los números salieron del documento, no de la boca de Whuilianny: conviene que ella los
confirme.** ✅ Y la **zona cercana** quedó en **$2**.

🔴 **Lo que sigue faltando de datos:** los productos que la plantilla ofrece y **no existen en el
catálogo** (hogaza, rústicos, hamburguesas, opciones veganas), sabores en 5 de 37 variantes,
9 productos sin foto, **0 feriados**, y **si la masa madre lleva almendra** (es un alérgeno y
sigue sin respuesta).

---

**NO entra en la v1** (ya decidido, no re-abrir): las plantillas proactivas de Meta · ordenar
Conocimiento · el cliente #2 (ya hay candidato). *(El modo DOS agentes ya NO es "no entra": Erwin
cerró sus 3 bloqueadores el 06-ago — ver abajo.)*

---

### ⚠️ REGLAS QUE COSTARON SANGRE (no re-aprenderlas)

- **El DINERO va en el CÓDIGO, nunca en el prompt.** El prompt decía *"no sumes el envío al total"* **dos veces** y el bot lo sumó igual, a una clienta real. *Lo que se puede desobedecer, se desobedece.*
- **Verificar en la BD, no en el chat.** El bot dijo *"te agendo"* con **cero pedidos** en la base.
- **Todo lo que mueve dinero necesita una LISTA CERRADA** (el "código de barras"): `variante_id` para el producto, `zona_id` para el envío. El modelo **elige**, nunca **escribe**.
- **Un contenedor en VERDE no significa que la base esté bien.**
- **Push a master = SOLO el taller.** Producción es a mano (`-f produccion=true`).

---

### 🧱 DEUDA TÉCNICA — LOS CIMIENTOS (lo PRIMERO, antes que cualquier feature nueva)

> **Por qué va primero:** esto no rompe el bot *hoy*; rompe **lo que ya funciona**, mañana, sin
> que nadie se entere. Verificado en el código el 2026-07-13 (no supuesto).

| # | El agujero | Qué pasa si no se toca |
|---|---|---|
| **D1** | ✅ **CERRADA (14-jul, fase 0): existe `schema_migrations` y el arranque FALLA RUIDOSAMENTE.** `init_db.py` descubre `migrations/*.sql` solo (adiós a la lista a mano), las aplica **una vez** y las anota. `main.py` ya **no** captura la excepción: migración rota ⇒ **el contenedor no arranca**. Banco `probar_drift.py` compara `models.py` contra el esquema real. | ~~Un `.sql` mal escrito duplica datos en el próximo reinicio, y el contenedor arranca verde con la base a medias.~~ Ya no: **se aplica una vez, y si falla, no arranca.** ⚠️ Lo que se descubió al cerrarla: **`002_seed_catalogo.sql` NO es idempotente** (`INSERT INTO productos` sin `ON CONFLICT`) — lleva candado propio. Toda migración nueva sigue debiendo ser idempotente y **sin `;` dentro de literales ni bloques `DO $$`** (el partidor de `_statements` los rompe). |
| **D2** | ✅ **CERRADA (14-jul): los bancos YA corren solos.** El workflow ejecuta los 10 bancos tras cada despliegue del taller (`correr_bancos.py`, vía SSH al contenedor nuevo). Rojo ⇒ flujo ROJO en GitHub + WhatsApp a la dueña. | ~~Alguien hace `git push`, se despliega, se rompe el cobro y nadie se entera.~~ Ya no: **el vigilante avisa solo.** |
| **D3** | 🟠 **Campos LEGADOS que duplican la verdad.** `productos.precio` y `productos.presentacion` siguen vivos "por compatibilidad" (`router.py:664`); los **"Sabores:"** siguen escritos dentro de las descripciones. | **Es exactamente la enfermedad que causó la fuga de la Kombucha**: el mismo dato en dos sitios. Hoy están desactivados (el bot lee el tamaño), pero **siguen ahí para volver a morder**. |
| **D4** | 🔴 **El respaldo automático NO corre en el TALLER** (solo en producción/netcup). Y el taller es donde se construye y donde corren las migraciones **destructivas**. | La cirugía del 2026-07-13 fue **la primera migración que borra una fila con contenido real**. Se salvó con un `pg_dump` a mano. **La próxima puede que no.** |
| **D5** | 🔴 **Llaves expuestas sin rotar** (ver punto 8, abajo). | Antes de abrir con clientes reales. |

**Cómo se ataca (recomendado):** una **auditoría de arquitectura adversarial** del sistema COMPLETO (no de una feature), con revisores de lentes distintas —el dinero, Meta/Tech Provider, los datos y las migraciones, el panel, la operación— y **cada hallazgo verificado contra el código** antes de reportarlo. Primero el diagnóstico; **el código, después**.

> ## ✅ HECHO (2026-07-13) — la auditoría se corrió (283 agentes, 9 lentes + triple refutación) y se
> cerraron **TODOS los bloqueantes**. Detalle en SESIONES. Resumen:
> - 🔒 **Candado del cobro** (`require_parameters`, era el punto 7/8 de abajo) · 🛠️ **Despliegue taller-primero**
>   (bot **y** panel: push→solo taller, prod a mano — cierra D2/A1) · 💰 **B4** fuga del precio del panel ·
>   🩹 **B3** el precio del día daba 500 · 🧨 **B2** el script de promoción decapitaba el cobro · ⚡ **panel**
>   (scroll del chat + refresco 3s). Todo probado y en producción.
> - **Sigue abierto (cimientos, no bloqueantes):** **D1** (tabla de migraciones), **D4** (respaldo en el
>   taller), **D3/D5** (menores). **B5** (cuenta sembrada) solo muerde con el 2º cliente.
> - **Decidido:** modelo → **quedarse en Haiku** (Gemini Pro no vale; `gpt-5.4-mini` ahorra ~$3/mes, no urge).
>   **Multi-agente → NO** (el catálogo es 17% del prompt; el fix es *retrieval*, YA construido: conmuta solo
>   pasados 60 productos → escala a 400 sin tocar código).

---

**✅ Terminado y verificado en vivo (junio 2026):**
- Comprobantes multi-método (Pago Móvil/Transferencia/Zelle/Binance) + validación de monto (Bs/USD/USD con descuento).
- Descuento 20% en divisas (cotiza Y reconoce el monto con descuento).
- Búsqueda escalable: **Fase 1** pg_trgm (encuentra aunque escriban con errores) + **Fase 2** embeddings semánticos vía OpenRouter (entiende por significado, ej. "celíaco"="sin gluten").
- **Ficha por producto** (duración, ¿se congela?, ¿apto diabéticos?, + info) y regla antiinvención (no inventa datos del producto).
- **Fotos y videos por producto**: subir en el panel → Cloudflare R2 → el bot las **envía por WhatsApp** cuando el cliente las pide.
- El bot "dice que mandó el catálogo": red de seguridad OK (`_asegurar_catalogo`).

## ⭐ 3 cosas críticas que descubrimos (por qué este enfoque)

1. **Aprobación humana antes de CUALQUIER envío proactivo.** Ningún recordatorio/campaña/reactivación sale solo: el sistema prepara la lista + el borrador y **tú apruebas con un botón.** Como eres Tech Provider **oficial de Meta**, un envío automático mal calibrado puede quemar la calidad del número y arriesgar la cuenta de **todos** tus futuros clientes. Regla dura. *(pendiente)*
2. **Manejo del pago que no calza** (parcial / pago de más). *(✅ ya hecho — ver SESIONES)*
3. **Tope de gasto del bot + anti-abuso.** *(✅ ya hecho — ver SESIONES)*

---

## 📦 Las 5 secciones del panel — lo que FALTA por sección

### 1) 🛒 Ventas y Cobro
- 🟢 **Delivery vs Retiro + costo de envío por zona** — el bot pregunta y suma el envío según tus zonas; total claro. *(medio)*
- 🟡 **Multi-método de pago** (Pago Móvil + Binance/USDT + Zelle + Efectivo) — ofreces lo que el cliente ya usa. *(medio)* — **Plan C**
- 🟡 **Recibo simple del pedido** — al confirmar, el bot manda un recibo limpio (productos, total $ y Bs, método). *(bajo)*
- 🟡 **Catálogo con fotos** — el bot envía la foto del producto cuando preguntan cómo se ve. *(medio)*
- 🟡 **Recordatorio de pago pendiente** (dentro de 24h) — un recordatorio amable si no mandó el comprobante. *(medio)*
- ⚪ **Combos y promociones** — sube el ticket promedio. *(medio)*
- ⚪ **Pedido mínimo** y aviso de monto faltante. *(bajo)*

### 2) 👥 Clientes (CRM simple)
- 🟢 **Memoria: que el BOT lea la ficha** (reconozca al cliente que vuelve por su nombre, pedidos y notas) — la info ya está guardada; falta conectarla al bot. **Plan A (en construcción).** *(medio)*
- 🟡 **Etiquetas de cliente** (Nuevo, Frecuente, VIP, Inactivo) — versión **simple**, sin Chatwoot. *(medio)*
- 🟡 **Recordatorio de recompra** (sugerido, no automático) — para consumibles que se acaban. *(medio)*
- 🟡 **Saludo de cumpleaños** — te avisa para felicitarlo. *(bajo)*
- ⚪ **Tarjeta de fidelidad** (sellos). *(medio)*

### 3) 🎛️ Control del Bot
- 🟡 **Horario de atención** — el bot ya lo INFORMA (vía Conocimiento); falta que avise/bloquee solo fuera de horario. *(bajo)*
- ⚪ **Freno a conversaciones que no son del negocio** — lo pidió Maired (1-sep): "si alguna persona habla algo que no tiene nada que ver con el negocio, construir algo aparte para evitar eso". Hoy existen el tope de gasto y el anti-abuso; esto sería la pieza de TEMA. Diseñar con ella qué corta y qué no antes de construir. *(medio)*

### 4) 📈 Crecimiento y Analítica
- 🟢 **Motor de plantillas HSM** (fuera de 24h) — el ladrillo obligatorio para avisar fuera de la ventana de WhatsApp. *(medio)*
- 🟢 **Aviso a la dueña que SIEMPRE llega** — si llevas +24h sin escribirle al bot, te avisa por plantilla. *(medio)*
- 🟢 **Aprobación humana antes de cualquier envío proactivo** — regla dura de seguridad. *(bajo)*
- 🟢 **Recuperación de pedidos sin pagar** — la venta más fácil de recuperar, hoy perdida en silencio. *(medio)*
- 🟡 **Alertas de pagos olvidados** — te avisa si un pago lleva +2h sin confirmar. *(bajo)*
- 🟡 **Productos más vendidos y ticket promedio** — qué reponer y qué promocionar. *(medio)*
- 🟡 **Campañas / difusión con plantilla** — "promo del fin de semana" a tus clientes. *(medio)*
- 🟡 **Reactivar clientes dormidos** — los que compraban y dejaron de hacerlo. *(medio)*
- ⚪ **Avisar productos nuevos a clientes interesados** — venta segmentada, no spam. *(medio)*
- ⚪ **Resumen diario a tu WhatsApp**. *(medio)*
- ⚪ **Hora pico de pedidos** — cuándo preparar más stock. *(bajo)*

### 5) 🛡️ Operación, Confianza y Tech Provider
- ✅ **Respaldo automático de los datos — ACTIVADO Y RESTAURACIÓN PROBADA (2026-07-12).** Corre en el servidor VIVO como contenedor `masvida-backup` (NO en Coolify: Coolify ignora el `docker-compose`, por eso nunca se había desplegado y el negocio llevaba meses **sin ningún respaldo**). Diario, cifrado con restic, a un bucket R2 **privado**. Probado restaurando de verdad: 40 clientes, 29 productos, 305 mensajes y la personalidad íntegra. Ver `RESPALDO.md`. ⚠️ Si el bot se muda de servidor, **hay que mover el respaldo**.
- 🟢 **Roles: dueña y empleado** — el empleado atiende pero NO confirma pagos ni ve datos bancarios. *(medio)*
- 🟡 **Sesiones seguras** (cierre por inactividad, cambiar contraseña). *(bajo)*
- 🟡 **Salud del negocio (semáforo)** — verde/rojo si WhatsApp se cae o la tasa falla. *(medio)*
- 🟡 **Bitácora de acciones** — quién confirmó qué pago (cuando haya empleado). *(medio)*
- ⚪ **Multi-negocio por número + aislamiento de datos** — el corazón Tech Provider. *(alto)*
- ⚪ **Onboarding de cliente nuevo por coexistencia** — alta de un negocio paso a paso. *(alto)*

---

## 🚦 Orden de construcción (lo que falta)

- ✅ **FASES 0 a 3 — ya hechas** (ver "Ya tienes funcionando" arriba).
- **FASE 4 — Motor de reenganche seguro**: Motor HSM + Aviso que siempre llega + Aprobación humana.
- **FASE 5 — Recuperar plata**: Recuperación de pedidos sin pagar · Alertas de pagos olvidados · Recordatorio de pago.
- **FASE 6 — Vender más a quien ya tienes**: Etiquetas · Más vendidos/ticket · Multi-método de pago · Delivery/envío · Recibo · Horario · Recompra · Campañas · Reactivar dormidos · Sesiones seguras.
- **FASE 7 — Operar en equipo**: Roles dueña/empleado + Bitácora + Salud del negocio.
- **FASE 8 — Convertirlo en fábrica** *(solo cuando asome el 2º cliente)*: Multi-negocio + aislamiento → Onboarding por coexistencia.

---

## ❌ Lo que NO vamos a construir (disciplina anti-sobre-ingeniería)

- **Selector de modelo/temperatura del bot** → palanca técnica que la dueña no debe tocar; el modelo lo decide el proveedor.
- **Segmentos/audiencias combinadas** (tipo email-marketing corporativo) → con etiquetas simples + filtro basta; abre la puerta al spam.
- **Historial de cambios de configuración** (auditoría) → no hay "quién" que auditar con una sola dueña; lo cubre la Bitácora cuando haya empleado.
- **Embudo de conversión multi-etapa** → ruido estadístico con pocos chats al día.
- **Motor de cupones/descuentos** (códigos, vencimientos) → subsistema de e-commerce que complica el cobro; un descuento puntual se aplica a mano.
- **Cualquier envío proactivo automático sin aprobación humana** → regla dura: arriesga la cuenta de Meta de todos los clientes.

---

*Documento vivo. Inspirado en lo bueno de Erwin (mentor) y SellerChat, adaptado a una marca de productos saludables — sin cargar complejidad médica ni de gran escala.*

---
> 🗃️ La historia (PDF, minas, ramas B/C/D, estados de julio) está en `archivo/ROADMAP-historia.md`.
