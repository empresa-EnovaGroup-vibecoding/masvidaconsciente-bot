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

> Cada mejora se construye con su propio `PRP-*.md`. Para trabajar una, dime: "trabajemos el Plan A".

- 🛠️ **Voz del bot (closer = Whuilianny real)** — en progreso/afinando. Guión en `BRIEF-closer-masvida.md`. Ya desplegado: habla en 1ª persona, varios mensajitos cortos, plano (sin viñetas/negritas), no re-saluda en cada mensaje.
- **PLAN A — Memoria + ficha del cliente** *(aprobado construir)*: que el bot reconozca al cliente que vuelve (nombre, nº de pedidos, última compra, notas) leyéndolo de Postgres. Decisión pendiente: cuántos días recuerda el detalle de la charla (sugerido 7).
- **PLAN B — Arreglar los comprobantes de pago** *(bug)*: el bot no reconoce bien las imágenes de comprobante. Toca el dinero → con cuidado.
- ✅ **El bot dice que mandó el catálogo pero NO lo manda** — **RESUELTO** (verificado en código 2026-06-20). Hay red de seguridad en `app/agent/agent.py` (`_asegurar_catalogo` + `_afirma_envio_catalogo`): si el agente afirma el envío sin llamar la herramienta, el código **envía el PDF de verdad**; si no hay PDF, reescribe el texto para no mentir. Riesgo residual mínimo: la detección de "lo afirmó" es por lista de frases (una redacción rara podría escapar).
- **PLAN C — Pagos multi-método + descuento por divisas** *(Paso 2)*: el bot pregunta el método y por efectivo/divisas aplica 20% + delivery gratis.

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
- 🟠 **Respaldo automático de los datos** — **YA ESTÁ EN CÓDIGO** (verificado 2026-06-20): `scripts/backup.sh` + servicio aislado en `docker-compose.yml` (pg_dump + comprobantes + catálogo, **cifrado con restic** y subido a Cloudflare **R2**, diario, con retención). FALTA **ACTIVARLO**: poner los secretos de R2 en Coolify (sin ellos el script se pausa y NO respalda). ⚠️ **Hasta activarlo, los datos NO tienen respaldo externo.** *(activación, no construcción)*
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
