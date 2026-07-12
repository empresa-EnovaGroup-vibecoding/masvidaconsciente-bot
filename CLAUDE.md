# CLAUDE.md — másvida (bot + dashboard de WhatsApp)

> Instrucciones para la IA (Claude) en este proyecto. **Se cargan solas** al abrir la carpeta.
> Léelas SIEMPRE antes de tocar nada.

## 0. Antes de empezar (OBLIGATORIO)
1. Lee **`SESIONES.md`** (bitácora: qué se hizo y qué falta) y **`ROADMAP.md`** (el plan y lo que NO se construye).
2. **Mapea el código real** antes de cambiar nada. NUNCA hables de memoria vieja ni inventes (alucinar). Si no lo verificaste leyendo, dilo.
3. Al terminar un cambio, **regístralo en `SESIONES.md`** y súbelo a GitHub.

## 1. Qué es másvida
Sistema de **ventas y cobro por WhatsApp** para *masvidaconsciente* (comida saludable, Cabudare, Venezuela). Primer cliente de **Enova (Maired)**, que es **Tech Provider oficial de Meta**. Diseñado para **replicarse cliente por cliente** (una "caja cerrada" por cliente: su VPS, su bot, su panel).
- **bot** (esta carpeta `masvidaconsciente-bot`): el cerebro. Recibe WhatsApp, responde como *"Whuilianny"*, cobra.
- **`masvidaconsciente-dashboard`** (carpeta hermana): el panel de la dueña.
- App de conexión `sistema-recepcion-digital` (en Vercel, usa Supabase): onboarding del número por **coexistencia**.

## 2. Stack REAL (NO es el Trust Stack genérico de Praxis)
- **Bot:** Python · FastAPI · Celery + Redis · PostgreSQL · SQLAlchemy · OpenRouter (Gemini 2.5 Flash, fallback GPT-4.1).
- **Dashboard:** Next.js 15 + React 19 + TypeScript + Tailwind.
- **Infra:** VPS (Hostinger) + Coolify + Docker. Desplegado en `api-masvida.enovagroup.tech`.
- La BD es **PostgreSQL propio en el VPS** (NO Supabase) → la regla "RLS en Supabase" **no aplica**; la seguridad es la **auth del endpoint** (`usuario_actual`). Supabase solo se usa en la app de onboarding, aparte.

## 3. Reglas duras (no negociables)
- **ADITIVO:** nunca borrar/reescribir lo que funciona. Las migraciones **suman** (`00X_*.sql`), nunca tocan las viejas. Avisar antes de cambiar algo real.
- **No romper el cobro:** los precios salen SIEMPRE de las herramientas (nunca inventados); el agente **NUNCA** afirma que verificó el dinero en el banco ni que un pago quedó "confirmado por el banco". El bot **reconoce** (por visión) si la imagen es un comprobante real —a las cuentas de la dueña; ignora imágenes cualquiera— y, si lo es (o si hay duda), lo **registra** (`reportado`) y **sigue la venta** (recibido + coordina entrega); si la visión está SEGURA de que NO es comprobante, pide la captura y no registra. La **dueña verifica en su banco** (su banco ya le avisa; el panel queda para auditar/**anular**). `pagado` solo se fija desde `/confirmar`. Las **reglas del cobro están blindadas** en el system prompt (no se editan desde el panel). Ver `PRP-cobro.md`.
- **Humanizar al máximo (agente, no bot):** los mensajes al cliente los **REDACTA el agente** (naturales, variados, con contexto), NUNCA plantillas fijas. Transcribe notas de voz; responde stickers con naturalidad.
- **Seguridad Tech Provider con Meta:** NINGÚN envío proactivo automático sin **aprobación humana**. Un envío mal calibrado quema la calidad del número y arriesga la cuenta de Meta de TODOS los clientes. Regla dura.
- **Datos:** validar entradas (Pydantic en el bot, tipos en el dashboard). Nunca exponer secretos. Comprobantes privados (endpoint con auth).
- **Verificar antes de dar por hecho:** `compileall` (bot) + `build`/`tsc` (dashboard) antes de cerrar.

## 4. Base de datos: red de seguridad
Antes de tocar datos reales, probar el cambio dentro de una transacción y hacer **ROLLBACK** para verificar. Nunca alterar producción sin ese ensayo. Las migraciones deben ser idempotentes (`CREATE TABLE IF NOT EXISTS`, `DROP CONSTRAINT IF EXISTS`, `INSERT ... ON CONFLICT DO NOTHING`).

## 5. Decisiones ya tomadas (NO re-proponer)
- **Selector de MODELO en el panel = SÍ, pero es palanca de PROVEEDOR (Maired), no de la clienta.** Vive en Configuración (clave `modelo_ia`, ver `leer_modelo_ia()`); cuando la clienta tenga su propio rol/login se le esconde. La **temperatura sigue SIN selector** (fija en código). La **voz (transcripción)** va aparte y FIJA en `OPENROUTER_MODEL_AUDIO` (solo Gemini acepta audio): el selector NUNCA la toca.
- Cobro: Pago Móvil manual; tasa BCV automática (dolarapi oficial) con **margen %** + **candado manual**; manejo de **pago parcial / sobrepago**.
- Ver la lista completa de "lo que NO se construye" en `ROADMAP.md`.

## 6. Dónde está cada cosa
- **`CLAUDE.md`** (este archivo) → las reglas del proyecto + este mapa de documentos.
- **`ROADMAP.md`** → la visión COMPLETA (qué se construye y qué NO) + la sección **"EN QUÉ ESTAMOS AHORA"** (la lista de pendientes, en orden).
- **`SESIONES.md`** → bitácora: qué se hizo cada día (el historial).
- **`ENOVA_BLUEPRINT.md`** → cómo montar un cliente nuevo (la fábrica).
- **`BRIEF-*.md`** → el diseño detallado de UN tema continuo (ej. `BRIEF-closer-masvida.md` = la voz/personalidad del bot).
- **`PRP-*.md`** → la receta de construcción de UNA mejora, antes de hacerla (**un PRP por mejora**).
- ⚠️ `BRIEF-*` y `PRP-*` son **LOCALES (gitignored)**: tienen estrategia/datos sensibles, **NO se suben** a GitHub.
- Código del bot: `app/` (`webhook/`, `agent/`, `services/`, `workers/`, `api/`). Migraciones: `migrations/`.

## 7. Principios de código (de Praxis, lo que aplica)
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
> ## 🔴 ⚠️ PERO **NO RECORTES EL PROMPT** PARA "DES-DUPLICAR" — YA SE PROBÓ Y **ROMPE EL COBRO**
> **Experimento del 2026-07-11 (A/B real, 3 repeticiones por servidor):** se quitaron del prompt las reglas
> que "ya estaban en el código" (secciones `# PRECIO`, `# CATÁLOGO: cuándo mandarlo`, formato, fotos, pasos
> del cobro). El prompt bajó de 11.648 → 9.338 chars y la voz se mantuvo… **pero el bot empezó a registrar el
> PRODUCTO EQUIVOCADO**: a *"quiero 2 paquetes de empanadas de plátano"* grabó **"Empanadas Keto"** ($12/4u,
> total $24) en vez de **"Empanadas"** ($14/8u, total $28). **2 de 2 veces.** Con el prompt original: correcto
> **2 de 2**. Se revirtió todo.
>
> **Conclusión (Auto-Blindaje):** con un modelo PEQUEÑO (Haiku) **la redundancia entre prompt y código NO es
> grasa: es lo que SOSTIENE la selección de producto**. "Una sola fuente por tema" es buena teoría y MALA
> práctica aquí. **El prompt largo se queda.**
>
> **Si alguna vez se vuelve a tocar el prompt, es OBLIGATORIO:** probar el cobro ANTES y DESPUÉS —registrar un
> pedido real y **verificar en la BD** (`SELECT items, total FROM pedidos`) que el **producto y el total** son
> los correctos—, quitar **una sola cosa a la vez**, y revertir a la primera diferencia. Nunca fiarse de que
> "la respuesta se ve bien": el bot **hablaba** de las de plátano y **cobraba** las Keto.

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

**En la Personalidad (panel/BD) va SOLO:** quién es Whuilianny + su **voz/esencia** + su **bienvenida** + sus **ejemplos de cómo habla** (la dueña los definió: son intocables, no reescribir), los **hechos del producto** (sin gluten, azúcar de coco, alulosa…), **reglas del negocio** (horario, delivery, anticipación), **pagos** y los **datos bancarios**. Copia canónica de la voz: `BRIEF-personalidad-whuilianny.md`. ⚠️ El texto VIVO manda: vive en la BD del servidor (clave `personalidad`); antes de editar, leerlo del servidor.

---
*Documento vivo. Si algo aquí ya no es cierto, corrígelo. Inspirado en el sistema del mentor (Erwin) y en Praxis, adaptado al stack real de másvida.*
