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
| **D1** | 🔴 **No hay tabla de migraciones aplicadas.** `init_db.py` re-ejecuta **los 23 `.sql` ENTEROS en cada arranque** del contenedor. La idempotencia la sostiene el que escribe cada archivo, a mano (`WHERE NOT EXISTS`, `IF NOT EXISTS`). | Un `.sql` mal escrito **duplica datos o borra algo en el próximo reinicio**. Es la bomba de relojería más grande que queda. Y `main.py` **se traga la excepción**: si una migración revienta, el contenedor **arranca verde** con la tabla sin crear. |
| **D2** | 🔴 **Los bancos de pruebas NO corren solos.** `.github/workflows/deploy.yml` despliega, pero **no ejecuta** `probar_cobro.py` / `probar_honestidad.py` / `probar_bandeja.py` / `probar_fase2.py`. Hoy se corren **a mano por SSH**. | Alguien hace `git push`, se despliega, **se rompe el cobro y nadie se entera**. La regla "si sale rojo, no se despliega" **hoy no la hace cumplir nadie**: depende de que un humano se acuerde. |
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
