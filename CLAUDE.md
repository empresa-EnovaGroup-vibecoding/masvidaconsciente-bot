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
- **No romper el cobro:** los precios salen SIEMPRE de las herramientas (nunca inventados); el agente **NUNCA** afirma que un pago está confirmado (eso lo hace la dueña desde el panel); `pagado` solo se fija desde `/confirmar`. Las **reglas del cobro están blindadas** en el system prompt (no se editan desde el panel).
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

---
*Documento vivo. Si algo aquí ya no es cierto, corrígelo. Inspirado en el sistema del mentor (Erwin) y en Praxis, adaptado al stack real de másvida.*
