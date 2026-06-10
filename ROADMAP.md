# 🗺️ Roadmap — El sistema "másvida"

> **Visión:** másvida es una plataforma de **ventas y cobro por WhatsApp** para marcas pequeñas de productos saludables: un agente IA **oficial de Meta** que atiende, cobra en bolívares con tasa BCV y le deja a la dueña el **control total** desde un panel simple — diseñada para **replicarse cliente por cliente** (la base del negocio Tech Provider de Maired).

**Cómo leer las prioridades:** 🟢 Ahora · 🟡 Pronto · ⚪ Futuro · *(esfuerzo: bajo/medio/alto)*

> 💡 **Dato clave:** muchas features 🟢 "Ahora" están **medio construidas por dentro** — el bot ya las soporta en su base de datos/API; solo falta la pantalla en el panel. Por eso son rápidas y de cero riesgo.

---

## ⭐ 3 cosas críticas que descubrimos (que nadie había pensado)

Esto es lo más valioso del análisis — son agujeros reales que ningún panel "bonito" cubre:

1. **Aprobación humana antes de CUALQUIER envío proactivo.** Ningún recordatorio/campaña/reactivación sale solo: el sistema prepara la lista + el borrador y **tú apruebas con un botón.** Como eres Tech Provider **oficial de Meta**, un envío automático mal calibrado puede quemar la calidad del número y arriesgar la cuenta de **todos** tus futuros clientes. Es regla dura, no opción.
2. **Manejo del pago que no calza** (parcial / pago de más). En Venezuela la tasa se mueve a diario → es **constante** que el monto en Bs no calce exacto. Hoy solo existe confirmar/rechazar; un pago de 99 cuando eran 100 no tiene salida limpia. Es el agujero operativo real del cobro en Bs.
3. **Tope de gasto del bot + anti-abuso.** Cada mensaje de IA cuesta. Un cliente en bucle o un troll con cientos de mensajes se come tu margen. Un límite que te avisa (y frena) protege tu bolsillo.

---

## 📦 Las 5 secciones de tu panel

### 1) 🛒 Ventas y Cobro
*El corazón que ya funciona — esta sección lo blinda y le suma palancas de venta.*
- 🟢 **Control de agotados en 1 clic** — un botón marca un producto como agotado; el bot deja de ofrecerlo. *(bajo)*
- 🟢 **Catálogo y precios editables desde el panel** — agregar/editar productos y precios; el bot toma el cambio al instante. *(bajo)*
- 🟢 **Panel de Tasa BCV con margen y candado manual** — ves la tasa, le sumas tu margen, y si la fuente falla la fijas a mano. *(bajo)*
- 🟢 **Manejo del pago que no calza** — marcar "pago parcial" o "pago de más"; el bot se lo dice al cliente con naturalidad. *(medio)*
- 🟢 **Delivery vs Retiro + costo de envío por zona** — el bot pregunta y suma el envío según tus zonas; total claro. *(medio)*
- 🟡 **Multi-método de pago** (Pago Móvil + Binance/USDT + Zelle + Efectivo) — ofreces lo que el cliente ya usa. *(medio)*
- 🟡 **Recibo simple del pedido** — al confirmar, el bot manda un recibo limpio (productos, total $ y Bs, método). *(bajo)*
- 🟡 **Catálogo con fotos** — el bot envía la foto del producto cuando preguntan cómo se ve. *(medio)*
- 🟡 **Recordatorio de pago pendiente** (dentro de 24h) — un recordatorio amable si no mandó el comprobante. *(medio)*
- ⚪ **Combos y promociones** — sube el ticket promedio. *(medio)*
- ⚪ **Pedido mínimo** y aviso de monto faltante. *(bajo)*

### 2) 👥 Clientes (CRM simple)
*Hoy un cliente es solo un teléfono suelto. Esto le da cara, contexto y memoria — reusando datos que YA se guardan.*
- 🟢 **Panel de Clientes** (lista y buscador) — todos tus clientes en una tabla buscable por nombre, gasto, última compra. *(medio)*
- 🟢 **Ficha del Cliente con historial** — desde cuándo compra, cuántos pedidos, cuánto gastó, qué ha comprado. *(medio)*
- 🟢 **Notas internas del cliente** — apuntes privados: "alérgica al maní", "pide sin azúcar". *(bajo)*
- 🟡 **Etiquetas de cliente** (Nuevo, Frecuente, VIP, Inactivo) — versión **simple** de las de Erwin, sin Chatwoot. *(medio)*
- 🟡 **Recordatorio de recompra** (sugerido, no automático) — para consumibles que se acaban. *(medio)*
- 🟡 **Saludo de cumpleaños** — te avisa para felicitarlo, con opción de detalle. *(bajo)*
- ⚪ **Tarjeta de fidelidad** (sellos) — cuando ya haya CRM básico. *(medio)*

### 3) 🎛️ Control del Bot
*Donde TÚ ganas el timón sin depender del técnico. La base reutilizable #1 para cualquier cliente futuro.*
- 🟢 **Encender / apagar el bot** (global o por horario) — el control de seguridad más pedido. *(bajo)*
- 🟢 **Datos de cobro y del negocio editables** — Pago Móvil, WhatsApp de avisos, ubicación, Instagram. *(bajo)*
- 🟢 **Personalidad del bot editable** — escribes en tus palabras cómo habla; el bot lo usa al instante (con el dinero blindado). *(medio)*
- 🟢 **Probar el bot (simulador)** — le escribes como cliente y ves cómo responde, sin gastar un mensaje real. *(medio)*
- 🟡 **Pausar el bot en un chat puntual** ("atiendo yo") — sin apagarlo para todos. *(medio)*
- 🟡 **Mensajes automáticos con guía editable** — ajustas la INTENCIÓN (bienvenida, pago confirmado/rechazado); el bot redacta natural. *(medio)*
- 🟡 **Conocimiento de productos (FAQ)** — cargas dudas típicas: "¿es libre de gluten?", alérgenos, beneficios. El bot responde sin inventar. *(medio)*
- 🟡 **Horario de atención** — el bot avisa con calidez fuera de horario. *(bajo)*

### 4) 📈 Crecimiento y Analítica
*Convierte los datos que ya se guardan en decisiones — con el motor seguro para reenganchar clientes de forma legal.*
- 🟢 **Motor de plantillas HSM** (fuera de 24h) — el ladrillo obligatorio para avisar fuera de la ventana de WhatsApp. *(medio)*
- 🟢 **Aviso a la dueña que SIEMPRE llega** — si llevas +24h sin escribirle al bot, te avisa por plantilla. Tapa el agujero más peligroso del cobro. *(medio)*
- 🟢 **Aprobación humana antes de cualquier envío proactivo** — regla dura de seguridad. *(bajo)*
- 🟢 **Reporte de ventas** (día/semana/mes) en $ y Bs. *(bajo)*
- 🟢 **Recuperación de pedidos sin pagar** — la venta más fácil de recuperar, hoy perdida en silencio. *(medio)*
- 🟡 **Alertas de pagos olvidados** — te avisa si un pago lleva +2h sin confirmar. *(bajo)*
- 🟡 **Productos más vendidos y ticket promedio** — qué reponer y qué promocionar. *(medio)*
- 🟡 **Campañas / difusión con plantilla** — "promo del fin de semana" a tus clientes. *(medio)*
- 🟡 **Reactivar clientes dormidos** — los que compraban y dejaron de hacerlo. *(medio)*
- ⚪ **Avisar productos nuevos a clientes interesados** — venta segmentada, no spam. *(medio)*
- ⚪ **Resumen diario a tu WhatsApp** — porque tú vives en WhatsApp, no en el panel. *(medio)*
- ⚪ **Hora pico de pedidos** — cuándo preparar más stock. *(bajo)*

### 5) 🛡️ Operación, Confianza y Tech Provider
*Protege el dinero, los datos y la reputación — y convierte "un bot para másvida" en una base repetible.*
- 🟢 **Respaldo automático de los datos** — copia diaria fuera del servidor. No opcional para una proveedora responsable. *(bajo)*
- 🟢 **Tope de gasto del bot + anti-abuso** — protección económica básica. *(medio)*
- 🟢 **Roles: dueña y empleado** — el empleado atiende pero NO confirma pagos ni ve datos bancarios. *(medio)*
- 🟡 **Sesiones seguras** (cierre por inactividad, cambiar contraseña). *(bajo)*
- 🟡 **Salud del negocio (semáforo)** — verde/rojo si WhatsApp se cae o la tasa falla. *(medio)*
- 🟡 **Bitácora de acciones** — quién confirmó qué pago (cuando haya empleado). *(medio)*
- ⚪ **Multi-negocio por número + aislamiento de datos** — el corazón Tech Provider: varios clientes sin cruzar mensajes. *(alto)*
- ⚪ **Onboarding de cliente nuevo por coexistencia** — alta de un negocio paso a paso, sin tocar código. *(alto)*

---

## 🚦 Orden de construcción recomendado

- **FASE 0 — Control inmediato** *(máximo valor, mínimo esfuerzo, cero riesgo; el backend ya existe)*: Catálogo y precios editables · Control de agotados · Datos de cobro y negocio editables · Reporte de ventas.
- **FASE 1 — Blindaje del dinero**: Tasa BCV con margen y candado · Pago que no calza · Respaldo automático · Tope de gasto.
- **FASE 2 — El timón de la dueña**: Encender/apagar el bot · Personalidad editable + Probar el bot.
- **FASE 3 — Conoce a tu cliente**: Panel de Clientes + Ficha + Notas internas.
- **FASE 4 — Motor de reenganche seguro**: Motor HSM + Aviso que siempre llega + Aprobación humana.
- **FASE 5 — Recuperar plata**: Recuperación de pedidos sin pagar · Alertas de pagos olvidados · Recordatorio de pago.
- **FASE 6 — Vender más a quien ya tienes**: Etiquetas · Más vendidos/ticket · Multi-método de pago · Delivery/envío · Recibo · Mensajes con guía editable · Conocimiento (FAQ) · Horario · Recompra · Campañas · Reactivar dormidos · Pausar bot por chat · Sesiones seguras.
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
