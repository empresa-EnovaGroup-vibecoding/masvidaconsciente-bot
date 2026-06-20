# 📔 SESIONES = lo que YA hicimos (el diario de másvida)

> **Dos prácticas adoptadas (inspiradas en el sistema del mentor Erwin), para no romper lo que funciona:**
>
> 1. **Registrar cada sesión** en este archivo: qué se cambió, por qué, y qué quedó pendiente.
> 2. **Cambios de base de datos con red de seguridad:** antes de tocar datos reales, probar el cambio dentro de una transacción y hacer **ROLLBACK** (deshacer) para verificar que está bien. Nunca alterar datos de producción sin ese ensayo previo.

---

## ⏳ Pendientes importantes (no olvidar)

> **⚠️ LEER ESTO PRIMERO (actualizado 2026-06-17):** las notas viejas de abajo que dicen *"Pendiente: redeploy"* están **DESACTUALIZADAS**. Esos redeploys YA SE HICIERON: todo eso está **EN PRODUCCIÓN y funcionando** (verificado en vivo contra `api-masvida.enovagroup.tech` — ver entrada del 2026-06-17 "Inventario verificado"). No las tomes como pendientes. Lo que SÍ sigue pendiente de verdad está listado en esa entrada.

- ✅ **Tasa BCV AUTOMÁTICA conectada (2026-06-10)**: fuente `https://ve.dolarapi.com/v1/dolares/oficial` (BCV oficial, campo `promedio`) puesta como default en `tasa.py` (`_FUENTE_BCV_DEFAULT`); `_parsear_tasa` ya la entiende (verificado: da 572,68 ≈ los 567,68 manuales). Se actualiza sola (cache 1h). El candado manual queda como freno de emergencia. OJO: para que use la automática, el candado manual debe estar DESACTIVADO en la pantalla Tasa.
- 🔴 **Respaldo automático de datos (Blindaje 4)** — DIFERIDO a pedido de Maired (2026-06-09). Plan: capa 1 = respaldo **local** en Coolify (nada sale del servidor); protección REAL = **offsite cifrado** (privado, encriptado con llave que solo ella controla — el cifrado resuelve su preocupación de filtración). Honesto: el local NO salva si muere el servidor entero, y NO incluye las fotos de los comprobantes. **Montar el offsite cifrado ANTES del lanzamiento real con clientes** (cuando haya dato con valor). Recordárselo.
- 🟡 **Afinar la personalidad como "closer de ventas nato" + ajustes finales de tono** — en la fase de PULIDO FINAL (cuando todo esté armado), escribir un guión de ventas potente en el editor de Personalidad (/bot): manejo de objeciones, cierre con cariño, terminar mensajes con pregunta, etc., y probarlo en el simulador. Maired lo difirió 2026-06-09 para hacerlo "cuando toque afinar todo para que quede perfecto".

---

## 2026-06-20 — Selector de modelo de IA desde el panel (probar Claude / OpenAI)

**Por qué:** Gemini Flash ignora matices (pan es pan, tono, no-saludar-siempre). Maired
(proveedora) quiere poder cambiar el modelo ELLA MISMA y probar cuál vende mejor, sin redeploy.
Reversa MATIZADA de "sin selector de modelo": es palanca de **proveedor**, no de la clienta.

**Qué se hizo (aditivo, verificado `compileall` bot + `tsc` dashboard):**
- `config.py`: nuevo `openrouter_model_audio` (default Gemini). La voz se transcribe SIEMPRE con
  Gemini (Claude/GPT no aceptan audio); el selector no la toca. `openrouter_model` queda como semilla/fallback.
- `agent/system_prompt.py`: `leer_modelo_ia()` — lee la clave `modelo_ia` de la tabla `configuracion`
  (mismo patrón que `leer_personalidad`); si no hay, cae al env. Cualquier fallo cae al default.
- `agent/agent.py`: `responder` lee el modelo 1 vez y lo pasa a `_llamar_con_fallback(messages, llm, modelo)`;
  `redactar_mensaje` usa el modelo elegido; `transcribir_audio` usa `openrouter_model_audio` (FIJO).
- `api/router.py`: `modelo_ia` agregado a `CLAVES_CONFIG` (el GET/PUT `/configuracion` ya lo aceptan).
- Dashboard `configuracion/page.tsx` + `lib/api.ts`: dropdown "Modelo de IA (avanzado)" con 4 opciones
  (Gemini / Claude Haiku 4.5 / Claude Sonnet 4.6 / GPT-4.1) + costo aprox. por 1000 msgs.
- `CLAUDE.md §5` actualizado (decisión matizada). Plan en `PRP-selector-modelo.md` (local).

**Blindaje confirmado:** las `_REGLAS` del cobro viajan en el system prompt a CUALQUIER modelo →
cambiar de modelo NO debilita el cobro (nunca confirma pago, precios desde tools).

**Pendiente:** redeploy del bot + worker (no requiere migración: `modelo_ia` se crea al guardar) y
deploy del dashboard. Luego: en el panel elegir Claude Haiku/Sonnet, mandar mensaje de prueba por
WhatsApp y **probar una nota de voz** (confirmar que sigue transcribiendo).

---

## 2026-06-18 (cont. 4) — Catálogo PDF AHORA EN LA BASE DE DATOS (fin del 404)

**El problema:** el volumen persistente para `/data/catalogo` (docker-compose) NO aguantó en Coolify — el PDF se borraba en cada redeploy (daba 404), aunque los comprobantes (otro volumen) sí persistían. No se pudo controlar desde fuera.

**Solución definitiva (aditiva, verificado `compileall`):** guardar el PDF **dentro de Postgres** (que SÍ sobrevive redeploys, como productos/clientes), en vez del disco.
- Migración **`008_catalogo_pdf.sql`**: tabla `catalogo_pdf` (una fila, `contenido BYTEA`). `models.py` + `init_db.py` aplican la 008.
- `api/router.py`: subir / servir / estado / borrar del catálogo leen y escriben en la BD (serve público devuelve `Response(bytes)` en vez de `FileResponse`).
- `agent/tools.py`: `enviar_catalogo` chequea la fila de la BD (no el flag + archivo).

**Pendiente:** **redeploy** del bot + worker (corre la migración 008) + **re-subir el PDF UNA vez** → queda permanente para siempre (en la BD).

## 2026-06-18 (cont. 3) — Catálogo "pan es pan" + PDF blindado + registro fino de la voz

**Código (aditivo, verificado `compileall` + prueba del filtro):**
- `agent/tools.py`: `ver_catalogo` ahora acepta `busqueda` (la palabra que pide el cliente) y filtra por NOMBRE — "pan" trae SOLO los panes (Pan de…), NO "Empanadas" (evita el falso positivo de em-PAN-adas: match por INICIO de palabra). Schema + regla del prompt actualizadas para que el bot use `busqueda` ante un pedido específico. Antes solo filtraba por categoría → "pan" traía toda la panadería.
- `docker-compose.yml`: **volumen persistente para `/data/catalogo`** (antes el PDF se borraba en cada redeploy porque la carpeta no era persistente; daba 404). Ahora aguanta los redeploys. (El PDF se re-sube UNA vez tras el redeploy del fix y queda permanente.)
- `agent/agent.py`: `_asegurar_catalogo()` (red de seguridad: si el bot dice que envió el catálogo sin llamar a la herramienta, lo envía de verdad).

**Guión (BRIEF, local):**
- **Registro de voz afinado:** cálida CON CLASE / educada / decente (estilo "sifrina" fina venezolana), NO callejera ("échale un ojo", "ahí te va" → prohibido) NI confianzuda NI rebuscada. Mensaje del catálogo: corto, variado y con clase (no plantilla, no decir "PDF").

**Pendiente:** redeploy del **bot + worker** para `ver_catalogo`. El registro de la voz se pega en "Mi Bot" (sin redeploy).

## 2026-06-18 (cont.) — Voz plana, fix del catálogo "fantasma" y orden de documentos

**Código (aditivo, verificado `compileall`):**
- `workers/tasks.py`: `_aplanar()` quita a la fuerza viñetas, *negritas* y los ".00" de los precios antes de enviar (el modelo a veces ignora la regla). Mensajes 100% planos.
- `agent/agent.py`: `_asegurar_catalogo()` — red de seguridad: si el bot DICE que envió el catálogo pero NO llamó a `enviar_catalogo`, el sistema lo envía de verdad (PDF primero, texto después). Si no hay PDF, evita la afirmación falsa. + `system_prompt`: regla "nunca afirmes un envío que no hiciste".
- Regla "no re-saludar en cada mensaje" (otro día sí saluda) — en el guión (BRIEF).

**Documentación (orden, a pedido de Maired):**
- Cada documento con su etiqueta: **ROADMAP = lo que FALTA · SESIONES = lo HECHO · BRIEF = cómo HABLA el bot**. Mapa en CLAUDE.md §6.
- ROADMAP limpiado: muestra **solo lo pendiente** + un resumen "ya funciona"; FASES 0–3 marcadas hechas. Se borró el `PRP-INDICE` duplicado (la lista vive en ROADMAP).

**Pendiente:** redeploy del **bot + worker**. Próximo: **Plan A** (memoria/ficha, será su propio PRP). Bug en cola: comprobantes (Plan B).

## 2026-06-18 — Personalidad "closer" con la voz REAL de Whuilianny + ajustes de código

**Qué se hizo:**
- Se armó el guión de personalidad del bot (en `BRIEF-closer-masvida.md`, **LOCAL/gitignored**) copiando el estilo REAL de la dueña (capturas de WhatsApp): mensajes muy cortos, **varios mensajitos**, **plano** (sin viñetas ni negritas), sus frases ("¿De qué lo quieres?", "Para mañana te lo puedo tener"), bendición al **cerrar**. El bot habla en **primera persona COMO Whuilianny** (no "asistente"). Se pega en "Mi Bot" (config, sin redeploy).
- **Código (aditivo, no rompe el cobro):**
  - `system_prompt.py`: la regla del cobro reescrita en **1ª persona** (ya no dice "la dueña lo verifica" en 3ª persona; sigue PROHIBIDO afirmar pago confirmado). Nueva regla de formato: **varios mensajitos separados por línea en blanco, PROHIBIDO viñetas/negritas, listar plano**.
  - `workers/tasks.py`: nuevo `_enviar_en_partes()` que **parte la respuesta del agente en varios mensajes** (por línea en blanco) con pausa breve entre cada uno; usado en texto, audio y comprobante. Tope de 6 globos (anti-spam).
  - `services/mensajes.py` y `api/router.py`: guías de "pago confirmado" reescritas neutras/1ª persona (sin "la dueña" en 3ª persona).
  - `agent/tools.py`: descripción/nota del comprobante reescritas (sin "la duena lo verifica").
- **Verificado:** `compileall` OK.

**Pendiente:** **redeploy del bot + worker** (ahí corren las reglas y el partir en globos). OJO: el **simulador NO muestra los globos partidos** (eso pasa solo en WhatsApp real, vía worker). Pagos **multi-método + descuento divisas (20% + delivery gratis)** = desarrollo **Paso 2**.

## 2026-06-17 — Inventario verificado en vivo (las notas "Pendiente: redeploy" ya están desplegadas)

**Por qué:** las entradas viejas decían "Pendiente: redeploy" y nunca se marcaron como hechas, confundiendo el estado real. Se hizo un inventario **verificando contra la API en producción** (no contra las notas).

**Cómo se verificó:** se consultó `https://api-masvida.enovagroup.tech/openapi.json` (lista de endpoints publicados) y el endpoint público del catálogo PDF. Resultado: **TODOS los endpoints de las features "pendientes" están en vivo.**

**✅ Confirmado DESPLEGADO y funcionando (los redeploys viejos YA se hicieron):**
- Catálogo y precios editables · agotados 1 clic · Configuración del negocio · Reporte de ventas.
- **Catálogo en PDF** (`/api/catalogo-pdf` + `/api/catalogo/archivo`) — y el PDF público responde `HTTP 200 application/pdf`: **la dueña YA subió su catálogo**.
- **Tasa BCV** con margen + candado (`/api/tasa`) — **candado DESACTIVADO → usa la automática** (confirmado por Maired).
- **Pago que no calza** (`/api/pagos/{id}/verificar-monto`) · Tope de gasto / anti-abuso.
- **Encender/apagar bot** (`/api/bot-estado`) · **Pausar bot por chat** (`/api/clientes/{tel}/pausa`).
- **Mi Bot**: personalidad editable + simulador (`/api/personalidad`, `/api/probar`).
- **Mensajes editables** (`/api/mensajes`) · **Conocimiento/FAQ** (`/api/conocimiento`).
- **Clientes/CRM** (`/api/clientes`, `/notas`) + ficha + historial.

**🟡 Realidad operativa (al 2026-06-17):**
- Número conectado = el de **PRUEBA +57 313 2933806** (aún NO el real de másvida en Venezuela).
- Bot **encendido pero en modo pruebas** (todavía no atiende clientes reales).

**⏳ Pendiente DE VERDAD:**
- **Respaldo automático (Blindaje 4)** — verificado quirúrgicamente: NO existe en el código (sin script, sin `pg_dump`, sin cron, sin servicio en `docker-compose.yml`). Solo podría estar configurado a mano en Coolify (Postgres → Backups). Montar (local + offsite cifrado) **antes del lanzamiento real con clientes**.
- **Migrar al número real de másvida (VE)** para atender clientes de verdad.
- **Afinar personalidad "closer de ventas" + tono final** (diferido para el pulido final).
- **Onboarding automatizado** (hoy el `override` del webhook es manual).
- Roadmap aún no construido: plantillas HSM / aviso fuera de 24h, recuperación de pedidos sin pagar, recordatorios de pago, delivery + envío por zona, multi-método de pago, recibo, fotos en catálogo, roles dueña/empleado, horario de atención, etiquetas, más vendidos, campañas.

## 2026-06-17 — CLAUDE.md del proyecto (la IA arranca sabiéndolo todo)

**Qué se hizo (aditivo, solo documentación):**
- Creado **`CLAUDE.md`** en la raíz del bot: instrucciones que la IA carga SOLAS al abrir la carpeta — reglas duras (aditivo, no romper el cobro, humanizar, seguridad Tech Provider con Meta), el stack REAL (no el Trust Stack genérico), decisiones ya tomadas (ej. NO selector de modelo en el panel), y la orden de leer `SESIONES.md` + `ROADMAP.md` al empezar.
- Objetivo: que cualquier conversación nueva en másvida arranque con todo el contexto y siguiendo las reglas, sin "empezar de cero". Pedido por Maired.

**Pendiente:** ninguno (es documentación; no requiere redeploy).

## 2026-06-10 — El bot envía el catálogo en PDF (capacidad nueva: archivos)

**Qué se hizo (aditivo):**
- `meta_client.enviar_documento(telefono, link, filename)`: envía un documento (PDF) por WhatsApp con link público (Meta lo descarga). El bot antes solo mandaba texto.
- Tool `enviar_catalogo` (tools.py) + schema + regla en system_prompt (si piden catálogo/menú/folleto → manda el PDF; si no hay, ver_catalogo texto). Lee la config `catalogo_pdf`; link = `public_base_url + /api/catalogo/archivo`. Fallback graceful a texto.
- Endpoints (router.py): `POST/GET/DELETE /api/catalogo-pdf` (auth) + `GET /api/catalogo/archivo` (**PÚBLICO**, FileResponse para Meta). Subida valida pdf por content-type/extensión **y magic bytes `%PDF`**, máx 25MB. Guarda en `catalogo_dir` (/data/catalogo).
- config.py: `catalogo_dir` + `public_base_url`. **requirements.txt: + `python-multipart`** (obligatorio para UploadFile).
- Frontend: sección "Catálogo en PDF" en la pantalla Catálogo (subir multipart con token / estado / quitar).
- **Revisión:** el workflow adversarial NO corrió (límite de subagentes); revisado a mano → se endureció la validación (magic bytes %PDF). Endpoint público sin path traversal (nombre fijo), sirve solo el PDF (contenido público a propósito).
- **Verificado:** bot `compileall` OK; dashboard `build` OK.

**Pendiente:** redeploy del **bot + worker** (instala python-multipart; el worker usa el tool) + **dashboard**. La dueña sube su PDF en Catálogo.

## 2026-06-10 — Tasa BCV automática (ya no manual)

- `tasa.py`: `_FUENTE_BCV_DEFAULT = https://ve.dolarapi.com/v1/dolares/oficial`; `_tasa_desde_api` usa `settings.tasa_api_url or _FUENTE_BCV_DEFAULT` (funciona sin env var; se puede sobreescribir con `TASA_API_URL`). El BCV oficial llega en `promedio` y `_parsear_tasa` ya lo lee.
- Verificado: API real da Bs 572,68; el parser lo extrae OK; bot compila.
- **Pendiente:** redeploy del **bot + worker**. En la pantalla Tasa, dejar el **candado manual DESACTIVADO** para que use la automática (el candado pasa a ser freno de emergencia). El margen % se sigue sumando encima.

## 2026-06-10 — Mensajes clave editables (guías; el bot las redacta)

**Qué se hizo (aditivo, "agente no bot"):**
- Nuevo `app/services/mensajes.py`: `MENSAJES_DEFAULT` (guías de pago confirmado, rechazado, comprobante recibido = los textos que estaban hardcodeados) + `leer_guia(clave)` (config editable o default; nunca lanza).
- La dueña edita la **intención** de cada momento; el agente **redacta natural** (no plantilla). `confirmar_pago`/`rechazar_pago` (router) y `_procesar_comprobante` (worker) ahora leen la guía editable.
- Backend: `GET/PUT /api/mensajes`.
- **Pantalla nueva `/mensajes`**: 3 guías editables (comprobante recibido, pago confirmado, pago rechazado) + nota de que la bienvenida/tono van en Mi Bot y que el "pago confirmado" sigue blindado. Nav + Mensajes.
- **Verificado:** bot `compileall` OK; dashboard `build` OK.

**Pendiente:** redeploy del **worker** (comprobante) + **bot** (endpoints + confirmar/rechazar) + **dashboard** (pantalla Mensajes).

## 2026-06-10 — Pausar el bot por conversación ("atiendo yo", estilo SellerChat)

**Qué se hizo (aditivo):**
- Migración **`007_cliente_bot_pausado.sql`** (columna `bot_pausado` en clientes) + modelo + init_db.
- **Worker**: `_cliente_pausado(telefono)`; el chequeo ahora es `if not _bot_activo() OR _cliente_pausado(telefono)` → si la dueña pausó SOLO ese chat, el bot no responde a ese número pero **sigue atendiendo a todos los demás**. Fail-safe: ante error de lectura, no se pausa.
- Backend: `PUT /api/clientes/{telefono}/pausa` + `bot_pausado` en la lista de conversaciones.
- **Frontend**: en cada conversación abierta (`/conversaciones`), botón **"Pausar bot aquí" / "Reactivar bot aquí"** + aviso ámbar cuando está pausado. (El interruptor global de "Mi Bot" sigue siendo el maestro.)
- **Verificado:** bot `compileall` OK; dashboard `build` OK.

**Pendiente:** redeploy del **worker** (chequeo) + **bot** (endpoints + migración 007) + **dashboard** (toggle en Conversaciones).

## 2026-06-10 — Encender / apagar el bot (interruptor de seguridad)

**Qué se hizo (aditivo):**
- **Worker**: `_bot_activo()` lee la config `bot_activo` (default ENCENDIDO; ante error de BD, queda encendido para no quedar mudo). Cuando está apagado, `_procesar` y `_responder_y_enviar` **guardan el mensaje entrante** (`_guardar_entrante`, para que la dueña lo vea en Conversaciones y responda ella) y **NO responden**. El lock se libera igual (return dentro del try).
- **Los comprobantes SIEMPRE se procesan** (procesar_comprobante no toca el interruptor): nunca se pierde un pago aunque el bot esté apagado.
- Backend: `GET/PUT /api/bot-estado`.
- **Frontend**: toggle Encendido/Apagado arriba en `/bot` ("Mi Bot") con semáforo verde/rojo y explicación.
- **Verificado:** bot `compileall` OK; dashboard `build` OK.

**Pendiente:** redeploy del **worker** (ahí se chequea el interruptor) + **bot** (endpoint) + **dashboard** (toggle).

## 2026-06-09 — Conocimiento del negocio (Base de FAQ/info que usa el bot)

**Qué se hizo (aditivo):**
- Migración aditiva **`006_conocimiento.sql`** (tabla conocimiento) + modelo `Conocimiento` + init_db aplica 006.
- Backend: CRUD `GET/POST/PATCH/DELETE /api/conocimiento`. La info se **inyecta en el system prompt** (`_conocimiento_texto`) para que el bot responda dudas con datos reales, reforzando el anti-invento.
- **Pantalla nueva `/conocimiento`**: entradas por categoría (FAQ, productos, horarios, políticas, ubicación, empresa) con agregar/editar/borrar. Nav + Conocimiento.
- **Revisión adversarial (workflow de 4 agentes)** antes de subir → 5 arreglos aplicados: (1) limpiar banner de error en caminos felices; (2) opción dinámica en el select para categorías "otras"; (3) `limit(40)` + truncado a 3500 chars del conocimiento inyectado (no inflar el prompt ni diluir las reglas de cobro); (4) reescribir el bloque: el bot usa el conocimiento solo para dudas generales, y para productos/precios/ingredientes **manda SIEMPRE el catálogo** (si difieren, gana el catálogo); (5) validación pydantic `StringConstraints` (título/contenido no vacíos) + normalizar categoría vacía a None.
- **Verificado:** bot `compileall` OK; dashboard `build` OK.

**Pendiente:** redeploy del **bot + worker** (corre la migración 006; el worker usa el conocimiento) y del **dashboard** (pantalla Conocimiento).

## 2026-06-09 — 🐛 Fix (encontrado en pruebas reales): producto no encontrado + bot pegado

- **Error:** al pedir "empanada carne mechada" (nombre real: "Empanada de carne mechada") el bot NO la encontraba (búsqueda por frase exacta `ilike %frase%`) y peor: respondía "dame un segundito / déjame revisar / ya te digo" y NO usaba la herramienta — se quedaba pegado en bucle.
- **Fix:** `_buscar_producto` (tools.py) tolerante: intenta la frase completa y, si no, exige que TODAS las palabras >2 letras aparezcan; usado en `info_producto` y `registrar_pedido`. `info_producto` ahora devuelve `productos_disponibles` si no calza, para ofrecer alternativas. Regla BLINDADA nueva en system_prompt: prohíbe "dame un segundito/ya te digo" y obliga a usar la herramienta y responder en el mismo mensaje.
- **Aplica en:** todos los bots (la búsqueda exacta y el "déjame revisar" son trampas comunes). Requiere redeploy del **worker** (ahí corre el agente).

## 2026-06-09 — Fase 3: Conoce a tu cliente (CRM simple)

**Qué se hizo (aditivo, reusa datos existentes):**
- Migración aditiva **`005_cliente_notas.sql`**: columna `notas` en clientes. `models.py` + `init_db.py` actualizados.
- Backend nuevo: `GET /api/clientes` (lista con nº de pedidos, total gastado = pagos confirmados, última compra; excluye `__simulador__`), `GET /api/clientes/{telefono}` (ficha con historial de pedidos), `PUT /api/clientes/{telefono}/notas`.
- **Pantalla nueva `/clientes`**: lista + buscador (por nombre/teléfono) y ficha del cliente (total gastado, nº pedidos, cliente desde, **notas internas privadas** editables, e historial de pedidos). Nav + Clientes.
- **Verificado:** bot `compileall` OK; dashboard `build` OK (13 rutas, /clientes incluida).

**Pendiente:** redeploy del **bot** (corre la migración 005) y del **dashboard** (pantalla Clientes).

## 2026-06-09 — Fase 2: el panel de control (Mi Bot)

**Qué se hizo (aditivo, con el cobro blindado):**
- **Personalidad editable**: `system_prompt.py` ahora separa la **voz** (editable, clave `personalidad` en configuracion) de las **reglas críticas del cobro** (BLINDADAS, se anexan siempre). `construir_system_prompt` es async y lee la personalidad activa (cae al default si falla). Si la dueña edita la voz, NO puede romper el flujo de dinero.
- **Simulador**: `POST /api/probar` corre el agente con un teléfono de prueba (`__simulador__`) y devuelve la respuesta SIN enviar nada por WhatsApp.
- Backend nuevo: `GET/PUT /api/personalidad` (+ default para "restaurar"), `POST /api/probar`.
- **Pantalla nueva `/bot` ("Mi Bot")**: editor de personalidad (con candado que recuerda que las reglas del cobro están protegidas) + **simulador de chat** lado a lado (estilo SellerChat). Nav + Mi Bot.
- **Verificado:** bot `compileall` OK; dashboard `build` OK (12 rutas, /bot incluida).

**Pendiente:** redeploy del **bot** (personalidad + simulador) y del **dashboard** (pantalla Mi Bot). Nota: el simulador puede crear pedidos de prueba bajo `__simulador__` (no afecta el reporte, que solo cuenta pagos confirmados).

## 2026-06-09 — Fase 1 (en progreso): blindaje del dinero

**Blindaje 1 — Tasa BCV con margen + candado manual** ✅
- `tasa.py`: `obtener_tasa_bcv` ahora aplica un **margen (%)** sobre la tasa base BCV y respeta un **candado manual** (tasa fija exacta). Refactor aditivo: sin margen ni candado configurados, devuelve la tasa base de siempre. Cadena de respaldo intacta (caché → API → tasa_manual → default).
- Backend nuevo: `GET/PUT /api/tasa`. Pantalla nueva `/tasa`: tasa efectiva que se cobra, BCV de referencia, margen y candado.

**Blindaje 2 — Tope de gasto / anti-abuso** ✅
- `config.py`: `LIMITE_MENSAJES_CLIENTE_DIA` (default **80**, env var; 0 = sin tope).
- `redis_client.py`: contador de mensajes por cliente/día (`abuso:{tel}:{fecha}`) + aviso único (`aviso_abuso_nuevo`).
- `webhook/router.py`: si un cliente supera el tope, se **pausan las respuestas automáticas** con él por hoy y se **avisa a la dueña** (una vez). Los **comprobantes (imagen/PDF) SIEMPRE pasan** (es dinero). Cualquier fallo del contador deja pasar el mensaje (no frena el bot).

**Blindaje 3 — Pago que no calza (parcial / sobrepago)** ✅
- Migración aditiva **`004_pago_parcial.sql`**: estado `parcial` + columna `monto_recibido` (Bs). `models.py` + `init_db.py` actualizados.
- Backend nuevo: `POST /api/pagos/{id}/verificar-monto` con `{monto_recibido}` (Bs). Si recibido ≥ total → **confirmado** (y si pagó de más, avisa el **saldo a favor**); si < total → **parcial** (el pedido sigue esperando el resto). El agente le avisa al cliente con naturalidad (falta X / saldo a favor X).
- Panel (Pagos): botón **"Monto distinto"** que abre un campo "¿Cuánto recibiste? Bs" → registra; muestra estado parcial con lo recibido y lo que falta.

**Pendiente Fase 1:** Blindaje 4 (respaldo automático — script + Coolify). Redeploy del **bot** (tasa + anti-abuso + pago parcial, **corre la migración 004 al arrancar**) y del **dashboard** (Tasa + Pagos).

## 2026-06-09 — Fase 0 del Roadmap: control desde el panel

**Qué se hizo (todo aditivo, no rompe nada):**
- **Catálogo editable + agotados en 1 clic**: la pantalla Catálogo ahora permite crear/editar productos (precio, descripción, presentación, categoría) y marcar Disponible/Agotado en un toque. El backend (POST/PATCH `/api/productos`) ya existía; solo faltaba la UI.
- **Configuración del negocio editable** (pantalla nueva): nombre, ubicación, Instagram, datos de Pago Móvil y el WhatsApp de avisos a la dueña. Backend nuevo: `GET/PUT /api/configuracion` (solo claves permitidas, upsert en la tabla `configuracion` que el bot ya lee).
- **Reporte de ventas** (pantalla nueva): ventas cobradas (pagos confirmados), nº de pagos y pedidos para hoy/semana/mes. Backend nuevo: `GET /api/reporte`.
- Menú del panel: + Reporte, + Configuración.
- **Verificado:** dashboard `tsc --noEmit` + `npm run build` OK (12 rutas); bot `compileall` OK.

**Pendiente:** redeploy del **bot** (endpoints nuevos) y del **dashboard** (pantallas nuevas) en Coolify. Luego Fase 1 (blindaje del dinero) y Fase 2 (personalidad editable + probar el bot).

## 2026-06-09 — Conectar número de prueba real, panel en vivo y seguridad del dinero

**Qué se hizo:**
- Conectado el número de prueba real **+57 313 2933806** (coexistencia) al bot: webhook por `override_callback_uri` → `api-masvida.enovagroup.tech`, y credenciales del número (phone_number_id + token permanente de System User) en el worker. El bot responde por ese número.
- **"Escribiendo…":** el webhook ahora marca leído + muestra el indicador de tipeo al recibir un mensaje (`marcar_leido_y_escribiendo`).
- **Login del panel arreglado:** `init_db` no corría al arrancar (se añadió un `lifespan` en `app/main.py`) y `_crear_admin` ahora **sincroniza** la contraseña del admin con `ADMIN_PASSWORD` en cada arranque. Dashboard apuntado al bot con `NEXT_PUBLIC_API_URL=https://api-masvida.enovagroup.tech`.
- **Conversaciones en el panel:** el worker ahora persiste cada charla en Postgres (clientes + mensajes), no solo en Redis.
- **Panel casi en tiempo real:** la pantalla de Conversaciones se auto-refresca cada 7s (polling).
- **Cinturón anti-alucinación del dinero:** `_proteger_afirmacion_de_pago` intercepta si el agente afirma un pago confirmado en una charla y lo reemplaza por un mensaje seguro de "revisando" (solo la dueña confirma desde el panel).

**Pendiente:** automatizar el `override` en el onboarding (hoy es manual); convertir el proceso de onboarding en un skill reutilizable.

## 2026-06-08 — Validación en vivo de la conexión por coexistencia

**Qué se hizo:** se conectó el número colombiano por coexistencia desde `/conectar` y se guardó en `whatsapp_clients` (pantalla verde). El guardado fallaba porque el proyecto Supabase estaba **pausado** (free tier); al despertarlo, funcionó.

**Pendiente:** mantener Supabase activo o subir de plan para que no se vuelva a pausar.

## 2026-06-04 — Despliegue del sistema y arreglo del onboarding por coexistencia

**Qué se hizo:**
- Arreglado el onboarding por coexistencia en `/conectar` (sistema-recepcion-digital): se deriva WABA + número desde el token (debug_token → phone_numbers), no del popup. Desplegado a Vercel.
- Sistema másvida desplegado en el VPS (Coolify): bot + dashboard + worker + Postgres + Redis.
- Arreglos: `JWT_SECRET`/`ADMIN_PASSWORD` faltantes, cert HTTPS válido (`api-masvida.enovagroup.tech`, no sslip), `META_APP_SECRET` (firma del webhook), `REDIS_URL` con contraseña, `Dockerfile.worker` faltante, y el bug **"Event loop is closed"** (loop asyncio persistente por proceso en el worker).
- Probado: el bot responde por WhatsApp con productos reales.
