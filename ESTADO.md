# 📸 ESTADO — qué corre en cada servidor

> **Esta es la ÚNICA fuente de verdad sobre qué versión está viva en cada sitio.**
> El ROADMAP dice qué falta. SESIONES dice qué pasó. **Este archivo dice dónde estamos parados HOY.**
>
> ⚠️ Si este archivo y el ROADMAP se contradicen, **manda este archivo**.

---

## ✅ RESUELTO EL 2026-08-21 — el código de agosto está en GitHub

Del 2 al 21 de agosto, Erwin trabajó desplegando por `docker cp` con Coolify **desconectado a
propósito** (la rama `DESCONECTADO-2026-08-02`), porque sin acceso al repositorio el despliegue
automático le borraba sus cambios manuales. Ese código vivía SOLO en el servidor, sin respaldo.

**Se rescató:** se le dio acceso de colaborador (+ un token fine-grained a los 2 repos), y el
21-ago subió **32 commits del bot + 6 del panel** a `master`. Ya está todo en GitHub y respaldado.

Incluye: arreglos del dinero (DIN-2/4, la "banda ciega del 1%", args del LLM sin filtrar),
el modo DOS agentes con sus 3 bloqueadores cerrados, `/salud`, el `barredor`, redes de asesoría
y salud, memoria que no se olvida a las 24h, y 8 migraciones nuevas (hasta la 034).

---

## 🪦 EL TALLER MURIÓ EL 2026-09-01 — Maired canceló el VPS de Hostinger

> **Decisión de negocio de Maired:** un solo cliente por entregar no justifica dos servidores.
> Antes de apagarse se hizo TODO esto (SESIONES 1-sep (12)):
> - **Respaldo final COMPLETO** de su BD bajado a la máquina de Maired:
>   `C:\Developer\AI\Proyectos\respaldos-masvida\taller_FINAL_antes_de_apagar_20260901.dump`
>   (3MB) + la personalidad en texto suelto al lado.
> - **Comparación dato a dato taller↔producción: IDÉNTICOS** en catálogo (32/37 con sabores),
>   personalidad (mismo hash), conocimiento, zonas, anticipación, fotos y métodos de pago. La
>   ÚNICA diferencia de fondo: `modelo_ia` (taller probaba con Sonnet 4.6; producción tiene
>   Haiku 4.5) — decisión pendiente de Maired.
> - **El pipeline quedó sin taller**: un push a master solo corre la CI; desplegar producción
>   exige `workflow_dispatch` + elegir "produccion" (default "no").
>
> **Qué reemplaza al taller:** la CI en cada push · los 784 tests y bancos en LOCAL ·
> producción con LISTA BLANCA como campo de pruebas seguro (nadie más recibe nada) · los 27
> bancos a mano en producción tras deploys grandes (limpian sus datos; 27/27 el 1-sep).
> ~~El número de pruebas de la agencia (+57 313 293 3806) quedó LIBRE~~ → **actualizado esa
> misma noche: ya NO está libre — quedó conectado al nuevo ENTORNO DE PRUEBAS de Enova**
> (bloque siguiente). Sigue FUERA del sistema de la clienta, como manda la regla.

## 🏭 EL ENTORNO DE PRUEBAS DE ENOVA — nació el 2026-09-01 (noche) y RESPONDE

> **El reemplazo real del taller** (SESIONES 1-sep (14)): mismo código, misma BD rescatada,
> mismo número de la agencia — pero en infraestructura PROPIA de Enova, aislado de netcup.
> Whuilianny no ve nada: servidor, BD, número y WABA separados de producción (verificado).

| | 🏭 PRUEBAS (Enova) |
|---|---|
| **Servidor** | VPS del socio de Enova `152.53.194.89` · Coolify propio `coolify.enovagroup.tech` (proyecto `masvida-pruebas`) |
| **Qué corre** | bot + worker en `master a798aac` (merge del PR #18) + panel + PostgreSQL 16 + Redis 7 · **auto-deploy OFF** en las 3 apps — deploy SOLO manual |
| **Número** | **+57 313 2933806** (WABA "Enova Soporte", SEPARADA de la de la clienta) |
| **Webhook** | re-apuntado por Graph API (`/{waba}/subscribed_apps` + `override_callback_uri`) → `https://jthc51nxqitd9opc8ywioocr.152.53.194.89.sslip.io/webhook/whatsapp` |
| **BD** | dump FINAL del taller restaurado: 36 migraciones · 6 clientes · 32 productos · 10 conocimiento |
| **Modelo IA** | `anthropic/claude-sonnet-4.6` (el aprobado por Maired en el taller) |
| **Salud** | `https://jthc51nxqitd9opc8ywioocr.152.53.194.89.sslip.io/salud` — verificado 3-sep 12:35 ET: `ok`, Meta GREEN, saldo $2.8987 |
| **Prueba de fuego** | ✅ "Hola" de Maired → respuesta en 5,8s **con la memoria del taller** (13 mensajes rescatados de Postgres) |
| **Panel** | ✅ **`https://panel-masvida.enovagroup.tech/login`** (dominio propio + HTTPS Let's Encrypt, desde el 2-sep; el sslip `http://h14aei…sslip.io` sigue vivo de respaldo). Rebuild con el bot por https: hecho. Login: `admin@masvidaconsciente.com` + la clave de siempre del taller. |
| **DNS (Namecheap)** | `panel-masvida` y `api-masvida` .enovagroup.tech → `152.53.194.89` (los creó Maired el 2-sep). ⚠️ `api-masvida` apunta al VPS pero **ningún servicio lo atiende todavía** — ver el bug del catálogo abajo. |

> ✅ **CATÁLOGO PDF ARREGLADO EN PRUEBAS (3-sep 12:35 ET; SESIONES (18)):** PR #18 fusionado y
> desplegado (`a798aac`) en bot + worker; `PUBLIC_BASE_URL` propia del entorno, creada cifrada por
> la API de Coolify. El archivo público responde **200 · `application/pdf` · 2.803.311 bytes ·
> `%PDF-`** y `/salud` sigue en `ok`. ⏳ Falta confirmar el último tramo pidiéndolo por WhatsApp al
> número de pruebas. 🔴 **Producción conserva la misma mina** hasta definir allí
> `PUBLIC_BASE_URL=https://api.masvidaconsciente.store` y redeployar con OK explícito de Maired.
> **NO asignar `api-masvida.enovagroup.tech` al bot de pruebas**: mezclaría los entornos.

## Última verificación: **2026-09-01 (~02:30 ET) — LA PROMOCIÓN A PRODUCCIÓN SE HIZO (y hubo incidente de seguridad, contenido)**

> 🚨 **El 1-sep, en medio de la promoción (la hizo ChatGPT con Maired), apareció un MINERO
> (`xmrig`) dentro del panel VIEJO de producción** (Next.js 15.1.3 con RCE público, sin
> actualizar desde julio). Se contuvo: panel comprometido detenido, panel nuevo parchado
> (Next 15.5.24, `npm audit` 0), servidor ENDURECIDO (SSH solo llave, fail2ban, firewall
> nftables, deploy por SSH cifrado). **Verificación CRUZADA por Claude, punto por punto: todo
> real.** Detalle y lo que queda: SESIONES 2026-09-01 (6) y (7) + el relevo de ChatGPT.

> ⚠️ **La columna TALLER de esta tabla es HISTÓRICA** (el taller murió el 1-sep). El entorno
> de pruebas VIVO es el de Enova, en el bloque de arriba.

| | 🏪 PRODUCCIÓN | 🧪 TALLER |
|---|---|---|
| **Servidor** | netcup `152.53.89.118` (endurecido 1-sep) | Hostinger `2.25.139.106` |
| **Quién le escribe** | las clientas reales | número de la agencia: **+57 313 293 3806** |
| **Bot: versión** | ✅ **`f4e200c`** (1-sep) — AL DÍA: trae todo agosto + el plan A→D completo. 36 migraciones aplicadas, sin drift | ✅ **`f4e200c`** (mismo master) |
| **Panel: versión** | ✅ **`608f61c`** parchado (Next 15.5.24 / React 19.0.8, audit 0 vulns) | `b9a97c8` + el parche va llegando por master |
| **Modelo IA activo** | ✅ `anthropic/claude-haiku-4.5` · **saldo IA $4.11** (recargado) | `anthropic/claude-sonnet-4.6` (Maired probando) |
| **Modo del agente** | UN agente | UN agente |
| **Lista blanca** | ✅ **ACTIVA: 1 solo número** (`NUMEROS_PERMITIDOS=573005690062`, extra=None). ⚠️ `bot_activo` no existe en la config ⇒ el código lo trata como ENCENDIDO: **lo que protege a las clientas es la lista blanca** | ✅ activa |
| **Bot en el mercado** | ❌ NO — cerrado a clientas reales (regla absoluta del relevo: no abrir sin autorización expresa de Maired) | pruebas |
| **Número real** | `+58 424-7047595` — 🔴 **COEXISTENCIA: Whuilianny lo ve en VIVO en su celular.** Cualquier mensaje ahí (aunque sea de prueba, aunque el remitente esté en la lista blanca) le suena el teléfono a ELLA. NUNCA probar aquí sin su OK de horario — ver CLAUDE.md §3 | `+57 313 293 3806` (agencia) — sin este riesgo |
| **Bancos** | ✅ **27/27** (1-sep, tras cargar el Zelle que faltaba — ver abajo) | ✅ 27/27 |
| **Datos** | 32 productos / 37 variantes / 277 clientes / 13.890 mensajes / **0 pedidos** (⚠️ confirmar con Maired que el 0 es esperado — hizo eliminaciones manuales; hay respaldo pre-migración en `/root/masvida-migration-backups/20260901_040209`) / personalidad 7.331 car | 32/37 + datos de prueba |
| **Respaldos** | diario cifrado (7 semanas corriendo) + pre-migración + pre-endurecimiento (con SHA-256) + copia local `C:\Developer\AI\Proyectos\respaldos-masvida` | D4 sigue abierta |

✅ **CERRADO el 1-sep: el Zelle de producción.** Producción YA tenía Pago Móvil, Transferencia
(Banesco) y Binance idénticos al taller — solo faltaba la fila Zelle (titular Luis Guevara,
correo familiapenazabala@gmail.com). Copiada con los datos EXACTOS del taller (leídos primero,
sin inventar nada), verificada con el banco específico y con el paquete completo: **27/27**.

✅ **CORREGIDO el 1-sep: el riesgo de Meta/OpenRouter/R2 era menor de lo que se dijo primero.**
Verificado con `docker inspect` (variables REALES, no de memoria) + el código del dashboard: el
panel SOLO tiene `NEXT_PUBLIC_API_URL` (pública por diseño) — CERO secretos. Esas llaves viven
únicamente en el contenedor del BOT, que nunca se comprometió (solo el panel viejo). Rotarlas
sigue siendo buena higiene (la deuda D5, "antes de abrir con clientes reales") pero **NO es una
urgencia nueva que dejó el minero** — se hace en su momento, sin apuro por el incidente.

🔴 **LO QUE QUEDA DEL INCIDENTE (además de los 12 pendientes del relevo de ChatGPT):**
1. **Rotar la contraseña del panel + el JWT** (punto 4 del relevo de ChatGPT) — cierra cualquier
   sesión que hubiera quedado abierta durante el incidente. 5 minutos, pero necesita a Maired
   presente (la desloguea a ella también).
2. **Pruebas de humo con el número real** (checklist de la meta, punto 5) — el siguiente paso
   que de verdad avanza el proyecto; la seguridad ya está estable.

### 🔬 2026-08-24 (2) — LA PRUEBA EN VIVO DE MAIRED: 3 bugs de código y EL TECHO DEL MODELO

Maired probó en vivo (00:35–00:52) con el bot sin redes de estilo, **monitoreado turno a turno**.
Su veredicto: *"tiene casi los mismos errores"*. Contado contra la BD: **1 era del código · 1 es
decisión suya · 4 son del modelo.** Las quejas de estilo del sábado SÍ se resolvieron (ficha
**2→0** · globos **6→3** · cifras **exactas las 5 veces**) y **la venta CERRÓ**: pedido **#1918**
($8+$2=$10 · zona centro $2 · martes 25 · 7.846,63 Bs con tasa BCV 784,6633 · $6,40 efectivo).

🔴🔴 **Lo que ella NO vio: el modelo inventó DATOS BANCARIOS dos veces** (cédulas 04165892147 y
04165432127, ambas falsas y distintas). **Los dos guardias del dinero los frenaron las dos veces** —
sin ellos, una clienta habría pagado a una cuenta inexistente. Y **las repeticiones que ella
reportó son la ESTELA de esos rescates**: al rehacer el turno, el modelo re-cita todo.

🟢 **3 BUGS DE CÓDIGO, arreglados con su caso literal en rojo primero y desplegados:**
`e5ef54b` la **madrugada no existía** en el saludo (le decía "son las 00:35 (buenos días)") ·
`28facc1` un **aviso falso a Whuilianny a las 00:39 AM** por un mensaje perfecto ·
`5a2a07f` el **personaje roto** (le citó `proxima_fecha_entrega` a la clienta).

🔴🔴 **EL VEREDICTO, y cierra el diagnóstico:** se verificaron **las 10 conductas falladas contra el
prompt VIVO** (55.174 car dentro del worker) — **las 10 están escritas**, varias en MAYÚSCULAS
(*"BREVEDAD ante todo"*, *"LAS FECHAS SE CONSULTAN"*, *"NO REPREGUNTES LO QUE YA SABES"*, *"LOS
DATOS DE PAGO… jamás de memoria"*). **No es "nadie se lo ordenó": se le ordenó lo contrario y lo
hizo igual.** `_REGLAS` **NO se tocó a propósito.** ➡️ **El trabajo #1 ya no es código ni prompt:
es SUBIR DE MODELO o encender el modo DOS**, midiendo con `smoke_guion_maired.py`.

**650 tests · 24/24 bancos local · 27/27 VPS · auditor 104/0 NO CUMPLE · saldo ~$0,93.**
📄 Informe para ella: `MAS VIDA/INFORME_MAIRED_2026-08-24.html` (los 30 globos anotados uno por uno).

### 🔪 2026-08-24 — LAS 3 REDES DE ESTILO FUERA, Y EL EXPERIMENTO MEDIDO (`ee9058b`)

**Decisión de Erwin:** quitar las redes que REESCRIBEN al modelo (pitch · ficha repetida ·
inserción de resúmenes) y dejar que el LLM razone solo — las 3 quejas de Maired del 23-ago las
causaban esas redes, no el modelo (la de la ficha llegó a MUTILAR el cobro: "7.799,52 Bs" salió
como "799,52 Bs", entregado). Las redes del DINERO/VERDAD/SALUD/Meta se quedan TODAS.

**Medido con el guion exacto de Maired, antes y después:** ficha repetida **2→0** · confirmación
repetida **3→0** · "retiro o delivery" **3→1** · globos por turno **6→3** · llamadas LLM
**~1/turno** (eran hasta 3) · cifras intactas · el "por qué no hoy" explicado **a la primera**.
🔴 **Lo que quedó a la vista (la clase P0, y ahora sin ruido):** la venta NO cerró — el modelo
bloqueó el registro pidiendo el **nombre** (0 pedidos en la BD). El estilo ya está; el cierre es
del modelo. Palancas: inyectar el ESTADO DEL PEDIDO (arreglo de fondo) · modo DOS · subir modelo.

**636 tests · 24/24 bancos local · 27/27 VPS · auditor: 104 requisitos, 90 CUMPLEN, 0 NO CUMPLE
(las 2 conductas pasaron de "red" a "conducta del LLM": 13→11 redes).**

### 🔬 2026-08-23 (3) — LA PLANTILLA DE MAIRED, AUDITADA EN UN COMANDO (`3cb7d8f`)

`scripts/auditar_plantilla.py` — **106 requisitos ejecutados contra el sistema vivo**, no leídos:
**92 CUMPLEN · 0 NO CUMPLE · 1 parcial · 5 piezas sin construir · 6 datos de Whuilianny · 2 N/A.**

🔴 **Dos huecos que cerró, y el primero era de venta:** la plantilla ofrece *hogaza*, *rústicos* y
*opciones veganas* que NO están en el catálogo, y el bot los contestaba con **Arepas Andinas** y
**Yogurt Kéfirado**, en tono de certeza (calce espurio por la descripción, 0.43). Arreglado con un
piso propio para la descripción; los typos siguen intactos. Y la plantilla insiste dos veces en que
el bot **no puede conceder una entrega fuera de horario por su cuenta** — no estaba escrito en
ninguna parte del prompt. Ya está.

🔴 **«¿Solo falta el modelo?» NO.** De la estructura no falla nada, pero quedan **N1** (pago 30/70),
**N2** (delivery extraordinario) y **N4** (día flojo) —las tres aplazadas por el propio documento— y
los **datos de Whuilianny**: 4 productos anunciados que no existen, 0 feriados, 9 sin foto, sabores
5/37.

### 🟢 2026-08-23 (2) — EL PROMPT, PULIDO Y DESPLEGADO (era el pendiente #1)

La causa raíz del *"bot bruto"* atacada donde estaba: **`_REGLAS` 31.418 → 25.567 car (−19%)** y el
**prompt completo 60.390 → 54.545** (~15.100 → ~13.600 tokens). Se cerraron **5 contradicciones**, y
las dos peores no las había encontrado nadie:

1. 🔴🔴 **`_REGLAS` ordenaba *"coordinas la entrega/envío"* al recibir el comprobante** — el día
   DESPUÉS de que el código pasara a ESPERAR el clic de «Pago aprobado». Instrucción del turno y
   regla permanente diciendo lo contrario, **un día desplegado con los 645 tests verdes**.
2. 🔴 **8 ejemplos del prompt usaban los `¿` y `¡` que el propio prompt prohíbe.**
3. *"Manda VARIOS mensajitos"* vs *"1 o 2 globitos"* de la personalidad → los **6 globos de un
   turno** que contó Maired.
4. Las dos primacías (con su desempate nuevo; las etiquetas se conservan por el modo dos).
5. Cuatro reglas repitiendo lo mismo.

Además: **orden de prioridad** (VERDAD > BREVEDAD > CIERRE) · **7 bloques por momento** · fuera lo
que ya dice la personalidad · y el **paso 11 de la plantilla** (resumen final), que era N5.

**654 tests · 24/24 bancos en local · 10 reversiones → 10 rojas.**
🟢 **DESPLEGADO** (`d9bce71`, con el PAT que pasó Erwin): CI ✅ · paso `desplegar` ✅ · **LOS BANCOS
✅** · producción `skipped`. **Checksum 135/135 bit a bit** en bot Y worker, y el prompt VIVO medido
dentro del worker: **54.545 car / ~13.636 tokens**, con las 4 invariantes del refactor comprobadas
ahí mismo.
🔴 **Falta medirlo en vivo** (la misma conversación de Maired, antes y después): **no se hizo porque
el saldo está en $1.70** y la medición son ~24 turnos de los ~106 que quedan. Recargar primero.

### 🔴 2026-08-23 — EL SALDO DE IA CRUZÓ EL UMBRAL: `/salud` está en `degradado`

**`/salud` no dice `ok`. Dice `degradado`, con `fallos: ["saldo_ia"]`.** El saldo de OpenRouter está
en **$1.70** y el umbral de la sonda es $2.00. A ~$0.016 por turno son **~106 turnos**, y cuando se
agote el bot **no da error: deja de responder**. Desde el chat es idéntico a "el bot está roto".
**Solo Erwin puede recargarlo** (openrouter.ai → Credits). Es lo único que puede arruinar una demo.

### 🟢 2026-08-23 — la auditoría exhaustiva, el entorno local y la causa raíz del "bot bruto"

7 commits más desplegados: `3d88cd1..c5ba1c4`. Lo verificado en vivo el 23-ago a las 17:40:

| | |
|---|---|
| Bot y worker | 🟢 **`c5ba1c4`** en el SHA de la imagen de los DOS contenedores |
| CI · paso `desplegar` · LOS BANCOS | 🟢 verdes en los **2 últimos push** (y producción `skipped`) |
| Tests | 🟢 **645** (eran 566) |
| Bancos | 🟢 **27/27** en el VPS · **24/27 en LOCAL** con `./banco_local.sh`, antes de desplegar |
| `/salud` | 🔴 **`degradado`** por el saldo · Postgres, Redis, Meta (**GREEN**), barredor, tasa y modelo en `ok` |
| Datos | 🟢 32 productos / 37 variantes / 34 media / 10 conocimiento / 35 migraciones |
| **`dias_anticipacion`** | 🟢 **CARGADO**: 16 en 0 · 12 en 1 · 4 en 2 *(estaba en 0 en los 32)* |
| **Zona centro** | 🟢 **$2.00** *(cobraba $3; la plantilla pide $2)* · oeste $5 · retiro $0 |

**Lo que cambió para la clienta:** a quien pide **"vegano"** ya no se le ofrece manteca de cochino
ni hígado deshidratado (freno de seguridad alimentaria) · la red de la salud caza el celíaco en sus
4 formas, no en 1 · el **recibo ya no sale duplicado** (lo insertaba el CÓDIGO, no el modelo:
comparaba texto literal y el modelo parafraseaba) · el cobro trae **desglose de 4 líneas** · y el
bot **ESPERA el clic de «Pago aprobado»** antes de coordinar la entrega, avisando a la dueña
(pasos 8-9 de la plantilla — ya reflejado en `CLAUDE.md` §3).

🔴 **EL TRABAJO #1 DE LA PRÓXIMA SESIÓN, y lo dejó pedido Erwin: PULIR EL PROMPT.** La causa de que
el bot "repita y suene robótico" no es un bug: son **44 reglas escritas por acumulación, varias
contradiciéndose**, sobre **Haiku 4.5** (el modelo más pequeño). Medido en vivo: **60.390 caracteres
(~15.100 tokens)** — y sigue creciendo (eran 59.381 ayer). Método y los 5 frenos que hay que
respetar: `prompt_proxima_sesion.md` §5 → P-PROMPT.

🟡 **Y un detalle que conviene decidir antes de la próxima prueba con Maired:** su chat **ya no está
en cero**. En la BD hay **58 mensajes, 4 pedidos y 6 clientes** (5 son `__simulador__`, 1 es ella)
de las pruebas del 22/23-ago. Si quiere volver a probar como clienta nueva, hay que limpiarlo otra
vez — con ensayo de ROLLBACK, y **acordándose de Redis** (`hist:`, `abuso:`, `cobro:`).

### 🟢 2026-08-22 (noche) — la plantilla de negocio de Maired, aplicada y viva

Los **9 commits** de la plantilla están desplegados y verificados: `a524822..15fb81f`.

| | |
|---|---|
| CI (`ruff` · `compileall` · `pytest`) | 🟢 **566 tests** verdes |
| Deploy del taller | 🟢 automático por el push · **producción `skipped`** (el candado funciona) |
| **LOS BANCOS** | 🟢 **27/27**, corridos por el vigilante tras el deploy |
| Checksum `master` vs los DOS contenedores | 🟢 **6/6 bit a bit** |
| `/salud` | 🟢 `ok`, `fallos: []` |
| Datos | 🟢 32 productos / 37 variantes / 34 media / 10 conocimiento / 35 migraciones — **sin cambios** |

**Qué cambió para la clienta:** el 20% + **delivery gratis** pagando en efectivo (y ya NO a Zelle
ni Binance) · el bot **consulta el calendario** en vez de inventarse las fechas · ya no dice que
revisó una cuenta que no tiene · saluda devolviendo la pregunta · no repite la ficha · las
alergias salen de la ficha del producto, nunca de una promesa general · el pie de foto ya no
lleva la lista de ingredientes. En la BD: **asesora** (no "asistente") y ya no devuelve el
"mi amor".

🔴 **Y DOS COSAS QUE SE DESCUBRIERON DESPLEGANDO, y conviene no olvidar:**

1. **El deploy de `a524822` (sesión de la tarde) había FALLADO en silencio.** El runner no pudo
   alcanzar el puerto 8000 de Coolify (`curl: (28) Timeout`, tres reintentos) porque Coolify
   estaba reiniciándose por el deploy anterior. **La CI salió verde, el paso TALLER en rojo, y
   nadie lo miró**: el taller se quedó con el código viejo mientras todo el mundo creía que
   estaba al día. Es el panel de los 13 días otra vez — *un automatismo que falla en silencio es
   peor que no tenerlo*. **Mirar el paso `desplegar`, no solo la CI.**
2. **Los bancos hacen su trabajo.** Salieron 2 rojos en el primer deploy, los dos míos: el conteo
   de tools (12 → 13) y —el bueno— `probar_carril_dinero`, que **fijaba la frase de la mentira**
   ("NO te aparece hecho a TU cuenta"). El banco defendía el bug. Reescrito para comprobar la
   intención, no el texto.

### 🔴 Lo que importa

**Producción sigue en la versión del 14-jul.** Todo el trabajo de agosto (y de fin de julio) está
en GitHub pero **aún no promovido a producción**. El bot NO atiende clientas reales todavía: es una
decisión deliberada hasta que esté listo para la entrega.

**🟢 RESUELTO EL 2026-08-22 (tarde): el taller ya está al día con `master`.** Los 5 commits que
estaban atascados en local (la CI arreglada · los dos huecos de la red del cierre · la RED DEL
TAMAÑO ADIVINADO del carril del dinero · el tercer caso del espejeo y los dos huecos que
destaparon las conversaciones reales) **se subieron y se desplegaron**: checksum 5/5, **27/27
bancos verdes**, `/salud` en `ok` y cero regresión de datos reales. Y desde `0426f3b` el
despliegue del taller **es automático en cada push** — el taller corre hoy **`aef1042`**.

**🔴 Lo que se descubrió el 2026-08-22 (2) y conviene no olvidar:** la **CI llevaba 3 commits en
ROJO** (`09f4253`, `13a064f`, `6c5d14c`) por 4 errores de `ruff`, y como `ruff` es el PRIMER paso
del job, `compileall` y `pytest` quedaban **skipped**: los 453 tests **no se ejecutaron ni una vez**
en esos tres commits. Ya está verde. Si el CI sale rojo, **no es cosmético: apaga la única puerta
que valida antes de desplegar.**

---

## ⚠️ CÓMO SE DESPLIEGA HOY (cambió OTRA VEZ el 2026-08-22 — leer antes de tocar nada)

🟢 **UN PUSH A `master` DESPLIEGA EL TALLER SOLO, si la CI está VERDE.** Lo pidió Erwin el
22-ago y **reemplaza su decisión del 2-ago** ("ningún push despliega nada"). Va por **GitHub
Actions**, no por el webhook de Coolify: el job `desplegar` lleva `needs: verificar`, así que con
`ruff`/`compileall`/`pytest` en rojo el `curl` a Coolify **no llega a existir** (lección L41).

🔒 **PRODUCCIÓN SIGUE SIENDO SOLO A MANO.** En un `push` el destino se **fuerza** a `taller`
(`env.DESTINO` en `deploy.yml`); `produccion` solo sale de un `workflow_dispatch` que un humano
lanzó y eligió. Un push no puede tocar a las clientas reales ni por accidente.

⚠️ **`is_auto_deploy_enabled` sigue en `false` en las 3 apps, y así se queda.** No es un olvido:
el webhook de Coolify dispara al recibir el push **sin esperar a la CI**, así que encenderlo
dejaría dos despliegues compitiendo por el mismo push y uno de ellos sin puerta. El automatismo
vive en `deploy.yml`, no en Coolify.

✅ **COOLIFY RECONECTADO EL 2026-08-22** (lo pidió Erwin). Las 3 apps volvieron a `git_branch =
'master'`.

*(Estado anterior guardado en el VPS: `/root/COOLIFY_ANTES_2026-08-21.csv`.)*

⚠️ Coolify reconstruye desde GitHub: todo archivo editado a mano DENTRO del VPS se pierde en el
siguiente despliegue. **Nada se edita en el servidor** (regla dura de CLAUDE.md §3).

---

## 🧯 Si algo falla, revisa esto EN ORDEN (antes de preguntarle a nadie)

| Síntoma | Revisa, en orden |
|---|---|
| **El bot no contesta** | 1. Abre el `/salud` del entorno (pruebas: la URL del bloque 🏭 de arriba; producción: `api.masvidaconsciente.store/salud`) → ¿todo `ok`? ¿`saldo_usd` > 0? · 2. Panel → ¿`bot_activo` encendido? · 3. ¿Ese número está en la lista blanca? · 4. ¿Ese chat está pausado (bandeja / "atiendo yo")? *(La URL vieja `api-masvida.enovagroup.tech` murió con el taller.)* |
| **Se acabó el saldo de IA** | `/salud` → `saldo_ia`. Recargar en OpenRouter. Sin saldo el bot NO responde. |
| **Contesta raro o inventa** | 1. Panel → Configuración → ¿qué MODELO está activo? (hoy `anthropic/claude-haiku-4.5`) · 2. ¿Alguien editó la Personalidad? (vive en la BD) · 3. NO culpar al modelo primero: sospecha del código/datos. |
| **Cobro o precio mal** | Verificar **en la BD**, no en el chat: `SELECT items, total FROM pedidos`. |
| **Hice push y no pasó nada** | Es lo esperado: el deploy es a mano desde el 2-ago. Hay que lanzarlo desde Coolify/Actions. |
| **El panel no carga datos** | ¿El build tiene `NEXT_PUBLIC_API_URL`? (sin eso: "Failed to fetch"). |

---

## Cómo verificarlo tú misma (30 segundos)

```bash
gh run list --limit 10          # qué se desplegó y cuándo
git log origin/master -5         # últimos cambios en GitHub
```

---

## Historial de verificaciones

| Fecha | Producción | Taller | Nota |
|---|---|---|---|
| 2026-08-23 | `7e80b8a` (14-jul) | **`c5ba1c4`** | 🟢 **7 commits más, desplegados solos por el push** (`3d88cd1..c5ba1c4`). **645 tests** (eran 566) · **27/27 bancos** en el VPS y **24/27 en LOCAL** con el nuevo `banco_local.sh` —los bancos por fin corren ANTES de desplegar, y en su primera corrida cazaron 2 bugs que el VPS no había visto. La auditoría exhaustiva sacó **200 requisitos** del documento de Maired (una revisión a mano previa había sacado 51), y **3 de los 4 fallos graves eran de arreglos de ESE MISMO DÍA**: código nuevo, con tests en verde, que no hacía lo que decía (L65). El más grave: a quien pedía **"vegano"** el bot le ofrecía **manteca de cochino e hígado deshidratado** (L63). **DATOS cargados:** `dias_anticipacion` (16/12/4, estaba en 0 en los 32) y zona centro **$3 → $2**. 🔴 **`/salud` en `degradado`: el saldo de OpenRouter cayó a $1.70** (umbral $2.00, ~106 turnos). 🔴 **Pendiente #1:** pulir el prompt — **60.390 car / 44 reglas contradictorias sobre Haiku 4.5** es la causa raíz del "bot bruto" (L68). |
| 2026-08-22 (tarde) | `7e80b8a` (14-jul) | **`aef1042`** | 🟢 **Los 5 commits atascados: subidos y desplegados.** Push con el token de Erwin (`c0a2f71`, CI **verde**) → deploy por la API de Coolify (worker primero, bot después). **Checksum 5/5** en los DOS contenedores · **27/27 bancos verdes** corridos uno por uno · `/salud` `ok` con `fallos: []` · **cero regresión de datos reales** (32 productos / 37 variantes / 2 pedidos / 34 media / 10 conocimiento / 35 migraciones, idéntico antes y después). ⚠️ `clientes` pasó de 2 a 3, y **es por diseño**: los bancos crean el cliente de prueba `__simulador__` (excluido de la lista del panel) — no es una regresión, pero **por eso `clientes` no sirve como métrica de línea base después de correr bancos.** Y **el despliegue del taller pasó a ser AUTOMÁTICO en cada push** (`0426f3b`), con la CI como puerta y producción todavía solo a mano; validado dos veces seguidas (`0426f3b` y `aef1042`). |
| 2026-08-18 | `7e80b8a` (14-jul) | desconectado de GitHub | Descubierto: Coolify en rama `DESCONECTADO`, código de agosto solo en el servidor. |
| 2026-08-21 | `7e80b8a` (14-jul) | código de agosto (en GitHub) | Rescatados 32+6 commits a `master`. Deploy ahora es manual. Falta reconectar y promover a producción. |
| 2026-08-22 (2 y 3) | `7e80b8a` (14-jul) | `13a064f` (**master va 5 commits por delante, SIN desplegar**) | 🔴 **La CI llevaba 3 commits en ROJO y los 453 tests no corrían** (4 errores de `ruff`; `pytest` quedaba *skipped*) — arreglado. Cerrados los 2 huecos de la red del cierre (la HORA + la lista y la pregunta en frases distintas), el 3er sitio que empujaba a pedir el sabor (el schema de `opciones`), **la RED DEL TAMAÑO ADIVINADO** (P0.5, carril del dinero), y —cruzando los dos documentos de Whuilianny con el código— el **tercer caso del espejeo (cliente MOLESTO)**, las peticiones **sin signo de pregunta** y `asesorar`. **515 tests** (eran 453) · **18 reversiones → 18 rojas** · cero cambios en la BD. |
| 2026-08-22 | `7e80b8a` (14-jul) | **`13a064f`** | 🟢 **Coolify RECONECTADO** (rama `master`, auto-deploy OFF) y primer despliegue por Coolify desde julio. Cola de media (el texto sale antes que la foto) + red del cierre. **27/27 bancos verdes** corridos uno por uno · 453 tests · `/salud` 8/8 · checksum 5/5 · **cero regresión de datos** (32/37/2/34/10/2/35 idéntico antes y después). |
| 2026-08-21 (2) | `7e80b8a` (14-jul) | **`4a482c5`** | 9 bugs cerrados y desplegados ese día (memoria de 24h + su puerta de atrás, índice duplicado, 2 bancos del calendario, la banda ciega del 1%, args del LLM sin filtrar, saludo al volver, y 3 cegueras de las fotos). Modelo devuelto a **Haiku 4.5**. Nº de Maired añadido a la lista blanca. 421 tests · `/salud` 8/8. |

---

*Actualizar este archivo cada vez que se despliegue a producción. Es corto a propósito:
si crece, deja de leerse — y entonces vuelve a no servir para nada.*
