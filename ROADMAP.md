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

---

## 🔨 EN QUÉ ESTAMOS AHORA (lo siguiente, en orden)

> 📍 **Pestaña NUEVA de Claude: empieza leyendo este bloque + la última entrada de SESIONES.** Ahí está el estado REAL (no asumir de memoria vieja).

---

# 🚦 ESTADO REAL A 2026-07-14 (verificado, no supuesto)

| | Estado |
|---|---|
| 🔴 **EL BOT ESTÁ APAGADO EN EL TALLER** | `bot_activo = false`. Se apagó porque **le estaba contestando a una CLIENTA REAL** e inventó un precio. **No encenderlo sin el OK de Maired.** |
| 🔇 **En PRODUCCIÓN el bot NO le habla a los clientes** | `NUMEROS_PERMITIDOS = 573005690062` (solo el número de Maired). Los clientes escriben y **ella les contesta A MANO** (esta semana: 65 mensajes de un cliente, **1** del bot, **33** de ella). |
| ✅ **La PARED DEL DINERO** | En taller **y producción**. El bot **no puede** sumar, ni confundir bolívares con dólares, ni decir un total que no calculó el sistema. |
| ✅ **El DELIVERY** | Construido: zonas de lista CERRADA + el **código** suma. **Solo en el TALLER** (falta promover a producción). Zonas: Retiro La Mendera $0 · Barquisimeto $3 · Barquisimeto oeste $5. |
| ✅ **Pantalla "Entregas"** | En el panel del taller. Maired ya puede cargar zonas sola. |
| ✅ **La base de datos** | Arreglada: producción llevaba **días** arrancando "en verde" con el esquema a medias (migraciones 019→022b sin aplicar). Detector nuevo: `probar_migraciones.py`. |
| ✅ **10 BANCOS DE PRUEBA** | `probar_cobro` (27/27) · `probar_delivery` · `probar_carril_dinero` · `probar_datos_bancarios` (nuevo 14-jul) · `probar_honestidad` · `probar_retomar` · `probar_bandeja` · `probar_fase2` · `probar_panel_tamanos` · `probar_migraciones`. **Todos VERDES.** Correrlos **siempre** antes de desplegar. |
| ✅ **El CANDADO de los datos bancarios** | Hecho 14-jul: los datos salen SOLO de `generar_datos_pago` (que ahora los lee de la tabla `metodos_pago` — una sola verdad, la misma del panel y de la visión) + **red en código** que frena cédulas/cuentas/correos que ninguna herramienta dio en ese turno. **Sacados del TEXTO de la personalidad** (taller; backup en `/root/personalidad_backup_20260714.txt`). Y **creado el row de ZELLE que NO existía** en la tabla: por eso la visión rechazaba comprobantes Zelle legítimos ("no te aparece a tu cuenta"). Ver SESIONES 14-jul. |
| ✅ **🛡️ EL VIGILANTE (deuda D2 — CERRADA 14-jul)** | Los **10 bancos corren SOLOS** tras cada despliegue del taller (workflow → SSH → `correr_bancos.py` en el contenedor nuevo). Rojo ⇒ el flujo queda ROJO en GitHub **y WhatsApp a la dueña**. Ya no depende de que un humano se acuerde. Probado en vivo el primer push. |
| ✅ **🎬 Cualquier formato de foto/video sirve** | Hecho 14-jul: `media_convert.py` + ffmpeg convierten **en la puerta** (al subir) a lo que WhatsApp exige (MP4 ≤16MB / JPEG ≤5MB). Había **5 videos .quicktime que WhatsApp rechazaba SIEMPRE** — migrados a .mp4 en el bucket y reparadas las referencias en LAS DOS bases (comparten bucket). El bot taller está **encendido con lista blanca** (Enova + Maired). |

### 🔴 LO QUE FALTA (en orden)

1. ✅ ~~EL CANDADO DE LOS DATOS BANCARIOS~~ — **HECHO 14-jul** (ver la tabla de arriba y SESIONES).
2. **LA PERSONALIDAD ORDENA LA MENTIRA — y hay TRES versiones divergentes (verificado 14-jul).** El TALLER ya no dice "Eres humana" pero sí *"la dueña"* + el bloque *"si te preguntan si eres un bot… sin entrar en más detalle"*; **PRODUCCIÓN todavía dice "Eres humana"**; el BRIEF quedó en la versión del 10-jul. **Es el texto de Maired: NO se toca sin su OK.** Propuestas listas (SESIONES 14-jul): quitar "la dueña" y ese bloque · reescribir el ejemplo de la alulosa (ordena una frase que la red prohíbe) · alinear lo del médico · reparar la frase rota de QUÉ NO HACER · quitar "RESPUESTA COMPLETA OBLIGATORIA" (apunta a un mecanismo que NO existe en el código).
3. **Promover el DELIVERY a producción** (`gh workflow run deploy.yml -f produccion=true`) y cargar las zonas allí. ⚠️ Al promover, `configuracion` viaja COMPLETA: la personalidad del taller (ya limpia) pisará la de producción — eso está bien, pero verificar `bot_activo` después.
4. **El ENSAYO GENERAL** (`scripts/ensayo_retomar.py` + los 12 clientes falsos) **antes de encender el bot**.
5. ✅ ~~**D1 de verdad:** tabla `schema_migrations` + que el arranque **falle RUIDOSAMENTE**~~ — **HECHO 14-jul (fase 0).** `init_db.py` descubre las migraciones solas, las aplica UNA vez y las anota; `main.py` ya NO se traga la excepción (si una migración falla, **el contenedor no arranca**). Banco nuevo `probar_drift.py` (compara `models.py` entero contra el esquema real). Ensayado contra un `pg_dump` de la BD REAL: 32→32 productos, nada corrompido. ⚠️ Se descubrió que **`002_seed_catalogo.sql` NO es idempotente** (su `INSERT INTO productos` no lleva `ON CONFLICT`): lleva candado, o habría duplicado el catálogo entero. Ver SESIONES.
6. **El RETOMAR sigue siendo un segundo camino** con su propia instrucción `[SISTEMA]`. Lo correcto es el **REPLAY** (los pendientes vuelven a entrar por el camino normal). Eso mata de raíz toda esa familia de bugs. Y de paso, la **puerta única de salida**: que las redes universales vivan en `_enviar_en_partes` (hoy están repetidas en cada carril).
7. **Conocimiento está desordenado** (lo dijo Maired). Falta ordenarlo.
8. ❓ **Pregunta de negocio abierta:** *¿el 20% de descuento por pagar en divisas aplica también al ENVÍO?* Hoy está implementado como **NO** (descuento solo al producto; el flete se cobra completo) — si aplicara al total, **Whuilianny pagaría el delivery de su bolsillo** en cada venta en dólares. **Falta confirmarlo con ella.** (Si dice que SÍ: cambiar `generar_datos_pago` Y `registrar_comprobante` a la vez, o el comprobante no calzará.)

### ⚠️ REGLAS QUE COSTARON SANGRE (no re-aprenderlas)

- **El DINERO va en el CÓDIGO, nunca en el prompt.** El prompt decía *"no sumes el envío al total"* **dos veces** y el bot lo sumó igual, a una clienta real. *Lo que se puede desobedecer, se desobedece.*
- **Verificar en la BD, no en el chat.** El bot dijo *"te agendo"* con **cero pedidos** en la base.
- **Todo lo que mueve dinero necesita una LISTA CERRADA** (el "código de barras"): `variante_id` para el producto, `zona_id` para el envío. El modelo **elige**, nunca **escribe**.
- **Un contenedor en VERDE no significa que la base esté bien.**
- **Push a master = SOLO el taller.** Producción es a mano (`-f produccion=true`).

---

### 🎯 EL ORDEN RECOMENDADO (decidido con Maired, 2026-07-13)

1. **La DEUDA TÉCNICA** (D1–D5, aquí abajo). No es lo más vistoso, pero **es lo que puede romper lo que YA funciona** — hoy, si alguien despliega sin correr los bancos a mano, rompe el cobro y nadie se entera.
2. **Las PLANTILLAS de Meta** (punto 4). Van pronto porque **Meta tarda hasta 24h en aprobar cada una**: cuanto antes empiece el trámite, mejor. Y **desbloquean media hoja de ruta** (recordatorios de pago, recuperar pedidos, reactivar dormidos).
3. **Las fases 3 y 4 de la bandeja** — las que hacen que la dueña **deje el celular**.
4. **El contenido** (trabajo de ella) y lo demás.

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

### 🔨 LO QUE SIGUE — en orden (actualizado 2026-07-12)

> **Estamos construyendo LA BANDEJA**: que la dueña **atienda DENTRO del sistema** en vez de irse a WhatsApp.
> **Fase 1 HECHA (2026-07-12):** ya responde desde el panel, el bot **se calla solo** en ese chat, y al devolvérselo **el bot sabe lo que ella prometió**. Con el **reloj de las 24h de Meta** a la vista (y la caja bloqueada si se cerró).
> **Fase 2 (EN CURSO):** que lo que ella escribe **desde su celular** entre al hilo y calle al bot (hoy le habla encima) · **el comprobante DENTRO del chat** (hoy no entra: el panel muestra al bot respondiéndole a un mensaje fantasma) · **entregado/leído/FALLÓ** en cada mensaje.
> ✅ **Desbloqueada:** `smb_message_echoes` activado y **verificado en vivo** — el bot **NO** recibe eco de sus propios mensajes, así que **no se puede quedar mudo**. Ver SESIONES 2026-07-12 (noche 10).
> Faltan las fases 3 (cola con no leídos + aviso en vivo), 4 (menú agrupado) y 5 (plantillas para reabrir chats de +24h). Ver `PRP-bandeja.md`.
>
> ⚠️ **ANTES DE TOCAR EL COBRO, LEE ESTO:** existe un **banco de pruebas** — `scripts/probar_cobro.py`. **Córrelo siempre** después de cualquier cambio en el catálogo, las herramientas o el cobro (`docker exec -w /app -e PYTHONPATH=/app <bot> python scripts/probar_cobro.py`). Si algo sale MAL, **no se despliega**. Ver CLAUDE.md §8 y SESIONES 2026-07-12.

**1. ✅ La BANDEJA "El bot te necesita" en el PANEL** *(repo `masvidaconsciente-dashboard`)* — **HECHA Y VERIFICADA (2026-07-12).**
Pantalla `/bandeja`: los avisos (motivo, cliente, lo que preguntó), botón **"Ya lo atendí (reactivar el bot)"**, link para abrir el chat en WhatsApp, y el bloque **"El precio de hoy"** (escribir el precio del día de Tortas keto / Premezclas / torta baja). Con **contador en el menú** que se refresca solo cada 45s — el aviso ya no pasa desapercibido. Ver SESIONES 2026-07-12.

**2. ✅ PRODUCTO · TAMAÑO · OPCIÓN — LA CIRUGÍA: HECHA (2026-07-13).** La fuga de la Kombucha ($3 por venta) está **cerrada**. El pedido va por **`variante_id`** de una lista CERRADA (el "código de barras"): el modelo **no puede escribir un id que no le dimos** y el precio lo resuelve el código. Precio del día **por tamaño**. El panel **bloquea nombres repetidos** y **rechaza** editar el precio en el producto si tiene varios tamaños. Migraciones 022 + 022b. `probar_cobro.py` **27/27** · `probar_panel_tamanos.py` **9/9**. Ver SESIONES 2026-07-13.
*(Lo que sigue abajo, el punto 5, es el detalle de esta misma cirugía — ya no es un pendiente.)*

**2.b ✅ LA BANDEJA — Fases 1 y 2 HECHAS (2026-07-12/13).** La dueña **atiende desde el panel** (el bot se calla solo en ese chat, con firma de **quién** apretó el freno) y **el hilo dice la verdad**: lo que ella escribe **desde su celular** entra al chat y calla al bot · el **comprobante se ve DENTRO del chat** · **entregado/leído/FALLÓ** por mensaje. Migraciones 019, 020 y 021. Ver `PRP-bandeja.md`.
**🆕 EL REMATE DEL HANDOFF — ✅ FASE A HECHA (2026-07-13, EN EL TALLER; producción espera tu OK):**
- **Que el bot CONTESTE al RETOMAR el chat.** Lo pidió Maired con una captura: el cliente escribió *"¿cuánto en Bs?"* durante la pausa y el bot **no contestó** al devolverle el chat. Faltaba el **disparador**: "Devolver al bot" solo apagaba la pausa, y el bot solo habla cuando ENTRA un mensaje nuevo. **Construido:** el **mismo botón se volvió inteligente** (sin botón nuevo, cero cambios de panel) + tarea Celery `retomar_chat` que lee el historial y llama a `responder()` con todas las herramientas, con **ventana-24h fail-closed** (cerrada ⇒ no escribe y te avisa), **candado de idempotencia** (doble click = un mensaje), guard de "no hablar sin nada pendiente" y las redes heredadas. Es RESPUESTA, no proactivo (seguro con Meta). Banco nuevo `probar_retomar.py` **12/12** + end-to-end real; cobro **27/27** sin regresiones. Ver `PRP-bandeja-fase3-retomar.md` (local) y SESIONES.
- **Falta la Fase B** *(nice-to-have)*: reconstruir el historial desde Postgres para pausas largas (hoy, si el **comprobante** entró **durante** la pausa, el bot podría re-pedirlo — el pago **sí quedó registrado**, no se pierde dinero) + microcopy en la bandeja.

**Faltan sus fases 3, 4 y 5:**
- **Fase 3 — que sea una COLA, no una lista:** orden por quien lleva más esperando · *"esperando hace 12 min"* · filtros (Sin responder · Me necesitan · Pago por verificar) · **aviso en tiempo real con sonido**. ⚠️ *Sin esto, la dueña **va a seguir viviendo en el celular**: el celular sí vibra.*
- **Fase 4 — que se sienta un producto:** el menú agrupado (hoy son **14 ítems planos** y *Conversaciones* está en el puesto #10) · la **Bandeja como pantalla de inicio** · la **ficha del cliente al lado del chat** (el mayor impacto visual por menos código de todo el plan).
- **Fase 5 — las PLANTILLAS** *(es el punto 4 de abajo: el mismo trabajo)*.
Decisión de Maired (2026-07-12): **NO al parche de renombrar la Kombucha.** Se hace la estructura correcta. Ver **`PRP-producto-variantes.md`** (local): un producto → sus **tamaños** (precio + foto + sabores + agotado propios) → sus **opciones** (no tocan el dinero), y `registrar_pedido` recibe un **`variante_id` de lista cerrada** ("código de barras") en vez de un nombre libre. Arregla de raíz la Kombucha ($3 por venta) y las tortas por tamaño.
⚠️ **El PRP fue ATACADO por 4 revisores antes de aprobarlo** (51 hallazgos → 34 reales) y quedó en **v2**. ✅ El bloqueante (respaldo automático) **ya está resuelto**: activado y con restauración probada.
**Fotos:** etiquetado **por demanda**, cero tarea para la clienta (la migración se lleva sola la etiqueta de la Kombucha; lo que nadie sabe nace neutro y el bot **no afirma tamaños que no sabe**).

**3. ✅ `dueno_telefono` configurado (2026-07-12)** — `573005690062`, en las DOS bases. **Verificado con un WhatsApp real enviado.** Los avisos del bot ("🔔 Nuevo pago reportado", "el bot te necesita") ya le llegan al teléfono.

**4. 🟡 Motor de PLANTILLAS (HSM) — el aviso que SIEMPRE llega.** `meta_client.py` hoy **solo manda `type: "text"`** (free-form) → un aviso solo llega si la dueña le escribió al negocio hace <24h (ventana de Meta). Hace falta: (a) `enviar_plantilla()` en el código, con fallback a texto si la ventana está abierta; (b) que **Maired cree y apruebe** la plantilla `bot_necesita_ayuda` (categoría **Utilidad**) en el WhatsApp Manager. ⚠️ **Es el ladrillo de media hoja de ruta** (recordatorios de pago, recuperar pedidos, campañas, reactivar dormidos).

**5. 📎 PRODUCTO + VARIANTES + OPCIONES — *es el detalle del punto 2, no un pendiente aparte*.** Hoy el precio vive pegado al producto, y por eso la dueña tuvo que crear **dos productos con el mismo nombre**. Lo correcto son **3 conceptos**:
- **PRODUCTO** = lo que ES (ficha, ingredientes, fotos). **Nombre ÚNICO.**
- **PRESENTACIÓN/VARIANTE** = cómo se compra: tamaño **+ PRECIO** (Kombucha: 350ml $4 · 700ml $7).
- **OPCIÓN** = lo que el cliente escoge: sabor/relleno/masa. **NO cambia el precio** (Empanadas: carne mechada · pollo · queso).
**La regla sale sola de los datos:** *el bot pregunta SOLO cuando hay más de una presentación.* Arregla: Kombucha · Galletas New York + Mini New York (misma galleta, 2 tamaños) · Tortas keto y torta baja (3 tamaños "250g/500g/1kg" metidos en un texto) · Premezclas. **El panel debe BLOQUEAR nombres repetidos** y llevarla a "agregar presentación". Y `registrar_pedido` pasa a recibir **`variante_id`** de una lista cerrada (el "código de barras") → **imposible cobrar mal**.
*(NO son variantes, son productos distintos: Tortillas vs Tortillas Taco · Empanadas/Keto/Horneadas · Wafles Salados vs Dulces — distintos ingredientes.)*

**6. 🟡 Contenido** (trabajo de la dueña): fichas de producto (duración/congela/diabéticos) y más **Conocimiento** (FAQ). ⚠️ **Ojo:** cuanto menos Conocimiento haya, **más handoffs** le van a llegar.

**7. ⚪ Modelo de IA** (investigado 2026-07-12, 7 agentes): el mejor costo-beneficio sería **`openai/gpt-5.4-mini`** — **más barato que Haiku 4.5** ($0.75/$4.50 vs $1/$5, caching automático) **y mejor en tool use**. ⚠️ **No acepta `temperature`** (OpenRouter la descarta en silencio) y **todos** los modelos baratos de 2026 son de razonamiento → hay que fijar `reasoning: minimal` o el costo/latencia se disparan. **Falta también** `provider: {require_parameters: true}` en el payload (`agent.py:75`) o OpenRouter puede rutear a un proveedor que **ignore las herramientas** → el bot inventaría precios. **NO es urgente:** el bug de "cobraba mal" era del CÓDIGO, no del modelo.

**8. 🔴 Seguridad — ROTAR llaves** expuestas en el chat: META_ACCESS_TOKEN + META_APP_SECRET, OPENROUTER_API_KEY, JWT_SECRET, ADMIN_PASSWORD, llaves R2 (las de las fotos **y** las nuevas del bucket de respaldos, 2026-07-12). **Antes de lanzar con clientes reales.**

**⚪ Otros:** pasar el repo de GitHub a **privado** · afinar la voz (`BRIEF-closer-masvida.md`; ⚠️ **la voz y la bienvenida de Whuilianny son INTOCABLES** — ver SESIONES 2026-07-10/11).

---

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
