# Bitácora de sesiones — masvidaconsciente

> **Dos prácticas adoptadas (inspiradas en el sistema del mentor Erwin), para no romper lo que funciona:**
>
> 1. **Registrar cada sesión** en este archivo: qué se cambió, por qué, y qué quedó pendiente.
> 2. **Cambios de base de datos con red de seguridad:** antes de tocar datos reales, probar el cambio dentro de una transacción y hacer **ROLLBACK** (deshacer) para verificar que está bien. Nunca alterar datos de producción sin ese ensayo previo.

---

## ⏳ Pendientes importantes (no olvidar)

- 🔴 **Conectar la tasa BCV AUTOMÁTICA** (`TASA_API_URL` en Coolify) para que la tasa se actualice sola y la dueña NO tenga que ponerla a mano. Hoy (2026-06-09) funciona con una **tasa manual fija** como respaldo (Bs 567,68), pero **NO es automática**. Es parte de la Fase 1 (blindaje del dinero). **Recordárselo a Maired cuando retomemos** — ella pidió diferirlo hasta terminar el panel.
- 🔴 **Respaldo automático de datos (Blindaje 4)** — DIFERIDO a pedido de Maired (2026-06-09). Plan: capa 1 = respaldo **local** en Coolify (nada sale del servidor); protección REAL = **offsite cifrado** (privado, encriptado con llave que solo ella controla — el cifrado resuelve su preocupación de filtración). Honesto: el local NO salva si muere el servidor entero, y NO incluye las fotos de los comprobantes. **Montar el offsite cifrado ANTES del lanzamiento real con clientes** (cuando haya dato con valor). Recordárselo.
- 🟡 **Afinar la personalidad como "closer de ventas nato" + ajustes finales de tono** — en la fase de PULIDO FINAL (cuando todo esté armado), escribir un guión de ventas potente en el editor de Personalidad (/bot): manejo de objeciones, cierre con cariño, terminar mensajes con pregunta, etc., y probarlo en el simulador. Maired lo difirió 2026-06-09 para hacerlo "cuando toque afinar todo para que quede perfecto".

---

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
