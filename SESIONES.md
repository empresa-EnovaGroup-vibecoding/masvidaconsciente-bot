# 📔 SESIONES = lo que YA hicimos (el diario de másvida)

> **Dos prácticas adoptadas (inspiradas en el sistema del mentor Erwin), para no romper lo que funciona:**
>
> 1. **Registrar cada sesión** en este archivo: qué se cambió, por qué, y qué quedó pendiente.
> 2. **Cambios de base de datos con red de seguridad:** antes de tocar datos reales, probar el cambio dentro de una transacción y hacer **ROLLBACK** (deshacer) para verificar que está bien. Nunca alterar datos de producción sin ese ensayo previo.

---

## ⏳ Pendientes importantes (no olvidar)

- 🧠 **Costo del bot:** terminar la comparación con conversaciones reales y medir modelos más baratos
  antes de sustituir Sonnet. La documentación ordenada ayuda a trabajar; no reduce por sí sola los
  tokens del WhatsApp.
- 📚 **Levantamiento:** el primer barrido de 42 agentes clasificó las 313 conversaciones; la auditoría
  detallada continúa desde los artefactos locales ya producidos, sin repetir esa corrida costosa.

---

## 2026-09-24 (40) — 🔓 EL BOT RETOMA SOLO: la pausa de la dueña deja de ser eterna, con condiciones (PR5)

**Por qué (decisión de Maired 24-sep, AskUserQuestion "se calla solo y vuelve solo"):** la pausa
`pausado_por='dueña'` no expiraba nunca — es la razón por la que producción está muda en 308/322 chats
(ella contesta a todos por su celular y jamás entra al panel) y por la que hoy no se pudo probar el bot
sin ir a devolver el chat a mano. "Habla siempre y solo ella lo calla" (SellerChat) es lo contrario y
choca en 6 de 7. El punto medio: el bot se calla cuando ella escribe (se queda), pero vuelve solo si
pasan N horas sin respuesta de ella, el cliente vuelve a escribir y no hay propuestas sin confirmar.

**Qué quedó hecho (bot, rama `retorno-automatico`):**
- `_horas_retorno_auto()` (tasks.py, patrón de `_bot_activo`): lee `retomar_auto_horas` (float, admite
  decimales y coma); ausente / vacío / ≤0 / no numérico / error → **0.0 = apagado**. Fail-CLOSED al revés
  de `_bot_activo`: un error NO despausa (hablar encima de ella es peor que no volver).
- `_retorno_automatico(telefono)` (tasks.py): en UNA sesión con `bloquear_cliente` (advisory lock);
  condiciones TODAS: flag>0 · `pausado_por=='dueña'` · no privado · bot_pausado · último `Mensaje rol='owner'`
  existe y > N h · cero `Intervencion` pendientes con motivo en `MOTIVOS_INFORMATIVOS`. Si se cumplen:
  despausa, cierra los `chat_tomado` pendientes con la nota "El bot retomó solo: {N} h…" (sin motivo nuevo),
  commit, `rc.notificar_conversacion` → el panel pasa a "Alejandra atiende" en vivo. Cualquier error → False.
- Gancho llamado en `_procesar` y `_responder_y_enviar` ANTES de `_cliente_pausado` (dentro del turno del
  cliente: jamás proactivo, jamás desde el barredor).
- Config `retomar_auto_horas` en `CLAVES_CONFIG` + `CLAVES_PROVEEDORA` (Maired es proveedora).
- **No se toca:** el barredor sigue sin despausar a nadie, `_lo_paso_una_persona`, `_estado_pausa`,
  `_retomar`, `pausar_bot_cliente`, `resolver_intervencion`. Un pago sigue siendo humano.
- Tests `test_retorno_automatico.py` (21): apagado/bot/privado/no-pausado → False; reloj (aún no / sin
  mensaje owner / propuesta pendiente) → False; camino feliz → despausa + nota + notifica; BD caída → False;
  lector (2 / 0.05 / 1,5 / vacío / abc / 0 / -3 / None); por fuente: el gancho va antes del freno en los dos
  carriles, la clave es de proveedora, el barredor no hace UPDATE clientes. Suite **1286/0**.

**Panel (rama `retorno-automatico-panel`):** `Pedido`→ no; en `ConfiguracionNegocio` la clave
`retomar_auto_horas` (proveedora); en Configuración, sección "El bot retoma solo · solo Enova" con un campo
numérico (0 = nunca) y la nota de que el pago siempre lo confirma ella y que un chat donde el bot pidió
ayuda no se reactiva solo. tsc limpio.

**Limitación documentada:** el botón "Yo atiendo" también escribe `pausado_por='dueña'` — indistinguible del
eco de su celular, así que una pausa puesta a mano SÍ caduca a las N h si ella escribió algo en ese chat.
Un "sin plazo" real necesitaría una columna aparte; fuera de este PR.

**Sigue (con "dale"):** fusionar bot + panel → desplegar pruebas (bot+worker+dashboard) → la prueba de
Maired SIN botones: N=0,05 (3 min) en Configuración → escribe como Whuilianny → espera 3 min → escribe como
clienta → el bot entra solo → vuelve a escribir como Whuilianny → se calla → deja N en 2. Luego PR6 replay.

## 2026-09-24 (39) — 🗣️ LA BOCA: la despedida del bot volvía a tragarse cuando él mismo se pausaba (PR4c)

**Lo que destapó la prueba de Maired (12:29-12:37 VET, pruebas en `60baacc`):** escribió como Whuilianny
("Te anoté 2 quesillos son 16$") → el bot se pausó por ella (diseño: `pausado_por='dueña'`, no expira) →
confirmó la propuesta (pedido #3131; el candado del gemelo no frenó porque el #3130 tiene >24 h: correcto)
→ escribió como clienta "¿A qué hora me llega?" → **silencio**, porque el chat seguía pausado por ella. Tuvo
que ir al panel y devolverlo. `retomar_chat` corrió (16:37 UTC, 7 s): leyó el expediente, **decidió bien**
(escalar con `pedir_ayuda`, sin inventar hora: ningún pedido tenía entrega acordada), se pausó a sí mismo
(`pausado_por='bot'`), avisó por WhatsApp a la agencia y enriqueció el aviso 3460… **y su despedida al
cliente no salió**: `_enviar_en_partes` preguntaba `_cliente_pausado` (¿está pausado?) en vez de
`_lo_paso_una_persona` (¿lo pausó una PERSONA?), la regla del 12-jul (migración 020). La regresión entró el
22-sep con el código de Codex rescatado sin cambios (98be60fb, dentro de PR #59): dos líneas (`:299` y
`:332`) delante de la "última mirada al freno" que sí preguntaba bien. **Solo en pruebas** (producción
`42d37de` no tiene #59); afectaba a toda escalada con pausa desde el 22-sep. Yo revisé ese PR y no lo vi.

**Arreglo (rama `despedida-tras-escalar`):** las dos preguntas pasan a `_lo_paso_una_persona`; la rama del
acuse del cerebro `confirmado` (`tomar_acuse`) y `_cliente_pausado` (que siguen usando `_procesar`,
`_retomar` y `_responder_y_enviar` ANTES de pensar, donde "pausado a secas" es lo correcto) no se tocan.
Tests `test_despedida_tras_escalar.py` (7): pausado por el bot → sale (uno y varios globos); pausado por la
dueña → nada; sin pausa → sale; el acuse del `confirmado` sigue reservándose; BD caída → calla; y por fuente:
el embudo no vuelve a preguntar `_cliente_pausado`.

**Decisión de Maired (24-sep) sobre QUIÉN calla al bot** (venía dudando entre "como está" y "como
SellerChat: habla siempre y solo ella lo calla"): **"se calla solo y vuelve solo"**. Cuando ella escribe, el
bot se calla en ese chat (se queda). Pero si el cliente vuelve a escribir, ella lleva N horas sin contestar
en ese chat y no hay propuestas del expediente sin confirmar, el bot retoma solo, sabiendo lo que ella vendió
(PR4). Botones solo para excepciones. N arranca en 2 h, editable desde el panel. Un pago lo confirma siempre
una persona. Razón: la pausa eterna es lo que tiene a producción muda en 308/322 chats y lo que impidió
probar el bot hoy sin botones; "habla siempre" es lo que chocó en 6 de 7 conversaciones. → **Sigue: PR5
retorno automático** (era PR7; se adelanta), diseño en el plan, sección (F).

## 2026-09-24 (38) — 👓 LOS LECTORES: el bot sabe dónde está entrando (expediente, PR4)

**Dónde estábamos:** el expediente ya se LLENABA de punta a punta y estaba probado en pruebas (SESIONES (37): "Te
anoté 2 quesillos son 16$" → propuesta → "Sí, es correcto" → pedido #3130 `origen='dueña'`, `confirmado`). Pero
NADIE lo leía: en modo `uno` un pedido `confirmado` era INVISIBLE (`_estado_cliente_texto` no tenía rama para él y
decía "No tiene un pedido abierto ahora" → el bot re-anotaba y re-cobraba lo que ella ya vendió, el choque de 6/7
del corpus); el cerebro `confirmado` cargaba UN solo pedido sin procedencia y relevaba TODA venta nueva; el
comprobante de un pedido tomado a mano caía en `comprobante_sin_pedido` (dinero verificado por visión que no
quedaba registrado); y "Devolver al bot" solo tenía un texto blando para no re-cobrar. Maired abrió el día con
"no sé qué estoy haciendo; ni siquiera me haces preguntas de dónde estamos" → primero el **mapa completo en una
página** (artefacto "Mapa del expediente": por qué, las tres capas, los 8 pasos, la prueba de 20 min, sus
decisiones, glosario) y después PR4.

**Decisiones de Maired (24-sep):** (1) *seguir vendiendo* = responder con datos y ofrecer/vender lo NUEVO **solo si
esa venta ya cerró** (pagada o entregada); con la venta a medias (acordada sin pago confirmado) el bot NO abre otra
venta encima: agregar o cambiar algo lo decide una persona (relevo `acuerdo_especial` diciendo QUÉ quiere agregar);
(2) "ya te lo entregué" SÍ se anota: tipo `entregado`, como propuesta.

**Qué quedó hecho (rama `expediente-lectores`, todo ADITIVO):**
- `models.py`: constantes `ORIGEN_BOT/DUENA/PANEL` y `ESTADOS_ACORDADOS = (confirmado, preparando)`. Existen para que
  `system_prompt.py` y `tools.py` comparen el origen SIN escribir la palabra "dueña" (el AST de
  `test_la_duena_en_silencio` los escanea).
- **Modo `uno` — `_estado_cliente_texto`** (`system_prompt.py`): rama nueva `_lineas_pedido_acordado` para TODOS los
  pedidos `confirmado`/`preparando` (hasta 3): quién lo tomó ("una persona del negocio a mano el 22/09" / "lo
  confirmó el negocio"), qué lleva (sin dinero), entrega acordada o "SIN acordar → pedir_ayuda" (a un pedido de
  ella NO se le ofrecen las franjas del bot), pago (CONFIRMADO / comprobante RECIBIDO en revisión / NO hay pago
  registrado → NUNCA afirmes que llegó), y la regla de seguir vendiendo según el pago. El total se manda a pedir a
  `ver_pedidos_cliente` (regla de oro del bloque: ni una cifra de dinero). Línea `_LINEA_PROPUESTAS` cuando hay
  propuestas pendientes (también SIN pedidos: "no des nada por hecho ni la contradigas"). Lecturas de pagos y
  propuestas fail-safe (sin dato, no sale la línea). Las ramas viejas (esperando/pendiente/pagado/cerrado) intactas.
- **`ver_pedidos_cliente`** (`tools.py`): por pedido `tomado_por`, `entrega` (fecha/momento/referencia/modo), `pago`,
  y el `total` FORMATEADO como dinero ("$16") además de `total_usd` — así `autorizados_por_moneda` lo reconoce y la
  red del TOTAL deja copiarlo. Descripción de la herramienta actualizada (también los pedidos tomados a mano;
  "cuánto debe", "cuándo le llega").
- **`get_pedido_esperando_pago`** (`tools.py`): también el pedido `origen='dueña' AND estado='confirmado'` (el
  `esperando_pago` del bot va primero). El comprobante de un pedido tomado a mano ya se PEGA y queda registrado.
  `_montos_cobrados` (worker) compara primero contra el pedido destino: si no tiene cotización, su total pactado
  en USD (antes: "sin cotización con la que comparar" → "no cuadra" aunque fuera exacto).
- **Cerebro `confirmado`:** `cargar_contexto` carga `ctx.pedidos` (hasta 3, con `origen`, `franja`, `pago_pendiente`),
  `ctx.pedido` sigue siendo el más nuevo, `propuestas_pendientes` y **`humano_sin_acuerdo = propuestas_pendientes > 0`**
  (definición elegida: lo pendiente es lo no resuelto; lo que el extractor descartó es charla; la red vieja de
  `[MENSAJE HUMANO…] sin pedido` sigue). `atender`: `_es_venta_nueva` (misma identidad que el candado de duplicados,
  `_firma_de_items`): venta CERRADA + OTROS productos ⇒ registra el pedido NUEVO y el cobro va a ESE id, jamás al
  pagado; mismos productos, a medias o sin items comparables ⇒ relevo. `humano_sin_acuerdo` ⇒ relevo en
  registrar/cobrar/entrega, en `comprobante` y en `estado_pedido`/`entrega`. "Ya pagué" sobre un pedido a mano sin
  pago ⇒ relevo `acuerdo_especial` (nadie afirma que llegó); con comprobante reportado ⇒ "Ya tengo tu comprobante".
  `resolver_atencion`: `estado_pedido` responde CON datos (qué lleva, entrega en palabras: "el miércoles 23 de
  septiembre en la tarde (2 a 5) en frente a la plaza"), tema nuevo **`entrega`** (con dato responde; sin acuerdo →
  relevo). `valor_actual` mira todos los pedidos.
- **Candado del gemelo:** `_crear_pedido_duena` llama a `_pedido_igual_reciente` → un segundo "Sí" sobre el mismo
  pedido devuelve 409 legible ("ya existe el pedido #N con estos mismos productos").
- **Tipo `entregado`** en el extractor: propuesta si hay pedido abierto (descarta sin pedido, cancelado o ya
  entregado); `aplicar` lo cierra (idempotente; se niega sobre cancelado). Instrucción del extractor actualizada.
- **Retomar:** `expediente.leer_expediente(telefono)` (pedidos a mano + pagos + propuestas, SIN dinero) →
  `_hay_pendiente(…, venta_cerrada_a_mano=)`: con la venta cerrada a mano y solo acuses, se calla aunque ella
  terminara en "?"; si habla, la instrucción lleva `[HECHO] El negocio ya le tomó a mano el pedido #N…`.
- Panel (bot): `listar_pedidos` devuelve `origen` y `confianza`.
- Tests: `test_expediente_lectores.py` (37) + 1 ajuste al helper de `test_retomar_pendiente` (sin base no hay
  expediente). Los 27 de Codex y `test_estado_del_pedido` intactos. Suite completa verde (ver el PR).

**PR hermano del dashboard (`expediente-lectores-panel`):** `Pedido.origen/confianza` en `api.ts`; en Pedidos el chip
**"Tomado por Whuilianny"** junto al número del pedido (aquí el nombre sí va: es el panel). El filtro "sin pago
registrado" queda para cuando Maired lo pida.

**Sigue:** Maired fusiona bot + panel → desplegar pruebas (bot + worker + dashboard) → su prueba de 20 min (ella es
Whuilianny desde el teléfono de la agencia y clienta desde el suyo): pedido a mano → "Sí" → "¿a qué hora me llega?"
→ "quiero empanadas" → "¿te llegó mi pago?" → PR5 el replay sobre las 305 conversaciones → P3 el cerebro
`confirmado` en pruebas.

## 2026-09-22 (37) — 🗂️ EL EXTRACTOR: lo que la dueña dice a mano se vuelve dato o propuesta (expediente, PR3)

**Antes de PR3, la verificación en pruebas (P1-check):** Maired fusionó #60 y #61; pruebas quedó en `944c51d`
(deploy por API de Coolify, `/root/deploy_pr61.sh`; al arrancar aplicó la 039 y la 040; `probar_migraciones.py`
todo OK). Ella mandó una nota de voz desde el teléfono de la agencia a su número y quedó **"🎤 Sí, sí tenemos
empanadas. Te lo puedo llevar el día de mañana."** en `mensajes` (tipo audio, media conservada), en la memoria
Redis (misma posición, cero placeholders) y en `llamadas_ia` con carril `eco_audio`: gemini-2.5-flash, 154+17
tokens, **$0,00018**, 1 s. La capa VER quedó probada de punta a punta.

**Decisión de Maired — el modelo del extractor:** el MÁS BARATO (`google/gemini-2.5-flash-lite`, default en
`config.py`), medido por el replay; se sube por la config `modelo_extractor` sin tocar código. Razón que ella
aceptó: el extractor solo propone y el código valida → lo peor de un modelo barato es una propuesta de más,
nunca una mentira. (Y la pregunta que había detrás: con qué modelo trabaja Claude → Fable 5.1 para lo que toca
dinero, datos o el cerebro; Sonnet para lo mecánico.)

**Qué quedó hecho (rama `expediente-extractor`):**
- `app/agent/expediente.py` — el módulo: `ventanas_owner` (pura: agrupa sus mensajes consecutivos hasta que
  habla el cliente o pasan 5 min; salta placeholders); `interpretar_duena` (una llamada con la tool
  `proponer_eventos_duena`, contrato cerrado `ExtraccionDuena`; el modelo ve SOLO nombres del catálogo, sin ids
  ni precios); `validar` (pura: resuelve nombre → producto → presentación → precio de hoy, cantidad y total; dicta
  `escribe` / `propuesta` / `descarta`); `procesar_ventana` (deja la propuesta en la Bandeja o, SOLO con
  `expediente_escritura=auto`, aplica lo inequívoco; no duplica propuestas pendientes; nunca lanza);
  `aplicar_propuesta` (la ÚNICA puerta de escritura, la usan el toque humano y `auto`: pedido nace
  `confirmado` con `origen='dueña'`, pago `confirmado` firmado por quien tocó + pedido `pagado`, entrega, precio
  especial, cancelación, respuesta → Conocimiento confirmado); `leer_config_expediente`.
- Contratos en `contratos_atencion.py`: `ItemDuena`, `EventoDuena`, `ExtraccionDuena` (cerrados, estrictos) y
  `PropuestaExpediente`/`ItemPropuesto` (lo que viaja en `intervenciones.propuesta`, cerrado).
- Reglas duras cableadas: **un pago es SIEMPRE propuesta**; evidencia que no consta literalmente → descarta;
  nombre ambiguo, presentación sin decir, total que no cuadra (se conserva el que ELLA dijo, para que una
  persona lo confirme), precio del día sin cargar → propuesta; entrega "mañana en la tarde" sobre el pedido
  abierto → fecha + franja de la lista cerrada; el extractor no le habla a nadie.
- Tarea `extraer_expediente` (worker): se encola **90 s después** de cada mensaje de la dueña (eco del celular
  —texto o audio— y mensaje desde el panel), lee desde la marca `cache:expediente_hasta:{tel}` (sin marca, 6 h),
  privado fail-closed, turno propio `expediente` en `llamadas_ia`.
- Panel (bot): `listar_intervenciones` devuelve `propuesta`; `POST /intervenciones/{id}/aplicar` ("Sí, es
  correcto": aplica por la única puerta, 409 legible si no se puede, cierra firmado) y `/descartar` ("No":
  cierra sin escribir, firmado — sirve para medir cuánto se equivoca el extractor). Ninguno despausa ni retoma.
- Palancas de la PROVEEDORA en `CLAVES_CONFIG`/`CLAVES_PROVEEDORA`: `modelo_extractor`, `expediente_escritura`
  (off | **propuestas** | auto).
- Tests: `test_expediente_extractor.py` (43) + `test_expediente_propuestas.py` (10). Suite **1210 / 0**.

**Lo que falta del PR3 (PR hermano del dashboard):** la tarjeta de la Bandeja para `motivo='propuesta_expediente'`
con los botones "Sí, es correcto" / "No" sobre la rama `control-atencion-panel` (PR #10). Mientras no esté, las
propuestas se ven en la Bandeja con su texto pero se aplican por API.

**Sigue:** PR3b (dashboard) → PR4 los lectores (`_estado_cliente_texto` → producción `uno` mejora;
`cargar_contexto` → `humano_sin_acuerdo` real; `_retomar`) → PR5 el replay sobre las 305 conversaciones.

## 2026-09-22 (36) — 🎤 LA NOTA DE VOZ DE LA DUEÑA SE TRANSCRIBE EN VIVO (expediente, PR2)

**El hueco:** en el corpus real la dueña mandó **877 notas de voz** y el bot vio "[nota de voz]" en TODAS
(`parser.py` guarda el placeholder; `_procesar_audio` solo transcribe al CLIENTE; y el respaldo de Postgres
filtraba `tipo='text'`, así que su audio ni volvía). En sus notas de voz ella **coordina las entregas** (38%
de las transcritas): de ahí salían choques como cobrar un envío que ella había regalado por voz.

**Qué quedó hecho (rama `expediente-eco-audio`, cero cambio en lo que el bot DICE):**
- `_procesar_eco` (webhook) **encola** `transcribir_eco` al final —tras el candado— solo si la burbuja es
  NUEVA (un reintento de Meta no transcribe dos veces) y el eco es audio con `media_id`. Si encolar falla,
  el eco termina igual: la pausa y la burbuja ya están.
- `transcribir_eco` / `_transcribir_eco` (worker, junto a `procesar_audio`): **pregunta si el contacto es
  PRIVADO fallando CERRADO** (sin respuesta de la base → no se transcribe; la voz de un familiar no sale de
  casa por un hipo), abre un turno propio `eco_audio` en `llamadas_ia` (para medir cuánto cuesta escucharla
  a ella), descarga con `descargar_media`, transcribe con el MISMO `transcribir_audio` del cliente, y
  reemplaza el placeholder **en su sitio**: `UPDATE mensajes … WHERE message_id AND contenido='[nota de voz]'`
  (idempotente; conserva `tipo='audio'` y `media_id`, el panel sigue mostrando el reproductor) y en Redis
  `reemplazar_en_historial` (LSET de la entrada heredada por el eco, misma posición — apilarla al final
  dejaría su frase después de lo que el cliente dijo mientras tanto). Audio caducado (400/404: Meta lo
  borró) → queda el placeholder, log, **sin aviso** a nadie.
- `historial_desde_postgres` acepta también `rol='owner' AND tipo='audio' AND contenido LIKE '🎤%'`.
- `PLACEHOLDER_AUDIO` con nombre propio en `parser.py` (lo buscan el webhook, el worker y los tests).
- Tests `tests/test_expediente_eco_audio.py` (15): encola / no encola texto / reintento no duplica / fallo al
  encolar no rompe el eco; privado; sin verificar; caducado sin aviso; vacío; ya transcrito; reemplazo exacto
  y orden en Redis; la más reciente de dos; placeholder único; el respaldo devuelve 🎤.

**Costo:** ~$0,002 por nota (audio-in de Gemini 2.5 Flash), ≈390 notas/mes al ritmo del corpus → **<$1/mes**.
Se verifica en `llamadas_ia` con `carril='eco_audio'` tras desplegar a pruebas.

**Sigue:** PR3 — el extractor (`app/agent/expediente.py`): de sus mensajes (texto y 🎤) a eventos tipados que
el código valida; lo dudoso → propuesta en la Bandeja.

## 2026-09-22 (35) — 🗂️ EL EXPEDIENTE DE LA VENTA: el bot tiene que saber en qué punto entra (PR1: cimientos)

**La pregunta de Maired que lo cambió todo:** *"¿la infraestructura que estamos creando es la mejor para el
objetivo final? Si Whuilianny ya le cobró, el bot tiene que saberlo. No puede seguir una conversación si no
sabe lo que la persona ya hizo, cuándo, si pagó o no. Matemos de raíz."* Se paró todo y se re-evaluó la
arquitectura con evidencia, no de memoria.

**Lo que se verificó (código + corpus anónimo de 313 conversaciones reales):**
- Los ecos de la dueña quedan como `Mensaje(rol='owner')` y le llegan al modelo como texto envuelto en
  `[MENSAJE HUMANO DEL NEGOCIO…]`. **Sus notas de voz NO se transcriben en vivo**: el bot ve "[nota de voz]" en
  el 100% de ellas (`parser.py`, `webhook/router.py`; y `memoria.py:164` filtra `tipo='text'`).
- **Ninguna función lee sus mensajes para deducir estado.** `pedidos` solo los crea el bot; `pagos` solo
  `registrar_comprobante`; no hay pedido manual ni "cobré sin comprobante" en el panel; no hay línea de
  tiempo ni procedencia. `Contexto.humano_sin_acuerdo` (Codex) está declarado y **nunca se calcula**.
- Corpus: **305 de 313 conversaciones las atiende Whuilianny; 10.555 mensajes de ella vs 55 del bot; 877
  notas de voz suyas (166 transcritas offline; 38% = coordinar entregas)**; el pago se confirma casi siempre
  implícito ("Listo", emoji, voz; acuse mediana 0,9 h tras el comprobante); **el bot chocó con ella en 6 de las 7
  conversaciones donde entró** (piloto 12-15 sep): total $62 vs $64 de ella y pidió un pago ya hecho; cobró $2
  de envío que ella había regalado; "¿te recuerdo lo que pediste?" con pedido tomado; re-saludó a mitad; 150
  mensajes de ella con correcciones; 50 clientes recurrentes (uno con 19 compras).

**Diagnóstico de raíz:** no es qué cerebro decide ni qué voz habla: **no existe un registro compartido de la
venta entre Whuilianny y el bot.** Cualquier cerebro entra ciego después de ella.

**Arquitectura final (aprobada por Maired):** un cerebro (código: la capa de Codex, modo `confirmado`) + una
voz (IA: la Voz del modo dos, reutilizada) + **un EXPEDIENTE** (memoria compartida con procedencia y fecha,
alimentada por el bot, el panel y **los mensajes de la dueña —texto y notas de voz transcritas en vivo— pasados
por un extractor**: modelo barato → JSON tipado → **el código valida**; lo dudoso queda como PROPUESTA que una
persona confirma con un toque; jamás se escribe un dato dudoso). Sin Operador-IA. Orden: **P1** migración 040 +
transcripción de ecos → **P2** extractor + propuestas + lectores (`_estado_cliente_texto` → producción `uno`
mejora ya) + replay sobre las 305 conversaciones (puerta ≥0,98) + gancho de retorno apagado → **P3** el cerebro
`confirmado` lee el expediente y Maired lo oye en pruebas → **P4** la Voz → **P5** Conocimiento desde el corpus →
**P6** puertas G1-G6 y producción por configuración. ≈10 sesiones; costo estimado $1-3/mes en tokens. Plan
completo: `~/.claude/plans/…crystalline-sprout.md`.

**Decisiones de Maired (22-sep noche):** (1) **el bot PUEDE volver solo tras Whuilianny, con condiciones** —
cliente escribe de nuevo + N horas sin respuesta de ella + expediente resuelto; flag `retomar_auto_horas`
APAGADO por defecto; se prueba en pruebas; ella fija N (la regla "pausado_por='dueña' no expira" sigue hasta
entonces). (2) **Lo dudoso lo confirma Maired por ahora desde la Bandeja**; Whuilianny cuando use el panel
(~4 propuestas de pago al día = el mismo botón "Pago aprobado", pre-señalado). (3) Un `pago_confirmado`
extraído es SIEMPRE propuesta: `pagado` solo nace de un clic humano (CLAUDE.md §3).

**PR1 (esta rama, `expediente-cimientos`) — cero conducta:** migración `040_expediente.sql` (`pedidos`/`pagos`
+ `origen` bot|dueña|panel, `evidencia_mensaje_id`, `confianza`, `extraido_at`; `intervenciones` + `propuesta`
JSONB, `aplicada_por`, `aplicada_at`; 2 índices), modelos, `probar_migraciones.py`, la constante
`MOTIVOS_INFORMATIVOS` (models.py) y las **6 guardas** para que una propuesta NUNCA se confunda con un chat
tomado: el eco no la cierra (`_procesar_eco`), el mensaje desde el panel no la cierra, el barredor no la cierra
(`NOT IN`), resolverla no reactiva al bot, `pedir_ayuda` no la enriquece ni le pisa el motivo, `tomar_acuse` no
se la lleva. Etiqueta en la bandeja: "Whuilianny dijo algo a mano: ¿lo confirmas?". Tests en
`tests/test_expediente_cimientos.py`. **Nadie escribe todavía en estas columnas.**

**Lecciones:** (a) antes de decidir arquitectura, medir la operación real: aquí el bot era el 0,5% de los
mensajes; (b) Codex y Claude comparten rama desde dos clones → `git fetch` antes de cada paso (hoy casi se
duplicó E1); (c) el ritmo con Maired es un OK por paso y cada mensaje empieza con "Estamos en / Falta".

**Sigue:** PR2 — transcribir en vivo las notas de voz de la dueña (`transcribir_eco`).

## 2026-09-22 (34) — 🛡️ EL CEREBRO SE CIERRA ANTES DE DARLE VOZ (E1, APAGADO)

**Decisión de Maired:** primero terminar la seguridad del modo `confirmado`; la Voz natural se conecta
después. Se trabajó sobre la rama de Claude `atencion-confirmada-voz`, conservando E0 y sin tocar la
configuración de ningún servidor.

**Qué quedó hecho:**
- Si no se puede consultar la pausa del chat, el bot se calla. No arriesga responder encima de
  Whuilianny.
- Los pagos y comprobantes del modo `confirmado` salen de un evento cerrado y de la fila real del pago.
  Un llamado sin evento se bloquea; no vuelve al redactor libre. Los modos `uno` y `dos` conservan su
  camino actual.
- El sobrepago confirmado muestra el saldo a favor y el abono parcial calcula recibido, total y faltante
  desde la base de datos.
- "Ya pagué" distingue entre cobro abierto sin captura, comprobante ya reportado y ausencia de un pedido
  cobrable. El comprobante entrante sigue pudiéndose guardar aunque el chat esté pausado.
- Los avisos humanos incluyen pedido, producto y dato pendiente cuando esa información existe. El saludo
  puede usar el primer nombre, pero omite URLs, emojis solos y nombres de perfil extraños.
- Las fuentes se vuelven a revisar **antes** de guardar, registrar o cobrar. Si el borrador no se puede
  guardar, no se ejecuta ninguna acción.

**Comprobación local:** ruff, `compileall`, 100 pruebas dirigidas y la suite completa en verde. Las pruebas
usan respuestas simuladas: no llamaron a OpenRouter, no consumieron tokens del bot y no enviaron WhatsApps.

**Estado:** código solo en la rama/PR #59. El modo `confirmado` sigue apagado y producción continúa en
`uno`. No hubo despliegue.

**Revisión de Claude (misma tarde):** bajada la rama, suite completa **1126 tests / 0 fallos**, ruff limpio.
Verificado leyendo: `uno`/`dos` no cambian (test por modo); las frases fijas de pago pasan la red
`_proteger_afirmacion_de_pago`; la verificación de fuentes movida ANTES de cobrar conserva el segundo cinturón
del envío (`_enviar_en_partes` re-verifica). Se agregaron los 2 tests que faltaban: los 3 endpoints del panel
(aprobar / rechazar / verificar monto) mandan el evento tipado junto a la situación natural; y dos mensajes
entrelazados del mismo cliente producen UN solo aviso y UN solo acuse. Paso 0 del plan también hecho: el panel
de Codex que estaba SIN COMMITEAR en su clon quedó en el PR borrador #10 del dashboard (Conocimiento
confirmado + botones de la Bandeja; los botones son decisión de UX de Maired, por eso borrador).

**Sigue (orden decidido por Maired, 22-sep tarde: CEREBRO PRIMERO, VOZ DESPUÉS — no al revés):** E2 = opción
`confirmado` en Configuración del panel, prenderlo SOLO en pruebas (VPS de Enova) y que Maired lo oiga 20 min
sabiendo que sonará plano (este modo NO usa la Personalidad del panel; se comprueba el cerebro: que no invente,
pregunte lo del cliente, avise y pause), más 48 h leyendo `intervenciones`/día y `llamadas_ia`/turno. E3 =
conectar la Voz de Alejandra (la Personalidad) sobre esta salida cerrada. Producción: decisión posterior, tras
las puertas G1-G5 (`~/.claude/plans/…crystalline-sprout.md`).

## 2026-09-22 (33) — 🧭 CODEX + LA VOZ: nace el modo `confirmado` (E0: cableado, APAGADO, suite verde)

**De dónde viene.** Maired trabajó con ChatGPT Codex en OTRA copia del repo (`C:/Mis_Proyectos_IA/...`) y se
le acabaron los tokens con 26 archivos sin commitear. Rescatado tal cual a la rama `control-datos-confirmados`
(98be60f). Lo que construyó: una capa de "atención confirmada" donde el modelo solo PROPONE intenciones tipadas
y el CÓDIGO decide los hechos desde fuentes confirmadas (`atencion.py`, `contratos_atencion.py`,
`fuentes_atencion.py`, `resolver_atencion.py`, `eventos_atencion.py`, migración 039). Muy bueno en el QUÉ; pero
reemplazó el CÓMO por frases fijas ("Tu pago está aprobado. Gracias por tu compra"), lo cableó como reemplazo
total del motor (sin interruptor), y su suite no pasaba (un candado de red en los tests bloqueaba también la BD
local).

**La tesis (plan aprobado por Maired el 22-sep):** no hay que elegir entre "seguro" y "humano". El CÓDIGO decide
QUÉ se dice (la capa de Codex), un MODELO sin catálogo ni cuentas decide CÓMO (la Voz del modo DOS de agosto,
apagada desde entonces), y el CÓDIGO verifica que el CÓMO no agregó nada (las redes que ya existen + dos nuevas:
nombres de producto y fechas). Codex reconstruyó la mitad de algo que ya existía; se casan las dos mitades.
Plantillas solo como ÚLTIMO recurso, nunca la respuesta normal. Etapas E0…E6 y puertas G1-G5 (0 fallos duros ·
empata o gana a Sonnet-`uno` en A/B ciego · fallback ≤ 5% · costo ≤ 50% · p95 ≤ actual + 3 s).

**E0 (rama `atencion-confirmada-voz`, este PR):** `agente_modo='confirmado'` es un MODO junto a `uno` y `dos`,
no un reemplazo. `responder` de master recupera su nombre y despacha las tres ramas (`_responder_confirmado` =
el `atender` de Codex; intérprete = `modelo_operador`); `redactar_mensaje` vuelve a ser la voz natural de los
pagos y el wrapper de Codex queda como `redactar_evento_confirmado` (sin cablear hasta E3). **Devueltos a
master**, porque E0 NO cambia el comportamiento de `uno`: los 13 tests que Codex había apuntado a
`_responder_legacy`, el retomar del botón individual "Devolver al bot" (`_disparar_retomar`; el #54 ya evita
reabrir ventas cerradas), `reactivar=True` en resolver intervención, las situaciones naturales de los 3 endpoints
de pago y de los 2 llamados del comprobante, y el fail-open de `_cliente_pausado`. **Conservado de Codex:** todos
los motivos pausan, puerta de pausa en las herramientas, `bloquear_cliente`, `tomar_acuse`, revalidación de
fuentes al enviar (`fuentes_vigentes`), migración 039, Conocimiento "confirmado" en el panel.
`conftest.sin_red_externa` exime 127.0.0.1/::1/localhost. Test adaptado:
`test_modo_confirmado_no_usa_motor_anterior` + nuevo `test_modo_uno_no_pasa_por_atender`.
**Suite: 1105 tests, 0 fallos** (ruff 0.9.6 limpio). Producción intacta en `uno`; no hay cambio de config.

**Dos líneas sueltas que Codex tocó FUERA de su plan — RESUELTAS por Maired el mismo 22-sep** (no eran el
corazón del diseño; mal presentadas por Claude como "preguntas de negocio"):
1. `verificar_monto`: cuando el cliente paga DE MÁS, master dice "le queda ese saldo a favor para su próxima
   compra". Codex lo había quitado porque nadie lo había confirmado. **Maired lo confirmó: el sobrepago ES saldo
   a favor para la próxima compra.** Queda como está (ahora es regla del negocio confirmada, no una frase suelta).
2. `_cliente_pausado` con la BD caída (falla técnica de un instante en la que el bot NI PUEDE LEER si la dueña
   tomó el chat, ni anotar un aviso — distinto de "no sabe algo", que va a "ya te confirmo"): Codex prefería
   callar (fail-closed). **Maired: coherente con la prioridad de no atropellar a Whuilianny → se ADOPTA en E1**
   con su test (en E0 quedó lo de master solo porque la regla del paso era "no cambiar nada de producción").
(Y una de medición, para E5: con Codex cada "ya te confirmo" pausa el chat hasta que ella lo resuelva; se cuentan
intervenciones/día en pruebas antes de decidir.)

**Lo que Codex diseñó y quedó INTACTO en el modo `confirmado`** (para no volver a explicarlo): sin dato
confirmado el código decide `relevo` (`resolver_atencion.py` L80-91: falta respuesta, vacía, contradictoria);
el cliente recibe "Ya te confirmo" / "Déjame revisarlo" / "Eso te lo confirmo enseguida" variando
(`atencion.py` L86); UN solo aviso a la dueña con pregunta + producto + dato faltante (`tomar_acuse`); el chat
queda pausado hasta que ella lo devuelva (`bloquear_cliente`); si lo que falta es del cliente (dirección,
cantidad) se le pregunta, no se escala. Nuestro plan solo cambia QUIÉN PONE LAS PALABRAS (la Voz de Alejandra
en vez de una lista fija); la decisión de parar sigue siendo del código, como la diseñó Codex.

**Lecciones:** dos copias del repo en la misma máquina = trabajo huérfano (consolidar en una); `git update-ref`
para adelantar master deja índice y árbol a medias (usar `reset --hard origin/master`); un candado de red en
tests debe eximir loopback o mata los tests de BD local.

**Sigue:** E1 — `Encargo` tipado en `contratos_atencion.py`, `_emitir(redactar=None)` en `atencion.py` (camino
de Codex byte a byte igual), `HojaDeHechos.desde_encargo`, `_responder_confirmado` → `_dar_voz` + redes +
un reintento + fallback al texto fijo SIN pausar. Tests `test_hoja_confirmada.py`, `test_atencion_con_voz.py`.

## 2026-09-16 (32) — REGLA OFICIAL DEL DELIVERY · REVISIÓN DEL MÉTODO DEL MENTOR

**Decisión del negocio:** ante la pregunta con las tres fórmulas posibles, Whuilianny respondió:
*"total de la cuenta con el delivery y descuento del 20%"*. La operación oficial queda así:
productos + delivery = subtotal; después se descuenta 20% a ese subtotal. Ejemplo: $18 + $2 = $20;
menos 20% = **$16**. Esto corrige la interpretación del 7-sep ($16.40 en ese mismo ejemplo).

**Cambio desplegado:** `monto_en_efectivo` aplica el 20% al total completo; el desglose muestra
productos, delivery, subtotal, descuento y total. `generar_datos_pago` y `registrar_comprobante`
siguen usando una sola función. Si una clienta ya recibió una cotización anterior, su
`cotizado_usd_divisas` congelado se respeta al revisar el comprobante. La regla blindada del agente
explica la fórmula y le ordena copiar la cuenta hecha por código. Suite completa, ruff, compileall y
`git diff --check`: verdes localmente.

**Método del mentor y las dos copias:** la opción documental elegida fue correcta y el PR #56 ya
dejó `FUNCIONES.md`, `ONBOARDING.md` y la poda sin tocar código ni prompt. La corrida de 42 agentes
terminó W1 (313 conversaciones, 166 respuestas del bot, precios y top-20) con 2.517.386 tokens de
Claude y cero gasto de OpenRouter; W2 no corrió porque se alcanzó el límite y Maired cambió a
documentación. Las carpetas locales de `C:\Mis_Proyectos_IA` y `C:\Developer\AI\Proyectos` son dos
clones del mismo GitHub; ambas quedaron en `master` `c1506d8`, sin trabajo único perdido.

**Despliegue:** PR #57 fusionado en `master` `b6d755d`; CI `35095767955` verde y promoción manual
`35095865528` verde, incluidos los dos detectores de esquema. `/salud` `ok`, fallos `[]`, Postgres y
Redis `ok`, Meta `GREEN`. La personalidad viva se actualizó por la API después de un ensayo con
`ROLLBACK`: md5 `a9aaafe61353` → `4e2eab0b5710`, coincide. La frase vieja quedó ausente. No se mandó
ningún mensaje de prueba a producción.

---

## 2026-09-15 (31) — AUTORÍA HUMANA · EVENTOS EN SILENCIO · AUDIO CON RELEVO

**Por qué:** el levantamiento de conversaciones mostró tres fallos concretos. El historial guardaba
los mensajes de Whuilianny como si los hubiera dicho Alejandra, por lo que el bot podía apropiarse
de frases personales o reconstruir acuerdos manuales. Además respondía a `reaction`, `edit` y
`revoke` como si fueran nuevas intenciones de compra. Por último, una avería de descarga o
transcripción de audio terminaba diciendo repetidamente que la nota no se escuchó bien, sin separar
un fallo técnico de un audio vacío.

**Qué cambia:**
- Los ecos humanos siguen usando el rol estándar `assistant` que exige el proveedor, pero llevan
  una marca interna neutra (`MENSAJE HUMANO DEL NEGOCIO`) tanto en Redis como al rescatar desde
  Postgres. La regla blindada conserva los acuerdos y prohíbe apropiarse de relaciones personales,
  acciones físicas o cambiar/cobrar nuevamente una transacción humana si falta estado estructurado.
- `reaction`, `edit` y `revoke` se guardan en el hilo con idempotencia y no muestran
  “escribiendo…”, no llaman al modelo y no generan una respuesta que reabra la conversación.
- Los fallos consecutivos de audio se cuentan durante 15 minutos. Una avería técnica avisa desde el
  primer fallo; dos audios vacíos seguidos también abren relevo. El aviso queda en la bandeja y la
  dueña recibe un WhatsApp con candado anti-repetición. Un audio entendido limpia la racha.
- Pruebas nuevas en `tests/test_eventos_y_autoria.py` y ajuste de la regresión del respaldo de
  memoria. Suite completa, ruff, `compileall` y `git diff --check`: verdes.

**Despliegue:** PR #55 fusionado en `master` `ee4f7af` y promovido manualmente a producción por
Actions (run `35038049114`). Bot y worker en la imagen completa del commit, detectores de esquema
verdes; `/salud` `ok`, Meta `GREEN`, 39 migraciones. No se mandaron mensajes de prueba.

**No cambia:** precios, descuentos, delivery, aprobación de pagos ni catálogo. Queda pendiente que
Maired/Whuilianny confirmen la regla comercial del envío antes de tocar el cobro.

## 2026-09-13 (30) — 🔇 RETOMAR NO REABRE VENTAS CERRADAS · el 402 que dejó producción en GPT-4.1 · el plan "modelo más barato sin adivinar"

**Lo que encontró la revisión del 12-sep (Maired: "hice cambios con ChatGPT, revisa dónde estamos"):**
producción corría `176d0de` (PRs #52/#53 desplegados directo a prod el 9-sep, sin pasar por pruebas —
pruebas se puso al día a `176d0de` el 12-sep). Y **el bot llevaba 5 días sin contestar a NADIE en
producción**: 0 mensajes `assistant`, 0 pedidos. No era un bug: 308 de 322 clientes estaban
`bot_pausado=true, pausado_por='dueña'` porque Whuilianny sigue contestando desde su celular
(coexistencia → eco → pausa que no caduca), incluidos los números piloto. La lista blanca se corrigió
(Rosi era `584128633913`, no `…363931`) y se amplió a 7 por `PUT /api/lista-blanca`. Detalle en la
memoria de la sesión y en ESTADO.

**13-sep 07:51 VET — el 402.** Con $1.59 de saldo, OpenRouter rechazó a Sonnet (402: no alcanza para
una llamada de 25k tokens) y `_llamar_con_fallback` cayó EN SILENCIO a `OPENROUTER_MODEL_FALLBACK =
openai/gpt-4.1`: sin caché Anthropic, $0.040/turno (3× Sonnet cacheado) durante toda la mañana.
Además el id configurado `~anthropic/claude-sonnet-latest` es un ALIAS que se mueve: el 12 resolvía
a sonnet-4.6, el 13 a sonnet-5. **Etapa 1 hecha (solo configuración):** `modelo_ia` =
`anthropic/claude-sonnet-4.6` exacto (puente mientras se mide); `OPENROUTER_MODEL_FALLBACK` =
`anthropic/claude-haiku-4.5` en Coolify (bot+worker prod; misma familia, mismo caché; entra con este
deploy); umbral de `/salud` $2 → $5 (aquí). Pendiente de Maired: llave de OpenRouter aparte para
pruebas con tope $15.

**El caso de Amanda (`584125198777`, 13-sep 07:52).** Maired devolvió chats al bot en lote. La dueña
había cerrado esa venta A MANO la noche anterior ($64, comprobante recibido, "Dios te multiplique";
la clienta: "Amén"). `_retomar` leyó "Amén" como pendiente ("el último turno es del cliente"),
REABRIÓ la venta con GPT-4.1: registró Caldo de Huesos (era Caldo de Carne), $62 (eran $64), pidió
referencia y volvió a cobrar. Clienta: *"Yo creo que estás confundida de persona"*. Whuilianny tuvo
que pedir disculpas.

**Qué cambia (este PR):**
- `tasks.py`: `_hay_pendiente(historial, pausado_por)` decide en CÓDIGO si hay algo que retomar: el
  bot escaló ⇒ sí; la casa habló última ⇒ no; la casa terminó preguntando ⇒ sí (la respuesta corta
  del cliente es lo que esperábamos); algún mensaje del bloque final pide/pregunta ⇒ sí; el bloque
  final son solo acuses ("ok", "gracias", "amén", "(comprobante)") ⇒ **no**. `_es_acuse` con listas
  ADITIVAS (`_ACUSES`, `_PIDE_ALGO`), sin acentos.
- `_INSTRUCCION_RETOMAR`: "Alejandra, la asesora" (decía "asistente virtual", contra R130) + "si la
  venta ya se cerró a mano, NO registres, NO generes datos de pago, NO vuelvas a cobrar".
- `router.py` `PUT /clientes-pausa-lote`: **silencioso** — despausa y avisa al panel, NO dispara
  `_disparar_retomar`. El botón individual sigue retomando (la dueña acaba de leer ESE chat: su clic
  es la aprobación humana que exige Meta). El lote es limpieza de bandeja sobre chats no releídos.
- `salud.py`: `UMBRAL_SALDO_USD = 5.0`.
- Tests: `tests/test_retomar_pendiente.py` (acuses vs pedidos; Amanda ⇒ `_pensar_y_enviar` no se
  llama; pregunta pendiente ⇒ sí; pagaré del bot ⇒ sí; lote sin retomar y botón con retomar por
  fuente; instrucción; umbral). Suite: 1.051 en verde, ruff 0.9.6.

**El plan aprobado (13-sep) — "Alejandra igual, modelo más barato, sin adivinar"**
(`~/.claude/plans/okay-pero-entonces-dime-crystalline-sprout.md`): 1) frenar la sangría (hecho);
2) este PR; 3) MEDIR con `scripts/ensayo_closer.py` 6 modelos × 5 escenarios × 3 repeticiones en el
contenedor de pruebas con la llave nueva (+ escenario "la dueña cerró a mano", + `tokens_cache`, juez
fuera del set), regla escrita: 0 fallos duros, juez ≥ Sonnet − 0,5, ≤ 12 s/turno, más barato que
Sonnet cacheado; 4) SOLO si ninguno barato pasa: modo DOS (Voz barata sin catálogo ni banco) y, al
final y medido, adelgazar el prompt; 5) pruebas → UNA conversación de Maired → producción. Lo que dijo
su amigo ("tool calling + prompt caching") ya está construido: 14 tools y caché 1h; lo que falta es
que el caché vale solo para Anthropic (Gemini cachea solo; OpenAI casi nunca) y que 24k tokens de
prompt hunden a los modelos baratos.

**Pendientes:** Maired crea la llave de pruebas ($15) → etapa 3. Whuilianny: zona Este en prod, regla
del delivery con Zelle (contradijo el #46 con María Luisa), devolver los 7 chats piloto. Datos:
sabores de Empanadas de yuca/plátano faltan en prod.

## 2026-09-07 (29) — 🚀 PRODUCCIÓN PROMOVIDA (`27f50ac`) · lista blanca con 3 clientes · "LA DUEÑA" EN SILENCIO

**Promoción (18:40-19:05 VET), la liturgia de ESTADO paso a paso.** Maired dio la orden ("vamos a
pasarlo todo a producción, que no quede nada") cuando Whuilianny dejó de editar el catálogo.
Respaldo en netcup (dump 3,9 MB + personalidad vieja md5 `e9e1349b6a66`) → `workflow_dispatch`:
**el primer intento se frenó en la puerta** — ruff 0.9.6 del CI marcó UP038 en
`tests/test_la_hora_es_muda.py` (isinstance con tupla); localmente pasaba porque el venv tenía
0.16.5. PR #47 de una línea, venv alineado a 0.9.6, segundo intento verde (run 34168204704) →
bot+worker `27f50ac`, 39 migraciones, personalidad nueva (md5 `a9aaafe61353`, coincide=True; el
bot viejo NO traía `scripts/promover_*.py`, llegan con el deploy), sabores hechos=3, **27/27
bancos**, panel `60f8b4d` (login 200), prompt vivo limpio y $14+$3 → $14.20. Detalle en ESTADO.

**Lista blanca:** Whuilianny dio 3 números y Maired decidió soltar gradual (3-4 clientes/día). La
clave `numeros_permitidos_extra` (29-ago) NO estaba en `CLAVES_CONFIG` → no se podía por la API
del panel → upsert directo en `configuracion` (leído antes: vacío; después: los 3). Este PR la
agrega a `CLAVES_CONFIG` para que la próxima vez sea por el panel. Recomendación dada y aceptada:
no abrir con `todos` todavía (prompt de 71k con "la dueña" 38 veces, balde compartido, $0,20-0,40
por conversación).

**"La dueña" en silencio (este PR, `la-duena-en-silencio`).** Maired: *"ayúdame a eliminar del
prompt las 38 menciones"*. Se midieron con AST: **52 literales** con "dueña" en los 5 módulos que
arman lo que lee el modelo; 45 los lee el modelo (reglas, catálogo, esquemas y notas de
herramientas, hoja del modo dos, correcciones `[SISTEMA]` del agente) y 7 son `logger` (para
nosotros; se quedan). Los 45 pasan a primera persona del negocio: "se revisa en el banco del
negocio", "se coordina después", "una persona del negocio entra al chat", "el negocio la
autorizó", "se prepara por encargo" (esta sola línea del catálogo eran 16 de las 38). Hallazgo
colateral: la corrección `[SISTEMA]` de identidad decía *"eres la asistente virtual del negocio"*
— contradecía a R130 (Alejandra, la asesora); corregida. **Red de código nueva** en
`_PROHIBIDO_SIEMPRE`: `la/nuestra/mi/una dueña|propietaria|jefa` — el prompt sugiere, el código
impide, en los dos carriles. **Test nuevo `tests/test_la_duena_en_silencio.py`:** `_REGLAS`,
`TOOL_SCHEMAS`, literales por AST (sin docstrings ni `logger`), la red (6 frases que frena, 6 que
deja pasar: "Titular: Whuiliany Zabala" incluida), la voz (se salta en CI) y `CLAVES_CONFIG`.
Ajustados `test_dia_imposible` y `test_metodo_de_pago_elegido`, que fijaban la palabra. Lo único
que sigue nombrando a Whuilianny: R130 (2 veces, para "¿eres Whuilianny?") y el titular del pago.

**Cierre (20:05 VET):** #48, #49 (nota de `pedir_ayuda` por motivo: con `pide_persona` puede nombrar a
Whuilianny — su nombre sí, ningún cargo) y #50 ("Restaurar original" del panel devolvía *"Eres
Whuilianny Zabala… la asistente de Whuilianny"*: ahora Alejandra) fusionados; pruebas y PRODUCCIÓN en
`b48d2e8`, 27/27 en ambos. Probado por el simulador y por Maired: a "¿qué pasó con Whuilianny?" el bot
se presenta como Alejandra y sigue (no explica); a "¿puedo hablar con ella?" / "ya no atiende, yo
siempre le compro a ella" → *"Whuilianny te escribe en un momento"* + relevo, y calla después. Maired
lo dio por bueno; queda anotado que a la primera pregunta le saca el cuerpo (opción futura: una línea
en la voz para "cliente que pregunta por Whuilianny").

**Pendientes:** 3 fotos rotas
en pruebas (balde compartido) · separar balde · Whuilianny: franjas + clave del panel · leer las
conversaciones de los 3 clientes cada día · adelgazar el prompt (~71k chars).

## 2026-09-07 (28) — 💵 EL DELIVERY SE COBRA TAMBIÉN EN DÓLARES: se cierra el flete gratis del 22-ago (regla de Whuilianny)

**La regla, en palabras de Whuilianny (por Maired, 7-sep tarde):** *"el único descuento es el 20% si
paga en dólares; el delivery sí lo tiene que pagar la persona"*. Hasta hoy el sistema regalaba el
flete en dólares (decisión del 22-ago, ampliada el 24-ago a Zelle y Binance): Galletas $14 + envío $3
se cobraban **$11.20** en divisas. Desde hoy: **$14.20** (20% sobre los productos + flete completo).
Los bolívares siguen al precio completo, y el 20% sigue atado a la MONEDA, no a la vía (Maired,
24-ago). Se conserva "sobre los productos": el flete ni se descuenta ni se regala.

**Dónde estaba regado (Maired: "creo que los prompts están regados" — sí, y no solo los prompts):**
- La CUENTA: `monto_en_efectivo` (tools.py), la única fuente que usan `generar_datos_pago` (lo que
  se cobra) y `registrar_comprobante` (contra lo que se compara la captura). Un solo cambio arregla
  las dos puertas; el worker (`_montos_cobrados`) lee lo que esa función guardó.
- El PITCH del cobro: *"…con el 20% de descuento y el delivery corre por nuestra cuenta"* → ahora
  *"…con el 20% de descuento sobre los productos (el delivery se paga igual)"*.
- El DESGLOSE: *"Delivery: $0 (normalmente $3, va por nuestra cuenta)"* → *"Delivery: $3"*.
- La VOZ (BRIEF línea PAGOS): *"…y el delivery corre por nuestra cuenta"* → *"…20% de descuento sobre
  los productos; el delivery se paga completo siempre"*. Promovida a PRUEBAS por la puerta del panel.
- Los comentarios del código que enseñaban la regla vieja (bloque del cálculo, `_MONEDA_POR_TIPO`).
- Conocimiento (BD): la entrada "¿Hacen envíos?" ya decía "DELIVERY con costo adicional según la
  zona" y la del descuento no habla del flete — consistentes, sin tocar. El panel no calcula nada.

**Tests y bancos:** `tests/test_pago_en_efectivo.py` reescrito a la regla nueva (la cuenta, las
dos cuentas que perdieron, las dos puertas iguales, el desglose que cuadra) **más 3 tests nuevos**
que vigilan que NADA de lo que lee el modelo diga "por nuestra cuenta", "delivery gratis" ni
"Delivery: $0" (cobro, reglas y voz — misma lección que "la hora es muda"). `probar_delivery`
(bloque 4: $X×0,80 + $3, y el resumen ya NO nombra "nuestra cuenta") y `auditar_plantilla`
($14+$3 → $14.20) actualizados. Suite: 983 en verde.

**Lo que asumí y hay que confirmar con Whuilianny:** (1) el 20% sigue siendo sobre los PRODUCTOS
(no sobre el total con flete); (2) el 20% sigue valiendo para las TRES vías del dólar (efectivo,
Zelle, Binance) — ella dijo "Binance"; si quiso decir SOLO Binance, es otro cambio.

**CI rojo al promover (18:43 VET):** el workflow de producción se frenó en la puerta por UP038 en  (isinstance con tupla): el CI corre ruff 0.9.6 (requirements-dev) y la máquina local tenía 0.16.5, que ya no aplica esa regla. Se corrigió en el PR  y el venv local quedó en 0.9.6. Lección: correr el linter con la versión pineada antes de fusionar.

**Colateral encontrado (no tocado):** hoy 16:41 VET alguien subió fotos nuevas de Galletas New York
en el panel de PRODUCCIÓN (claves `fb6652…` y `992eaa…`) y el objeto viejo (`064f8ef8…`) se borró
del balde R2 — que es COMPARTIDO con pruebas. En pruebas la fila 9 de `producto_media` sigue
apuntando a ese objeto → la foto falló con 131053 en la prueba de las 17:48 y saltó el aviso
"media_no_entregada". Es la mina del balde compartido (ROADMAP → cacería); Maired decide: separar
baldes, o mientras tanto re-subir la foto en el panel de pruebas.

## 2026-09-07 (27) — 🔇 LA HORA ES MUDA, SEGUNDA VUELTA: la frase estaba ESCRITA en 4 sitios más, y la voz presentaba a la dueña

**Lo vio Maired en vivo (pruebas, 16:34 VET):** "A las 10" → *"La hora exacta la confirma la dueña
según su ruta, ella te escribe el martes para coordinarlo"*. Y "¿Tú no eres Whuilianny?" → *"No, soy
Alejandra, la asesora de masvidaconsciente. Whuilianny es la dueña, ella es quien prepara todo y
confirma la entrega"*. Su pregunta: *"¿por qué se confunde así? ¿es el prompt o qué? ¿qué estoy
haciendo mal?"* Respuesta corta: nada; es el prompt, literal. El bot no se confundió: **copió**.

**Autopsia (conversación de la BD + `llamadas_ia` + prompt EXACTO bajado del contenedor con
`construir_partes_prompt` y `TOOL_SCHEMAS`):**
1. **Cronología:** esos dos mensajes (20:34:02Z y 20:34:38Z) los contestó el worker VIEJO (`70b849c`).
   El worker con el #44 arrancó a las 20:34:45Z y el bot a las 20:35:56Z. Esa prueba no vio el #44.
2. **Pero el #44 tampoco alcanzaba:** borró la frase de 5 sitios y la dejó en 4 que el modelo SÍ lee:
   el calendario (`_calendario_texto`, bloque dinámico), la descripción del parámetro `entrega` de
   `registrar_pedido`, la descripción de `anotar_entrega` y el default de `msg_guia_confirmado`
   (tapado en la BD de pruebas). Y encima la nombró 3 veces "en negativo" (*"no digas 'la hora te la
   confirma la dueña'"*), que la hace MÁS presente, no menos — un test la exigía así, literal.
   Conteo en el prompt vivo de `834ef86`: "dueña" **47 veces** (34 estable, 3 dinámico, 10 en las
   herramientas); "confirma la dueña" 4.
3. **"Whuilianny es la dueña"** salía de la voz, línea 7: *"nunca digas que eres Whuilianny (ella es
   la dueña)"* — el paréntesis era una nota para el modelo y el modelo la recitó. R130 repetía lo
   mismo: *"(ella es la dueña, no tú)"*. Con 47 menciones de "la dueña" como tercer personaje que
   cocina, revisa el pago y pone la hora, el modelo describe el reparto que le pintamos.

**Lección (con test esta vez):** arreglar "capa por capa" deja la frase viva en la capa que no se
miró, y prohibir una frase NOMBRÁNDOLA la enseña. La regla queda en positivo y muda: *"de la hora
exacta no digas nada: ni la prometes ni explicas quién la pone"*.

**Dónde se arregló (PR `la-hora-es-muda`):** `system_prompt.py` (R115, R119, R130,
`_lineas_entrega_pendiente`, `_calendario_texto`), `tools.py` (descripciones de `registrar_pedido.entrega`
y `anotar_entrega`; notas de `proxima_fecha_entrega` y `anotar_entrega`), `services/mensajes.py`
(`MENSAJES_DEFAULT`, `_frase_entrega`) y `BRIEF-personalidad-alejandra-2026-09-06.md` — que NO viaja en el repo (`BRIEF-*.md` está en
.gitignore: el repo es público y la voz es de la clienta; vive en la máquina de Maired y su copia en
`respaldos-masvida` quedó actualizada) — (línea 7: *"Soy
Alejandra, la asesora de masvidaconsciente"* y nada más, sin explicar quién es quién; línea 53
sincronizada con la BD de pruebas, que Maired ya había editado desde el panel). **Test nuevo
`tests/test_la_hora_es_muda.py`:** recorre TODO lo que el modelo puede leer sin BD — `_REGLAS`, los
esquemas de las herramientas, los string literales (por AST, sin comentarios ni docstrings) de los
módulos que arman el prompt y las notas, la voz del BRIEF y las salidas de `_lineas_entrega_pendiente`
y `_frase_entrega` — y exige CERO apariciones de "confirma la dueña", "según su ruta", "te la confirma",
"le anuncies", "ella es la dueña". **Banco `probar_prompt_coherente`, bloque 8:** lo mismo EN VIVO, con
la personalidad y las guías de la BD dentro del prompt exacto. `test_prompt_sin_contradicciones` dejó
de exigir la negación. **Personalidad de PRUEBAS** actualizada por la puerta del panel
(`scripts/promover_personalidad.py`); la anterior quedó respaldada fuera del repo.

**Anotado, sin tocar (decisión de Maired):** las 47 menciones de "la dueña" en lo que lee el modelo.
Es la causa de fondo de que narre el negocio en tercera persona; bajarlas es un PR aparte y pide
leer el prompt entero otra vez.

**Pendiente:** fusionar → redesplegar pruebas → Maired repite "a las 10" y "¿tú no eres Whuilianny?"
(esperado: una línea y silencio; "Soy Alejandra, la asesora de masvidaconsciente" sin presentar a nadie)
→ producción (ESTADO → Última verificación) con la voz de este PR (paso 5 usa este mismo BRIEF).

## 2026-09-07 (26) — 🔇 QUE MUERA LA CONVERSACIÓN: sin resumen final tras el pago, sin "la dueña te confirma la hora"

**Lo vio Maired en vivo (pruebas, 16:22 VET):** al decir "a las 10" el bot guardó bien el momento
(pedido #2926: `de 10 a 12 de la mañana`, referencia guardada) pero cerró con *"Listo! Tu pedido
queda así: Galletas New York de chocolate · delivery en… · martes 8 · en la mañana (10 a 12). La
hora exacta te la confirma la dueña según su ruta."* Su veredicto: *"ya se lo dijo arriba, ¿para
qué volver a decirlo? Eso de 'la dueña te confirma' no va. Quiero que muera la conversación."*

**Decisión de negocio REVERTIDA (era suya):** el paso 11 de su plantilla ("resumen final antes del
despacho, pedir confirmación") vivía como regla R135 desde el 22-ago y un test lo defendía (esa
misma noche frenó mi intento de borrarlo, con razón entonces). Hoy ella lo tacha: el resumen se da
UNA vez, al registrar (antes de cobrar); tras el pago solo se confirma el momento de entrega en
una línea y se cierra con calidez. Y "la hora exacta la confirma la dueña" es regla INTERNA (no
prometer hora), no una frase para el cliente: se quitó de R119, R135, del ESTADO DEL CLIENTE, de
las notas de `anotar_entrega` / `proxima_fecha_entrega` y de `_frase_entrega`.

**Dónde se arregló (PR `que-muera-la-conversacion`):** `system_prompt.py` (R119, R135, `_lineas_entrega_pendiente`),
`tools.py` (notas de `anotar_entrega` y `proxima_fecha_entrega`), `services/mensajes.py`
(`_frase_entrega`). Tests reescritos: `test_prompt_sin_contradicciones.test_tras_el_pago_no_hay_resumen_final_y_la_conversacion_muere`
(antes `test_el_resumen_final_antes_del_despacho_existe`) y `test_prompt_compacto.test_5`.
**Colateral visto:** el modelo escribió "(10 a 12)" con paréntesis aunque el dato ya es "de 10 a 12
de la mañana" — lo copió de su propio historial (mensajes viejos de la misma conversación); se
disipa en conversaciones nuevas.

**Pendiente:** Maired limpia la conversación; la pestaña nueva promueve a producción con TODO
(ESTADO → Última verificación, 7 pasos) después de fusionar este PR y redesplegar pruebas.

## 2026-09-06 (25) — 🧭 LA NOCHE DE LAS CUATRO CAPAS: del relleno mudo al prompt compacto, el caché de 1 hora y la voz nueva

**Contexto:** Maired probó el mismo guion (galletas → pistacho → delivery → pago → "8 am") una y
otra vez en pruebas, y cada corrida destapó una capa. Al final pidió dos cosas que quedan como
doctrina: *"deja de arreglar capa por capa: baja el prompt entero y audítalo"* y *"cuestióname
como un senior, no me des la razón"*. Este es el registro de lo que se fusionó (#32→#41) y de lo
que se aprendió.

**1. El relleno mudo, medido CUATRO veces con Sonnet 4.6 ("¿de cuál relleno te llevo?" sin nombrar
ninguno).** Cada arreglo fue real y ninguno bastó solo:
   · #33 la personalidad decía "no recites sabores" y el flujo pedía elegir → regla "una pregunta
     de elección lleva sus opciones".
   · #35 el DATO no existía: los rellenos estaban solo en `descripcion` y `sabores` del tamaño
     vacío → rescate `_opciones_en_descripcion` + `sabores` cargados en pruebas (variantes 9/10/34).
   · #39→#40 el rescate quedó DENTRO de "[SOLO PARA TI, NO lo digas]" y el modelo obedeció el
     rótulo → las opciones van en la LÍNEA VISIBLE del producto.
   · #40 la regla 5 del catálogo decía "sin soltar los rellenos" cuando calzan varios productos
     ('galletas' calza New York y Mini) → era la orden que sobrevivía a todo lo anterior.
   **Lección (test `test_opciones_a_la_vista`):** antes de tocar el prompt, bajar el prompt EXACTO
   del contenedor y leerlo entero. Un dato ausente o un rótulo de silencio se disfrazan de "el
   modelo no obedece".

**2. La auditoría de las tres capas (#40, "prompt compacto")** — se bajó el prompt vivo (67 KB:
personalidad + reglas + catálogo + zonas + dinámico + 14 herramientas) y se cerraron 6 choques de
una vez: regla 5 del catálogo; R102 (el "pitch con de qué está hecho" que producía "harina de
almendra y coco" sin que nadie preguntara); R107 (ejemplo que ponía la fecha a votación); R108
(orden de venta con referencia y franja); R134/R135 (tras el pago: franjas + resumen final del
**paso 11 de la plantilla de Maired** — ojo: la primera versión iba a BORRAR ese resumen por el
"te cuadra así?" de GLM y el test viejo lo frenó: el resumen es requisito del negocio; lo que
sobraba era reconfirmar el pago); schema `registrar_pedido.entrega`. Test `test_prompt_compacto`.

**3. La personalidad nueva (auditoría personalidad vs código; la pegó Maired en pruebas, md5
`6328efca0e2f`).** Sin perder un hecho del negocio: fuera "no recites sabores" (choque 1), fuera
"ningún producto lleva lácteos" (FALSO: kéfir y yogurt de cabra), fuera "seguro para celíacos"
(promesa de salud), fuera la lista de métodos de pago (la fuente es el panel), fuera el ejemplo
literal de la bendición (los dos modelos lo copiaban), alulosa solo si la ficha la trae, y
duplicados con el código (bot/persona, pedir ayuda, tip al cerrar). Nueva línea "al grano": el
producto y sus opciones, sin ingredientes si no los piden. Decisión de Maired: saludo e info en
DOS globos está bien (más real). Copia local gitignored `BRIEF-personalidad-alejandra-2026-09-06.md`.

**4. Lo que el cliente ya eligió no se repregunta (#41).** "Me lo enviaras por delivery" → el bot
ofreció "o retiras en La Mendera": la elección vivía solo en el chat (sin pedido no hay ESTADO)
y la lista cerrada de zonas trae la de retiro como una más. `modo_de_entrega_en` destila
delivery/retiro del chat del cliente y va como HECHO al dinámico; el bloque de zonas y la caja
dejan de preguntar "retira o delivery" sin condición. **Cacería de la clase:** producto/masa/
tamaño/sabor (hilo), cantidad/zona/fecha/método/franja/referencia (ESTADO), nombre (ficha) —
faltaba UNA casilla, el modo de entrega antes del pedido. Las 11 decisiones de una venta la tienen.
Respuesta a Maired ("¿casillas para todo?"): casilla SOLO para lo que no puede fallar ni una vez
(dinero, fecha, entrega); el tono se deja respirar y se mide. La otra palanca real es adelgazar
el prompt (24k tokens) — trabajo lento, con medición, después de producción.

**5. Foto antes de la pregunta final (#36)** — si el último globo pregunta, la media sale antes de
él (Erwin se conserva: nunca antes del saludo). **Fotos ya enviadas en el ESTADO (#37)** para que
el modelo no llame a la foto que la memoria iba a frenar (3 vueltas botadas por venta).

**6. 💾 COSTO (#37 + #38).** Telemetría real: pruebas $0,39/venta con Sonnet; producción 30 días,
6 llamadas, **0% de caché** (TTL 5 min; un negocio chico no recibe mensajes cada 4 min). Cambios:
`cache_control ttl 1h` en las 4 puertas (una constante); `redactar_mensaje` manda las mismas tools
con `tool_choice: none` para pegar en el mismo prefijo (las herramientas viajan por **ContextVar**:
el kwarg rompió 3 bancos cuyos dobles reciben `(messages, modelo)` y `probar_telemetria` vigila esa
firma — #38). Medido: primera llamada de la hora $0,148 (escritura 2×), las siguientes **$0,011**.
GLM-5.3-Flash quedó descartado para la voz ($0,022 pero 7 grietas y 12,6 s por respuesta).

**7. Doctrina de PRs (lo pidió Maired):** un PR por TEMA (problema), no por síntoma ni por área;
la prueba: "¿se explica en una frase sin 'y'?". Un PR es una entrega, no una carpeta: master es la
casa. Y **diagnosticar completo antes de arreglar**.

**Estado al cierre:** pruebas `f60c3f7` + panel `d8b94ab`, 27/27 bancos, cero PRs de código abiertos (solo el #42, los docs de este cierre).
Producción sigue en `42d37de` con la personalidad vieja. **Falta:** que Maired termine el guion
en pruebas y dé el OK → liturgia de promoción (ESTADO, "Última verificación": 7 pasos, incluye
`scripts/promover_personalidad.py` y `scripts/promover_sabores.py`). Cerrado el pendiente de (24):
el schema de `pedir_ayuda` ya no dice "tú ERES Whuilianny" (#34); queda solo `PERSONALIDAD_DEFAULT`
(system_prompt.py) con el nombre viejo, inofensivo mientras la BD tenga personalidad. Anotado sin
cerrar: `probar_vigilante` "el primero se lleva el turno" flaky tras deploy (carrera del lock 120s);
deploys simultáneos bot+worker en Enova fallaron una vez en apt (relanzar solo el que faltó);
datos para Whuilianny (info de las empanadas con plantilla sin llenar, coma final en sabores de
la torta, "CHOCOLATE" en mayúsculas); adelgazar el prompt; medir modo DOS.

## 2026-09-06 (24) — 🚚 LA ENTREGA COMPLETA: franja + referencia con casilla (migración 038)

**El caso (pruebas, 6-sep, el MISMO guion con dos modelos — GLM-5.3-Flash y Sonnet 4.6):** el
cliente dijo "a las 8 am", el bot dijo "anotado"… y no había DÓNDE anotar (`pedidos.notas` y
`clientes.notas` vacíos). Los dos registraron un delivery a "Barquisimeto centro" sin pedir jamás
la dirección: pedido pagado, repartidor sin destino. Y `services/mensajes.py` ordenaba al cierre
del pago "pregúntale a qué hora le queda bien": el propio código empujaba a prometer una hora
que la dueña no controla. Misma clase que el método de pago (035): LA VENTANA SIN ESTADO.

**La regla de negocio (la decidió Maired, 6-sep):** el cliente NO elige una hora, elige una
**FRANJA** de la lista CERRADA de la dueña (`franjas_entrega`, en Horario del panel; sin
configurar, las de fábrica: "en la mañana (10 a 12)" / "en la tarde (2 a 6)"). La HORA EXACTA la
pone Whuilianny según su ruta y la confirma ella desde su teléfono. Un DELIVERY sin referencia
**NO se cobra**. No se construyó "productos que no van en la mañana" (YAGNI: ella confirma igual).

**Lo que cambió (bot, PR `entrega-completa`):**
1. **Migración 038:** `pedidos.entrega_franja` + `pedidos.entrega_referencia` (aditiva, nullable).
2. **Tool nueva `anotar_entrega(franja, referencia, pedido_id)`** — BLINDADA. Franja de vocabulario
   cerrado (`_matchear_franja`: exacta → contención → palabra clave; "8 am" NO calza a propósito);
   referencia con las palabras del cliente (recortada a 300). Acepta pedidos PAGADOS (coordinar
   ocurre después del pago). La referencia no viaja de vuelta al modelo.
3. **`proxima_fecha_entrega` devuelve `franjas_de_entrega`** y su nota ya no dice "la hora la
   coordina Whuilianny" sino "la hora exacta NO existe como opción: ofrece las franjas".
4. **Candado en la CAJA:** `generar_datos_pago` rechaza cobrar un delivery sin referencia
   (`_falta_referencia`, fail-open: solo traba cuando se SABE que la zona no es de retiro).
   `registrar_pedido` avisa lo mismo en su nota para que se pida en el turno natural.
5. **El cierre del pago** (`_frase_entrega` / `contexto_entrega`) pregunta la FRANJA, recuerda la
   ya elegida, pide la referencia si falta — y la pared del dinero no se mueve (test).
   `msg_guia_confirmado` por defecto ya no dice "a qué hora". ⚠️ La copia GUARDADA de la dueña en
   ambas BD dice "dile que coordinan la entrega" (texto viejo, inofensivo): el contexto del código
   manda igual.
6. **ESTADO DEL CLIENTE** muestra franja/referencia guardadas y lo que FALTA (también en pedidos
   pagados). Regla 118 y el bloque del calendario reescritos: franja sí, hora no.
7. **Aviso del guardia de promesas ya no sale desfasado un turno:** `pedir_ayuda` acepta
   `mensaje_cliente` y `_escalar` le pasa el mensaje en vuelo (antes leía `mensajes`, que se
   escribe al FINAL del turno → el aviso del "8 am" decía "(comprobante)").
8. **Panel:** Horario → sección "Franjas de entrega" (una por línea); tarjeta del pedido muestra
   Franja y Referencia ("falta la dirección" en rojo si es delivery sin ella).

**Tests:** `tests/test_entrega_completa.py` (30) — la suite completa en verde (exit 0), ruff limpio,
`tsc` + `next build` del panel en verde.

**Corrección del día:** la agente se llama **Alejandra** (asesora), no Whuilianny — está así en
la voz viva de la BD. "La dueña te confirma la entrega" es correcto en tercera persona.

**Medido hoy (telemetría `llamadas_ia`, mismo guion):** GLM-5.3-Flash $0,022 / 12,6 s por
respuesta / 7 grietas de humanidad; Sonnet 4.6 $0,39 / 2,2 s / mejor voz. Sonnet se queda. El
costo se ataca por código (próximo PR): fotos repetidas frenadas (3 vueltas botadas, ~9%), prefijo
del prompt del comprobante alineado a caché (~19%), y a mediano plazo adelgazar los 24k tokens.

**Pendiente:** repetir el guion en pruebas con esto desplegado; PR "menos tokens, misma voz";
medir modo DOS (Haiku operador + Sonnet voz); el schema de `pedir_ayuda` aún dice "tú ERES
Whuilianny" (debería decir Alejandra).

## 2026-09-05 (23) — 🔒 EL CARRIL DEL PAGO, BLINDADO: los 7 huecos de la cacería (C3, C5-C11)

**Lo ordenó Maired ("arregla los del camino del dinero antes de abrirlo a clientas reales").**
Son los hallazgos confirmados de la cacería adversarial del 3-sep que ningún PR había tocado:

1. **C7 — el doble clic ya no duplica avisos.** Confirmar/Rechazar/Verificar-monto pasaban por
   check-then-act: dos POST casi simultáneos (celular + PC) confirmaban dos veces y el cliente
   recibía DOS mensajes. Ahora la transición se RECLAMA con un `UPDATE … WHERE estado=`
   condicionado (`_reclamar_transicion`): un ganador, el otro recibe 409 — la doctrina de la
   026 aplicada a la transición.
2. **C9 — rechazar un residual ya no resucita el cobro.** Si el pedido está PAGADO por otro
   pago confirmado, rechazar un 'reportado' viejo deja el pedido quieto (antes lo devolvía a
   'esperando_pago' y volvía a ser imán del próximo comprobante). Helper compartido
   `_otro_pago_confirmado_de`, también usado por `/reabrir`.
3. **C8 — `/reabrir` con 409 legible, nunca 500.** Con otro 'reportado' vivo o con el pedido ya
   cobrado, reabrir explica el porqué; el `IntegrityError` de la 026 queda de cinturón (409).
4. **C11 — Redis caído no rompe la confirmación.** El encolado de `notificar_cliente_pago` va
   en `_encolar_notificacion` (try/except): el pago queda bien, la respuesta trae
   `notificacion_encolada: false` y el log grita — antes era un 500 post-commit y el reintento
   chocaba con "ya está confirmado": cliente pagado y mudo para siempre.
5. **C5/C10 — el guardián de la REFERENCIA.** Un comprobante reenviado trae media_id NUEVO (la
   idempotencia no lo ve) y se pegaba a OTRO pedido abierto; si el monto coincidía, el aviso
   decía "CUADRA… pulsa Pago aprobado" — doble cobro con un clic. `_referencia_repetida`
   compara la referencia bancaria contra los pagos vivos del sistema y DEGRADA el aviso a
   "⚠️ OJO: esta referencia YA está en el pago #N del pedido #M — compara antes de aprobar".
   El registro jamás se frena: el dinero no se descarta, el aviso se pone honesto.
6. **C6 — se acabó el silencio del comprobante sin pedido.** Visión dice "es real" pero no hay
   'esperando_pago' (cliente repitente que paga antes del cobro, o reenvío sobre pagado): ahora
   avisa a la dueña con bandeja + WhatsApp (candado 15 min) — "el carril del dinero nunca es
   silencioso", también en este else.
7. **C3 — el simulador toma el lock.** `POST /bot/probar` corre `responder` con el mismo lock
   por teléfono del worker (ocupado ⇒ 429 legible): dos clics rápidos ya no duplican registros
   en la BD de pruebas.

**Tests:** `tests/test_carril_del_pago.py` (15 casos: el doble clic pierde con 409 y cero
commits, el residual no toca el pedido cobrado, los dos 409 de reabrir, el broker caído, el
gemelo de la referencia, y los cableados fijados en fuente). **Suite completa: 883 en verde** ·
ruff ✅ · compileall ✅. Queda anotado (no cerrado): la carrera arquitectónica del lock de 120s
sin renovación (C3-a) — necesita heartbeat, va aparte.

## 2026-09-05 (22) — 🚀 PRODUCCIÓN PROMOVIDA A MASTER COMPLETO (89aea1d) — con la liturgia entera

**Lo pidió Maired de frente ("dale, vamos con la primera") y el gatillo del deploy lo apretó
ella misma** (el clasificador de permisos de Claude bloqueó —con razón— que la IA disparara sola
un deploy a producción; ella dio la orden expresa y se ejecutó). Producción estaba 9 PRs atrás.

**La liturgia, paso a paso y verificada:**
1. **Respaldo previo**: `pg_dump -Fc` (3.8MB) + personalidad a texto →
   `/root/respaldos-pre-promocion/prod_pre_promocion_20260905_2337.*` + foto de datos anotada.
2. **`PUBLIC_BASE_URL=https://api.masvidaconsciente.store`** en bot Y worker de netcup, creada
   por la **API de Coolify** (token de `~/.ssh/coolify_token.txt`; la API de netcup vive
   encendida porque el workflow de deploy la usa — NO se toca). Quedó **CIFRADA** (256 bytes,
   par producción+preview: normal de Coolify). La lección del 3-sep se respetó: jamás INSERT
   directo del value.
3. **Deploy oficial**: `gh workflow run deploy.yml -f destino=produccion` → verde en 2m12s,
   detectores de esquema OK dentro del contenedor nuevo.
4. **Verificación**: bot y worker `89aea1d` (= master exacto) · `PUBLIC_BASE_URL` adentro ·
   **38 migraciones** (036+037: `metodos_pago` ya trae `efectivo`) · datos IDÉNTICOS
   (32/37/299/14.910/0/34) · personalidad 7.331 car intacta · lista blanca intacta (solo
   Maired) · `/salud` ok · Meta GREEN · saldo $8.02.
5. **LOS 27 BANCOS EN VERDE** en el contenedor de producción (`/root/bancos_post_promo.log`),
   corridos con `nohup` para sobrevivir cortes de conexión.
6. **Panel re-desplegado** (API de Coolify): contenedor nuevo arriba, `/login` 200 — la ★ de la
   foto principal ya está en producción.

**Producción trae ahora TODO septiembre**: catálogo PDF que llega · resolvedor de
títulos/sabores/familias · los 5 videos MP4 reales (bucket compartido ya sano) · foto principal
· proactivo=1 + variedad en el "otra vez" · candado de duplicados v2 + prioridad de intención ·
efectivo coherente.

**Lo que sigue (en orden):** pruebas de humo con el número real (casilla 5 de TERMINADO — con
OK de horario por la coexistencia) · anular el pedido basura #2603 de PRUEBAS (sigue en
esperando_pago) · la deuda del carril del pago C5-C11 (cacería del 3-sep, próximo PR) · llave
de IA por cliente + reporte de consumo (adoptado de la comparación con fooddy, SESIONES aparte)
· decisiones de Maired: modelo de producción, rotar clave panel+JWT (D5), abrir lista blanca.

## 2026-09-03 (21) — 🧠 LA INTENCIÓN ACTUAL MANDA + 🔒 DUPLICADO v2 + 💵 EFECTIVO COHERENTE (PR #24)

**Por qué el bot estaba respondiendo tan mal:** la conversación real de pruebas demostró que
una pregunta nueva (*"Recomiéndame algo para la cena"*) seguía atada a las empanadas habladas
antes. El hilo convertía masa/sabor viejos en estado vinculante y las redes de asesoría,
cierre y reapertura corregían el mismo turno en direcciones distintas. La telemetría confirmó
entre **2 y 6 llamadas al modelo por un solo mensaje** (hasta 153.216 tokens de entrada), sin
resolver la pregunta. A las 19:29 ET se sumó otro factor: Sonnet empezó a devolver **HTTP 402**
por saldo casi agotado y el fallback contestó con GPT-4.1; `/salud` quedó degradado con
`saldo_ia` en **$0,6341**. Eso es operación, no un defecto que deba esconder el código.

**Arreglo de arquitectura en esta rama:** una asesoría explícita ahora abre una frontera de
intención: no destila elecciones anteriores como HECHOS, inyecta la prioridad del mensaje
actual y ejecuta la red de asesoría antes de las redes de cierre/reapertura. Consulta catálogo
o ficha, recomienda 1–2 opciones concretas y espera la elección antes de pedir entrega o cobro.
El caso literal quedó como regresión: borrador malo → consulta → recomendación, **3 llamadas y
un solo aviso interno**, sin reabrir la venta anterior.

**Pedido duplicado #2603, candado v2:** la ventana ahora se ancla a la última actividad de
dinero durante 24 h; solo bloquea pedidos con dinero comprometido; acumula cantidades por
variante y no permite escapar con paráfrasis en `opciones`; rechaza `items=[]`; una segunda
tanda idéntica ya pagada pasa a la dueña mediante `pedir_ayuda`. El estado `pagado` deja de
empujar *"pedido nuevo"*: hora y entrega continúan en el pedido pagado, sin registrar ni cobrar
otra vez. También se de-duplica el PDF si el modelo llama dos veces en el mismo turno.

**Efectivo:** la personalidad y el cálculo aceptaban efectivo en dólares con 20%, pero la tabla
activa no tenía esa fila; por eso el bot lo negó. La migración 037 lo siembra solo si falta y
sin duplicar ni reactivar decisiones existentes. El efectivo ya no pide cuenta ni captura de
comprobante: informa el monto calculado y queda pendiente de confirmación de la dueña cuando
reciba el dinero. Los métodos digitales conservan su comprobante.

**Verificación local:** suite completa en verde, `ruff --no-cache` verde y `git diff --check`
verde. Los dos fallos de lectura UTF-8 de los bancos en Windows quedaron corregidos. **Aún no
desplegado**: primero se sube al PR #24; para una prueba viva válida hay que recargar OpenRouter,
desplegar manualmente en Enova y repetir los casos exactos. Producción no se tocó.

---

## 2026-09-03 (20) — 🎬 EL VIDEO QUE NUNCA LLEGÓ + 📸 LA FOTO PRINCIPAL (PRs #21, #22 y panel #1)

**Además esta sesión: el resolvedor (#20) quedó DESPLEGADO en pruebas** (deploy manual por la API
de Coolify, contenedores en `b6c4be9`, `/salud` ok) y el banco `probar_buscador` corrió VERDE
dentro del contenedor vivo contra los 31 productos. La prueba de Maired de las 15:25 confirmó el
resolvedor en vivo ("La Torta keto tiene sabor a chocolate" — nombró la correcta 2 veces).

**1) 🎬 AUTOPSIA + ARREGLO DEL VIDEO 131053 (PR #21).** El video de Tortas keto (msj 9499) NO
murió por peso (0.45 MB) ni URL (HTTP 200): **los 5 videos del catálogo son QuickTime de iPhone
renombrados a .mp4**. Raíz: `_video_ya_sirve` hacía `"mp4" in format_name` y ffprobe reporta
`"mov,mp4,m4a,3gp,3g2,mj2"` para CUALQUIER .mov → el barrido del 14-jul fue un NO-OP (re-subió
bytes idénticos; los 5 Last-Modified en 5 segundos lo prueban). Cura: lista blanca de
`major_brand` ISO + `-map 0:v:0 -map 0:a:0?` (mata los streams `data`) + los videos ya no se
saltan por extensión en `convertir_media_vieja.py`. Reversión verificada (viejo=True/nuevo=False
con el caso qt). **Ops pendiente tras fusionar** (OK de Maired, hora valle): correr el script
corregido — sobreescribe los mismos objetos del bucket compartido, sin borrar — y pedirle a la
dueña un video real de Tortas keto (el actual es 1 frame + audio).

**2) 📸 LA FOTO PRINCIPAL (PR #22 bot + PR #1 dashboard).** Decisión de producto de Maired: el
proactivo muestra LA CARA del producto (1 foto, la ★ de la migración 036; sin marcar = la
primera de siempre), "pidió ver" mantiene hasta 3, y **variedad en el "otra vez"**: las no
vistas primero (jamás desplazan una versión pedida). Endpoint `PATCH /media/{id}/principal`
(molde marcar_agotado; índice parcial único = UNA por producto, doctrina 026). 13 contratos del
proactivo actualizados CON la conducta a propósito; 16 tests nuevos; suite+ruff verdes. El panel
pinta la ★ (2 archivos, molde etiquetarMedia; tsc+build verdes).

**3) 🛡️ REVISIÓN DEL PR #22 CERRADA DESPUÉS DEL LÍMITE DE CLAUDE.** Los 12 reportes crudos se
redujeron a 7 fallos distintos y se corrigieron en la misma rama. La política de fotos quedó en
UNA sola función compartida por la llamada del modelo y la red de rescate: proactivo = 1;
cliente pidió ver/repetir = hasta 3 + `reenviar`. Ahora reconoce frases naturales como
"muéstramela", "quiero verlas", "cómo se ve" y "enséñame"; un `reenviar=True` que ya entendió
el modelo tampoco se recorta. La variedad ya no desplaza ni una etiqueta ni un `variante_id`
pedido; el simulador reporta exactamente lo que guardó; un fallo al leer la memoria fina hace
ROLLBACK y no envenena la sesión; y una carrera al mover la ★ devuelve 409 limpio.

**Pruebas fortalecidas:** el endpoint se ejecuta de verdad (contrato `es_principal` + SQL con
`ORDER BY es_principal DESC`), el UPDATE comprueba producto/valor, y `probar_media.py` ensaya la
★ contra PostgreSQL dentro de una transacción con ROLLBACK. Tras integrar el `master` que ya
incluye el PR #21: **850 tests verdes con UTF-8 · ruff verde · compileall verde · dashboard
tsc+build verdes.** El banco PostgreSQL no pudo correrse en
esta instalación local: su `.venv` no contiene `asyncpg` y no hay PostgreSQL escuchando en
`localhost:5432`; queda como puerta obligatoria post-deploy en pruebas, donde vive el runtime
completo. Producción no se tocó.

**4) ✅ FUSIÓN + DESPLIEGUE EN PRUEBAS + OPERACIÓN DE VIDEOS.** PR #22 fusionado en
`f85f781`; la CI de GitHub pasó y el job de producción quedó omitido por su candado. Antes de
desplegar se creó el respaldo de PostgreSQL
`/root/masvida-pruebas-backups/pre-f85f781-20260903.dump` (3.075.878 bytes, SHA-256
`512b66d75e1a00c281757902f8b66a8494b9b2f516ea05bb8fc368a26935a8f9`) y la migración 036 se
ensayó dentro de una transacción: antes 0/0 columna/índice, dentro 1/1, tras ROLLBACK 0/0.
Bot y worker quedaron en el mismo commit; `/salud` `ok`, Meta GREEN, 37 migraciones.
`probar_migraciones`, `probar_drift` y `probar_media` completos: verdes.

El dashboard PR #1 se fusionó en `c8bf95d` y quedó desplegado en pruebas; el dominio respondió
200 y el OpenAPI del bot publica `PATCH /api/media/{media_id}/principal`. Antes de reescribir R2
se guardaron los 5 originales en
`/root/masvida-pruebas-backups/r2-videos-pre-f85f781` (cada archivo con SHA-256). El barrido
convirtió los 5 QuickTime a MP4 real; una segunda corrida confirmó **0 convertidos / 5 videos ya
sanos**. El video de Tortas keto (media 12) ya es enviable, pero sigue siendo un cuadro fijo con
audio: hace falta que la dueña suba una grabación real. **Producción no se desplegó y la lista
blanca no se tocó.**

## 2026-09-03 (19) — 🧠 PRODUCTO, SABOR Y FAMILIA YA NO COMPITEN POR LA MISMA PALABRA

**Caso real que lo destapó:** la clienta pidió ver las tortas. El bot nombró las dos tortas y,
al describir ``Tortas keto``, escribió *"sabores limón, almendras, chocolate y pistacho"*. La red
de fotos leyó también ``CHOCOLATE`` como un tercer producto (existe con ese título en el catálogo)
y su tope anti-spam apagó las dos fotos. La misma familia aparecía en ``kéfir``: el filtro por
prefijo también alcanzaba ``kéfirado`` y mezclaba Kéfir de Leche con Yogurt Kéfirado.

**Arreglo en la rama `resolver-catalogo-contextual`, PR #20 (sin desplegar):**
- Un mismo resolvedor de títulos gobierna asesoría, fotos y cobro: título completo primero;
  después prefijos naturales del título solo si conservan la identidad. ``kéfir`` resuelve al
  Kéfir de Leche porque es una palabra completa de SU título; ``kéfirado`` sigue siendo otra.
- El contexto conserva los límites de cada idea y separa producto de atributo: ``chocolate`` a
  secas = producto CHOCOLATE; ``sabor chocolate`` o ``torta de chocolate`` = atributo; ``untable
  de chocolate`` = el título más largo, Untable de Chocolate.
- Una familia abreviada conserva todos sus miembros: ``las tortas`` devuelve las DOS tortas y la
  red puede mandar una foto de cada una; en el carril del dinero devuelve ambigüedad y obliga a
  preguntar cuál. Una categoría directa manda sobre un parecido de título: ``harinas`` incluye
  Premezclas y ``dulces`` devuelve toda Dulcería.
- El difuso ya no puede borrar un tipo exacto: ``torta de chocolate`` nunca cae en Untable de
  Chocolate. Si hay varias tortas con ese sabor, pregunta cuál antes de cobrar.

**Pruebas:** caso unitario con la respuesta REAL del chat + Kéfir/Yogurt/Chocolate/Untable,
categorías y carril estricto; **815 tests en verde** + ruff ✅. El banco `probar_buscador` se
corrió de solo lectura contra los **31 productos vivos de pruebas**: todo verde, incluidas las dos
tortas y la red de fotos. No se modificó la BD, no se envió WhatsApp y no se tocó producción.

**Pendiente:** revisar/fusionar el PR #20, desplegar bot + worker en pruebas y hacer una conversación
manual con el número de Enova antes de considerar producción.

---

## 2026-09-03 (18) — 🔧 INFRA DEL PDF ARREGLADA EN PRUEBAS: VARIABLE CIFRADA + REDEPLOY VERDE

**Cerrado el paso de infraestructura del entorno de pruebas.** El PR #18 ya estaba fusionado en
`master` (`a798aac`), pero el primer intento de redeploy de bot + worker falló antes de construir.
La causa NO era el código: `PUBLIC_BASE_URL` se había insertado directamente en la BD de Coolify
como texto plano con `is_literal=false`; al cargarla, Coolify intentó descifrarla y abortó con
`Illuminate\Contracts\Encryption\DecryptException: The payload is invalid`. Los contenedores
anteriores siguieron arriba y sanos durante el fallo.

**Reparación, con red de seguridad:** se identificaron las dos filas exactas (bot y worker), se
ensayó su borrado dentro de una transacción y se hizo **ROLLBACK** (2/2 restauradas). Después se
retiraron solamente esas filas y `PUBLIC_BASE_URL` se creó por el endpoint oficial de variables de
Coolify. La versión 4.1.2 genera por diseño una fila activa y otra `preview` por aplicación: **4/4
cifradas**, 2 activas + 2 preview, 2 aplicaciones. Coolify pudo leer ambos juegos antes de desplegar.
La API temporal quedó otra vez en `false` y el token temporal se borró (**0 restantes**).

**Resultado vivo (3-sep, 12:35 ET):** bot `fjnlsug6i4mt…` y worker `t53qwgg10u2o…` terminaron en
`finished`, ambos con commit **`a798aac`** y `PUBLIC_BASE_URL` correcta dentro del contenedor.
`/salud` = **200 · `estado: ok` · Meta GREEN · 36 migraciones**. El enlace que recibe Meta
(`/api/catalogo/archivo`) devuelve **200 · `application/pdf` · 2.803.311 bytes · firma `%PDF-`**.

**No se tocó producción y no se envió ningún WhatsApp proactivo.** Falta que Maired pida el catálogo
desde el número de pruebas para verificar el último tramo Meta → cliente. Después, con su OK
explícito, repetir en producción con `PUBLIC_BASE_URL=https://api.masvidaconsciente.store`.

---

## 2026-09-03 (17) — 📄 EL CATÁLOGO EN PDF: MATA EL DEFAULT MUERTO Y AVISA CUANDO NO LLEGA (PR #18, el paso 3 del plan)

**El código que cierra el bug diagnosticado el 2-sep (entrada (15)).** Maired lo pidió de frente:
*"que no se repita"*. La autopsia ya tenía la causa raíz con evidencia de la BD; esta sesión
escribió el arreglo — el paso 3 del plan (el del código; los pasos 1 y 2 son variables de entorno,
al final). Reproducido primero en el mapa del código, no de memoria.

**La raíz, recordada:** `config.py` traía **hardcodeada** la URL del taller
(`https://api-masvida.enovagroup.tech`) como default de `public_base_url`. Muerto el taller, el
link del PDF apuntaba a un dominio muerto en TODOS los entornos; Meta no podía descargarlo (**131053
Media upload error**) y el envío moría en silencio. Verificado: `public_base_url` se usa en UN solo
sitio, [`tools.py`](masvidaconsciente-bot/app/agent/tools.py) → el link del catálogo.

**Tres piezas, que cierran CADA camino de recaída (aditivo, nada borrado):**
1. 🩹 **`config.py` mata la enfermedad.** `public_base_url` **sin default** (cada entorno pone la
   suya en Coolify) + validador que **avisa fuerte al arranque** pero **NO bloquea**. La decisión
   doctrinal —y aquí desobedecí a propósito el *"fail-fast como JWT_SECRET"* del ROADMAP—: el
   validador del buffer que vive 20 líneas más arriba ya sentó el precedente (*"en esta casa se
   DEGRADA, nunca se bloquea la venta"*). La URL solo la usa el catálogo: apagar webhook + worker
   + bancos + scripts por un PDF sería bloquear la venta por una pieza que se degrada sola — y un
   `raise` reventaría hasta el import de los tests (lo avisa `conftest.py`). Helper puro
   `url_publica_utilizable()`.
2. 🩹 **`enviar_catalogo` degrada limpio.** URL que no sirve ⇒ manda el **catálogo de TEXTO**
   (`ver_catalogo`) en vez de un PDF condenado. El cliente igual recibe el catálogo; el simulador
   queda exento (es falso, no baja nada de Meta).
3. 🛡️ **El webhook evita la PRÓXIMA autopsia.** `_avisar_media_no_entregada` le manda **WhatsApp a
   la dueña** cuando Meta reporta el `fallido` **131053/131052**. Es el ÚNICO que atrapa el caso que
   1 y 2 no pueden: un link `https` con buena forma pero **host caído** (exactamente lo que pasó con
   el taller). Antes ese fallo solo dejaba un `logger.error("ENVÍO FALLIDO")` que nadie miraba —
   por eso hizo falta la autopsia. Códigos nuevos en `meta_client.py` (`CODIGOS_DE_MEDIA`), disjuntos
   de los de calidad: cada `failed` dispara como mucho una de las dos telemetrías.

**Pruebas.** `tests/test_catalogo_url_publica.py` (CI, puro) con **reversión-roja verificada** (sin
el guardia, el test cae) + caso `caso_media_no_entregada` en el banco `probar_meta.py` (post-deploy).
**793 tests en verde**, ruff ✅, compileall ✅. *(Los 2 rojos de la corrida local son un problema de
encoding de Windows —Python 3.14 lee los `.py` como cp1252—, fallan igual en master y son verdes en
la CI de Linux.)*

**⚠️ FALTA (infra, NO código) — los pasos 1 y 2 del plan, para Maired/Erwin:** definir
`PUBLIC_BASE_URL` en Coolify → **pruebas** (`https://jthc51…sslip.io`) y **producción**
(`https://api.masvidaconsciente.store`, con su OK), en bot **y** worker, y redeploy. 🟢 **El orden
ahora es SEGURO gracias a este PR:** desplegar el código sin la variable NO tumba el bot — solo
degrada el catálogo a texto y grita en el log; la variable RE-ENCIENDE el PDF. Se fue el
acoplamiento peligroso de orden que tenía el plan original.

## 2026-09-03 (16) — 🛡️ LAS MINAS "YA" QUEDAN DESACTIVADAS (con OK general de Maired: "si hay que hacerlo, hay que hacer todo esto")

**Ejecutado y VERIFICADO el mismo día de la cacería (las minas: entrada (15) y checklist en ROADMAP):**
1. ✅ **Secreto de sesiones del panel SEPARADO en pruebas** — generado EN el servidor (nunca
   impreso), aplicado a bot y worker vía la API de Coolify de Enova (activada→usada→apagada,
   token temporal borrado) + deploy real. Verificado dentro de los contenedores: huella nueva,
   idéntica bot=worker, distinta de la compartida. Login de Maired intacto (solo se cerró la
   sesión abierta). *(El de PRODUCCIÓN se rota con el resto de D5 — sigue pendiente.)*
2. ✅ **`DUENO_TELEFONO` definido en los 4 contenedores** (bot/worker × pruebas/producción) —
   el aviso "el panel está perdiendo mensajes" por fin tiene a quién llegar. Producción se
   re-desplegó (lista blanca verificada intacta; `/salud` ok, Meta GREEN, 36 migraciones).
3. ✅ **EL VIGÍA de producción** — nació la vigilancia externa que `salud.py:49` prometía contra
   el dominio muerto: cron cada 2 min EN EL VPS DE ENOVA (cruzado: un servidor vigila al otro)
   → `GET /salud` de producción con la regla DOBLE (200 **y** `"estado":"ok"`) → al 2º fallo
   seguido, **WhatsApp a la dueña** desde el bot de pruebas (credenciales leídas del contenedor
   al momento, nada en disco), repetición máx. cada 30 min + mensaje de "volvió". Probado en
   vivo con fallo simulado: la alerta y la recuperación llegaron al teléfono de Maired.
   Vive en `/root/vigia-masvida/vigia.sh` (VPS de Enova). *(Mejora futura: UptimeRobot además,
   por si se cae el propio VPS de Enova.)*
4. ✅ De paso verificado: **el respaldo de producción VIVO** (snapshot del mismo día, 54 en
   total) · el saldo IA bajó $4.11→$2.90 en dos días — la evidencia viva de la llave de
   OpenRouter compartida (su separación quedó "antes de la entrega": la llave nueva la crea
   Maired en OpenRouter, 3 min guiados).
5. 🔵 **Fotos (bucket compartido): Maired decidió ESPERAR.** Aclarado que NO es "reinstalar
   Coolify": es crear un balde nuevo + copiar ~35 archivos + 5 variables (~30-45 min). Las
   llaves R2 actuales están limitadas al balde (verificado: ListBuckets denegado), así que hará
   falta que ella cree el balde y su token en Cloudflare (~2 min) el día que se haga. **Mientras
   tanto rige la regla: NO borrar fotos desde el panel de pruebas** (subir sí es seguro).

## 2026-09-02 (15) — 🌐 El panel recupera su dirección bonita + 🔬 LA AUTOPSIA DEL CATÁLOGO: por fin se sabe POR QUÉ el PDF no llega

**Primera sesión completa sobre el entorno de pruebas de Enova. Dos frentes: uno cerrado, uno
diagnosticado listo para cerrar.**

### 🌐 Frente 1 (CERRADO): `panel-masvida.enovagroup.tech` vive de nuevo

Maired intentó entrar al panel por su marcador de siempre y estaba muerto (ese dominio apuntaba
al Hostinger cancelado). Lo que se hizo, en orden:
1. **Maired creó en Namecheap** (`enovagroup.tech` → Advanced DNS) dos registros A →
   `152.53.194.89`: `panel-masvida` y `api-masvida`. *(El registro `coolify` ya existía — es del
   socio, no se toca. Ojo: hay un comodín `*` → la IP vieja de Hostinger que confunde los
   `nslookup` con caché; verificar propagación por DNS-over-HTTPS `dns.google/resolve`.)*
2. **Claude configuró Coolify por la API** (que estaba APAGADA a nivel de instancia: se activó
   por la BD, token Sanctum temporal por `artisan tinker` con `team_id=0`, y al terminar se
   **restauró todo** — API apagada de nuevo, token borrado): dominio dual en el panel
   (`https://panel-masvida.enovagroup.tech` + el sslip de respaldo) y redeploy.
3. **Verificado de punta a punta:** cert Let's Encrypt válido, `/login` 200, el bundle del panel
   llama al bot por `https` y el login responde 200. *(El tropiezo del login de Maired era una
   minúscula en la clave — el endpoint `/api/login` sirvió de oráculo para confirmar la variante
   correcta sin tocar nada.)*
4. **Decisión de Maired (con razones):** el BOT se queda en su sslip — NO se le da el dominio
   bonito por ahora. El beneficio era higiene/futuro, y aunque el cambio de webhook por-WABA es
   seguro (lección del 1-sep), su instinto de no tocar Meta sin necesidad es sano. **Y la
   autopsia de abajo le dio la razón sin saberlo:** ver la trampa del final.

### 🔬 Frente 2 (DIAGNOSTICADO): el catálogo en PDF — "el bot dice que lo mandó y no llega"

Maired lo dijo de frente: *"el catálogo no sé qué pasa… por qué seguimos con ese MISMO error"*.
Esta vez el error dejó huellas frescas (ella probó la noche del 2-sep) y la autopsia lo cerró
**con evidencia de la BD, no de memoria** (regla de oro §8):

**La conversación de las 22:29 en `mensajes` (BD de pruebas):**
| id | qué fue | estado |
|---|---|---|
| 9479 | Ella: "Me envías el catálogo por favor" | — |
| 9480 | Bot (texto): "Ahí te dejo el catálogo…" | ✅ entregado |
| 9481 | Bot (document): el PDF | 🔴 **`fallido` — `131053: Media upload error`** |
| 9482 | Ella: "No me has enviado" | — |

**La causa raíz, en una línea de código:** `config.py:110` →
`public_base_url: str = "https://api-masvida.enovagroup.tech"` **hardcodeado como default**. El
link del PDF se arma con eso (`tools.py` → `{public_base_url}/api/catalogo/archivo`), Meta
intenta DESCARGARLO de un dominio que murió con el taller, no puede, y el documento jamás sale
de Meta. El texto sí llega — por eso el bot "jura" que lo mandó. Ni el bot ni el worker de
pruebas definen `PUBLIC_BASE_URL` (verificado en los env de los contenedores).

**🔴 Y PRODUCCIÓN TIENE LA MISMA MINA** (verificado en el contenedor VIVO de netcup: misma línea,
misma variable sin definir). Hoy la tapa la lista blanca; la pisará la casilla 5 de "TERMINADO"
(pruebas de humo con catálogo) o la primera clienta real que pida el PDF.

**Por qué es "el MISMO error" para Maired y NO es el mismo error por dentro:** en junio el bot
DECÍA que mandaba el catálogo sin llamar la herramienta — eso lo tapó la red `_asegurar_catalogo`
y quedó cerrado. Ahora el bot SÍ llama la herramienta y SÍ manda: es **Meta quien no puede
descargar el archivo**. Mismo síntoma en el chat, raíz nueva. Lección: una red que garantiza el
ENVÍO no garantiza la ENTREGA — `estado='fallido'` en `mensajes` es la columna que dice la verdad.

**Bonus de la misma autopsia:** los errores SSL del panel ("No se pudo traer el archivo remoto
del mensaje 9478") son la MISMA raíz — 2 mensajes viejos con `media_url` del taller muerto, que
hoy resuelve al VPS de Enova sin router → Traefik responde con su cert self-signed. Cosmético.

**El plan de arreglo (3 pasos, en ROADMAP → "LA SIGUIENTE TAREA") espera el OK de Maired:**
pruebas por env (2 min, sin Meta) → producción por env (2 min, sin Meta, con su OK) → PR que
mata el default hardcodeado (fail-fast como `JWT_SECRET`). ⚠️ **La trampa documentada:** darle
`api-masvida.enovagroup.tech` al bot de pruebas ANTES de arreglar producción serviría el catálogo
de pruebas a los clientes de producción — el "dominio bonito para el bot" quedó correctamente
pospuesto.

### 🩻 Y LA RADIOGRAFÍA COMPLETA: "el catálogo" no es UN error — son 5 familias

La misma sesión corrió una radiografía del subsistema (4 investigadores en paralelo — historia
del diario, código, datos, historial git — + síntesis; hallazgos clave verificados a mano
después). **Lo que Maired ve como "ese mismo error" son 5 síntomas distintos que se han arreglado
en oleadas** (el ROADMAP declaró "catálogo RESUELTO" el 21-jun, commit `102fadf`, y hubo 15+
arreglos después — "resuelto" siempre fue "resuelta UNA causa"):
1. **Ofrece/cobra el producto EQUIVOCADO** — 6 oleadas jun→ago, cada una con raíz distinta.
   Vivo hoy: "hamburguesa" calza por prefijo con "Pan de Hamburguesa" y lo presenta con certeza.
2. **Repregunta lo ya elegido** — el plan A→D lo cerró (1-sep) con 3 fronteras admitidas:
   sabores que viven SOLO en la prosa (invisibles al hilo), modo `dos`, vigilante de una pasada.
3. **El catálogo fantasma** — la familia de ESTA autopsia. Dos huecos vivos verificados:
   el default muerto de `config.py:110` (la causa de anoche) **y** que `enviar_catalogo` devuelve
   `ok=True` al ENCOLAR, no al entregar (`cola_media.py:136-137` traga el fallo y solo loguea) —
   la red `_asegurar_catalogo` de junio es ciega a esta vía; `mensajes.estado='fallido'` es el
   único testigo.
4. **Niega lo que el negocio SÍ ofrece** — hueco de DATOS, no de código (hogaza, rústicos,
   hamburguesas, veganas no están en la BD; 9 productos sin foto). Ningún PR lo arregla: o se
   cargan los productos o se sacan de la oferta. Es de Whuilianny/Maired.
5. **El PDF es una TERCERA copia de la verdad sin vigilante** — la dueña lo subió una vez
   (`catalogo_pdf` en BD, bytes estáticos); si después cambió precios/agotados en el panel, el
   PDF viejo se sigue mandando tal cual y nadie avisa (ni fecha de subida guarda).

**Hallazgo colateral del panel (verificado a mano en `router.py:752-754`):** editar un producto
de tamaño único PISA `variantes[0].presentacion` y `.disponible` con lo del formulario — corregir
un typo en la descripción puede **resucitar un tamaño agotado** en silencio. Candidato fuerte si
lo que Maired ve es "el catálogo del panel se porta raro". Anotado para verificar el circuito
completo (formulario → API) la próxima sesión.

**Para arrancar la próxima sesión — las preguntas que clasifican SU síntoma (1 captura basta):**
¿el bot nombró un producto DISTINTO al pedido? → familia 1 · ¿repreguntó algo ya dicho? →
familia 2 (¿dónde y cuándo probó? ¿el sabor vive en la casilla o en la prosa?) · ¿dijo "te lo
mando" y no llegó? → familia 3 (la de anoche, arreglo ya diseñado) · ¿negó algo que sí venden? →
familia 4 (datos) · ¿el PDF muestra precios viejos? → familia 5.

### 🧨 Y LA SEGUNDA PREGUNTA DE MAIRED: "¿tenemos riesgo de que otra cosa así pase, con la estructura que tenemos?"

Pregunta correcta — el catálogo pertenece a una CLASE ("algo apunta a infraestructura muerta o
de OTRO entorno, funcionaba de casualidad, y falla o contamina en silencio"), así que se corrió
una **cacería de esa clase completa**: 4 lentes en paralelo (defaults hardcodeados · fallos
silenciosos · infra muerta ejecutable · acoplamientos entre entornos) + síntesis, alimentadas
con datos EN VIVO de los dos servidores (envs comparadas por huella sha256, sin exponer valores)
y las minas graves re-verificadas a mano.

**Lo SANO primero (que también es respuesta):** BDs, Redis y WABAs bien separadas entre pruebas
y producción · el pipeline de deploy limpio (push a master = SOLO la CI; producción exige un
humano eligiendo `produccion=true`) · ningún script Python con hosts muertos en el camino que
corre · **el respaldo de producción está VIVO** (verificado ese día: `[backup] OK` a las 11:11,
54 copias, la última del mismo día).

**Las minas, rankeadas (el detalle operativo quedó con Maired; aquí las acciones):**
1. 🔴 **Pruebas y producción COMPARTEN 3 cosas que no deben** (herencia de clonar el env del
   taller): el **secreto de sesiones del panel** (separarlo por entorno: 1 variable + redeploy,
   ya en la lista de rotación D5) · el **balde de fotos R2** (`masvida-media` único: borrar o
   "mantener" fotos desde pruebas toca los archivos que producción sirve — separar
   bucket/prefijo; MIENTRAS TANTO: no borrar fotos en el panel de pruebas, y
   `recomprimir_fotos.py`/`convertir_media_vieja.py` SOLO en producción) · la **llave de
   OpenRouter** (las pruebas gastan el saldo de producción y comparten sus límites — llave
   propia con tope para pruebas; hoy el freno anti-abuso cuenta mensajes en el Redis LOCAL de
   cada entorno, no dólares de la cuenta común).
2. 🔴 **Nadie vigila producción desde fuera:** `salud.py:49` afirma que un monitor externo vigila
   `/salud`… en la URL del taller MUERTO, y no hay rastro de monitor contra la URL viva. El
   incidente de julio (bot mudo por saldo, todo en verde) puede repetirse idéntico. 15 min.
3. 🔴 **El aviso "el panel está perdiendo mensajes" no tiene a quién llegar:** lee
   `DUENO_TELEFONO` del ENV a propósito (se dispara cuando la BD — donde vive el teléfono
   editable — acaba de fallar), pero la variable no está definida en NINGÚN contenedor y el
   default es `""` → ese aviso jamás sale (`tasks.py:134` + `config.py:96`). Definirla en los 4.
4. 🟠 **El respaldo de producción no tiene testigo** (si un día muere — p. ej. al rotar las
   llaves R2 de D5 — nadie grita) y su manual de emergencia apunta a una ruta que NO existe en
   la máquina de Maired y a un servicio del compose que nunca se desplegó. Corregir RESPALDO.md
   + sonda de edad del último snapshot (o ping a Healthchecks).
5. 🟠 **D2 se REABRIÓ en silencio:** "los bancos corren solos tras cada deploy" murió con el
   taller (el deploy de producción los corre A MANO); el ROADMAP aún la da por cerrada. La CI
   podría recuperar 24/27 con un Postgres desechable (la receta ya existe: `banco_local.sh`).
   Y **D4 cambió de casa:** pruebas-Enova tiene CERO respaldo (verificado: ni contenedor, ni
   cron, ni programado en Coolify) — el piso es el dump local del 1-sep.
6. 🟡 Menores: `promover_a_produccion.sh` apunta por default al Hostinger muerto y su
   `TRUNCATE+COPY` de `producto_media` re-ataría los buckets en la próxima promoción (decidir
   las fotos ANTES de promover) · `correr_bancos.py` aún avisa por WhatsApp hablando "del
   taller" · la palabra "taller" quedó como guarda vacía en scripts que escriben BD
   (`--confirmar-taller`) · el compose de la fábrica re-sembraría el default muerto en un
   cliente nuevo.

**Falsas alarmas (no re-investigar):** `HISTORIAL_RESPALDO_DIAS` solo-en-pruebas (default
idéntico, mecanismo 100% local) · `META_APP_SECRET` compartido (CORRECTO: ambas WABAs viven
bajo la misma app de Meta) · subir fotos nuevas en pruebas (cada archivo nace con uuid propio,
no pisa nada).

**El patrón de fondo (doctrina):** pruebas nació CLONANDO el env del taller, y el taller
compartía con producción cosas que un entorno de la agencia no debe compartir. La regla que mata
la clase entera: **cada entorno con SUS llaves y SUS baldes; lo único compartido es el código.**
Al montar el próximo entorno (demo/cliente de Enova), esa es la lista de chequeo.

**Además esta sesión:** PR #16 fusionado por Maired (2:28am) · sigue pendiente rotar las
credenciales expuestas del 1-sep (Coolify de Enova + proveedor del VPS) y decidir el `modelo_ia`
de producción (Sonnet aprobado vs Haiku actual).

## 2026-09-01 (14) — 🏭 NACE EL ENTORNO DE PRUEBAS DE ENOVA: el taller resucita en el VPS del socio — y RESPONDE

**El plan de la entrada (13) cambió sobre la marcha, por decisión de Maired:** el espacio de
práctica NO va en netcup — va en el **VPS del socio de Enova** (Coolify propio,
`coolify.enovagroup.tech`), porque es infraestructura de la AGENCIA (sirve para practicar hoy y
para demos/clientes mañana) y deja netcup SOLO para la clienta. Aislamiento total: servidor, BD,
número y WABA propios.

**Qué quedó corriendo** (proyecto `masvida-pruebas`): bot + worker + panel + PostgreSQL 16 +
Redis 7, construidos desde GitHub `master` (el merge del PR #15), mismos Dockerfiles que
producción. **Auto-deploy OFF en las 3 apps** (la regla de siempre: deploy solo manual). BD =
el **dump FINAL del taller restaurado** (36 migraciones, 6 clientes, 32 productos, 10 de
conocimiento). Modelo: `claude-sonnet-4.6` — el que Maired probó y aprobó en el taller.

**🎓 La lección GRANDE de Meta (costó una hora de vueltas, que nadie la repita):** hay DOS
niveles de webhook, y confundirlos es peligroso:
- **Nivel App** ("Enova API", en Meta Developers): la central del Tech Provider
  (`sistema-recepcion-digital`). **NO SE TOCA JAMÁS** — por ahí entran TODOS los clientes de
  Enova; re-apuntarla mezclaría los mensajes de todos.
- **Nivel WABA** (por cliente): el del bot. La WABA del número de la agencia ("Enova Soporte",
  +57 313 2933806) es **SEPARADA** de la de la clienta — verificado EN VIVO leyendo los env de
  los DOS servidores (phone_number_id y WABA distintos, y el token de pruebas ni siquiera puede
  LEER la WABA de producción). Cambiar la de prueba no roza producción.

**El webhook se re-apuntó SIN interfaz** (la vista de socios de WhatsApp Manager negaba el
acceso): por la **Graph API** — `GET/POST /{waba_id}/subscribed_apps` con
`override_callback_uri` (el POST solo da `success` si Meta verifica el handshake contra el bot
en ese momento). Antes apuntaba al taller muerto (`api-masvida.enovagroup.tech`). ⚠️ El endpoint
del bot es **`/webhook/whatsapp`**, no `/webhook`.

**Tropiezos del montaje, documentados para la próxima vez:** Coolify auto-detecta la rama
`main` (estos repos usan `master`) · el dominio en Coolify debe declararse `https://` o Traefik
no crea la ruta TLS (da 503 — y Meta EXIGE https) · el bot heredó el verify token VIEJO del env
`erzq` (el canónico es el del taller final, `env_qlfrx` en `respaldos-masvida/`) — alineado ·
el panel necesita `NEXT_PUBLIC_API_URL` como build-arg o nace ciego.

**🔥 PRUEBA DE FUEGO SUPERADA (~10pm):** Maired escribió "Hola" desde su número → respuesta en
**5,8 segundos**. En los logs, el circuito entero: webhook 200 → worker → **memoria del taller
rescatada de Postgres (13 mensajes de ella)** → sonnet-4.6 con 13/13 tools → 2 mensajes de
vuelta por Graph API. No es un bot nuevo: **es el taller con su memoria intacta, en casa de
Enova.** Producción ni se enteró (cero logs).

**🔴 Pendientes que deja esta sesión:** (1) **rotar las credenciales que se pegaron en un chat
durante el montaje** (Coolify de Enova + panel del proveedor del VPS) — sin urgencia de
incidente, pero pronto; (2) decidir el `modelo_ia` de producción (Sonnet aprobado vs Haiku
actual — viene de la (12)); (3) el panel de pruebas se construyó apuntando al bot por `http` —
funciona, pero conviene rebuild con la URL `https`; (4) decidir el DNS muerto
`api-masvida.enovagroup.tech` en Namecheap (apunta al Hostinger cancelado): re-apuntarlo al VPS
de Enova o retirarlo.

## 2026-09-01 (11) — 🛡️ HARDENING punto 1: el SO al día + parches de seguridad AUTOMÁTICOS

**La causa raíz del incidente era software sin actualizar; esto lo cierra.** Con OK de Maired,
taller primero:
- **TALLER (Debian 11):** 7 paquetes (toda la suite Docker → 29.7.2). Sin reinicio requerido.
  Los contenedores rebotaron por el restart del daemon y volvieron solos; `/salud` ok.
- **PRODUCCIÓN (Debian 13):** los 45 paquetes aplicados (Docker 29.7.2, libssl de seguridad,
  systemd, libc…). 🎯 **Cazado un retenido:** `apt-get upgrade` clásico NO instala paquetes
  nuevos, y el KERNEL de seguridad (6.12.107) quedó "kept back" — se instaló explícito
  (`apt-get install linux-image-amd64`); está en `/boot` y GRUB listo. Contenedores volvieron
  solos, `/salud` ok, firewall+fail2ban activos, **los límites en caliente SOBREVIVIERON** al
  restart del daemon (docker update persiste; solo un re-CREATE los borra).
- **`unattended-upgrades` instalado y activo EN AMBOS**: solo orígenes Debian-Security,
  `Automatic-Reboot "false"` explícito (jamás se reinicia solo). Los 45 pendientes no se
  acumulan más.
- ⏳ **PENDIENTE: el REINICIO de producción** para ACTIVAR el kernel 6.12.107 (hoy corre
  6.12.94). Tumba todo 1-3 min; contenedores con `unless-stopped`/`always` verificado (vuelven
  solos); bot cerrado a clientas ⇒ impacto cero. **Decisión de Maired el momento.**

## 2026-09-01 (13) — 🧳 RESCATE FINAL DEL TALLER + el plan del "entorno de práctica" de Maired

**El porqué de fondo, dicho por Maired:** *"no quiero mostrarle a Whuilianny fallas... por eso
yo estaba utilizando todo allá [el taller], en mi número"*. Su necesidad real: un espacio de
práctica por WhatsApp REAL, invisible para la clienta. El taller era eso.

**Rescatado del taller a `respaldos-masvida/` ANTES del apagón (además del dump de la BD):**
- `taller-env-FINAL/`: los env COMPLETOS de bot/worker/panel — incluidas las 4 credenciales de
  Meta del **número de la agencia** (+57 313 293 3806): phone_number_id, token, app secret,
  WABA. Sin esto, reconectar ese número habría requerido re-hacer el onboarding.
- `taller-root-FINAL/`: `banco_local.sh` (¡vivía SOLO en /root del taller, contra la regla
  "todo en GitHub"! — **añadido al repo**, adaptado: siembra desde el dump rescatado o desde
  producción, y tolera venv de Windows) · `auditar_coolify.sh` · las 6 personalidades
  históricas + `masvida_antes_variantes.sql.gz` en un tgz.

**El plan propuesto para su espacio de práctica (pendiente de su OK y del DNS):** un segundo
stack (bot+worker+postgres+redis propios) EN EL MISMO VPS de netcup — costo $0/mes — conectado
al número de la agencia, con la BD restaurada del dump del taller (nace idéntico a lo que ella
conocía). Whuilianny no ve nada (número y BD separados). Necesita: re-apuntar el DNS de
`api-masvida.enovagroup.tech` (Namecheap) de Hostinger → netcup, crear las apps en Coolify con
los env rescatados, y restaurar el dump. Cabe en recursos (~500MB; hay ~2GB libres con los
límites puestos). ⚠️ NO mezclar con el sistema de la clienta: ni el botón de alternar números
ni dos números en un bot — la BD compartida mezclaría pruebas con ventas reales.

## 2026-09-01 (12) — 🪦 EL TALLER SE JUBILA: respaldo final, verificación "¿todo está en producción?", y el pipeline a un solo servidor

**Maired canceló el VPS del taller (Hostinger) — se apaga HOY.** Su lógica: un cliente por
entregar no justifica dos servidores, y gastar en diagnosticar el deploy roto del taller era
tirar esfuerzo. Correcto. Lo que se hizo ANTES de que se apague, en orden:

1. **RESPALDO FINAL a la máquina de Maired** (el seguro de vida, primero que todo):
   `respaldos-masvida/taller_FINAL_antes_de_apagar_20260901.dump` (3MB, pg_dump -Fc completo)
   + `personalidad_taller_FINAL_20260901.txt` (7.473 car).
2. **Su pregunta "¿todo lo del taller está en producción?" — verificada dato a dato, NO de
   memoria:** reporte de 125 líneas por lado (config por hash, variantes con sabores,
   conocimiento, zonas, anticipación, fotos con etiqueta) y `diff`. **IDÉNTICOS en todo lo del
   negocio** — personalidad con el MISMO hash, los sabores de Maired, las 10 de conocimiento,
   zonas $2/$5/$0, anticipación 16/12/4. Diferencias solo operativas (timestamp del barredor,
   claves vacías del modo dos, `numeros_permitidos_extra` del taller) **menos UNA de fondo:
   `modelo_ia` — el taller (donde Maired probó hoy y le gustó) corre `claude-sonnet-4.6`;
   producción tiene `claude-haiku-4.5`. Si se entrega hoy, la clienta viviría el bot de Haiku,
   NO el que Maired aprobó. Decisión de Maired pendiente** (Sonnet = calidad probada, más
   gasto de saldo; el saldo es de Erwin, hoy $3.2).
3. **El pipeline quedó a UN servidor** (`deploy.yml` reescrito): push a master = SOLO la CI
   (ruff/compileall/pytest); desplegar producción = `workflow_dispatch` manual + elegir
   "produccion" en el menú (default "no" — un click accidental no despliega). Se retiraron el
   deploy automático del taller, su espera de Coolify y sus bancos post-deploy. El deploy
   fallido del taller (pendiente de la entrada 11) **muere con el taller: ya no hay nada que
   diagnosticar.** Qué reemplaza la red del taller: CI + tests/bancos en local + producción
   CERRADA con lista blanca como campo de pruebas + los 27 bancos a mano (27/27 el 1-sep).
4. El número de pruebas de la agencia (+57 313 293 3806) queda LIBRE para el futuro
   (staging/cliente #2). NO se mete al sistema de la clienta (mezclaría datos de prueba con el
   negocio real — la lección del pedido fantasma #2351, pero con ventas de verdad).

## 2026-09-01 (11) — ⏸️ CIERRE DE TANDA (token casi agotado): 2 cosas abiertas para mañana

**Estado sano y sin nada roto.** Producción y taller siguen con los contenedores corriendo,
`/salud` ok, bot cerrado a clientas, límites de recursos EN CALIENTE vivos en ambos. Dos cosas
quedan abiertas, ninguna urgente:

1. ✅ **REINICIO de producción HECHO (Maired lo pidió y se hizo en el acto).** La verificación
   PREVIA reveló el dato que decidió todo: `reboot-required` no estaba marcado, PERO el kernel
   corriendo era `6.12.94` con el `6.12.107` ya instalado esperando, y **0 paquetes/seguridad
   pendientes** (ChatGPT ya los había aplicado). O sea: el reinicio SÍ aportaba (activar el
   kernel parcheado) y era el último paso del punto 1. Reinicio con `shutdown -r now`; **volvió
   en ~30s**. Verificado post-reboot: kernel **6.12.107 activo**, TODOS los contenedores arriba
   (bot/worker/panel/postgres/redis/backup/coolify, los de datos healthy), `/salud` **ok**
   (Meta GREEN, 36 migraciones, saldo $3.22), firewall + fail2ban **activos**, RAM 1.3/3.9GB.
   **Punto 1 del hardening CERRADO.** ⚠️ Los límites de recursos EN CALIENTE de producción se
   perdieron con el reboot (docker update no persiste) — al volver, los contenedores nacieron
   sin límites. Re-aplicar en caliente (o resolver la persistencia por la UI de Coolify) es
   parte del pendiente de abajo.

2. 🔴 **El deploy del TALLER falla en `rolling_update` (2 veces hoy) — SIN diagnosticar del
   todo.** El 1º fue por mi `custom_docker_run_options` (revertido a NULL). El 2º, tras
   revertir, TAMBIÉN falló — causa no confirmada (el esquema de la BD de Coolify no cooperó para
   el diagnóstico rápido y no valía quemar el token que quedaba). **El taller NO se cayó** (un
   deploy fallido no recrea: los contenedores viejos + límites en caliente siguen, `/salud` ok).
   Mañana: diagnosticar por qué el `rolling_update` del taller falla (¿residuo de mi cambio?
   ¿health check? ¿preexistente?) ANTES de confiar en un deploy del taller. Mirar el log del
   deployment en Coolify (la tabla `application_deployment_queues` usa `application_id` que NO
   es el UUID ni el id numérico directo — resolver el esquema primero, o leer el log por la UI).

**Este registro se dejó en un commit LOCAL sin subir a propósito** — un push dispararía otro
deploy del taller que volvería a fallar. Se sube mañana junto con el arreglo del deploy.

## 2026-09-01 (10) — 🛡️ HARDENING post-incidente: LÍMITES DE RECURSOS (punto 6 del relevo, el de mayor impacto)

**Maired pidió atacar los 12 pendientes de seguridad de ChatGPT; autorizó que Claude los haga
"con cuidado, taller primero".** Se empezó por el punto 6 (límites de memoria/CPU/procesos) — el
que directamente cierra lo que el minero explotó: verificado que los contenedores tenían **CERO
límites y corrían como root** (`docker inspect`: mem=0, sin pids, sin security-opt).

Uso real medido primero (para no ahogar al bot): bot ~119MB, worker ~172MB, panel ~47MB.
Límites fijados con holgura 3-4x:
- bot: 640MB (swap 768) · worker: 768MB (swap 1024) · panel: 512MB (swap 640) · 1 CPU y
  pids-limit 200-300 cada uno. Techo total ~1.9GB de los 3.9GB de netcup.

**Aplicado EN CALIENTE (`docker update`, reversible, sin recrear el contenedor) en LOS DOS
servidores** — taller y producción. Verificado: los 3 `running`, `/salud` OK en ambos, host de
producción con 2.1GB libres. Un runaway (minero, fuga) ya no puede tragarse el VPS: Docker lo
mata a él, no al servidor.

**Persistencia — INTENTO FALLIDO Y REVERTIDO (la lección de esta tanda):** se puso
`custom_docker_run_options` en la BD de Coolify del taller y el push de validación **rompió el
deploy del taller** — Coolify falló en `rolling_update` (`ApplicationDeploymentJob.php:3878`) al
arrancar el contenedor con esos flags: **espera un formato distinto al que le di**. El taller NO
se cayó (el deploy fallido no recrea: los contenedores viejos + los límites en caliente
siguieron, `/salud` ok). Se **revirtió** `custom_docker_run_options` a NULL en las 3 apps del
taller. 🪦 **Lección: no pelear con el mecanismo de deploy de Coolify por SQL a ciegas** — la
persistencia va por la UI de Coolify (formato guiado/validado) o por los campos DEDICADOS
`limits_memory`/`limits_cpus` (no por `custom_docker_run_options`). A producción NUNCA se le
puso el campo roto — se salvó por hacer taller primero.

**DESENLACE (verificado):** el push siguiente a la reversión salió **VERDE completo** (deploy del
taller + bancos) — la causa del fallo era el `custom_docker_run_options` con mi formato,
confirmado por reversión. Tal como estaba previsto, el taller redesplegado **nació sin límites**
(mem=0): los en-caliente se pierden al recrear el contenedor. Se re-aplican al taller al FINAL
de la jornada (cada push los borra; re-aplicarlos entre pushes es ruido).

**ESTADO REAL tras la tanda:** 🟢 **PRODUCCIÓN protegida** (límites en caliente vivos: bot 640M ·
worker 768M · panel 512M · pids 200-300; duran hasta su próximo deploy MANUAL, no inminente).
🟡 TALLER sin límites entre deploys (aceptable: sin clientas). ⏳ PERSISTENCIA pendiente:
configurar por la UI de Coolify (formato guiado) o los campos dedicados
`limits_memory`/`limits_cpus` — validándolo en el taller ANTES de producción, como esta vez.
El token de `~/.ssh/coolify_token.txt` es de producción (401 contra el taller).

**Pendientes del hardening (los siguientes, en orden de impacto):** punto 1 actualizar el SO
(45 paquetes, necesita ventana/posible reinicio) · punto 5+7 no-root en los Dockerfiles +
cap-drop (código, se prueba en taller) · punto 4 contraseña+JWT (con Maired presente) · resto
(dominio Coolify, token nuevo, monitoreo) necesitan accesos externos o son de Erwin.

## 2026-09-01 (9) — ⏸️ LAS PRUEBAS DE HUMO EN PRODUCCIÓN, PAUSADAS: el número real despierta a Whuilianny

**Al arrancar el paso 1 (saludo) de las pruebas de humo, Maired preguntó si eso le llegaría a la
clienta AHORA MISMO** (de madrugada). La respuesta era SÍ, y no debió preguntarse: `SESIONES.md`
de junio (línea ~5120) ya lo decía — *"la dueña responde en el WhatsApp del negocio, ya ve el
chat por coexistencia"*. Estaba enterrado porque `CLAUDE.md` §0 manda leer SOLO la última
entrada de este archivo. **Se subió a regla dura** (`CLAUDE.md` §3) y a hecho operativo
(`ESTADO.md`, fila "Número real"): el `+58 424-7047595` es por coexistencia, cualquier mensaje
ahí —de prueba o real— le suena el teléfono a Whuilianny, sin importar la lista blanca.

**Decisión de Maired: pausar hasta que ella despierte, mañana.** El plan de smoke test (6 pasos:
saludo · catálogo · fotos · pedido+delivery · cobro+método · comprobante) queda listo para
retomar. El teléfono autorizado para probar es `573005690062` (verificado limpio: 0 mensajes,
0 pedidos, `bot_activo=true`) — pero el destino correcto por defecto es el número del TALLER
(agencia, sin este riesgo), y usar el de producción solo con su OK expreso de horario.

## 2026-09-01 (8) — 💳 EL ZELLE DE PRODUCCIÓN CARGADO — 27/27 bancos (autorizado por Maired)

**El único banco rojo (§7) era dato, no código: `metodos_pago` de PRODUCCIÓN no tenía la fila
Zelle** — Pago Móvil, Transferencia (Banesco) y Binance YA estaban ahí, idénticos al taller (el
14-jul se arregló en las dos bases; el Zelle se quedó atrás, o llegó después solo al taller).

**Maired autorizó copiar las filas exactas del taller** (en vez de tipearlas ella). Procedimiento
con el cuidado de datos bancarios reales: 1) se leyó la fila completa de Zelle en el taller
(tipo/titular/correo/orden) 2) se leyó ANTES la tabla de producción para no duplicar ni pisar
nada — confirmado: faltaba SOLO esa fila, las otras tres coincidían byte a byte 3) un INSERT
único con esos datos exactos (id=4, autogenerado) 4) verificado leyendo de vuelta 5) corrido el
banco específico (`probar_datos_bancarios.py`, todas sus 18 comprobaciones OK) 6) corrido el
paquete completo: **27/27**. Ni una fila tocada de las que ya existían.

## 2026-09-01 (7) — ✅ VERIFICACIÓN CRUZADA DEL RELEVO DE ChatGPT: la promoción es REAL — 26/27 bancos

**ChatGPT terminó su plan y dejó relevo escrito; Claude lo verificó punto por punto en el
servidor (la regla acordada: verificación cruzada).** Resultado:

- ✅ **Endurecimiento REAL**: `passwordauthentication no` · root solo por llave · fail2ban
  activo (ya baneó 2) · firewall nftables activo (6001/6002/8000/8080 bloqueados en input y
  forward) · cero mineros · panel nuevo `608f61c` arriba (Next 15.5.24, audit 0).
- ✅ **La promoción es real**: bot+worker de producción en `f4e200c` (el master de hoy con el
  plan A→D completo) · 36 migraciones aplicadas · `/salud` TODO ok · **saldo IA $4.11**
  (recargado; era $1.70) · Meta GREEN · datos 32/37/277/13.890 · personalidad 7.331 car.
- ✅ **El bot sigue CERRADO a clientas**: `NUMEROS_PERMITIDOS=573005690062` (solo 1), extra
  None. ⚠️ `bot_activo` no existe en la config de producción y el default del código es
  ENCENDIDO (tasks.py:451): la protección real es la lista blanca.
- ✅ El cambio de ChatGPT al workflow (`0216874`) revisado: el deploy de producción ya no manda
  el token de Coolify por HTTP público — entra por SSH con host key fijada y pega a
  `127.0.0.1`. Bien hecho.
- 🟡 **LOS BANCOS EN PRODUCCIÓN (nadie los había corrido): 26/27.** El rojo es
  `probar_datos_bancarios` §5 y NO es bug de código: **la tabla `metodos_pago` de PRODUCCIÓN no
  tiene Zelle** (ni el resto de métodos nuevos) — el arreglo del 14-jul se hizo en la BD del
  taller y las bases son independientes. El banco avisó a la dueña por WhatsApp (ese aviso le
  llegó a Maired). Arreglo: cargar los métodos REALES en el panel de producción (datos de
  Whuilianny, no se inventan).
- ⚠️ **`pedidos = 0` en producción** — plausible (el bot nunca vendió en producción; Maired hizo
  eliminaciones manuales) pero queda como pregunta abierta para ella. Hay respaldo pre-migración.

**Los huecos del relevo de ChatGPT (lo que su lista de 12 pendientes NO cubre):** rotar
`META_ACCESS_TOKEN`/`APP_SECRET` (prioridad #1: la cuenta Tech Provider), `OPENROUTER_API_KEY`
y llaves R2 (la deuda D5) · cargar métodos de pago en producción · pruebas de humo con número
real. Quedaron escritos en ESTADO.md.

## 2026-09-01 (6) — 🚨 INCIDENTE DE SEGURIDAD EN PRODUCCIÓN: minero xmrig en el panel viejo de netcup

**Maired estaba haciendo LA PROMOCIÓN A PRODUCCIÓN con ChatGPT** (otro asistente, con acceso al
servidor) y en medio del proceso ChatGPT encontró **un minero de criptomonedas (`xmrig`,
procesos disfrazados como `system-check`) DENTRO del contenedor del panel VIEJO de netcup** —
consumiendo más de la mitad de la RAM y CPU. ChatGPT lo detuvo junto con el panel comprometido
y desplegó bot+worker nuevos. **Verificado por Claude con SSH en solo-lectura (sin tocar nada,
para no pisar a ChatGPT que seguía trabajando):**

- ✅ Minero MUERTO: cero procesos de minería, carga 7.5→0.95, RAM 166MB→2.4GB disponibles.
- ✅ El contenedor comprometido (panel de JULIO, `o1jo590e…` imagen `a8b52f07`) está `Exited`.
- ✅ Bot+worker de producción arriba con `f4e200c` (el último master, el merge del PR #14).
- ✅ Postgres/Redis de másvida sanos · `masvida-backup` lleva 7 semanas corriendo (respaldo
  diario cifrado) · cron limpio (sin persistencia visible) · sin conexiones a pools de minería.
- ✅ Los procesos PHP/Horizon/Soketi que parecían sospechosos son **Coolify mismo** (es Laravel).
- ✅ **EL TALLER ESTÁ SANO** (revisado igual: carga 0.19, cero mineros).

**🔴 LO QUE EL "compromiso aislado del panel" de ChatGPT DEJA ABIERTO (la lectura de Claude):**
1. **¿CÓMO entró?** Sin la vía de entrada, puede volver. Hipótesis más probable: el panel de
   producción llevaba SIN ACTUALIZAR desde julio (Next.js viejo con CVEs públicos), expuesto a
   internet por Traefik. El panel nuevo probablemente cierra esa puerta — pero es hipótesis.
2. **ROTAR LAS LLAVES (la deuda D5, ahora URGENTE):** quien ejecuta código en un contenedor lee
   sus variables de entorno. El panel habla con la API (no con la BD directa), así que el radio
   parece corto — pero la regla de un compromiso es asumir lo peor hasta saber la vía. Prioridad:
   **META_ACCESS_TOKEN/APP_SECRET** (la cuenta Tech Provider es el activo más valioso),
   OPENROUTER_API_KEY, JWT_SECRET, ADMIN_PASSWORD, llaves R2. Coordinar con Erwin.
3. **Verificaciones post-promoción** (cuando ChatGPT termine su plan): `/salud` · 35 migraciones
   aplicadas · conteos de datos intactos (productos/variantes/pedidos/personalidad) · 🔴 **LISTA
   BLANCA ACTIVA y `bot_activo`** — si el deploy la pisó, el bot nuevo le contestaría a clientas
   REALES sin que nadie lo decidiera · los bancos en producción.

**Regla operativa acordada: DOS asistentes no escriben a la vez en el mismo servidor.** Claude
en solo-lectura mientras ChatGPT ejecuta su plan; verificación cruzada al terminar.

## 2026-09-01 (5) — 🔬 AUTOPSIA: "borré el chat pero el bot recuerda la entrega" — NO es bug, es diseño

**Maired borró el chat de Enova desde el panel y al reescribir el bot dijo "ya tienes la entrega
acordada para el jueves 3... pago por Binance".** Autopsia con 3 agentes (código + BD + Redis/logs
por SSH, solo lectura). Veredicto: **las casillas de las ramas B/C/D funcionan — demasiado bien.**

- El botón **"Borrar" (chat)** → `DELETE /api/conversaciones/{tel}` (router.py:2025-2039): borra
  `Mensaje` + `Intervencion` + la memoria de Redis (`borrar_memoria`: hist/buffer/cobro/…). **NO
  toca `pedidos`, `pagos` ni `clientes`** — a propósito, y el propio diálogo del panel lo avisa:
  *"Sus pedidos y pagos NO se borran"*. En Redis: confirmado limpio (hist solo la charla nueva).
- Quedaba vivo el pedido **#2351** (`esperando_pago`, empanadas $14, entrega 2026-09-03,
  `metodo_elegido='Binance Whuillianny'`), creado ANTES del borrado. `_estado_cliente_texto`
  consulta los pedidos SIN filtro de fecha y reinyecta cada turno "Entrega YA ACORDADA" + "Ya
  ELIGIÓ cómo pagar" — exactamente lo que salió. La memoria no vino del chat (ese sí se borró):
  vino de Postgres, por diseño.
- 🟢 **En PRODUCCIÓN esto es CORRECTO** (perder el pedido de una clienta que ya encargó/pagó
  porque se limpió el chat sería el bug). 🟡 En el TALLER molesta para "probar como nueva". Ya
  existe el botón que SÍ pone en cero — **"Borrar cliente"** (clientes → `DELETE /api/clientes`,
  borra pagos→pedidos→mensajes→cliente) — pero el panel lo deshabilita si hay pago confirmado.
- **Decisión de Maired: NO cambiar nada.** Ella misma borró los pedidos de Enova. Verificado
  después: **0 pedidos, 0 pagos**, cliente vivo (nombre Enova), 15 mensajes + 8 renglones Redis
  de la charla nueva. El pedido fantasma ya no puede reaparecer en ese chat.
- 🔎 **Anotado para revisar aparte (NO tocado):** los 2 pagos confirmados viejos de Enova
  compartían la MISMA referencia `410919226588905472` en fechas distintas — o dato de prueba
  repetido, o la validación del comprobante no exige referencia única. Vale mirarlo algún día.

## 2026-09-01 (4) — 🛡️ RAMA D: EL VIGILANTE PREGUNTA-vs-ESTADO — cierra el plan A→D (PR #14)

**La última pieza del plan "que no repregunte".** Las ramas #6 y C INYECTAN lo ya elegido como
estado, pero eso es prompt y el prompt SUGIERE. Este vigilante lo IMPIDE: antes de que el
mensaje salga, compara el BORRADOR contra las elecciones vigentes (`elecciones_hilo` +
`elecciones_var`) y, si reabre algo ya elegido, regaño `[SISTEMA]` + `continue` — el modelo
redacta de nuevo. Rama `vigilante-pregunta-vs-estado`, **PR #14**.

- **`_reabre_eleccion_ya_hecha` (pura):** detecta reapertura en las tres dimensiones —
  **sabor/relleno** (reusa `_dato_opcional_pedido`), **versión/masa** (reusa
  `_versiones_tocadas`: ofrecer la otra o nombrar las dos), **tamaño** (pregunta genérica,
  `_PREGUNTA_TAMANO`). Mencionar SOLO lo elegido es confirmación, no dispara.
- **Precedente `_correccion_fantasma`:** la sentencia la dicta el ESTADO, no una lista de
  palabras. El código NO reescribe el texto (la frontera del 24-ago intacta): solo devuelve la
  decisión al modelo.
- **Nace con la regla de los guardias (1-sep (2)):** usa `pregunta_cliente` — si el cliente
  PIDIÓ ese dato o lo CAMBIÓ en su último mensaje, el bot RESPONDE y el vigilante NO dispara.
- **NO mata el texto:** una pasada; si el modelo insiste, el mensaje SALE igual (insistir no es
  mentir — patrón de la red del cierre). Va justo después de ella (prima: aquella cuida lo NO
  elegido, esta lo YA elegido).
- 🕳️ **Hueco conocido, sin cambio:** el hilo y el vigilante se calculan en el camino del modo
  'uno'; si algún día se enciende `agente_modo='dos'` no aplican ahí. Bandera hoy en 'uno'.

**Validación:** 13 tests nuevos (`test_vigilante_pregunta_estado.py`: pieza pura por dimensión +
carril de punta a punta con el caso real de la masa) · **2 reversiones → rojas en su test
exacto** (quitar la absolución del cliente → dispara respondiendo lo que pidió; quitar la
bandera de una pasada → nunca deja salir) · suite **784** · ruff · compileall — verdes.

🎯 **PLAN A→D COMPLETO.** B (método de pago en 2 pasos) · C (hilo a tamaño/sabor) · D (el
vigilante) fusionados o en PR, más la rama de los guardias que destapó la prueba de Maired. Con
esto las repreguntas de datos del negocio quedan entre "casi nunca" (registrar temprano +
estado) y "frenadas antes de salir" (el vigilante). El "nunca jamás con cualquier palabra" NO
existe (dicho a Maired el 31-ago) — las frases 100% libres no se vuelven dato sin adivinar.

## 2026-09-01 (3) — 🧵 RAMA C: EL HILO EXTENDIDO A TAMAÑO Y SABOR (PR #13)

**El #6 seguía la versión que vive en el NOMBRE (masa yuca/plátano); esta rama lo extiende a las
otras dos elecciones pre-registro, las que viven en las CASILLAS de la BD:** el TAMAÑO
(`presentacion`) y el SABOR (`variantes.sabores`). El hueco es idéntico —"la ventana sin
estado"—: entre que el cliente dice "de 250, de limón" y que la tool registra, esa elección vive
solo como chat crudo y una ficha fresca (info_producto trae los 3 tamaños y los 8 sabores) la
reabre. Se destila del chat y se inyecta en la MISMA línea EL HILO DE LA VENTA. Rama
`hilo-tamanos-y-sabores`, **PR #13**.

- **Prerrequisito de datos de Maired: CUMPLIDO** (verificado por SSH al taller): las Empanadas
  Keto y Horneadas ya tienen sus rellenos en `variantes.sabores` — las 11 variantes con opciones
  reales están cargadas (2 kombuchas, 3 empanadas, 3 torta baja, 3 tortas keto).
- **`catalogo_variantes_para_hilo` (tools.py):** solo lectura, vocabulario CERRADO —tamaños de
  `presentacion`, sabores de la casilla, NUNCA de la prosa (la deuda D3, el regex prohibido)—.
  Solo entra el producto con >1 opción real que elegir.
- **`elecciones_de_variante_en` (agent.py, pura):** mismo esqueleto y reglas del #6 por
  dimensión — la más reciente gana; nombrar DOS valores deja SIN elección; y la **atribución por
  producto** (reusada) evita el bug del **Kéfir de cabra vs el sabor 'queso de cabra'**. El
  reconocedor de tamaños es el YA probado `_menciona_tamano` (así "quiero 1" sigue siendo
  cantidad, no 1kg); el de sabores es nuevo (`_sabores_tocados`, por tokens distintivos).
- 🔒 **El tamaño NO es palanca de dinero aquí:** la línea solo evita la repregunta; el precio
  sigue naciendo del `variante_id` al registrar y la RED DEL TAMAÑO ADIVINADO sigue vigilando.

**Validación:** 16 tests nuevos (el caso de Maired "carne mechada", el bug del Kéfir, la trampa
del "1", ambigüedad, más-reciente-gana, y el carril de punta a punta) · **2 reversiones → rojas
en su test exacto** (quitar la atribución → se cuela el Kéfir; ignorar la ambigüedad → elige con
dos nombrados) · suite **771** · ruff · compileall — verdes. El #6 (versiones-en-el-nombre) NO
se tocó: sigue en tools.py, y su inyección ahora comparte línea con la de tamaño/sabor.

**Queda la rama D** (el vigilante pregunta-vs-estado, la única pieza que IMPIDE antes de que el
mensaje salga) — y nace con la regla de los guardias del 1-sep (2) ya puesta.

## 2026-09-01 (2) — 🛡️ LOS GUARDIAS MIRAN AL CLIENTE: la autopsia del sabor censurado + auditoría de las 13 redes (PR #12)

**El caso que lo destapó (Maired probando en el taller, 21:08).** La clienta preguntó *"De que
sabor tienes?"* y el bot contestó *"¿Para cuándo la necesitas…?"* — como si no supiera. La
autopsia (SSH solo lectura: logs del worker + `LRANGE hist:` en Redis) demostró **lo contrario
de lo que parecía**: el modelo escribió la respuesta PERFECTA — *"Los sabores disponibles son:
limón, zanahoria, naranja, piña, vainilla, marmoleada, manzana canela y cambur. Cuál te
provoca?"* (los sabores que Maired acababa de cargar en `variantes.sabores`) — y **la RED DEL
CIERRE la censuró**: vio "pregunta el sabor" + "ya lo preguntó antes" + "sin registrar" y la
trató como el bucle que existe para cortar. El regaño empujó al modelo a saltarse el tema.
NO fue falta de datos (la carga de Maired estaba bien y era el prerrequisito de la rama C) y
NO se montó con la rama B (otro código). Fue un guardia con un punto ciego.

**La clase de bug, nombrada, y la REGLA DE DISEÑO nueva (a pedido de Maired: "arreglen la raíz,
no caso por caso"):** *un guardia que juzga un BORRADOR tiene que mirar también lo que el
CLIENTE acaba de pedir. Responder no es insistir; negar no es prometer.* Quedó escrita en
CLAUDE.md §8.

**La auditoría de los 13 guardias con ese lente (uno por uno, contra el código):**
- 🔴 **RED DEL CIERRE — TENÍA el punto ciego. ARREGLADA:** cuarta condición
  `not _cliente_pidio_ese_dato(pregunta_cliente)` (pregunta con o sin signo, o "dime…" +
  el dato). Absuelve SOLO la petición: si el cliente ya DIO el sabor o habla de otra cosa,
  el bucle real se sigue cortando (test de contraste). Se mira `pregunta_cliente` (no
  `mensaje_usuario`): en el RETOMAR el mensaje es una orden interna.
- 🔴 **RED DEL DÍA IMPOSIBLE — variante del mismo punto ciego. ARREGLADA:** disparaba al
  NOMBRAR un día no entregable aunque el bot lo estuviera NEGANDO ("Los domingos no
  entregamos" — la respuesta correcta a "¿entregas el domingo?"). La lección ya existía para
  'hoy'; se extendió a los días con nombre y mañana/pasado mañana, **por CLÁUSULA** (en "el
  domingo no entregamos, pero el sábado sí te lo dejo", el sábado PROMETIDO sigue contando).
  `_NIEGA_LA_ENTREGA` = lista corta y estable de negaciones, la filosofía del patrón del hoy.
  El mecanismo de 'hoy' NO se tocó (estaba probado).
- ✅ **Sin el punto ciego, verificado:** DINERO, DATOS BANCARIOS, HONESTIDAD, SALUD, PEDIDO
  FANTASMA, ENVÍO FANTASMA (redes de VERDAD/DINERO: lo prohibido sigue prohibido aunque el
  cliente lo pida — y la de fotos ya recibe `pidio_fotos`/`pidio_media`); ASESORÍA y TAMAÑO
  ADIVINADO (ya nacen mirando al cliente); CATÁLOGO y FOTO aseguradoras (de acción, ya miran
  `pregunta_cliente`); VOZ (estilo, 1 pasada, sale si insiste); RELEVO y BUCLE genérico (no
  cortan el texto: solo avisan a la dueña); SALUDO (añade, no corta).

**Validación:** 8 tests nuevos (`test_guardias_miran_al_cliente.py`, con el borrador censurado
LITERAL del log como caso) · **las 2 reversiones → rojas en su test exacto** (la primera
reprodujo en el log el error de la noche, palabra por palabra) · suite **755** · ruff ·
compileall — verdes. Los tests viejos de las dos redes (`test_red_del_cierre`,
`test_dia_imposible`) pasaron sin tocarlos: el bucle real y la promesa de día imposible se
siguen cortando igual.

**Para el plan:** la rama D (el vigilante) HEREDA esta regla de nacimiento — su diseño del
ROADMAP ya lo decía ("mencionar la elegida es confirmación legítima, no dispara") y ahora tiene
el precedente construido. Pendiente de datos de Maired para la rama C: los rellenos de
**Empanadas Keto** y **Empanadas Horneadas** siguen SOLO en la prosa de la descripción
(verificado en la BD del taller; las de masa de yuca/plátano, las kombuchas y las 2 tortas ya
tienen su casilla llena).

## 2026-09-01 (1) — 💬 "EN BOLÍVARES TAMBIÉN CUENTA" + el tipo real de la tabla (PR #11, el remate de la rama B)

**Maired fusionó el PR #10 apenas lo revisó** — y su revisión trajo DOS mejoras reales que van
en el PR #11 (rama `metodo-de-pago-afinado`):

**1. Las palabras de MONEDA afinan la pregunta — decisión de Maired, PREGUNTADA con las
opciones delante (1-sep).** El camino tuvo dos vueltas y vale la pena dejarlas escritas:
primero se implementó "bolívares → candidatos"; su pantallazo (DOS métodos en Bs: Pago Móvil +
cuenta Banesco tipo `'Transferencia'`) se leyó como "dáselos JUNTOS" y se cambió a entrega de
grupo; **ella corrigió y pidió que se le preguntara** — se le pusieron las 3 conductas con
diálogos de ejemplo y eligió: **preguntar cuál de las vías y mandar SOLO la elegida** (*"si te
dice pago móvil, no tiene que mandarle al Banesco"*). Lo que quedó:
- **"bolívares"/"bs"** son sinónimos de TODOS los tipos en Bs ⇒ con varias vías salen como
  CANDIDATOS y el bot pregunta "¿pago móvil o transferencia?" (solo esas); con UNA sola vía,
  calza directo. La casilla siempre guarda UNA vía concreta.
- **"dólares"/"divisas"** igual, entre las tres vías del dólar ("¿efectivo, Zelle o
  Binance?"). `dolares fisicos` → efectivo directo (el sinónimo EXACTO gana antes que la
  contención). `usdt` → Binance.
Su pregunta de si necesita "un diccionario aparte": NO — vive en el código
(`_SINONIMOS_TIPO_METODO`); si las clientas usan más palabras, se añaden ahí en minutos.
🪦 **Lección de método (me la ganó ella):** entendí su corrección de negocio como un cambio de
conducta y lo construí SIN confirmarle — hubo que revertirlo. Con una instrucción de producto
ambigua, primero la pregunta con opciones, después el código.
📌 **Idea suya anotada para DESPUÉS (no construir ahora):** algo aparte que frene las
conversaciones que no tienen nada que ver con el negocio (quedó en el ROADMAP, Control del Bot).

**2. 🔴 El banco del VPS salió ROJO tras fusionar el #10 — y el rojo enseñó algo que valía la
pena.** ÚNICO check caído: `metodo_elegido_tipo == "zelle"`… porque **en la tabla real del
taller el tipo está cargado `'Zelle'` (mayúscula), no `'zelle'`** como dice la migración 009.
Todo el flujo de dos pasos PASÓ en el servidor (nombres sin datos, Zelle solo, una moneda,
casilla escrita); el estricto era el CHECK. Pero destapó el riesgo real: un tipo cargado
"Pago Móvil" (espacio y acento) habría dejado a esa fila SIN moneda y el bot re-pitcheando las
dos monedas a quien ya eligió — exactamente el bug de la rama B, de vuelta por una mayúscula.
**El arreglo:** `_tipo_canonico()` (sin acentos, minúsculas, `_`/`-` como espacio) en TODOS los
consumidores del tipo (el mapa de moneda de la tool, `_estado_cliente_texto`, los sinónimos del
matcher y el check del banco). La casilla sigue congelando el tipo TAL CUAL está en la tabla.
**Y la fuente de la verdad encontrada:** el panel guarda el tipo como su ETIQUETA legible —
`TIPOS_METODO` en `configuracion/page.tsx` del dashboard: `'Pago Móvil' | 'Transferencia' |
'Zelle' | 'Binance' | 'Efectivo' | 'Otro'` — no los valores de la migración 009. Por eso el
mapa de monedas ahora entiende `'Transferencia'` (la cuenta Banesco del pantallazo): sin esa
entrada, la fila quedaba SIN moneda.

**747 tests (27 de la rama B) · ruff · compileall — verdes.** Al fusionar el #11 el flujo del
taller debe volver a VERDE; ahí sí: la prueba en vivo de Maired (guion en el ROADMAP, rama B).

---
> 🗃️ Las entradas de jun-ago están en `archivo/SESIONES-jun-ago.md`. Aquí, solo septiembre en adelante.
