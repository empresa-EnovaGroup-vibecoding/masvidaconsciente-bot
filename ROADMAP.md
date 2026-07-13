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

**2. 🔴 PRODUCTO + TAMAÑO + OPCIÓN — la cirugía (PRP escrito y auditado, ESPERA EL OK DE MAIRED).**
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
