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

## 2026-06-24 (tarde) — Voz: puerta de saludo + decisión anti-sobreguión · Prompt caching · Editar cliente/pedidos

**1) Voz / saludo (bot `7e54049` + `5fad1fe`):** red de seguridad EN CÓDIGO (`_asegurar_saludo` en agent.py) que, SOLO al inicio de la conversación, garantiza que si el cliente saluda y/o pregunta "¿cómo estás?" el bot devuelva el saludo + "Muy bien, gracias a Dios" (con nombre + franja horaria VE). Es la "puerta/gate" determinista que mencionaba su amigo — sin agente extra ni costo.
- **Decisión clave (de Maired):** NO sobre-guionar la conversación. La puerta queda como **respaldo invisible** (solo actúa si el modelo falla); con un buen modelo no se activa → el bot responde natural y autónomo. Lo único que se BLINDA en código es lo crítico (**dinero, no inventar**). La conversación = libertad del modelo + la personalidad como guía. Maired cambió a un buen modelo y respondió natural → el problema era el MODELO, no faltar reglas. Ver memoria `no-sobreguionar-conversacion-bot`.

**2) Prompt caching (bot `0a640c0`):** `construir_partes_prompt` separa el prompt en ESTABLE (personalidad+reglas+catálogo+índice conocimiento) y DINÁMICO (hora, estado, ficha). El bloque estable se marca `cache_control: ephemeral` → OpenRouter lo cobra a **¼** en los mensajes siguientes. **Misma calidad (mismo texto al modelo), ~mitad de costo.** Aplicado en `agent.responder` y `redactar_mensaje`. Modelo activo: **Haiku 4.5** (~$10–25/mes a volumen real CON caché; $1/M entrada, $5/M salida). `construir_system_prompt` queda como wrapper de compatibilidad.

**3) Editar/borrar cliente + editar items de pedido (bot `d266f00` + dashboard `532b3fc`):**
- `PUT /clientes/{tel}` (editar nombre/notas) · `DELETE /clientes/{tel}` (resetea cliente: ficha + pedidos sin cobro + mensajes + memoria Redis). UI Clientes: nombre editable + "Guardar cambios" + botón "Borrar cliente" (con confirmación y aviso del blindaje).
- `PUT /pedidos/{id}/items` (corrige items/cantidades; recalcula el total desde el catálogo con `_buscar_producto`, **nunca inventa**). UI Pedidos: botón "Editar" → editor con selector del catálogo + cantidad + agregar/quitar + total estimado en vivo.
- **BLINDAJE de cobro (igual que borrar pedido):** NO se borra un cliente ni se editan items si hay pago confirmado/parcial/reportado. El dinero nunca se borra/altera en silencio.
- compileall (bot) + `tsc --noEmit` (dashboard) OK.

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
