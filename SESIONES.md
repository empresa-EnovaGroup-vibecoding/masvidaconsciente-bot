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

## 2026-07-12 (noche) — 🖥️ La BANDEJA "El bot te necesita" YA SE VE EN EL PANEL (repo dashboard)

**Qué se hizo:** la pantalla que faltaba del handoff (el motor y la API ya estaban desplegados desde la tarde). Repo **`masvidaconsciente-dashboard`**, todo **aditivo** (no se tocó ninguna pantalla existente):
- **Pantalla nueva `/bandeja`** (`src/app/(app)/bandeja/page.tsx`): los avisos con **motivo** (color por motivo), **cliente**, **lo que preguntó** (citado), fecha/hora; botón **"Ya lo atendí (reactivar el bot)"** (→ `POST /api/intervenciones/{id}/resolver`), link **"Abrir el chat en WhatsApp"** (`wa.me`, oculto si no es un teléfono real, ej. el simulador), y filtro **Te esperan / Ya atendidos**.
- **Bloque "El precio de hoy"** en la misma pantalla (`GET|PUT /api/precio-dia`): los 3 productos de precio variable (Tortas keto, Premezclas, torta baja) con su campo para escribir cuánto están HOY; muestra "Hoy: $X" o "Sin precio de hoy". Texto que explica que **vale solo por hoy**.
- **Contador en el menú** (`layout.tsx`) que **se refresca solo cada 45 s**: era el punto ciego real — *el bot avisaba y nadie lo veía*. Ahora se ve sin recargar la página.
- `lib/api.ts` (tipos + 4 endpoints) y `lib/estados.ts` (color por motivo, misma fuente única que pedidos/pagos).

**Verificado de verdad (no "debería funcionar"):** `tsc` limpio + `npm run build` OK; panel local **contra la API real** (servidor viejo) con Playwright: login → la bandeja mostró el aviso REAL que dejó el bot (*"Cliente pregunta el precio de la Torta Keto de 1kg"*) → se escribió el precio de hoy ($38 → badge "Hoy: $38,00") → "Ya lo atendí" → el aviso pasó a **Ya atendidos** y el bot quedó reactivado. **Comprobado en la BD** (`intervenciones.estado='resuelta'` + `resuelta_at`; `precio_dia` con producto 11, $38, fecha de hoy), no en la respuesta de la pantalla.

**Dónde se probó y por qué:** se usó el servidor **VIEJO** (2.25.139.106) a propósito, porque **hoy WhatsApp entra por NETCUP** (152.53.89.118: 254 mensajes en 7 días; el viejo no recibe nada desde el 06-jul). Escribir un precio de prueba en el servidor vivo habría hecho que el bot **le venda a un cliente real a un precio inventado por mí**. Al terminar se **borraron las filas de prueba** (`precio_dia` y el aviso del simulador) — el viejo quedó limpio.

**Hallazgos (para no olvidar):**
- ⚠️ **El servidor VIVO es netcup, no el viejo** (contradice notas viejas). El env del viejo **no tiene `NUMEROS_PERMITIDOS`**; el de netcup sí (`573005690062`, solo Maired).
- ⚠️ **`DUENO_TELEFONO` sigue vacío en los dos** → el ping de WhatsApp del handoff no le llega a nadie todavía. Es el punto 3 del ROADMAP.
- ⚠️ El **precio del día es por PRODUCTO, no por tamaño**: "Tortas keto" tiene los 3 tamaños (250g/500g/1kg) metidos en un solo producto, así que hoy solo se le puede poner **un** precio. Lo arregla el punto 5 (PRODUCTO + VARIANTES).

**Pendiente inmediato:** desplegar el panel (push a master → GitHub Actions) y que Maired lo mire.

---

## 2026-07-12 (tarde) — 🔔 "EL BOT TE NECESITA": handoff a la humana + PRECIO DEL DÍA

**El descubrimiento que lo motivó (dicho por Maired):** las **Tortas keto**, la **torta baja en carbohidratos** y las **Premezclas** están en el catálogo **SIN PRECIO A PROPÓSITO**. No es un olvido: **en Venezuela el precio cambia de un día a otro** y la dueña responde ella esas consultas. ⚠️ **Yo asumí que era descuido y afirmé una causa inventada** ("el sistema no te dejaba"). Maired me lo reclamó con razón: *"eso es lo que me da rabia, que no me cuestionas"*. **Regla: si no sé algo, decir "no lo sé" — no rellenar el hueco con una explicación plausible.**

**🔴 Bug de dinero que salió de ahí:** `registrar_pedido` hacía `subtotal = (prod.precio or Decimal("0")) * cantidad` → un producto SIN precio se registraba en **$0**. El bot podía cerrar un pedido de **tortas GRATIS**. **Tapado.**

**🔴 Otro hallazgo:** `dueno_telefono` está **VACÍO** (config y env, en bot y worker, en los dos servidores) → el aviso que YA existía (`_avisar_duena`, "🔔 Nuevo pago reportado" al entrar un comprobante) **nunca le ha llegado a nadie**. Falta configurarlo.

**Lo construido (aditivo):**
- **Migración `015_intervenciones.sql`**: tabla `intervenciones` (la bandeja "el bot te necesita") + tabla `precio_dia`.
- **Herramienta nueva `pedir_ayuda(motivo, detalle)`** (`tools.py`): **pausa** ese chat (`bot_pausado`), deja el aviso en la bandeja, y le manda un WhatsApp a la dueña (best-effort: si no hay número o Meta cierra la ventana de 24h, **el aviso igual queda en el panel**). Un solo aviso vivo por chat (no la inunda). Los 4 motivos: `precio_del_dia` · `no_se` · `pide_persona` · `reclamo`.
- **Regla blindada** en `_REGLAS` (`system_prompt.py`): cuándo llamarla. Y el **catálogo inyectado** ahora marca esos productos como **"PRECIO DEL DÍA — TODAVÍA NO LO SABES"** (prohibido inventarlo, estimarlo o usar el de ayer).
- **`_precio_efectivo()`**: precio fijo → ese; precio variable → el que la dueña dio **HOY**; si no lo dio → **None** y `registrar_pedido` **RECHAZA** (nunca $0, nunca el de ayer).
- **API para el panel**: `GET /api/intervenciones` (bandeja) · `POST /api/intervenciones/{id}/resolver` (cierra el aviso y **reactiva el bot**) · `GET|PUT /api/precio-dia` (la dueña dice cuánto está hoy; vale **solo por hoy**).
- **Banco de pruebas ampliado** (`scripts/probar_cobro.py`): guardián permanente de que ningún producto sin precio se pueda cobrar.

**Verificado en vivo (servidor viejo, contra la BD real):** *"¿cuánto cuesta la torta keto de 1kg?"* → el bot **NO inventó**, respondió *"te confirmo ese precio enseguida 💚"* (su voz, sin plantilla), **se calló** en ese chat, y dejó el aviso `🔔 [precio_del_dia] Cliente pregunta el precio de la Torta Keto de 1kg`. Con precio del día puesto ($38) → registra **2 × $38 = $76** ✓. Sin precio → **rechaza** ✓. Banco de pruebas: **cero regresiones**.

**Decisiones de Maired:** avisos → **al panel** (canal confiable) + ping de WhatsApp **a ella (Maired, 573005690062)** mientras se prueba, no a la clienta. El precio del día **se guarda por HOY** (mañana el bot vuelve a preguntar). La dueña responde **en el WhatsApp del negocio** (ya ve el chat por coexistencia) y reactiva el bot desde el panel.

**Pendiente:** (a) configurar `dueno_telefono`; (b) la **bandeja en el panel** (repo del dashboard); (c) **PRODUCTO + VARIANTES** (la estructura correcta: Kombucha = 1 producto con 350ml $4 / 700ml $7 — hoy son 2 productos con el MISMO nombre y el bot cobra el de $4 siempre); (d) el "código de barras" (`producto_id` en vez de nombre libre).

---

## 2026-07-12 — 🔴🔴 EL BUG DE VERDAD: el CÓDIGO cobraba el producto equivocado (y el prompt era INOCENTE)

**Resumen:** el bot **nunca se equivocó**. Mandaba el nombre EXACTO y CORRECTO (`"Empanadas"`). **El que elegía mal era el código**, en el camino del DINERO.

**La causa (bug objetivo, presente desde siempre):** `app/agent/tools.py` → `_buscar_producto` (lo usan `registrar_pedido`, `info_producto`, `enviar_fotos_producto` y la **edición manual del panel**) buscaba con `ilike('%nombre%')` + **`.first()` SIN `ORDER BY`**. Con el catálogo real hay **3 productos que empiezan con "Empanadas"** (Empanadas $14/8u · Empanadas Keto $12/4u · Empanadas Horneadas $14/4u), así que pedir `"Empanadas"` **calzaba con los 3** y Postgres devolvía **uno arbitrario**. Verificado en vivo con la MISMA consulta: **viejo → "Empanadas Keto" ($12)** · **netcup → "Empanadas" ($14)**. Mismo código, distinto resultado. **Lotería** — y podía voltearse sola al editar un producto en el panel. Bonus: `'pan'` calzaba por substring con em-**pan**-adas.

**⚠️ Y esto INVALIDA la conclusión de ayer (2026-07-11).** Aquel A/B ("recortar el prompt rompe el cobro") estaba **VICIADO**: se corrió el prompt limpio en el servidor VIEJO contra el original en NETCUP → lo que cambiaba era **el servidor**, no el prompt. **La limpieza del prompt era inocente.** La advertencia que se había escrito en `CLAUDE.md` quedó **corregida** (ver §8).

**El arreglo (`_buscar_producto`, aditivo, sin tocar el prompt):**
1. **Nombre EXACTO primero** (sin acentos/mayúsculas) → `"Empanadas"` jamás puede cobrar las Keto.
2. **Singular/plural exacto** (`_singular`) → `'empanada'` → **Empanadas**, nunca las Keto.
3. **El pedido contiene el nombre completo** → gana el MÁS específico ("quiero Empanadas Keto" → Keto).
4. **Prefijo de PALABRA**, no substring (reusa `_coincide_texto` del catálogo) → `'pan'` ya NO calza con em-pan-adas; `'empanadas de plátano'` → Empanadas (el plátano está en SU descripción) y NO las Keto (almendra).
5. **Ambiguo de verdad ⇒ NO adivinar**: `'pan'` calza con 3 panes de precios distintos → devuelve `None` y el agente **pregunta** (antes adivinaba **Pan Keto $25**, el más caro).
6. **Si no existe ⇒ rechazar, jamás aproximar**: `registrar_pedido` devuelve `productos_validos` (la lista real) y obliga al agente a usar el nombre exacto.
7. **`ORDER BY id`**: orden estable → **mismo resultado en cualquier servidor**.

**Verificado (9/9 en AMBOS servidores, contra la BD real):** Empanadas→$14/8u · Keto→$12/4u · Horneadas→$14/4u · 'empanada'→Empanadas · 'empanadas de platano'→Empanadas · 'Pan de Sandwich'→Pan de Sándwich · 'galetas'→Galletas New York · **'pan'→pregunta** · 'Torta de unicornio'→rechaza.

**Aprendizajes (Auto-Blindaje):**
- **NUNCA comparar un A/B entre servidores distintos.** Misma máquina, una sola variable.
- **Verificar el cobro en la BD** (`SELECT items, total FROM pedidos`), **no en la respuesta**: el bot *hablaba* de las de plátano y *cobraba* las Keto; el texto se veía perfecto.
- **Antes de culpar al modelo o al prompt, sospechar del código.** Aquí el modelo era inocente — y por eso **cambiar a un modelo más caro NO habría arreglado nada**.
- Probar sin tocar producción: `docker cp` del archivo + `docker exec -w /app python` (el proceso vivo sigue con el código viejo en memoria hasta reiniciar).

**Pendiente (blindaje definitivo, el "código de barras"):** que `registrar_pedido` reciba un **`producto_id` de una lista CERRADA** (enum con los ids reales del catálogo) en vez de un nombre en texto libre. Los modelos aciertan mucho más **eligiendo** de una lista que **escribiendo** un nombre.

**Nota de modelo (investigación aparte, 7 agentes):** el mejor costo-beneficio verificado hoy sería `openai/gpt-5.4-mini` (**más barato que Haiku 4.5** y mejor en tool use, caching automático). Ojo: **no acepta `temperature`** (OpenRouter la descarta en silencio) y **todos** los modelos baratos de 2026 son de razonamiento → hay que fijar `reasoning: minimal` o el costo/latencia se disparan. También falta mandar `provider.require_parameters: true` (si no, OpenRouter puede rutear a un proveedor que **ignore las herramientas** → el bot inventaría precios). **Nada de esto es urgente ahora**: el bug era del código.

---

## 2026-07-11 — ⚠️ CONCLUSIÓN ERRÓNEA (corregida el 2026-07-12): "recortar el prompt rompe el cobro" — NO era el prompt, era el CÓDIGO

> 🔴 **LEER LA ENTRADA DE ARRIBA (2026-07-12).** El A/B de esta entrada estaba **VICIADO** (prompt limpio en el servidor VIEJO vs. original en NETCUP → lo que cambiaba era el SERVIDOR). El bot registraba "Empanadas Keto" por un **bug de `_buscar_producto`**, no por la limpieza del prompt. La limpieza era **inocente**. Se conserva lo de abajo como historial del error.

**Qué se intentó (Paso 3 del plan):** la "versión senior" del prompt — quitar de la Personalidad las reglas que YA están blindadas en el código (`# PRECIO`, `# CATÁLOGO: cuándo mandarlo`, formato/viñetas, fotos, pasos del cobro, cliente-conocido), **manteniendo la voz de Whuilianny letra por letra**. Bajó de **11.648 → 9.338 chars**.

**Corrección clave de Maired (a mitad de camino):** una primera versión REESCRIBIÓ la voz y la bienvenida ("Soy Whuilianny, **bienvenido**… ¿qué te trae por aquí?"). Ella la rechazó fuerte: *"así NO habla", "yo ya te di la esencia y las imágenes de cómo habla"*. **Regla nueva: la voz + bienvenida + ejemplos son INTOCABLES** (ver memoria `esencia-whuilianny-no-reescribir`). Se rehízo conservando su texto verbatim; la bienvenida salió perfecta (*"Buenas noches 💚 ¿Cómo estás? Soy Whuilianny, de masvidaconsciente. ¿Deseas ver nuestro catálogo…?"*).

**🔴 PERO ROMPIÓ EL COBRO.** A *"quiero 2 paquetes de empanadas de plátano de carne mechada"* el bot **hablaba** de las de plátano pero **REGISTRABA "Empanadas Keto"** ($12/4u → total **$24**) en vez de **"Empanadas"** ($14/8u → **$28**). Verificado **en la BD** (`SELECT items, total FROM pedidos`), no en la respuesta. **A/B con 3 repeticiones por servidor:** prompt limpio → MAL 2/2; prompt original → BIEN 2/2.

**Conclusión (Auto-Blindaje):** con un modelo PEQUEÑO (Haiku) **la redundancia prompt↔código NO es grasa: SOSTIENE la selección de producto**. "Una sola fuente por tema" es buena teoría y mala práctica aquí. **El prompt largo se queda.** Documentado en **CLAUDE.md §8** con la advertencia y el protocolo obligatorio (quitar UNA cosa a la vez + probar el cobro contra la BD antes y después).

**Estado final: TODO REVERTIDO Y VERIFICADO.** Ambos servidores con el prompt original (**11.648 chars, md5 `07ba508a8968798f0e8936b429a9d026`**). Pedidos de prueba del simulador borrados en los dos. Respaldos: `/root/personalidad_backup_pre_limpia_20260711.txt` (viejo) y `/root/personalidad_backup_pre_senior_20260710.txt` (netcup).

**Hallazgos secundarios (útiles):**
- Las **negritas** (`**Pago Móvil:**`) que se ven al llamar al agente por dentro **NO llegan al cliente**: `_aplanar` (`app/workers/tasks.py:111`) borra asteriscos/viñetas y pasa `$18.00`→`$18` antes de enviar. **No es un defecto.** Ojo: probar con `responder()` a secas ENGAÑA — hay que pasar por `_aplanar` para ver lo que recibe el cliente.
- ⚠️ **Probar crea pedidos de prueba** (`telefono='__simulador__'`) que **SÍ aparecen en el panel y en los reportes** (solo la lista de *clientes* excluye al simulador). **Borrarlos siempre al terminar.**
- **Dónde prueba Maired:** en el servidor **VIEJO (Hostinger `2.25.139.106`)**, no en netcup.

---

## 2026-07-10 (noche) — 🧹 Limpieza del prompt (Personalidad): fuera la contradicción del precio + repeticiones

**Contexto:** Maired se sintió bloqueada al mirar el "cerebro" del bot. Mapeando el código real (`app/agent/system_prompt.py`, `tools.py`, `agent.py`) se confirmó que las instrucciones de comportamiento viven en **3 capas** —Personalidad editable (BD) + `_REGLAS` blindadas (código) + notas de las herramientas— y se **repiten** entre ellas y dentro de la propia Personalidad. Se le entregó un **mapa visual** (artifact) del solapamiento. Plan acordado de 4 pasos: (1) ordenar el panel → (2) handoff a lo humano → (3) versión senior del prompt → (4) contenido. Se hizo el **Paso 1**.

**Hallazgo clave:** el texto VIVO del panel había **divergido** del `BRIEF` local: tenía de vuelta la **contradicción del precio** (`# PRECIO` decía "no de frente", pero `# EL CAMINO` paso 2 y `# CATÁLOGO` decían "dile los productos **y su precio**") y le faltaba `# SIGUE EL HILO`. Por eso se leyó y limpió la **BD**, no el BRIEF.

**Lo aplicado (solo el texto `personalidad` en `configuracion`, SIN tocar código):**
1. **Arreglada la contradicción del precio** — decisión de Maired: **precio SOLO cuando lo pregunten o al comprar**; se unificó todo a eso.
2. **Quitadas 3 de las 4 repeticiones** de "no digas que el pago quedó confirmado" (queda 1).
3. **Quitado el bloque `# IMPORTANTE: NUNCA DE MEMORIA`** y los recordatorios de "no calcules el dinero" → ya blindados en `_REGLAS`; repetirlos inflaba el prompt y hacía que Haiku **copiara frases** (causa del "suena a robot").
4. **Recortadas las reglas técnicas del catálogo** (dejando solo el tono) y **deduplicada** `# QUÉ NO HACER`.
- **Intactos:** voz, trato, diabéticos, horarios, delivery, ejemplos y **datos de pago** (Pago Móvil/Transferencia/Zelle/Binance).

**Cómo (con red de seguridad):** SSH a netcup (`152.53.89.118`, donde corre el bot HOY — verificado por DNS de `api`/`panel`). **Respaldo** del texto anterior (local + `/root/personalidad_backup_20260710.txt`, 12.968 bytes). Aplicado por `docker exec -i <pg l2z8uksl…> psql` con **dollar-quoting** (`UPDATE 1`). **VIVO al instante** (`leer_personalidad` lee la BD cada turno; sin re-deploy).

**Verificación:** 12.623 → **11.648** chars; contradicción eliminada; "quedó confirmado" 4→1; "NUNCA DE MEMORIA" fuera; datos de pago + emojis intactos. **Prueba en vivo (3 mensajes reales, envíos bloqueados):** "¿tienes pan?" → nombra panes SIN precio ✓ · "¿cuánto cuesta el pan de sándwich?" → da $20 (de la herramienta) ✓ · "¿tienes empanadas de plátano?" → sigue el hilo, solo plátano + rellenos ✓.

**Sincronizado en AMBOS servidores (a pedido de Maired):** aplicado a netcup (VIVO, `152.53.89.118`, pg `l2z8ukslzip59w1nl3omhf1e`) y al viejo (respaldo, `2.25.139.106`, pg `zedzrztx4bntf5227wedzvt7`). **Descubrimiento clave: las BD NO se sincronizan solas entre servidores** (igual que el env) — por eso netcup traía la contradicción (el fix del 2026-07-03 fue al VIEJO) y el viejo NO la tenía (era otra versión, 13.967 chars). Además, tras el primer apply, netcup había **perdido 2 emojis 💚** (en la línea de avisar el catálogo) + el salto final (3 chars; probable guardado/paste en el panel); se repuso el texto INTENDED en ambos. Verificado **md5 idéntico** en los dos (`07ba508a8968798f0e8936b429a9d026`, 11.648 chars). Respaldos: `/root/personalidad_backup_20260710.txt` en cada servidor + local. (En un apply al nuevo hubo un "connection reset" de red — el UPDATE es atómico, no quedó a medias; se reintentó y entró.)

**Pendiente (acordado):** (2) **handoff a lo humano** (SÍ hacen **envío nacional** → el bot debe dejar de responder y notificar a la dueña; y para cualquier cosa que no sepa); (3) **versión senior del prompt desde cero** (menos "NUNCA"/más "haz así", más corto para Haiku, una sola fuente por tema, ejemplos con cuidado); (4) contenido. Base ya buena: blindaje en código + caché. Nota: cambio en la BD, NO se subió a GitHub (no es código).

---

## 2026-07-10 — 🚀 PRIMER CLIENTE montado en el servidor NUEVO (netcup) + fix del "folleto" + lista blanca de pruebas

**1. Fix del "folleto" (commit `e0b48cf`, bot `web`+`worker`).** Con Haiku, a *"las empanadas / dame más información"* el bot soltaba un **muro de texto**: nombraba los 3 tipos (Keto/Horneadas/plátano) + TODOS sus rellenos de golpe. **Diagnóstico (workflow multi-agente):** NO era desobediencia — el prompt (reglas 4-5 de `_catalogo_bloque` + la nota de `ver_catalogo`) le ORDENABA *"di de qué son, con sus rellenos"*, y como "empanadas" barre 3 familias, el modelo cumplía. **Fix:** la nota de `ver_catalogo` ahora es **dinámica por conteo** — si devuelve VARIOS productos, "nombra SOLO los tipos y retén el `de_que_es` hasta que el cliente elija cuál"; regla 5 reescrita (conservando la sub-regla de precio y el ancla anti-invención). De paso, `_aplanar` normaliza la **rayita larga `—` → coma** (nacía del separador del catálogo `• nombre — categoria`, que también se cambió a `(categoria)`). Anti-invención INTACTO. Verificado en vivo.

**2. Montaje del PRIMER CLIENTE en el servidor NUEVO (netcup `152.53.89.118`).** Cada cliente = su propia "caja" (fábrica). Lo montado:
- **Dominio de la clienta: `masvidaconsciente.store`** (Namecheap → Advanced DNS): A records `@`, `www`, `panel`, `api` → `152.53.89.118`. SSL automático por Coolify una vez propagado. (El dominio `.store` NO estaba muerto: es el de la clienta.)
- **Dashboard:** `NEXT_PUBLIC_API_URL` = `https://api.masvidaconsciente.store` (⚠️ es build-time en Next.js → hay que **Redeploy**, no basta guardar) + dominio `panel.masvidaconsciente.store`. **Bot:** dominio `api.masvidaconsciente.store`.
- **Meta — override por WABA:** WABA de la clienta = **`100526692613101`** (asset de WhatsApp Manager); phone_number_id = **`500909798292606`** (número +58 424-7047595). Override: `POST /100526692613101/subscribed_apps` con `override_callback_uri=https://api.masvidaconsciente.store/webhook/whatsapp` + `verify_token`. **El PATH del webhook es `/webhook/whatsapp`** (prefix `/webhook` + `@router.post("/whatsapp")` en `app/webhook/router.py`) — NO solo `/webhook` (usar `/webhook` da fallo de verificación).
- **Token: System User de Enova** (usuario **"Enova-api"**, id `61589674157552`) con `whatsapp_business_management`/`whatsapp_business_messaging` + la WABA masvidaconsciente asignada (control total). ⚠️ El token de USUARIO del Graph API Explorer **NO** sirve para gestionar la WABA de un cliente (da `error 100 / subcode 33` "does not exist / missing permissions") — hay que usar un **System User token**. El `META_VERIFY_TOKEN` del bot nuevo = `masvida-activo-2026`.

**3. Lista blanca de pruebas (commit `f3c947b`).** Nueva var **`NUMEROS_PERMITIDOS`** (`config.py` + helper `_numero_permitido` en `tasks.py`). Si NO está vacía, el bot **SOLO responde a esos números**; a los demás **guarda el mensaje en el panel pero NO responde** (mismo camino que "bot apagado"). Compara por la **cola de 10 dígitos** (tolera código de país). Puesto en los **3 caminos** (texto `_procesar`, voz/eventos `_responder_y_enviar`, comprobantes `_responder_situacion`). Para probar en producción sin contestarle a clientes reales (regla dura de Meta). Valor de prueba: `573005690062` (número de Maired). Para abrir a TODOS: dejar la var vacía + Redeploy.

**4. 🐛 BUG "cayó en otro" — encontrado y arreglado.** El bot tiene **DOS apps** en Coolify: **web** (`masvidaconsciente-bot`, recibe el webhook y encola) y **worker** (`masvidaconsciente-worker`, procesa y ENVÍA). Al montar el nuevo, se actualizó el env del **web** pero el **worker seguía con la config VIEJA de prueba** (`META_PHONE_NUMBER_ID=1116308758237612` = número viejo, token viejo, WABA `1761005704911145`, sin whitelist). → el worker generaba la respuesta de Whuilianny BIEN (logs: OpenRouter 200 OK) pero la **enviaba desde el número viejo** → caía en otro chat. **Fix (vía Coolify UI):** se corrigió el env del **worker** (phone `500909798292606`, token System User, WABA `100526692613101`, `NUMEROS_PERMITIDOS`) + **Redeploy del worker**. Verificado en logs + en vivo: responde desde el número correcto. **APRENDIZAJE CLAVE: el env NO se comparte entre apps — al cambiar Meta hay que tocar bot Y worker.**

**5. 🚀 AUTO-DESPLIEGUE — investigado a fondo, construido y PROBADO; falta 1 permiso que solo puede la dueña.** Meta: un push a `master` → los dos servidores se reconstruyen solos.
- **Descartado: webhooks GitHub→Coolify (manual).** Se crearon 6 y respondían HTTP 200, pero NO desplegaban: (a) el `manual_webhook_secret_github` está **CIFRADO** en la BD de Coolify (empieza `eyJp…`), así que el valor crudo no sirve como secreto → "Invalid signature"; (b) el handler solo reconoce apps cuyo `git_repository` canoniza bien: el **nuevo** (URL `git@github.com:…`) sí lo reconocía, el **viejo** (formato `owner/repo`) NO ("No applications found"). Frágil y dependiente de la versión de Coolify. **Los 6 webhooks se BORRARON.**
- **Elegido: GitHub Actions → API de Coolify** (robusto, uniforme y VISIBLE en la pestaña "Actions" del repo). Hecho y verificado en vivo: (a) **API de Coolify ENCENDIDA** en ambos (`instance_settings.is_api_enabled`, estaba en `f`); (b) **token de API creado** en cada Coolify vía `php artisan tinker` (⚠️ el del VIEJO debe ir en el equipo **2 "Enova"** —el viejo tiene 3 equipos, másvida está en el 2—, NO el 0 "Jhon ADS"; se fija con `session(["currentTeam"=>Team::find(2)])`); (c) **secretos** `COOLIFY_OLD_TOKEN`/`COOLIFY_NEW_TOKEN` guardados en los dos repos (bot y dashboard); (d) flujo `.github/workflows/deploy.yml` escrito (despliega bot+worker por uuid en ambos; uuids viejo bot=`qlfrx5yviileijm6lmovy67i` worker=`erzq5ycbrs323vwkcbam54a9` dash=`jvlqemh8s225qjftsev7ss8n`; nuevo bot=`y20mosanb19cw8ukso56hv7e` worker=`hrkrh8f9buora7aqxt8rsbna` dash=`o1jo590exxeuco5s8j0arisy`). **Deploy por API PROBADO**: encola en ambos servidores (`{"deployments":[…"queued"]}`).
- ✅ **RESUELTO Y FUNCIONANDO (2026-07-10).** La dueña autorizó el permiso `workflow` (`gh auth refresh -h github.com -s workflow`, device flow — ⚠️ ojo: había 3 cuentas gh; la que escribe el repo es `empresa-EnovaGroup-vibecoding`, esa es la que necesita el scope). Se subió `.github/workflows/deploy.yml`. **Gotcha final:** el dominio `coolify.enovagroup.tech` (fqdn del Coolify viejo) apunta a OTRO server (`152.53.194.89`) → daba **401**; se cambió a la **IP directa `http://2.25.139.106:8000`** (el nuevo ya usaba `http://152.53.89.118:8000`). Run del Action = **success**; verificado en el log: los 4 (bot+worker × viejo+nuevo) responden `deployment queued`. **Acceso:** llave SSH `~/.ssh/masvida_vps` = root en LOS DOS servidores. **Dashboard: HECHO también** — mismo `deploy.yml` en el repo del dashboard (uuids viejo `jvlqemh8s225qjftsev7ss8n`, nuevo `o1jo590exxeuco5s8j0arisy`). ⚠️ El Coolify VIEJO a veces tarda >90s en RESPONDER al deploy del panel si ya hay uno en curso (Next.js tarda ~1-2 min en reconstruir) → el Action marca "failure" AUNQUE el deploy SÍ ocurre; en uso normal (un solo push) responde en ~10s (probado). Ambos flujos llevan `--retry` para tropiezos de red. Además se **desconectó la cuenta gh `ChiclayoPropiedades`** (no se usa; quedan `empresa-EnovaGroup-vibecoding` activa + `enovagroup0oficial-web`).

**Estado:** primer cliente EN VIVO en el servidor nuevo, con la voz de Whuilianny, arreglo del folleto, y modo de prueba (lista blanca) activo. Modelo = Haiku (`anthropic/claude-haiku-4.5`).

**Pendientes:**
- ✅ **Auto-deploy HECHO Y PROBADO** (2026-07-10): push a master → GitHub Actions despliega bot+worker en AMBOS servidores (verificado, run "success"). Ver punto 5. El **dashboard/panel también** tiene su auto-deploy (ambos servidores). (El env/config sigue SIN sincronizarse: el auto-deploy mueve solo CÓDIGO.)
- 🔴 **Rotar** el System User token y el `META_APP_SECRET` (quedaron expuestos en el chat).
- ✅ **Limpieza de docs HECHA**: borrado `dns-newrow.yml`; carpeta `archivo/` (gitignored) con los PRP ya cumplidos + `MIGRACION.md`; informe entregado + **Tablero visual** creado.
- 🟡 Cuando termine de probar: quitar `NUMEROS_PERMITIDOS` (dejar vacío) + Redeploy para abrir a todos los clientes.

---

## 2026-07-03 (noche) — ✅ Filtrado por ingrediente DETERMINISTA (el bot ya NO ofrece lo que no calza)

**Problema (chat real):** a *"¿tienes empanada de plátano?"* el bot ofrecía también las **Empanadas Horneadas** (yuca/garbanzo), que NO son de plátano. Maired: *"él tiene que ser DIRECTO; si solo hay una empanada de plátano, dila y pregunta cuántas quiere; no metas las horneadas"*. Preguntó (aprendiendo agentes) si convenía **dos agentes** (orquestador + catálogo).

**Diagnóstico (espiando qué tools llama el agente):** el modelo (Haiku) hacía el **filtrado por ingrediente EN SU CABEZA** — respondía de memoria desde el catálogo inline y **lumpeaba** productos que comparten el nombre ("empanadas"). Y la búsqueda `ver_catalogo` existente **solo miraba el NOMBRE**, no los ingredientes ("plátano" no está en el nombre "Empanadas"). O sea: la decisión de "cuáles calzan" la tomaba el modelo (mal) o una herramienta ciega a los ingredientes.

**La lógica de raíz (respuesta a lo de "dos agentes"): NO son dos agentes.** La regla es **"el CÓDIGO elige, el agente redacta"** (RAG/grounding): recuperar lo correcto de forma determinista y solo entonces redactar. Dos agentes = más costo/latencia/piezas que se rompen, sin arreglar la causa (que es dejarle al modelo una decisión que es del código).

**Fix (commit `a33512c`, bot `web`+`worker`):**
1. **`tools.py` — `ver_catalogo` filtra por NOMBRE + INGREDIENTES** (la descripción), con AND de cada palabra significativa por **prefijo de palabra** y **sin acentos** (`_coincide_texto`). "empanada plátano" → SOLO las que de verdad son de plátano; "pan" no calza con em-**pan**-adas; la categoría NO entra (evita que 'pan' calce con 'panadería'). Devuelve `de_que_es` + precio/unidades (marcados internos).
2. **`system_prompt.py` — catálogo inline COMPACTO:** solo **nombres** (ancla para no inventar productos) + la línea `[SOLO PARA TI]` con precio/unidades/detalles. **Los INGREDIENTES ya NO van inline** → el modelo NO puede lumpear de memoria: TIENE que usar `ver_catalogo` (determinista) para filtrar/describir. Regla #4 reforzada; regla que da el precio cuando SÍ lo piden.

**⚠️ Lo que se cuidó — ANTI-INVENCIÓN (la regla #1, la más sagrada): INTACTA.** Verificado: "¿las galletas llevan huevo?" → llama tools y da el dato REAL; "¿se congelan?" (dato no cargado) → *"lo verifico y te confirmo"*, **no inventa**. Es la misma lógica del catálogo grande (400 productos) → además **escala**.

**Verificación (exhaustiva):** ~48 casos en TODAS las familias (empanadas, panes, tortillas, tortas, wafles, kombucha, tequeños, galletas, garbanzo, almendra, coco, merey…) + negaciones/multi-pedido + **verificación adversarial con 22 jueces independientes** contra el catálogo real. Resultado: **0 over-offer de producto equivocado, 0 invención real, 0 blurt de precio.** (Los jueces marcaron 5, pero al revisar: 3 eran falsos positivos del juez —su resumen del catálogo no traía "desalmidonada"/"activada"/"búfala", que SÍ están en las fichas reales— y 2 eran estilo menor: ofrecer la otra variante/sabor del MISMO producto pedido, no un producto ajeno.)

**Seguimiento (mismo día) — SIGUE EL HILO (closer):** Maired insistió (con razón) en que a *"empanadas de plátano"* el bot NO debe contestar *"de plátano y yuca"* — la clienta ya eligió plátano; hay que seguir ESE hilo. Matiz que ella pidió: ofrecer la otra variante (yuca) está bien, pero **DESPUÉS y aparte**, no mezclada en la misma respuesta (como un closer). **Fix:** regla de variantes en `_REGLAS` reforzada ("SIGUE EL HILO") + nota de `ver_catalogo` + sección nueva **`# SIGUE EL HILO DEL CLIENTE`** en la Personalidad (BD + BRIEF). Verificado **multi-turno**: el flujo del screenshot queda limpio (plátano → relleno → precio $14/8u → cierra). Commit `5a0f02e`.

**Seguimiento 2 — NO confundir temas parecidos (envío nacional ≠ entrega local):** Maired notó (con razón) que a *"¿hacen envíos nacionales?"* el bot respondía con la ENTREGA LOCAL (La Mendera/delivery) como si contestara. Causa: `buscar_info` hizo match difuso con la única entrada de "envíos" (que es local). **Fix** (`393c3db`): regla de `buscar_info` en `_REGLAS` + nota de la tool → responde SOLO si de verdad contesta; si es un tema RELACIONADO pero DISTINTO, lo dice y ofrece confirmar ("de envíos nacionales déjame confirmarte"). Verificado: distingue nacional/local SIN volverse miedoso (delivery local sigue directo). **Ojo de contenido:** en el Conocimiento SOLO hay entrega local; NO hay política de envíos nacionales cargada → Maired debe decidir (¿hacen nacional?) y cargarla, o dejar claro "solo local". Es su llamada (yo no invento la política). Relevante para su ansiedad de "que no invente": esto es el bot siendo MÁS preciso.

**Seguimiento 3 — comprobante no reconocido: mensaje humano y honesto (`34ec66d`):** Maired notó que al mandar comprobantes el bot repetía VERBATIM "no veo bien el comprobante, mándame captura con monto/referencia" (parecía plantilla/robot), y decía "no veo bien" aunque la imagen se veía clarísima. Causa real: los comprobantes de prueba eran a OTRAS cuentas (SOLUTIONS SUCRE, y un BANCAMIGA a nombre de Maired PERO cta 04121883675/V28468877 ≠ su Banesco registrado 04247047595/V-21367558) → `_beneficiario_coincide` False → es_comprobante False → rama "no reconocido". **Fix (solo el MENSAJE, NO el candado/registro):** en `tasks.py` `_procesar_comprobante` se separa el caso con `es_pantalla_bancaria`: (a) SÍ es pantalla de pago pero NO a su cuenta → honesto "ese pago no me aparece a mi cuenta, verifica que lo enviaste a mi Pago Móvil y reenvía"; (b) no es comprobante → "no veo el comprobante, mándame captura clara". Ambas piden redactar con palabras propias y DISTINTAS cada vez (no plantilla). Verificado: redactar_mensaje da 3 respuestas distintas y humanas. **Acción de Maired:** si esa cta BANCAMIGA es suya y quiere que le paguen ahí, agregarla en Métodos de pago (panel) para que el bot la reconozca; si eran solo pruebas, ya responde bien. **Nota:** el reconocimiento sigue ESTRICTO (2026-06-24: solo pagos a sus cuentas registradas) — eso es a propósito, protege el cobro.

**Seguimiento 4 — MENOS plantilla + fotos para cerrar (`b8b2212`, `bb7cbc8`):** Maired (punto de experta) notó que el bot repetía frases porque yo le metí demasiadas FRASES-EJEMPLO en las reglas/situaciones y Haiku las copia. Fix: 1ª regla "TUS PALABRAS, NO PLANTILLAS" + se quitaron las frases-ejemplo literales y el "enseguidita" repetido. **Se probó la temperatura como palanca de variación: NO sirve** (0.15→0.4/0.5 da poca variación —Haiku converge— y falla el precio a veces; se dejó 0.15). La variación real = quitar ejemplos, no subir temp. Si quiere MUCHA más humanidad → modelo más grande (cuesta más), su decisión. Ver [[no-sobreguionar-conversacion-bot]]. **Fotos como arma de cierre:** 24/29 productos tienen media; se amplió el disparador de `enviar_fotos_producto` (ahora también ante "cómo se ve / qué tan grande" o cuando el cliente DUDA) + pitch con gancho real + anti-invención si no se puede enviar. Ver [[plan-media-productos]]. **PENDIENTE de Maired:** dar TAMAÑO real + GANCHOS de los productos estrella (para que el pitch venda con la verdad, no improvise) — cargar en el campo `info`/Conocimiento. **Conocido:** "¿cuánto cuesta el pan de sándwich?" a veces da unidades/duración en vez del precio (selección de campos de info_producto, no temp).

**Seguimiento 5 — Logo +VIDA CONSCIENTE en el panel + se destapó el bug del deploy de Coolify:** Maired pidió el logo del negocio en todo el panel (nivel pro). Hecho en el repo **dashboard** (commit `1a151a2`): logo real en barra lateral + header móvil + login (reemplazando el ícono de hoja SVG) + **favicon** (`src/app/icon.jpg`, convención Next.js). Logo en `public/logo.jpg` (venía de `OneDrive\Escritorio\logo.jpg.jpeg`). Build OK, **desplegado y verificado en vivo** (`/logo.jpg` y `/icon.jpg` → 200; el login referencia el logo; `<link rel=icon>` presente). **DESCUBRIMIENTO GORDO:** el "la API de Coolify da HTTP 000/400" que me frenó TODO el día era **un bug mío** — `psql ... returning id` devuelve el id + el tag `INSERT 0 1`, que se colaba en el id → token malformado → 000/400. **La API de Coolify SIEMPRE funcionó.** El dashboard (Next.js standalone) SÍ necesita rebuild por la API (no basta docker cp). Método corregido en [[deploy-viejo-docker-cp-restart]]. El panel corre en el contenedor `jvlqemh8s225qjftsev7ss8n` (app id 3).

**Detalles menores conocidos (no rompen nada):** las Tortillas se llaman literalmente "Tortillas de Plátano o Yuca", así que al nombrarlas menciona ambas masas (artefacto del nombre del producto); under-offer ocasional (ej. "algo de merey" solo la harina). Ninguno es over-offer ni invención.

---

## 2026-07-03 (tarde) — ✅ El bot ya CONVERSA como vendedora (no suelta precio/unidades de golpe)

> Resuelve el 🔴 pendiente de la entrada de abajo (2026-07-03 mañana).

**Qué pedía Maired:** que al pedir *"información de X"* el bot responda cálido y BREVE (qué es + rellenos), **pregunte relleno/cantidad primero**, y dé precio/unidades SOLO cuando el cliente los pida o vaya a comprar; y que ofrezca **solo** productos que de verdad tengan el ingrediente pedido.

**Raíz (mapeando el código + probando en vivo con `/api/probar`):** eran DOS causas, no una.
1. **Contradicción en la Personalidad:** la sección `# PRECIO` decía "no des el precio de frente", PERO "EL CAMINO HACIA EL CIERRE" y "CATÁLOGO" decían responder a una pregunta puntual *"con su precio"*. Haiku 4.5 seguía la segunda.
2. **El precio y las unidades vivían en la CABECERA de cada ficha** del catálogo inyectado (`_catalogo_bloque`): `• Empanadas — $14 — 8 unidades — …`. El modelo los trataba como parte de "describir" el producto y los recitaba (a veces con markdown/folleto). La regla anti-blurt existente solo cubría "¿tienen X?", no "info de X".

**Fix — dos palancas (justo las que sugería la entrada de abajo):**
- **Personalidad (BD, donde vive el comportamiento):** 3 reemplazos QUIRÚRGICOS que quitan la contradicción (respaldo en `/tmp/personalidad_backup.txt` del contenedor + `BRIEF-personalidad-whuilianny.md` local sincronizado). NO se tocaron datos de pago ni la voz. Es cambio VIVO al instante (se lee de la BD cada turno, sin deploy).
- **Código `_catalogo_bloque` (commit `51e99ce`):** nuevo formato de ficha. VISIBLE = nombre + categoría + "de qué es" (ingredientes/rellenos — lo necesita para describir y para filtrar por ingrediente). Precio, unidades y detalles (duración, congela, apto, alérgenos) pasan a una línea **`[SOLO PARA TI, NO lo digas salvo que lo pregunten]`** = referencia INTERNA: el bot los CONOCE (no inventa, responde al instante cuando se los piden) pero NO los suelta solo. Regla #5 reescrita para apuntar a esa etiqueta + "nada de folleto ni negritas".

**No rompe el DINERO:** precios/subtotales/total siguen saliendo de las herramientas; solo cambió CUÁNDO se revelan. **Regla dura respetada:** el comportamiento va en la Personalidad; el ajuste de código es la "regla corta en `_catalogo_bloque`" que la bitácora ya autorizaba, atada al dato del catálogo (no se regó comportamiento suelto por el código).

**✅ Verificado en vivo con `/api/probar` (HTTP, código ya desplegado en web+worker):**
- "info de las empanadas de plátano" → describe qué es + rellenos y pregunta, SIN precio/unidades.
- "info de las galletas" → describe + sabores, sin markdown ni precio (ojo: a veces menciona las unidades UNA vez — variación de Haiku, menor).
- "¿cuánto cuestan?" → da $14 + 8 unidades. "¿cuántas trae el pan?" → "18 rebanadas". (precio/unidades cuando SÍ los piden.)
- "quiero 2 paquetes" → conoce el paquete (8 c/u) y avanza al cierre.
- "algo de plátano" → solo productos con plátano, sin falsos positivos.
- Anti-invención OK (galletas "¿se congelan?" → "lo verifico", porque su ficha no trae ese dato).

**⚠️ Deploy — cómo se hizo ESTA vez (importante):** la API de Coolify del viejo NO sirvió para desplegar: `/api/health` da 200 pero `/api/v1/*` devuelve **HTTP 000** (conexión reseteada — probable allowlist de IPs de la API; revisar). Se desplegó por la vía determinista: `docker cp` del archivo commiteado a **web y worker** + `docker restart` de ambos. Es DURABLE (persiste ante restart/reboot; el código está en git, así que un rebuild futuro trae lo mismo). Worker Celery arrancó limpio (`ready`), web sirviendo 200. **Bot uuid `qlfrx5yviileijm6lmovy67i`, worker `erzq5ycbrs323vwkcbam54a9`.**

**Sigue pendiente (aparte):** el dominio `masvidaconsciente.store` (Namecheap bloqueado) — ver entrada de abajo. Maired lo desbloquea desde su dispositivo.

---

## 2026-07-03 — 🤖 El bot ya LEE la ficha completa; PENDIENTE: que sea conversacional (no suelte precio/unidades) + arreglar el dominio

> **👉 SI RETOMAS ESTO EN UNA SESIÓN NUEVA, empieza por aquí.**

**⚠️ Dónde corre TODO ahora mismo:** el bot corre en el **servidor VIEJO** (Hostinger `2.25.139.106`), **NO en netcup**. Se **revirtió** al viejo porque el **dominio nuevo `masvidaconsciente.store` se cayó**: la cuenta de **Namecheap se bloqueó** ("actividad inusual", disparada por logins automatizados) y el DNS dejó de resolver (NXDOMAIN). **Maired debe desbloquear Namecheap desde SU propio dispositivo** + verificar el correo del dominio. **NO automatizar Namecheap.** Mientras tanto: bot + webhook de WhatsApp en el viejo (funciona), panel en `panel-masvida.enovagroup.tech`. El viejo está sano e intacto. (La memoria `infra-actual-masvida-netcup` decía "100% en netcup"; quedó corregida a este estado.)

**✅ Lo que se logró (la RAÍZ de "el bot no lee la info"):** en `app/agent/system_prompt.py` → `_catalogo_bloque()`, el menú del catálogo ahora incluye la **ficha COMPLETA** de cada producto (descripción/ingredientes, duración, se_congela, apto_diabeticos, info), no solo nombre+precio. Verificado en vivo con `/api/probar`: el bot ya lee ingredientes exactos (Keto = almendras/psyllium…), si se congela y apto diabéticos, y **ya NO inventa** (lo de "aptas para diabéticos" en las Empanadas es dato REAL de la ficha, `apto_diabeticos='si'`, no invento del bot). Commits `05f1e6a`, `6e43153`, desplegados al viejo (web+worker).

**🔴 Lo que FALTA pulir (pedido claro de Maired, aún NO resuelto):** el bot **todavía suelta el PRECIO y las UNIDADES de golpe** y recita toda la ficha cuando el cliente pide *"dame información sobre X"* (verificado: a "info sobre las empanadas de plátano" respondió con "$14, 8 unidades" + todo). Ella quiere **comportamiento de vendedora humana**: responder cálido y BREVE (qué es + rellenos), **preguntar relleno/cantidad primero**, y dar **precio/unidades SOLO cuando el cliente pregunte o vaya a comprar**. Además: ofrecer **solo** los productos que de verdad tienen el ingrediente pedido (no meter las Horneadas —yuca/garbanzo— cuando piden "de plátano"). ⚠️ **Regla dura al arreglarlo:** solo el **dinero** va blindado en código (`_REGLAS`); el **estilo/comportamiento** va en la **Personalidad (panel)** — no regar comportamiento por el código. La palanca probable: reforzar la **Personalidad** + una regla corta en `_catalogo_bloque` tipo "no sueltes precio/unidades sin que te los pidan; primero conversa".

**🧪 Cómo PROBAR sin usar WhatsApp:** `POST https://api-masvida.enovagroup.tech/api/login` con `{email:"admin@masvidaconsciente.com", password:<env ADMIN_PASSWORD del contenedor del bot>}` → token; luego `POST /api/probar` con `{mensaje:"..."}` y `Authorization: Bearer <token>` → devuelve la respuesta del bot SIN mandar nada por WhatsApp.

**🚀 Cómo DESPLEGAR al viejo:** la API de Coolify del viejo está **deshabilitada** (seguridad). Para desplegar: SSH al viejo (`/c/Users/herid/.ssh/masvida_vps`) → `docker exec coolify-db psql -U coolify -d coolify` → `update instance_settings set is_api_enabled=true;` + crear token en `personal_access_tokens` (team_id=2) → `curl -k -X POST "https://localhost/api/v1/deploy?uuid=<bot>,<worker>"` con `Host: coolify.enovagroup.tech` y `Authorization: Bearer <token>` → al terminar, volver a `is_api_enabled=false` y borrar el token. **Bot uuid `qlfrx5yviileijm6lmovy67i`, worker `erzq5ycbrs323vwkcbam54a9`.** (El `coolify.enovagroup.tech` público apunta a OTRO server sin acceso; por eso se usa la API local con Host header.)

---

## 2026-07-02 (tarde) — 🚀 MIGRACIÓN COMPLETA: de Hostinger a netcup + dominio propio

**Resultado:** másvida corre **100% en el servidor NUEVO** (netcup `152.53.89.118`, hostname `v2202607375079477495`) con **dominio propio** `masvidaconsciente.store`. WhatsApp verificado y funcionando de punta a punta (mensaje real procesado por el bot nuevo → OpenRouter → respuesta → guardado en la BD nueva). El servidor **viejo** (Hostinger `2.25.139.106`) queda **de RESPALDO, intacto — NO borrar** hasta tener días de estabilidad.

**Nuevas URLs (todas con https/Let's Encrypt automático):**
- Bot / webhook: `https://api.masvidaconsciente.store` (webhook Meta: `/webhook/whatsapp`)
- Panel de la dueña: `https://panel.masvidaconsciente.store`
- Coolify infra nuevo: `http://152.53.89.118:8000` (admin `masvidaconsciente1@gmail.com`)

**Cómo se hizo (SSH + API Coolify + Playwright + Graph API):**
1. **Respaldo doble** de la BD (pg_dump, en viejo + local).
2. En el Coolify nuevo (ya tenía proyecto "masvida" + deploy keys): se crearon vía **API** PostgreSQL **16.14** + Redis **7.2** (mismas imágenes) y las **3 apps** (bot `/Dockerfile` :8000, worker `/Dockerfile.worker`, dashboard repo dashboard) jalando los repos privados con las deploy keys.
3. **Datos restaurados** (11 tablas) y **env vars migradas** (decrypt del Coolify viejo → set en el nuevo; se **deduplicaron** `ADMIN_PASSWORD`/`JWT_SECRET`, y se **reapuntaron** `DATABASE_URL`/`REDIS_URL` a las bases nuevas por su UUID interno). Ojo gotcha: el endpoint `envs/bulk` **duplica** cada var → hay que deduplicar por SQL (ROW_NUMBER por key).
4. **DNS** en Namecheap (Playwright): registros A `api` y `panel` → 152.53.89.118. Dominios asignados en Coolify → https automático.
5. **Verificado idéntico** viejo vs nuevo (44 mensajes, 3 clientes, 29 productos, 19 config, 36 media…) → sin resync necesario.
6. **Palanca WhatsApp**: se cambió el webhook por la **Graph API** (`POST /{WABA}/subscribed_apps` con `override_callback_uri` + `verify_token`) — override **POR CLIENTE**, así que **solo másvida** cambió, los otros clientes de la app "Enova API" quedaron intactos. Meta respondió `{"success":true}` y su verificación llegó al bot nuevo (200 OK). **NO se tocó Facebook ni el webhook a nivel de app** (evita riesgo Tech Provider).

**Reversible:** volver a apuntar el `override_callback_uri` al viejo restaura todo al instante (el viejo sigue vivo).

**Pendientes de la migración:** (1) Maired debe **cambiar** las contraseñas que pegó en el chat (Namecheap, Hostinger) y las que se fijaron (root del viejo). (2) Darle acceso al Coolify nuevo si lo quiere (resetear clave de `masvidaconsciente1`). (3) Decomisionar el viejo (Hostinger) cuando haya confianza (semanas), preservando respaldos. (4) Opcional: ponerle una landing al dominio raíz.

---

## 2026-07-02 — Arreglo de unidades DESPLEGADO en producción + rescate de acceso a Coolify

**Resultado:** el arreglo de las unidades (commit `8c4d0ce`) ya está **EN PRODUCCIÓN y verificado en vivo**. Bot **web** y **worker** redeployados desde `master`; ambos contenedores nuevos corriendo, con el código nuevo confirmado dentro (`_catalogo_bloque` con "cuántas unidades trae"), worker Celery arrancó limpio, `api-masvida.enovagroup.tech` responde 200. Base de datos del bot, Redis y dashboard **intactos** (no se tocaron).

**Cómo se hizo (con navegador Playwright + SSH):** se entró a Hostinger, se dio acceso SSH a la IA en el VPS viejo (`2.25.139.106`, donde vive el bot) y se desplegó por la **API de Coolify** (habilitada temporalmente y **vuelta a desactivar** al terminar; token temporal borrado).

**Bug de infra encontrado y arreglado (causa por la que Coolify no podía desplegar):** Coolify no podía entrar por SSH a su propio servidor → *"Server is not functional / Permission denied"* → por eso también mostraba todos los contenedores como "exited:unhealthy" (estado falso; los reales estaban *Up*). Causa raíz: en `/root/.ssh/authorized_keys` la **llave RSA del servidor-1 de Coolify quedó corrupta** — al plantar la llave de la IA la sesión previa, el archivo no terminaba en salto de línea y la llave nueva se **concatenó dentro** de la de Coolify, invalidándola. Fix: se reconstruyó `authorized_keys` limpio (llave localhost + llave server-1 de Coolify + llave IA), con **respaldo previo** (`authorized_keys.bak.*`) y validación (`ssh-keygen -l` = 3 llaves OK). Verificado: Coolify ya hace SSH a su server (`COOLIFY_SSH_OK`). **Aprendizaje:** al hacer `echo key >> authorized_keys`, asegurar SIEMPRE que el archivo termine en `\n` antes (o usar un método que lo garantice), o se corrompe la última llave.

**Descubrimiento importante (migración a medias):** `coolify.enovagroup.tech` ya **NO** apunta al VPS viejo — resuelve a **`152.53.194.89`** (otro servidor, seguramente el Coolify NUEVO de la migración). Pero `api-masvida` y `panel-masvida` siguen en el VPS **viejo** (`2.25.139.106`), donde corren el bot/worker/dashboard/BD. Por eso el login de Maired a `coolify.enovagroup.tech` fallaba: el navegador entraba al Coolify **nuevo** mientras las apps y sus datos viven en el **viejo**. **Pendiente:** decidir el plan de migración y arreglar el acceso de Maired al Coolify que de verdad usa (el nuevo, `152.53.194.89`) — requiere acceso a ese servidor. La clave root del VPS viejo se cambió a un valor conocido (entregado a Maired por chat, NO se guarda aquí).

---

## 2026-07-01 — El bot ahora SABE cuántas unidades trae cada producto

**Problema (visto en un chat real):** el cliente pidió "empanadas" y el bot preguntó "¿cuántas quieres?" sin decir cuántas trae el paquete (el cliente terminó preguntando "¿cuántas trae el paquete?"). Causa hallada **mapeando el código real** (workflow de lectura): el menú que se inyecta SIEMPRE en el system prompt (`_catalogo_bloque` en `app/agent/system_prompt.py`) solo llevaba **nombre + precio + categoría** — NO la `presentacion` (el campo de texto libre donde viven las unidades, ej. "8 unidades"). El bot no las conocía en su "menú de cabeza" sin llamar una herramienta, y nada lo empujaba a hacerlo.

**Fix (bot, `_catalogo_bloque`):** cada línea del catálogo permanente ahora incluye la presentación → `- Empanadas ($14, 8 unidades) — Congelados`. Y una nota corta en el encabezado del bloque: puede decirle al cliente cuántas unidades trae "cuando venga al caso" (autónomo, **NO guionado** — respeta la decisión anti-sobreguión). **No toca el cálculo del dinero** (precios/subtotales/total siguen saliendo SIEMPRE de las herramientas). Cambio **aditivo**, en la parte blindada (código, no editable desde el panel). `compileall` OK.

**Deploy:** ✅ HECHO el 2026-07-02 (bot **web + worker**, verificado en vivo — ver entrada de esa fecha).

**Diferido (acordado con Maired — ir de a uno, sin abrumar):** (B) regla "nombre exacto manda"; (C) desambiguar en `_buscar_producto` cuando el cliente escribe corto ("empanadas" → hoy agarra una de las 3 al azar con `.first()` sin `ORDER BY`; falta priorizar el match exacto y, si de verdad hay varias, preguntar cuál); (D) afinar la voz para seguir el hilo de la venta. Fase 2 opcional: campo de **sinónimos/alias** por producto ("salteñas", "de plátano"). Nota: el seed `002_seed_catalogo.sql` está desactualizado — la verdad son los datos que la dueña editó en el panel.

---

## 2026-06-24 (tarde) — Voz: puerta de saludo + decisión anti-sobreguión · Prompt caching · Editar cliente/pedidos

**1) Voz / saludo (bot `7e54049` + `5fad1fe`):** red de seguridad EN CÓDIGO (`_asegurar_saludo` en agent.py) que, SOLO al inicio de la conversación, garantiza que si el cliente saluda y/o pregunta "¿cómo estás?" el bot devuelva el saludo + "Muy bien, gracias a Dios" (con nombre + franja horaria VE). Es la "puerta/gate" determinista que mencionaba su amigo — sin agente extra ni costo.
- **Decisión clave (de Maired):** NO sobre-guionar la conversación. La puerta queda como **respaldo invisible** (solo actúa si el modelo falla); con un buen modelo no se activa → el bot responde natural y autónomo. Lo único que se BLINDA en código es lo crítico (**dinero, no inventar**). La conversación = libertad del modelo + la personalidad como guía. Maired cambió a un buen modelo y respondió natural → el problema era el MODELO, no faltar reglas. Ver memoria `no-sobreguionar-conversacion-bot`.

**2) Prompt caching (bot `0a640c0`):** `construir_partes_prompt` separa el prompt en ESTABLE (personalidad+reglas+catálogo+índice conocimiento) y DINÁMICO (hora, estado, ficha). El bloque estable se marca `cache_control: ephemeral` → OpenRouter lo cobra a **¼** en los mensajes siguientes. **Misma calidad (mismo texto al modelo), ~mitad de costo.** Aplicado en `agent.responder` y `redactar_mensaje`. Modelo activo: **Haiku 4.5** (~$10–25/mes a volumen real CON caché; $1/M entrada, $5/M salida). `construir_system_prompt` queda como wrapper de compatibilidad.

**3) Editar/borrar cliente + editar items de pedido (bot `d266f00` + dashboard `532b3fc`):**
- `PUT /clientes/{tel}` (editar nombre/notas) · `DELETE /clientes/{tel}` (resetea cliente: ficha + pedidos sin cobro + mensajes + memoria Redis). UI Clientes: nombre editable + "Guardar cambios" + botón "Borrar cliente" (con confirmación y aviso del blindaje).
- `PUT /pedidos/{id}/items` (corrige items/cantidades; recalcula el total desde el catálogo con `_buscar_producto`, **nunca inventa**). UI Pedidos: botón "Editar" → editor con selector del catálogo + cantidad + agregar/quitar + total estimado en vivo.
- **BLINDAJE de cobro (igual que borrar pedido):** NO se borra un cliente ni se editan items si hay pago confirmado/parcial/reportado. El dinero nunca se borra/altera en silencio.
- compileall (bot) + `tsc --noEmit` (dashboard) OK.

**4) Decisión "la dueña manda" (bot `faed388`→`a70321e` + dashboard `e2e6375`→`f20bd49`):** primero hice que los botones Borrar/Editar se **deshabilitaran con candado** si el pedido/cliente tenía pago (flags `pago_bloqueante`/`puede_borrar` desde el API). Maired lo rechazó ("muy rígido, candado por todos lados, no le veo la razón"). **Decisión final: SIN candados** — borrar/editar pedido y borrar cliente **siempre disponibles**; antes de tocar plata, el `confirm` muestra la consecuencia ("sale de tus reportes" / "el monto puede no cuadrar") y ella decide. Se quitaron los 409 de `borrar_pedido`/`borrar_cliente`/`editar_items_pedido`; las flags quedan solo para el texto del aviso. Las reglas del BOT con el cobro NO cambian. Ver memoria `panel-la-duena-manda-sin-candados`.

**Deploy pendiente:** bot **web** (API nueva) + **dashboard** (Coolify). El saludo + caché necesitan **web + worker**.

---

## 2026-06-24 — Ficha por producto, fix de modal, selector de modelos + antiinvención

- **Selector de modelos ampliado (panel `f498a53`):** DeepSeek V3.2, Gemini 2.5 Flash Lite + opción "Personalizado" (pegar cualquier ID de OpenRouter). OpenRouter SÍ tiene embeddings (se usó en Fase 2). Investigado: Gemini subió de precio (3 Flash ~$0,50/$3), DeepSeek bajó (V3.2 ~$0,14/$0,28).
- **Ficha por producto (bot `3cb904f` + panel `64b0ca5`):** `productos` += duracion, se_congela, apto_diabeticos, info (migración 013). Modal "Información para el bot" (3 casillas + texto). `info_producto` devuelve la ficha; regla: detalle de un producto sale de SU ficha, no se generaliza.
- **Fix modal catálogo (panel `23494d7`):** el modal crecía y se salía de pantalla (overflow, Guardar inalcanzable). Ahora `max-h-[90vh]` + cuerpo con scroll + footer fijo. **Verificado con Playwright en PC (1280) y móvil (390)** vía página temporal `/preview` (ya borrada).
- ⚠️ **Hallazgo: el modelo importa para "no inventar".** Probando con **DeepSeek (razonamiento)**: con la ficha de Galletas VACÍA, el bot INVENTÓ "duran 5 días en nevera / 3 meses congeladas / envase hermético". Se **reforzó la regla ANTIINVENCIÓN** (1ª regla blindada, muy explícita: prohibido inventar duración/conservación/etc.; si la ficha no lo trae → "lo confirmo con la dueña"). Honesto: con modelos baratos de razonamiento la obediencia es menor; si sigue inventando, usar **Gemini 2.5 Flash** (barato + obediente). Pendiente: reprobar con la regla reforzada.
- **Media por producto (fotos/videos) — CONSTRUIDO sobre Cloudflare R2 (estándar S3).** La dueña activó R2 (bucket `masvida-media` + Public Dev URL `pub-5bcf…r2.dev`; las 5 variables `R2_*` en Coolify). Bot: `services/r2.py` (boto3, subir/borrar, fail-safe), tabla `producto_media` (migración 014 — guarda SOLO la ruta/clave; cambiar el dominio público luego = cambiar `R2_PUBLIC_URL`, cero migración), endpoints subir/listar/borrar media, `meta_client.enviar_imagen/enviar_video`, herramienta `enviar_fotos_producto` (manda la media de un producto cuando el cliente la pide; nunca afirma un envío que no hizo). Panel: sección "Fotos y videos" en Editar producto (subir múltiple + galería + borrar). Límites WhatsApp: foto 5 MB, video MP4 16 MB. **Deploy: web + worker + dashboard** (+ las 5 var R2 ya en Coolify). compileall + tsc OK.
- ✅ **VERIFICADO EN VIVO (2026-06-24):** el bot **envió la foto del quesillo** por WhatsApp (panel → R2 → Meta). Commits bot `d7c3776`/`eaaf2eb`/`01d3ea7`, panel `0b3987e`.
- 🔧 **Lecciones (auto-blindaje), MUY útiles para próximas sesiones:**
  1. En Coolify **el web y el worker son apps SEPARADAS** (IDs distintos): cada una necesita **su propio Redeploy** y **sus propias variables de entorno**. Agregar R2 solo al web no basta — el worker también.
  2. **"Redeploy" reconstruye con el código nuevo; "Restart" NO** (reinicia el viejo). Para cambios de código: siempre **Redeploy**, y verificar en la pestaña **Deployments** que el build corre (1-2 min, no instantáneo).
  3. **Historial contaminado:** si el bot repitió "no tengo X" varias veces, el modelo se **ancla en su propio historial** y deja de llamar la herramienta. Solución: **borrar esa conversación** (resetea la memoria) — o blindar con red de seguridad en código.
  4. **Diagnóstico por turno:** `agent.responder` loguea `responder: modelo=… tools=N fotos_tool=…` al procesar cada mensaje → confirma de un vistazo qué código/modelo corre. (Los logs de arranque de Celery se pierden; no sirven para esto.)
- 🔴 **SEGURIDAD (rotar):** durante el setup se expusieron en el chat varias claves de producción (META_ACCESS_TOKEN, META_APP_SECRET, OPENROUTER_API_KEY, JWT_SECRET, ADMIN_PASSWORD, y las llaves R2 + DATABASE/REDIS internos). **Rotar las críticas** (Meta, OpenRouter, JWT, ADMIN, R2) antes de lanzar con clientes reales. De ahora en adelante: secretos SOLO en Coolify, nunca en el chat.

---

## 2026-06-23 — Descuento 20% en divisas + Búsqueda escalable (Fase 1: pg_trgm)

**1) Descuento 20% por pagar en DIVISAS** (Zelle/Binance/efectivo en dólares; en Bs va completo). Commit `4d51436`.
- `generar_datos_pago` (tools.py): calcula `monto_usd_divisas = monto_usd * 0.80`, lo guarda en `cobro:{tel}` y el `resumen_cobro` ofrece **ambos** precios (Bs por Pago Móvil/transferencia, o USD con 20% en divisas).
- Reconocimiento (tasks.py): el monto del comprobante **cuadra** si coincide con Bs pleno, USD pleno **O** USD con 20% (divisas). Así un pago por Binance/Zelle con descuento ya NO sale "monto no cuadra".
- El descuento NO se proclama de más: el precio/detalles solo si preguntan (decisión de la dueña). El "¿sube de precio con alulosa?" → va en **Conocimiento** (no en el prompt).

**2) Búsqueda escalable — Fase 1 (nativa, cero infra nueva).** Detonante: el bot "olvidaba" Conocimiento (tope de 3.500 chars truncaba) y no encontraba productos mal escritos. Este código se replicará a un negocio con **400 productos** → tiene que escalar.
- **Migración 011** (`011_busqueda_difusa.sql`, idempotente, fail-safe por statement): activa `pg_trgm` + `unaccent` (vienen en `postgres:16`) + índices GIN trigram. Cableada en `init_db.py`.
- **Búsqueda difusa de productos** (`_buscar_productos_difuso` en tools.py): tolera typos y acentos ("galetas"→Galletas, "limon"→limón). `ver_catalogo`: PRIMERO precisa por prefijo ('pan'=panes, NO empanadas), y SOLO si no calza, difusa. `_buscar_producto` (camino del DINERO): exacto→palabras→difuso con **umbral alto 0.6** (un typo se resuelve, pero jamás se cobra el producto equivocado).
- **`buscar_info(consulta)`** (nueva herramienta): el bot consulta el Conocimiento **on-demand** (top-4 relevante por trigram) en vez de cargarlo entero → **se acabó el truncado/olvido**. El prompt ya no inyecta el contenido: solo un **índice de títulos** (temas que sabe) y el bot busca el detalle.
- **Prompt auto-escalable** (`system_prompt.py`): catálogo chico (≤60) = lista completa (ancla anti-invención); catálogo grande = solo categorías + obliga a usar las herramientas. Reglas blindadas: dudas generales → `buscar_info` (nunca inventar).
- ⚠️ Deploy: **web + worker** (web corre la migración 011; el agente corre en el worker). compileall OK.
- **Por qué pg_trgm y NO pgvector aún:** OpenRouter no hace embeddings y no hay otro proveedor en el stack; meterlo ahora = dependencia nueva + riesgo de tumbar el bot. pg_trgm resuelve el "encontrar aunque escriban chueco" sin riesgo. Los **vectores/embeddings semánticos** (entender que "celíaco"="sin gluten") quedan para **Fase 2**, fail-safe, sobre esta base ya probada.

**3) Búsqueda semántica — Fase 2 (embeddings, fail-safe, SIN pgvector).** Hallazgos clave al investigar: (a) **OpenRouter SÍ tiene embeddings ahora** (`/api/v1/embeddings`, misma llave → cero dependencia nueva); (b) a escala de cientos de entradas **no hace falta pgvector** — el coseno se calcula en código. Por eso NO se tocó el Postgres (cero riesgo de infra).
- `app/services/embeddings.py`: `obtener_embedding(s)` vía OpenRouter (modelo `openai/text-embedding-3-small`, config `openrouter_model_embedding`). Fail-safe: si falla/sin saldo → None y se usa solo lo léxico.
- `conocimiento.embedding` JSONB (migración 012, aditiva). Se llena al crear/editar (router) y un **backfill** en `init_db` indexa lo viejo en lote.
- `buscar_info` ahora es **HÍBRIDO**: semántico (coseno sobre embeddings) + léxico (pg_trgm), dedupe, top-4. Si no hay embeddings → cae a léxico (= Fase 1). Nunca rompe.
- Deploy: **web + worker**. compileall OK. Pendiente: probar en vivo (ej. "¿es apto para celíacos?" debe encontrar la entrada de "sin gluten").
- ✅ Probado en vivo: "sirve para celíacos?" → encontró "sin gluten" (semántico OK).

**4) Ficha por producto (info específica de cada producto, modelo MIXTO).** Detonante: hablando de Galletas, el cliente preguntó "¿se puede congelar?" y el bot aplicó la duración de los PANES ("3 meses") a las galletas → **generalizaba info entre productos**. Solución: cada producto carga SU propia info.
- `productos` += `duracion`, `se_congela`, `apto_diabeticos`, `info` (texto libre) — migración 013, aditiva. (BOT: modelo, ProductoIn, listar/crear/editar; `info_producto` devuelve la ficha.)
- **Regla blindada:** detalles de un producto salen de SU ficha (info_producto); JAMÁS se aplica el dato de otro producto; si falta → confirma con la dueña, no inventa. `buscar_info` queda solo para dudas GENERALES (no de un producto puntual).
- **Panel:** Catálogo → Editar producto → sección "Información para el bot" (3 casillas: Duración, ¿Se congela?, ¿Apto diabéticos? + texto libre "Más información"). Ojo: `toggleDisponible` ahora reenvía la ficha completa para no borrarla al cambiar Disponible/Agotado.
- Deploy: **web + worker** (bot) + **dashboard** (panel). compileall + tsc OK.

---

## 2026-06-21 — Módulo "Métodos de pago" (varias cuentas) + validación de monto

Tras pruebas: el bot aceptaba mal (un voucher de Provincial pasó por coincidir solo el NOMBRE; y el monto no se comparaba). Decisión de arquitectura (con la proveedora): **los datos de pago viven en la BD/panel (una fuente), NO en el prompt**; el prompt solo "los da la herramienta". Brief: `BRIEF-verificacion-pagos.md`.
- **Tabla `metodos_pago`** (migración 009, idempotente; siembra el Pago Móvil viejo). Modelo `MetodoPago`. Varias cuentas: Pago Móvil/Transferencia/Zelle/Binance/Efectivo (campos: titular, banco, telefono, cedula, correo, wallet, instrucciones, activo).
- **Reconocimiento (agent.py/tasks.py):** la visión EXTRAE el beneficiario; el CÓDIGO valida contra TODAS las cuentas activas por identificador FUERTE (teléfono/cédula/correo/wallet — el nombre NO basta). Valida el **monto** contra lo cobrado (`cobro:{telefono}`); si no cuadra, registra pero NO confirma. Imágenes estrictas (memes/fotos → se rechazan). Logs de diagnóstico. (commit `5e57595`)
- **CRUD + panel:** `/api/metodos-pago` (bot `0f3fd00`) + sección "Métodos de pago" en Configuración (panel `92a3dbf`). La dueña agrega/edita sus cuentas; el bot las usa para reconocer.
- **OJO deploy:** el reconocimiento corre en el **WORKER** → al tocar el bot hay que redeployar **web + worker**. compileall + build OK.
- **Afinados (2026-06-22):** se agregó `cuenta` (nº de cuenta bancaria, transferencias — migración 010) e **ID de Binance (UID)**; el monto cuadra contra **Bs O USD** (Binance/Zelle vienen en USDT); reconocimiento **robusto** del beneficiario (junta todos los nº ≥6 dígitos del comprobante y los cruza con las cuentas — el UID de Binance puede venir en cualquier campo). Commits `a87fad6`, `8214759`, `5aaf7f8`.
- ✅ **VERIFICADO EN VIVO (2026-06-22):** el bot **reconoció un pago real por Binance** (UID 326103739) y **siguió vendiendo** ("recibí tu pago, coordino tu entrega, ¿algo más?"). El diagnóstico se hizo con un endpoint temporal `/api/debug/comprobante` (ya **retirado**, commit `1c006bf`). **Causa raíz del rato de pruebas:** el reconocimiento corre en el WORKER y este se quedaba en código viejo entre cada fix (cada cambio del bot necesita redeploy de **web + worker**); + la visión a veces ponía el UID en otro campo (resuelto con el match robusto). Datos cargados por la dueña en el panel ✓.
- **Siguiente paso (opcional):** que el bot **OFREZCA** los métodos desde la tabla (hoy los ofrece desde el prompt y reconoce desde la tabla — funcionan, pero hay que mantener ambos iguales). Mover el ofrecer a la tabla = una sola fuente.

---

## 2026-06-21 — Closer que RECONOCE el comprobante y sigue vendiendo (BOT — pendiente de probar en vivo)

A pedido de la proveedora (como su flujo en **SellerChat**). Antes el bot aceptaba **cualquier imagen** como comprobante y se detenía. Ahora:
- **Visión** (`leer_comprobante` en `agent.py`, modelo Gemini igual que la transcripción de voz): lee la imagen y dice si es un **comprobante real a las cuentas de la dueña** (titular/teléfono/banco de `configuracion`); ignora fotos/stickers/capturas de chats/redes.
- `_procesar_comprobante` (`tasks.py`): si la visión está **segura (confianza alta)** de que NO es comprobante → pide la captura y no registra; en cualquier otro caso (es comprobante, dudoso o ilegible) → **registra como `reportado`** (red de seguridad: nunca pierde un pago real) y el **closer sigue vendiendo** (agradece, dice que recibió el pago, coordina entrega, ofrece más).
- **Sin aviso "tienes una venta"** a la dueña (su banco ya le avisa): `registrar_comprobante(... avisar=False)`. El bot **no afirma** que verificó el dinero; la dueña confirma en su banco; el panel queda para auditar/**anular**.
- `pagado` sigue fijándose solo desde `/confirmar` (no se auto-confirma). Doctrina actualizada en `CLAUDE.md`. Plan/bitácora en `PRP-cobro.md` (doc único del cobro; se borraron PRP-001/002 sueltos).
- Verificado: compileall OK + **revisión adversarial** (1 ALTA corregida: falso negativo de visión perdía un pago → ahora solo descarta con confianza alta).
- **PENDIENTE: redeploy del BOT en Coolify + probar con un comprobante REAL en WhatsApp** (y una imagen cualquiera, para ver que la rechaza).

---

## 2026-06-20 (cont. 9) — LOTE 4: elevación visual "Sereno" tipo Apple (solo panel `9e5d748`)

Se generaron **3 looks** de la pantalla Resumen (Sereno / Cálido / Nítido) como maquetas HTML y la dueña **eligió "Sereno"** (minimalista tipo Apple). Aplicado al **sistema de diseño** (re-skinea las 12 pantallas de una vez, sin reescribirlas):
- **Lienzo** cálido casi blanco y PLANO (se quitaron los degradados verdes del fondo) → más calma.
- **Sombras** de tarjeta más suaves, difusas y ligeras (neutras, no verdes); hairline afinado (`globals.css`, `tailwind.config.ts`).
- **Resumen:** más aire en las tarjetas (`p-6`) y cifras más grandes (`text-4xl`).
- Verificado EN LOCAL con datos reales (login del dev contra el API en vivo): Resumen + Pagos se ven Sereno y consistentes. build OK.
- Solo panel → **un redeploy del panel** trae Lote 3 + Lote 4 juntos.

**Ajuste posterior (commit `a1a5eed`):** la dueña pidió que el Resumen quedara **igual a la maqueta** elegida (no solo el retoque de tokens). Se **reconstruyó el Resumen** para coincidir con el Look A (sin emojis): tarjetas KPI con ícono arriba-derecha + cifra grande, "Pagos por verificar" con barra verde + "Revisar", tarjeta ancha de Tasa con ícono, "Últimos pedidos" como lista limpia mostrando el **nombre** del cliente, y barra lateral con el ítem activo suave (verde tenue + punto) en vez de pastilla sólida. Verificado en local con datos reales.
- **OJO deploy:** se observó que el sitio en vivo SÍ se actualizó tras el push sin que ella tocara nada (probable webhook de Coolify activo) — pero si "se ve igual", casi siempre es **caché del navegador**: pedir **Ctrl+Shift+R**.

**Consistencia TOTAL (commit `35993de`):** la dueña pidió que NO solo el Resumen, sino TODA la plataforma tenga el mismo diseño. Se alinearon las **11 pantallas restantes** al lenguaje del molde (1 agente por pantalla + revisión adversarial 11/11 OK, 0 hallazgos): encabezado estándar (h1 `text-[28px]`), tarjetas con aire (`p-6`), tarjetas de cifra estilo KPI en Reporte/Clientes (chip de ícono + cifra grande + barra verde en la destacada), listas limpias (Clientes/Conversaciones: avatar + nombre + meta), botones/inputs uniformes, **sin emojis**. Sin tocar lógica/cobro (verificado). tsc + build OK; verificado en local (Resumen, Reporte, Clientes). **Con esto el panel entero comparte el mismo look Sereno.**

**Con esto el plan de 4 lotes queda COMPLETO** (Pedidos/Tasa, Pagos, robustez/a11y/DRY, visual). Pendiente futuro: lo de siempre en ROADMAP.

---

## 2026-06-20 (cont. 8) — LOTE 3: robustez + accesibilidad + validaciones + DRY (solo panel `419f691`)

Verificación EN VIVO de Lotes 1-2 OK: el **blindaje del cobro funciona** (intentar eliminar un pedido pagado se bloquea con "Usa Cancelar"), filtros de Pagos y contacto en Pedidos operando. Luego barrido de 14 pantallas (1 agente c/u) + **revisión adversarial** (0 hallazgos altos; 9 medios/bajos corregidos a mano).

- **Robustez:** estado **"No se pudo cargar + Reintentar"** (`<ErrorState>`) en todas las pantallas con datos (antes: skeleton infinito si fallaba la carga); badge del sidebar ya no muestra "0" falso; indicador del bot = "desconocido" si no se lee (antes asumía "activo"); **Mi Bot** separa error de carga vs acción y ofrece Reintentar para el interruptor; `cargar()` limpia el error al reintentar (sin banner "fantasma"); el banner no se duplica con ErrorState.
- **Accesibilidad:** `ErrorBanner role="alert"`; modales (Catálogo, Conocimiento) cierran con Escape y con labels asociados; labels del login.
- **Validaciones:** precio del catálogo (>0); **WhatsApp de avisos** en Config se NORMALIZA (acepta +/espacios, guarda limpio) y compara por los últimos 10 dígitos contra el número del bot (antes una validación estricta podía bloquear guardar TODO).
- **DRY:** `<EstadoBadge>`, `<EmptyState>`, `<ErrorState>` (con variante `embedded` para no anidar tarjetas), `formatFecha/formatHora` con guard, estilos de pago en `lib/estados`; dashboard usa `lib/format`.
- Solo panel → **un redeploy del panel**. tsc + build OK.

**Pendiente:** Lote 4 (elevación visual tipo Apple — con opciones para elegir).

---

## 2026-06-20 (cont. 7) — Completitud funcional (auditoría + plan en 4 lotes) — LOTE 1

Workflows: **auditoría de ingeniería** (37 hallazgos: robustez/estados de error, validaciones, a11y, DRY/SOLID) + **análisis funcional** (24 acciones faltantes por sección). **Decisiones de la proveedora:** anular pago confirmado = **SÍ** (reversa segura, lote futuro); **NO** borrar definitivamente pagos/clientes (conservar historial → anular/cancelar/ocultar). Plan en 4 lotes: 1) Pedidos+Tasa, 2) Pagos+robustez global, 3) resto de secciones+a11y+DRY, 4) visual Apple.

**LOTE 1 (hecho — bot `af294f6`, panel `970cec0`; compileall+build OK; cobro revisado a mano):**
- **Pedidos — Eliminar SEGURO** (`DELETE /api/pedidos/{id}`): BLINDAJE — 409 si tiene pago confirmado/parcial ("Usa Cancelar") o reportado ("confírmalo/recházalo antes"); solo borra si no hay pagos o solo rechazados (los borra por la FK `pagos.pedido_id`; items son JSONB, sin huérfanos).
- **Pedidos — Cancelar** (botón explícito, estado='cancelado'), **contacto del cliente** (nombre vía join en `GET /pedidos` + enlaces WhatsApp/ficha), **robustez** (try/catch al cambiar estado, `ocupado` por id, select con aria-label).
- **Tasa — fix de cobro:** no se puede activar el candado manual sin valor válido (>0) — backend `PUT /tasa` → 400 + validación inline en el panel. Evita dejar al bot SIN tasa.

**LOTE 2 (hecho — bot/panel):** Pagos — **filtros** por estado (Por verificar/Confirmados/Rechazados/Parciales), **reabrir** (`POST /pagos/{id}/reabrir`: rechazado/parcial → reportado), **anular pago confirmado** (`POST /pagos/{id}/anular`: pago → rechazado + pedido 'pagado' → 'esperando_pago' → se descuenta de `/reporte`; sin notificar al cliente; historial conservado, no se borra), validación de monto (NaN/≤0) y fallback de estado desconocido.

**Pendiente:** Lote 3 (robustez global: estados de error/reintentar + validaciones + accesibilidad + DRY) y Lote 4 (elevación visual tipo Apple).

---

## 2026-06-20 (cont. 6) — Catálogo: arquitectura confirmada + botón Eliminar producto

**Arquitectura del catálogo (CONFIRMADA con la proveedora — NO cambiar):** el bot CONOCE y RESPONDE solo desde el **catálogo digital (BD `Producto`) + base de conocimiento**. El **PDF es SOLO para enviar** (folleto hecho en Canva); el bot **NUNCA lo lee/parsea** (para que no "se vuelva loco"). Verificado en código: `system_prompt._catalogo_texto()` y `tools.ver_catalogo` leen de la BD; `tools.enviar_catalogo` solo manda el archivo (link a `/api/catalogo/archivo`); el system prompt prohíbe inventar productos. Lema: *"el bonito (PDF) para presumir, el digital para vender."* **No se tocó nada de esto** (a la proveedora le gusta cómo envía el PDF hoy).

**Nuevo:** botón **Eliminar producto** (`DELETE /api/productos/{id}`, commit bot `8cb129a`; panel `50928fc`) junto al toggle **Agotado**. Borrar NO afecta pedidos anteriores (`Pedido.items` es JSONB, guarda copia). Guía de uso: **Agotado** = temporal / puede volver (p.ej. el Chucrut que la clienta quitó pero quizá revenda → usar Agotado, no Eliminar); **Eliminar** = descontinuado de verdad.

**Despliegue:** redeploy del bot y del panel.

---

## 2026-06-20 (cont. 5) — "Borrar chat", scroll del chat y auditoría del panel

**Conversaciones (UX):**
- Bug de scroll arreglado: el chat baja solo al último mensaje (zona con scroll propio `max-h` + auto-scroll, como WhatsApp). Ya no hay que arrastrar toda la página.
- **"Borrar chat" (nuevo):** botón con confirmación. Borra los mensajes del cliente + su memoria en Redis (`borrar_memoria`: hist/buffer/lock/anti-abuso de hoy), **SIN tocar cliente, pedidos ni pagos**. Endpoint nuevo `DELETE /api/conversaciones/{telefono}` (solo `delete(Mensaje)`). `listar_conversaciones` ahora omite clientes sin mensajes → el chat borrado desaparece de la lista. **NO toca WhatsApp** (solo la BD). Permitido por Meta (sus reglas son de ENVÍO, no de administrar la propia BD).

**Auditoría del panel (workflow, 4 agentes adversariales) — corregido:**
- **Formato de dinero unificado:** `formatUSD` ahora usa coma decimal venezolana (es-VE), consistente con `formatBs` y el Resumen (antes Pedidos/Pagos/Reporte/Clientes mostraban punto → "pan es pan" pero un mismo $ se veía distinto).
- Tasa usa `formatBs` (2 decimales consistentes); login alineado al sistema de diseño (focus-ring + token `accent-fg`); Catálogo: el select de categoría muestra el valor real aunque esté fuera de la lista; Mi Bot: Enter respeta IME.
- Verificado: la lógica del cobro intacta en todo (revisión vía `git diff`); sin hallazgos críticos.

**Pulido final (HECHO):** DRY — componente `<ErrorBanner>` (`src/components/error-banner.tsx`) + `inputCls` compartido (`src/lib/ui.ts`) aplicados en las 12 pantallas (commit panel `22171f9`, −45 líneas netas); labels asociadas en Configuración con `useId`. Sin duplicación pendiente. Build + lint + tipos OK.

**Despliegue:** requiere redeploy del **BOT** y del **PANEL** en Coolify (manual).

---

## 2026-06-20 (cont. 4) — Rediseño PREMIUM del panel (pantalla Resumen)

**Por qué:** a la dueña no le gustaba el diseño del panel; quería que se viera lo más premium posible. Referencia: los "400 recursos de diseño web" de su mentor (SinergIA / Juan Lara, `app.snrgia.ai`); señaló una plantilla clara/elegante (Nexora).

**Proceso (workflows + render real con Playwright para que ELLA decidiera MIRANDO, no con descripciones):**
- Pilotos HTML autocontenidos en `docs/design-pilot/` (NO tocan código real hasta aprobar). Rechazó la serif fina ("muy finitas, para otra cosa"); eligió la "Opción C" (Nunito, verde, cálido). Pidió quitar emojis y conservar la zona "Salir" estilo Apple. Se hizo un "pase premium" (paleta calmada, tipografía con criterio, hairlines, sombras sutiles).

**Qué se aplicó al panel real (`masvidaconsciente-dashboard`, ADITIVO, `next build` OK):**
- `globals.css` + `tailwind.config.ts`: tokens nuevos (light con tinte verde, `warn` ámbar reservado SOLO a "pendiente", sombras `card/soft`, `<alpha-value>` para que funcionen las opacidades) y fuente **Nunito** (`app/layout.tsx`).
- `(app)/layout.tsx`: sidebar premium — perfil con datos reales (`getConfiguracion`), badge ámbar de pagos, `aria-current`/`aria-label`.
- `(app)/dashboard/page.tsx`: Resumen premium conectado a datos REALES — métricas, **Cobrado** hoy/semana/mes (`getReporte`), **Tasa BCV** (`getTasa`), **últimos pedidos** (`getPedidos`), **Bot activo** (`getBotEstado`). Bs calculado de la tasa real. SIN inventar números.

**Revisión adversarial (workflow, 4 revisores) — corregido antes de cerrar:**
- ⚠️ Clave (rozaba la regla del cobro): la tarjeta decía "Ventas hoy" usando `metricas.ventas_hoy_usd` = **facturado** (pedidos del día, pagados o no), mientras la sección usaba `reporte.ventas_usd` = **cobrado** (pagos confirmados). Verificado en `app/api/router.py`. Renombrado: tarjeta = **"Facturado hoy"**; sección = **"Cobrado"** (conteo con `num_ventas`).
- Contraste de `--fg-faint` subido a nivel AA; quitada la interactividad falsa del avatar; `<caption>` en la tabla; tipografía de h2/thead alineada al diseño aprobado.

**Pendiente (Paso 2):** el gráfico de ventas de 7 días y los deltas "+X% vs ayer" NO existen en el API (hoy `getReporte` solo da agregados hoy/semana/mes). Falta un endpoint de ventas diarias en el bot para activarlos (read-only, bajo riesgo).

**Estado git:** Resumen + shell en `master` (commit `7839fc6`), desplegado y **aprobado en vivo** por la proveedora ("se ve muy lindo"). Luego se restilizaron **Pedidos, Pagos y Tasa** al mismo nivel premium (commit `865ccac`) — **lógica del cobro intacta**, verificado por revisión adversarial vía `git diff`. ⚠️ Coolify es **deploy MANUAL** (un push NO despliega; la proveedora da Redeploy en Coolify). Luego se restilizaron las **8 pantallas restantes** (commit `aa65916`): catálogo, clientes, conversaciones, bot, conocimiento, mensajes, configuración, reporte → **TODO el panel premium y consistente** (build OK; lógica intacta verificada por revisión adversarial vía `git diff`). Reversible con `git revert` + redeploy.

**Ajustes finales (verificados EN VIVO con login de la proveedora):** barra lateral **fija** → "Salir"/perfil siempre visibles sin scroll (`600dda3`); **"Bot activo" clickeable** → lleva a Mi Bot (`aa2b7c1`); **estados de pedido completos** vía módulo único `lib/estados.ts` → "Esperando pago"/"Pagado" con etiqueta+color, y el desplegable de Pedidos muestra el estado real (`131c34a`).

**Paso 2 (gráfico 7 días + deltas "+% vs ayer"): DESCARTADO por ahora** a pedido de la proveedora — no quiere elementos solo-decorativos; el Resumen ya usa datos reales con la tarjeta "Cobrado". Retomar solo si lo pide.

---

## 2026-06-20 (cont. 3) — Respaldo cifrado offsite (Blindaje 4, por fin)

**Por qué:** auditoría senior marcó que NO había respaldo de la BD = riesgo CRÍTICO hoy (si muere el VPS se pierde todo). Maired aprobó montarlo (destino barato/gratis).

**Solución (servicio `backup` aislado en docker-compose):** `pg_dump` (con `--no-owner --no-acl`) + las imágenes de `/data/comprobantes` → cifrado con **restic** (clave que solo controla la proveedora) → subido a **Cloudflare R2** (10 GB gratis = $0/mes a este tamaño). Diario, con retención rolling (forget diario + prune los domingos). Si faltan las llaves R2, el servicio se **pausa solo** (no rompe el bot). Mounts de comprobantes/catalogo en `:ro`.

**Archivos nuevos:** `scripts/backup.sh`, `Dockerfile.backup` (alpine + postgresql16-client + restic), servicio `backup` en `docker-compose.yml`, y `RESPALDO.md` (guía paso a paso para crear R2 + poner 4 secretos en Coolify + cómo verificar y RESTAURAR).

**Revisión adversarial (2 agentes) — arreglado antes de subir:**
- Doc de restauración tenía rutas mal (restic restaura con rutas absolutas) → corregido (`/restore/backup/db_*.sql.gz`, bind-mount real, psql DENTRO de la red compose).
- `forget --prune` diario silenciado → ahora forget diario visible + prune solo domingos; backoff de 1h en fallo.
- Build del servicio `backup` es BLOQUEANTE del deploy (si su build falla, no arranca el bot) → paquetes verificados en alpine:3.20; documentado; a futuro publicar imagen pre-construida.

**Pendiente (Maired):** crear cuenta Cloudflare R2 (gratis) + bucket, y pegar 4 secretos en Coolify (RESTIC_REPOSITORY, RESTIC_PASSWORD, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY). Luego: probar UNA restauración. ⚠️ Guardar bien RESTIC_PASSWORD (sin ella el respaldo es ilegible).

---

## 2026-06-20 (cont. 2) — Pedidos SEPARADOS: el estado del pedido va en código, no en el chat

**El bug (visto en vivo):** cliente pagó un pedido; pidió 1 cosa nueva → el bot mezcló todo en un pedido de $71 (arrastró items viejos del chat) e inventó "ya pagaste $65". Y repreguntó la variante ya dicha ("¿plátano o yuca?" con "de plátano"). La calculadora SÍ quedó bien.

**Diagnóstico (workflow: mapeo + diseño + crítica adversarial):** el bot NO recibía de la BD qué pedido está abierto/cerrado; lo INFERÍA del historial Redis (20 turnos). El código ya NO arrastra items (registrar_pedido crea pedido nuevo solo con sus items) — el arrastre es pura alucinación del modelo leyendo el chat. La crítica corrigió 3 errores del primer diseño (no tocar redactar_mensaje, alinear con get_pedido_esperando_pago, manejar pendiente/esperando_pago/parcial).

**Qué se hizo (aditivo, compileall OK):**
- `system_prompt.py`: `_estado_cliente_texto(telefono)` — lee de la BD los últimos pedidos y arma un bloque "ESTADO DEL CLIENTE" inyectado cada turno (pedido en esperando_pago = al que se pega el comprobante; pendiente; o último cerrado → "lo nuevo es pedido NUEVO"). `construir_system_prompt` acepta `telefono` (solo `responder` lo pasa; `redactar_mensaje` NO → sus avisos no se tocan). 2 reglas nuevas en `_REGLAS`: pedidos separados + no inventar reconciliación de pagos; y respetar la variante ya dicha.
- `agent.py`: `responder` pasa `telefono` a `construir_system_prompt`.
- `tools.py`: `registrar_pedido` nota = "pedido NUEVO #id con SOLO estos items".
- Plan en `PRP-pedidos-memoria.md` (local).

**Principio:** igual que el dinero, el ESTADO del pedido lo pone el código y se inyecta; el modelo no adivina.

**Pendiente:** redeploy bot + worker. Probar: pagar → pedir algo nuevo → debe abrir pedido NUEVO (no mezclar, no inventar pago). Y "tortilla de plátano" → sin repreguntar variante.

---

## 2026-06-20 (cont.) — BLINDAJE del cobro: el modelo NUNCA suma de cabeza

**El bug (visto en vivo con Haiku):** cliente pidió 2 productos de $8 c/u; el bot cobró $8 (la prueba: dijo "8$ o 4.859,14 Bs" = 8×tasa, o sea cobró un pedido incompleto/viejo) y al reclamar sumó $16 de cabeza. La **calculadora del código está bien** (`registrar_pedido` suma en Python); el problema es que el modelo (1) registraba el pedido incompleto y (2) sumaba/decía montos de su cabeza.

**Diagnóstico (workflow multi-agente):** auditoría confirmó que el plan inicial de 3 capas tenía un hueco — `redactar_mensaje()` (avisos de pago) también podía escribir montos sin regla. Además se decidió el modelo: **quedarse en Haiku 4.5** (mejor voz/tono por su precio; Flash Lite es más barato pero es el más flojo justo en los matices; la matemática ya está en código así que el modelo barato es seguro para la plata). Costos reales: Haiku ~$11–27/mes a 2–5k msgs.

**Qué se hizo (aditivo, `compileall` OK; formateadores probados):**
- `system_prompt.py` `_REGLAS` (lo comparten chat Y avisos): regla de oro de DINERO — nunca calcular/sumar/redondear; copiar EXACTO el monto de la herramienta; registrar el pedido COMPLETO en una sola llamada y decir el total del campo `resumen`; pasar el `pedido_id` a generar_datos_pago (no cobrar uno viejo).
- `tools.py`: `_fmt_usd` / `_fmt_bs` (formato Bs venezolano); `registrar_pedido` devuelve `resumen` (línea por línea + Total, ya calculado en código); `generar_datos_pago` devuelve `resumen_cobro` ("Son $X o Y Bs"). Descripciones de las tools reforzadas (todo en una llamada + pasar pedido_id).
- **Decisión:** NO se metió un validador que parsee montos del texto (frágil, podría dañar mensajes buenos). La plata sale armada desde el código y el modelo solo copia = más robusto.

**Pendiente:** redeploy bot + worker. Probar el MISMO pedido de 2 productos → debe dar **$16** y el cobro en Bs correcto; probar un aviso de pago parcial.

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
