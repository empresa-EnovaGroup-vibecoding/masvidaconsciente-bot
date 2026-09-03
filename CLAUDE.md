# CLAUDE.md — másvida (bot + dashboard de WhatsApp)

> Instrucciones para la IA (Claude) en este proyecto. **Se cargan solas** al abrir la carpeta.
> Léelas SIEMPRE antes de tocar nada.

## 0. Antes de empezar (OBLIGATORIO)
1. Lee, EN ESTE ORDEN: **`ESTADO.md`** (qué corre en cada servidor — si contradice a otro documento, manda este) → el bloque "EN QUÉ ESTAMOS AHORA" de **`ROADMAP.md`** → la **ÚLTIMA entrada** de `SESIONES.md` (solo la última; el resto es historial).
2. **Mapea el código real** antes de cambiar nada. NUNCA hables de memoria vieja ni inventes (alucinar). Si no lo verificaste leyendo, dilo.
3. Al terminar un cambio, **regístralo en `SESIONES.md`** y súbelo a GitHub.

## 1. Qué es másvida
Sistema de **ventas y cobro por WhatsApp** para *masvidaconsciente* (comida saludable, Cabudare, Venezuela). Primer cliente de **Enova (Maired)**, que es **Tech Provider oficial de Meta**. Diseñado para **replicarse cliente por cliente** (una "caja cerrada" por cliente: su VPS, su bot, su panel).
- **bot** (esta carpeta `masvidaconsciente-bot`): el cerebro. Recibe WhatsApp, responde como *"Whuilianny"*, cobra.
- **`masvidaconsciente-dashboard`** (carpeta hermana): el panel de la dueña.
- App de conexión `sistema-recepcion-digital` (en Vercel, usa Supabase): onboarding del número por **coexistencia**.

## 2. Stack REAL
- **Bot:** Python · FastAPI · Celery + Redis · PostgreSQL · SQLAlchemy · OpenRouter (Gemini 2.5 Flash, fallback GPT-4.1).
- **Dashboard:** Next.js 15 + React 19 + TypeScript + Tailwind.
- **Infra:** Coolify + Docker en dos servidores: **producción** en netcup (`api.masvidaconsciente.store`) y **pruebas** en el VPS de Enova (desde el 1-sep; URLs y detalle en `ESTADO.md`; panel de pruebas: `panel-masvida.enovagroup.tech`). *(Hostinger murió con el taller el 1-sep. El default hardcodeado `api-masvida.enovagroup.tech` se eliminó en el PR #18; pruebas ya tiene su `PUBLIC_BASE_URL` propia y producción sigue pendiente — ver `ESTADO.md`.)*
- La BD es **PostgreSQL propio en el VPS** (NO Supabase) → la regla "RLS en Supabase" **no aplica**; la seguridad es la **auth del endpoint** (`usuario_actual`). Supabase solo se usa en la app de onboarding, aparte.

## 3. Reglas duras (no negociables)
- **TODO pasa por GitHub. NADA se edita a mano dentro del VPS** (ni `docker cp`, ni tocar archivos en el contenedor). Lo que no está en el repositorio NO existe: el siguiente despliegue lo borra. *(Lección del 2026-08: hubo trabajo atrapado en el servidor sin respaldo — rescatado y unificado en GitHub el 21-ago. Ver `ESTADO.md`.)*
- **ADITIVO:** nunca borrar/reescribir lo que funciona. Las migraciones **suman** (`00X_*.sql`), nunca tocan las viejas. Avisar antes de cambiar algo real.
- **No romper el cobro:** los precios salen SIEMPRE de las herramientas (nunca inventados); el agente **NUNCA** afirma que verificó el dinero en el banco ni que un pago quedó "confirmado por el banco". El bot **reconoce** (por visión) si la imagen es un comprobante real —a las cuentas de la dueña; ignora imágenes cualquiera— y, si lo es (o si hay duda), lo **registra** (`reportado`), le dice al cliente que lo recibió y lo está **revisando**, y **AVISA A LA DUEÑA** — pero **NO coordina la entrega todavía**; si la visión está SEGURA de que NO es comprobante, pide la captura y no registra. 🔴 **El clic de «Pago aprobado» de la dueña es lo que REACTIVA al bot** para confirmar y coordinar (`confirmar_pago` → `notificar_cliente_pago`). *(Cambiado el 2026-08-22 a petición de Maired —pasos 8-9 de su plantilla de negocio—: antes el bot seguía la venta de una. Pausar y avisarle a ella van JUNTOS: si el bot espera y ella no se entera, el cliente queda colgado después de haber pagado.)* La **dueña verifica en su banco** (su banco ya le avisa; el panel queda para auditar/**anular**). `pagado` solo se fija desde `/confirmar`. Las **reglas del cobro están blindadas** en el system prompt (no se editan desde el panel). Ver `PRP-cobro.md`.
- **Humanizar al máximo (agente, no bot):** los mensajes al cliente los **REDACTA el agente** (naturales, variados, con contexto), NUNCA plantillas fijas. Transcribe notas de voz; responde stickers con naturalidad.
- **Seguridad Tech Provider con Meta:** NINGÚN envío proactivo automático sin **aprobación humana**. Un envío mal calibrado quema la calidad del número y arriesga la cuenta de Meta de TODOS los clientes. Regla dura.
- 🔴 **EL NÚMERO DE PRODUCCIÓN (+58 424-7047595) ES POR COEXISTENCIA: Whuilianny lo ve en VIVO en su propio celular** (confirmado por Maired, 2026-09-01; ya estaba en `SESIONES.md` de junio, enterrado). Cualquier mensaje a ese número —de prueba o real— le suena el teléfono a ELLA, sin importar la lista blanca del bot. **Nunca escribirle a ese número sin el OK expreso de Maired sobre el horario** (piensa "le estoy escribiendo a una persona dormida", no "estoy probando un servidor). 🪦 El TALLER murió el 1-sep (Maired canceló el VPS), pero esa misma noche nació su reemplazo: el **ENTORNO DE PRUEBAS de Enova** (VPS propio del socio), con el número de la agencia (`+57 313 293 3806`) conectado — **para probar por WhatsApp real, escribe AHÍ, jamás al de la clienta** (detalle en `ESTADO.md` bloque 🏭 y SESIONES 1-sep (14)). Para probar SIN WhatsApp sigue el **SIMULADOR del panel** (clientes `__simulador__`, no contaminan).
- **Datos:** validar entradas (Pydantic en el bot, tipos en el dashboard). Nunca exponer secretos. Comprobantes privados (endpoint con auth).
- **Verificar antes de dar por hecho:** `compileall` (bot) + `build`/`tsc` (dashboard) antes de cerrar.

## 4. Base de datos: red de seguridad
Antes de tocar datos reales, probar el cambio dentro de una transacción y hacer **ROLLBACK** para verificar. Nunca alterar producción sin ese ensayo. Las migraciones deben ser idempotentes (`CREATE TABLE IF NOT EXISTS`, `DROP CONSTRAINT IF EXISTS`, `INSERT ... ON CONFLICT DO NOTHING`).

## 5. Decisiones ya tomadas (NO re-proponer)
- **Selector de MODELO en el panel = SÍ, pero es palanca de PROVEEDOR (Maired), no de la clienta.** Vive en Configuración (clave `modelo_ia`, ver `leer_modelo_ia()`); cuando la clienta tenga su propio rol/login se le esconde. La **temperatura sigue SIN selector** (fija en código). La **voz (transcripción)** va aparte y FIJA en `OPENROUTER_MODEL_AUDIO` (solo Gemini acepta audio): el selector NUNCA la toca.
- Cobro: Pago Móvil manual; tasa BCV automática (dolarapi oficial) con **margen %** + **candado manual**; manejo de **pago parcial / sobrepago**.
- Ver la lista completa de "lo que NO se construye" en `ROADMAP.md`.

## 6. Dónde está cada cosa — 9 DOCUMENTOS EN LA RAÍZ (no crear más)

**Los 4 del día a día** (en orden de lectura):
1. **`ESTADO.md`** → qué corre en cada servidor. **Si contradice a otro documento, manda este.**
2. **`ROADMAP.md`** → qué falta, en orden + "lo que NO se construye".
3. **`SESIONES.md`** → bitácora (leer solo la última entrada).
4. **`CLAUDE.md`** (este) → las reglas.

**Los 4 de referencia** (casi no cambian): `RESPALDO.md` · `BRIEF-personalidad-whuilianny.md` · `BRIEF-closer-masvida.md` · `PRP-cobro.md`.

> Nota: el material del Tech Provider (modelo de negocio "ENOVA_BLUEPRINT", montar un cliente nuevo, onboarding de datos) ya NO vive aquí — es de Enova, no de másvida. Vive en su proyecto propio y está disponible como skill `/meta-tech-provider` en cualquier proyecto.

**`archivo/`** → todo documento CUMPLIDO se mueve ahí (su `LEEME.md` dice qué es cada cosa).
**Regla dura: un documento nuevo en la raíz solo si reemplaza a otro. Lo cumplido baja a `archivo/`.**

- ⚠️ `BRIEF-*` y `PRP-*` son **LOCALES (gitignored)**: tienen estrategia/datos sensibles, **NO se suben** a GitHub.
- 🟢 **SÍ EXISTEN en la máquina de Maired** (verificado 2026-08-21): `BRIEF-personalidad-whuilianny.md`, `BRIEF-closer-masvida.md`, `PRP-cobro.md` en la raíz + 9 más en `archivo/`. *(Nota histórica: el 2026-08-03 se anotó aquí que "no existía ni uno" — cierto desde el servidor/GitHub, donde por ser gitignored NUNCA aparecen; pero la copia local de Maired los conserva. NO se perdieron.)* Aun así, **la voz VIVA manda y vive en la BD** (tabla `configuracion`, clave `personalidad`): léela de ahí antes de tocar la personalidad.
- Código del bot: `app/` (`webhook/`, `agent/`, `services/`, `workers/`, `api/`). Migraciones: `migrations/`.

## 7. Principios de código
KISS · YAGNI · DRY · una responsabilidad por pieza · nombres claros · archivos cortos · nunca `any` en TypeScript.

## 8. El cerebro del bot: qué vive en el CÓDIGO vs. en el PROMPT (NO duplicar)

> El comportamiento del bot se arma en **3 capas** que el código junta en cada mensaje
> (`app/agent/system_prompt.py` → `construir_partes_prompt`):
> 1. **Personalidad** (editable en el panel / BD, clave `personalidad`) = **SOLO la voz/esencia de Whuilianny**.
> 2. **`_REGLAS`** (blindadas en `system_prompt.py`, NO editables) = el cobro y las conductas duras.
> 3. **Catálogo + notas de herramientas** (`_catalogo_bloque`) + **redes de seguridad** en `app/agent/agent.py`.
>
> **REGLA:** lo de abajo **YA está en el código**. Al **AGREGAR** cosas nuevas al prompt, NO las repitas aquí.
> Si hay que **cambiar** una de estas conductas, se edita el **CÓDIGO** (`system_prompt.py` / `agent.py`),
> **NUNCA** el prompt del panel.
>
> ## 🔴 AUTO-BLINDAJE: cómo NO repetir el error del 2026-07-11/12
> **El 2026-07-11 escribí aquí que "recortar el prompt rompe el cobro". ERA FALSO — y peligroso.** Lo dejo
> escrito para que nadie repita ni el bug ni el razonamiento.
>
> **Lo que pasó:** un A/B "probó" que al limpiar el prompt el bot registraba **"Empanadas Keto"** ($12/4u)
> en vez de **"Empanadas"** ($14/8u). **El A/B estaba VICIADO:** el prompt limpio se corrió en el servidor
> VIEJO y el original en NETCUP. Lo que cambiaba **era el servidor, no el prompt**.
>
> **La causa REAL era un bug del CÓDIGO** (`tools.py` → `_buscar_producto`, el camino del DINERO): buscaba con
> `ilike('%nombre%')` + `.first()` **SIN `ORDER BY`**. Como hay 3 productos que empiezan con "Empanadas",
> pedir `"Empanadas"` (¡el nombre EXACTO y correcto!) calzaba con los 3 y **Postgres devolvía uno ARBITRARIO**:
> el viejo cobraba Keto ($12), netcup cobraba Empanadas ($14), **mismo código, misma consulta**. Además `'pan'`
> calzaba por substring con em-**pan**-adas. **El modelo nunca se equivocó: mandaba el nombre correcto.**
> **Arreglado el 2026-07-12** (exacto primero · prefijo de palabra, no substring · orden estable · ambiguo
> real ⇒ preguntar, jamás adivinar · si no existe ⇒ rechazar con la lista, jamás aproximar). 9/9 en ambos
> servidores.
>
> **Las 3 reglas que quedan (valen para siempre):**
> 1. **Nunca compares un A/B entre servidores distintos.** Misma máquina, cambia UNA sola variable.
> 2. **Verifica el cobro en la BD, no en la respuesta:** `SELECT items, total FROM pedidos`. El bot *hablaba*
>    de las de plátano y *cobraba* las Keto — el texto se veía perfecto.
> 3. **Antes de culpar al modelo o al prompt, sospecha del código.** Aquí el modelo era inocente.
>
> *(Queda pendiente el blindaje definitivo: que `registrar_pedido` reciba un `producto_id` de una lista
> CERRADA —"código de barras"— en vez de un nombre en texto libre.)*

> ## 🪦 LA FRONTERA DEL 2026-08-24: las redes de ESTILO se QUITARON — no reintroducirlas
> **Decisión de Erwin (24-ago):** se eliminaron las 3 redes que REESCRIBÍAN o CENSURABAN el texto
> del modelo — la **RED DEL PITCH** (fabricó las 2 fichas repetidas que reportó Maired y descartó
> borradores buenos), la **RED DE LA FICHA REPETIDA** (mutiló el cobro: "7.799,52 Bs" salió como
> "799,52 Bs", entregado) y la **INSERCIÓN DE RESÚMENES** (plantaba el texto que la otra recortaba
> — L28). No repetirse, hacer el pitch y presentar el recibo son ahora **conducta del LLM** (las
> reglas 66 y 107-108 de `_REGLAS` ya lo ordenan). **El motivo es de diagnóstico:** si sin muletas
> el modelo repite u omite cifras, la señal es limpia — el techo es el MODELO, y la palanca es
> subir de modelo o el modo DOS, no más redes de estilo. Las redes del DINERO, la VERDAD, la SALUD
> y Meta se quedan TODAS (`_dinero_inventado`, `_datos_sensibles_inventados`, tamaño, día
> imposible, frase del banco, etc.): esas protegen contra cualquier modelo. **Antes de reintroducir
> una red de estilo: medir antes y después, y leer las lápidas 🪦 en `agent.py`.**

> ## 🛡️ LA REGLA DE LOS GUARDIAS (2026-09-01): miran también al CLIENTE
> Un guardia que juzga un BORRADOR tiene que mirar además **lo que el cliente acaba de pedir**
> (`pregunta_cliente`): **responder no es insistir; negar no es prometer.** Nació de un caso
> real: la red del cierre censuró la lista de sabores que la clienta acababa de PEDIR (el bot
> la sabía perfecta), y la del día imposible obligaba a reescribir "los domingos no entregamos".
> Las redes de MENTIRA y DINERO no llevan esta absolución a propósito: lo prohibido sigue
> prohibido aunque el cliente lo pida. Auditoría completa de los 13 guardias y el detalle:
> `SESIONES.md` 2026-09-01 (2). **Todo guardia NUEVO (p. ej. el vigilante de la rama D) nace
> con esta regla puesta.**

**Ya vive en el código (no ponerlo en el prompt):**
- **Formato al escribir:** corto, varios mensajitos, sin viñetas ni negritas, espejear al cliente. → `_REGLAS` (BREVEDAD, "Planos sin formato", ESPEJEA).
- **Saludo:** saludar según la hora de Venezuela y responder "muy bien, gracias a Dios" al "¿cómo estás?". → `_REGLAS` + `_saludo_hora_texto` (inyecta la hora) + red `_asegurar_saludo` (agent.py, lo garantiza aunque el modelo falle).
- **Cliente conocido:** saludarlo por su nombre, no re-presentarse, recordar sus datos. → `_REGLAS` (MEMORIA DEL CLIENTE) + `_ficha_cliente_texto` (inyecta la ficha cada turno) + tool `recordar_cliente`.
- **No inventar (regla #1):** nunca inventar productos, precios, ingredientes ni datos; nombres exactos; usar siempre las herramientas. → `_REGLAS` (ANTIINVENCIÓN).
- **Precio:** no soltarlo de frente, darlo solo cuando lo piden, copiarlo EXACTO de la herramienta, nunca calcularlo. → `_catalogo_bloque` (línea `[SOLO PARA TI]`) + `_REGLAS` (DINERO).
- **Catálogo:** cuándo mandar el PDF vs. nombrar productos, no agrupar por categoría, no decir que lo envió si no lo hizo. → `_REGLAS` + `_catalogo_bloque` + red `_asegurar_catalogo`.
- **Fotos/video:** cuándo mandarlas y qué hacer si no hay. → `_REGLAS` (FOTOS/VIDEO, tool `enviar_fotos_producto`).
- **Todo el cobro:** tomar el pedido (`registrar_pedido`), dar datos de pago (`generar_datos_pago`), registrar comprobante (`registrar_comprobante`), y **NUNCA decir que el banco confirmó** el pago. → `_REGLAS` (blindaje del cobro).
- **Sigue el hilo:** si el cliente ya eligió variante, seguir solo con esa. → `_REGLAS`.
- **Sin promesas médicas:** no decir que cura/sana ni dar consejo médico. → `_REGLAS`.
- **Notas de voz y stickers:** responder con naturalidad. → `_REGLAS`.
- **Dudas del negocio:** ubicación/pago/horarios (`info_negocio`), un producto (`info_producto`), generales (`buscar_info`; distingue envío nacional ≠ entrega local). → `_REGLAS`.

**En la Personalidad (panel/BD) va SOLO:** quién es Whuilianny + su **voz/esencia** + su **bienvenida** + sus **ejemplos de cómo habla** (la dueña los definió: son intocables, no reescribir), los **hechos del producto** (sin gluten, azúcar de coco, alulosa…), **reglas del negocio** (horario, delivery, anticipación), **pagos** y los **datos bancarios**. 🔴 **La copia VIVA y canónica de la voz es la BD del servidor** (tabla `configuracion`, clave `personalidad`): **léela de ahí antes de tocar nada** — manda sobre cualquier archivo. *(El `BRIEF-personalidad-whuilianny.md` SÍ existe en la máquina de Maired como referencia de diseño —verificado 21-ago—, pero es gitignored: no aparece en el servidor ni en GitHub. Úsalo como contexto, nunca como la verdad actual de la voz.)*

---
*Documento vivo. Si algo aquí ya no es cierto, corrígelo.*

*Nota (2026-08-21): las plantillas genéricas de "SaaS Factory / Praxis" se sacaron de este proyecto
—eran de otro stack (Next.js + Supabase) y contaminaban el contexto de este bot de Python. Viven
aparte en `projects/saas-factory-setup/`; este proyecto NO depende de nada de ahí.*
