# 📸 ESTADO — qué corre en cada servidor

> **Esta es la ÚNICA fuente de verdad sobre qué versión está viva en cada sitio.**
> El ROADMAP dice qué falta. SESIONES dice qué pasó. **Este archivo dice dónde estamos parados HOY.**
>
> ⚠️ Si este archivo y el ROADMAP se contradicen, **manda este archivo**.

---

## ✅ 2026-09-16 — REGLA OFICIAL DEL DELIVERY EN PRODUCCIÓN

PR #57 fusionado en `master` `b6d755d` y desplegado manualmente por Actions (run
`35095865528`). Bot y worker corren la imagen completa del commit. La puerta salió verde (`ruff`,
`compileall`, suite completa) y los dos detectores de esquema pasaron dentro del contenedor nuevo.
`/salud`: `ok`, fallos `[]`, Postgres y Redis `ok`, Meta `GREEN`, 39 migraciones.

Whuilianny aclaró la fórmula: al pagar en dólares se suman productos + delivery y después se
descuenta 20% a la cuenta completa. Ejemplo oficial de la revisión: $18 + $2 = $20; menos 20% =
**$16**. El código, el desglose, la revisión del comprobante y las pruebas usan la misma regla. Las
cotizaciones ya entregadas conservan su monto congelado para no cambiarle la cuenta a una clienta
que esté por pagar.

La personalidad viva de producción se cambió por la puerta de la API después de ensayar la misma
operación con `ROLLBACK`: md5 `a9aaafe61353` → `4e2eab0b5710`, `coincide=True`. Verificación final:
regla nueva presente, frase vieja ausente. No se enviaron mensajes de prueba al número real.

## ✅ 2026-09-15 — AUTORÍA HUMANA, EVENTOS SILENCIOSOS Y RELEVO DE AUDIO EN PRODUCCIÓN

PR #55 fusionado en `master` `ee4f7af` y desplegado manualmente por Actions (run
`35038049114`). Bot y worker corren la imagen completa `ee4f7afd5053ae52b18bb94640ffa9ddb6d4ad43`.
La puerta salió verde (`ruff`, `compileall`, suite completa) y los dos detectores de esquema
pasaron dentro del contenedor nuevo. `/salud`: `ok`, fallos `[]`, Postgres y Redis `ok`, 39
migraciones, Meta `GREEN`.

El cambio conserva la autoría de los mensajes humanos en la memoria, guarda sin contestar
`reaction`/`edit`/`revoke`, y abre relevo ante una avería técnica o dos fallos consecutivos de
audio. No cambió precios, descuentos, delivery, catálogo ni aprobación de pagos. No se enviaron
mensajes de prueba al número de producción.

## 🏭 EL ENTORNO DE PRUEBAS DE ENOVA — nació el 2026-09-01 (noche) y RESPONDE

> **El reemplazo real del taller** (SESIONES 1-sep (14)): mismo código, misma BD rescatada,
> mismo número de la agencia — pero en infraestructura PROPIA de Enova, aislado de netcup.
> Whuilianny no ve nada: servidor, BD, número y WABA separados de producción (verificado).

| | 🏭 PRUEBAS (Enova) |
|---|---|
| **Servidor** | VPS del socio de Enova `152.53.194.89` · Coolify propio `coolify.enovagroup.tech` (proyecto `masvida-pruebas`) |
| **Qué corre** | bot + worker en `master f60c3f7` (hasta el PR #41, fusionado 6-sep 21:32 VET) + panel `d8b94ab` (PR #4) + PostgreSQL 16 + Redis 7 · **auto-deploy OFF** en las 3 apps — deploy SOLO manual (API de Coolify con token temporal: ver `entorno-pruebas-vps-enova` en la memoria de Claude / SESIONES (15), (16) y (18)) |
| **Número** | **+57 313 2933806** (WABA "Enova Soporte", SEPARADA de la de la clienta) |
| **Webhook** | re-apuntado por Graph API (`/{waba}/subscribed_apps` + `override_callback_uri`) → `https://jthc51nxqitd9opc8ywioocr.152.53.194.89.sslip.io/webhook/whatsapp` |
| **BD** | dump FINAL del taller restaurado el 1-sep (36 migraciones entonces); hoy **39 migraciones** (038 franja + referencia) · 32 productos · 10 conocimiento · `sabores` cargados en Galletas / Mini / CHOCOLATE (6-sep) |
| **Modelo IA** | `anthropic/claude-sonnet-4.6` (el aprobado por Maired en el taller) |
| **Salud** | `https://jthc51nxqitd9opc8ywioocr.152.53.194.89.sslip.io/salud` — snapshot del 3-sep tras `f85f781`: `ok`, Meta GREEN, 37 migraciones, saldo $2.552 · **tras `f60c3f7` (6-sep): `ok`, fallos `[]`, 39 migraciones** |
| **Prueba de fuego** | ✅ "Hola" de Maired → respuesta en 5,8s **con la memoria del taller** (13 mensajes rescatados de Postgres) |
| **Panel** | ✅ **`https://panel-masvida.enovagroup.tech/login`** (dominio propio + HTTPS Let's Encrypt). `d8b94ab` (PR #4: Franjas de entrega en Horario + franja/referencia en el pedido; trae la ★ del #1, el ojito del #2 y las contraseñas del #3). Login: `admin@masvidaconsciente.com` + la clave unificada con producción (6-sep). |
| **DNS (Namecheap)** | `panel-masvida` y `api-masvida` .enovagroup.tech → `152.53.194.89` (los creó Maired el 2-sep). ⚠️ `api-masvida` apunta al VPS pero **ningún servicio lo atiende todavía** — ver el bug del catálogo abajo. |

> ✅ **CATÁLOGO PDF ARREGLADO EN PRUEBAS (3-sep 12:35 ET; SESIONES (18)):** PR #18 fusionado y
> desplegado (`a798aac`) en bot + worker; `PUBLIC_BASE_URL` propia del entorno, creada cifrada por
> la API de Coolify. El archivo público responde **200 · `application/pdf` · 2.803.311 bytes ·
> `%PDF-`** y `/salud` sigue en `ok`. ⏳ Falta confirmar el último tramo pidiéndolo por WhatsApp al
> número de pruebas. ✅ **La mina de producción quedó desactivada el 5-sep**: `PUBLIC_BASE_URL`
> propia, cifrada, y deploy completo (ver "Última verificación" abajo).
> **NO asignar `api-masvida.enovagroup.tech` al bot de pruebas**: mezclaría los entornos.

> ✅ **FOTO PRINCIPAL + VIDEOS CERRADOS EN PRUEBAS (3-sep; SESIONES (20)):** bot y worker
> `f85f781`, panel `c8bf95d`, migración 036 aplicada (37 totales), `probar_migraciones`,
> `probar_drift` y `probar_media` verdes. La ★ encabeza el `ORDER BY` real con ROLLBACK. Los 5
> QuickTime disfrazados se respaldaron y convirtieron a MP4 ISO; una segunda corrida dejó los 5
> sin tocar. Queda pedir un video real de Tortas keto: el archivo ya es compatible, pero su
> contenido sigue siendo un solo cuadro con audio. ✅ **Producción alcanzó todo esto el 5-sep.**

## Última verificación: **2026-09-07 (~20:05 VET) — 🚀 SEGUNDO LOTE EN PRODUCCIÓN: LOS DOS ENTORNOS EN master `b48d2e8` (la dueña en silencio · relevo por nombre · "Restaurar original" = Alejandra)**

> ✅ Tras el OK de Maired en pruebas ("¿Whuilianny ya no atiende? Yo siempre le compro a ella" →
> *"Whuilianny te escribe en un momento"* + relevo, y el bot calló después): `workflow_dispatch
> destino=produccion` (run 34171705956, verde) → bot y worker en `b48d2e8` · `/salud` ok · 39
> migraciones · **27/27 bancos** (`/root/bancos_lote2_7sep.log`) · prompt vivo con **0 "dueña"** en
> reglas, dinámico y herramientas · "Restaurar original" del panel = "Eres Alejandra, la asesora…".
> Panel sin cambios (`60f8b4d`); personalidad (md5 `a9aaafe61353`) y sabores ya estaban.
> PRUEBAS en el mismo `b48d2e8` (27/27). Detalle: SESIONES (29).

## Verificación anterior: **2026-09-07 (~19:05 VET) — 🚀 PRODUCCIÓN PROMOVIDA: LOS DOS ENTORNOS EN master `27f50ac` + panel `60f8b4d` · voz nueva · sabores · lista blanca con 3 clientes**

> ✅ **Liturgia completa (SESIONES (29)):** respaldo `pg_dump` en netcup
> (`/root/respaldos-pre-promocion/db_20260907_224159.sql.gz`, 3,9 MB, 19 tablas) + personalidad
> vieja (`personalidad_vieja_20260907_224317.txt`, md5 `e9e1349b6a66`; copia local en
> `respaldos-masvida/personalidad_PRODUCCION_vieja_2026-09-07.txt`) → `workflow_dispatch
> destino=produccion` (el primer intento se frenó en la puerta: ruff 0.9.6 del CI, UP038 → PR #47;
> el segundo, run 34168204704, verde) → bot `y20mosanb19cw8ukso56hv7e` y worker
> `hrkrh8f9buora7aqxt8rsbna` en `27f50ac` · `/salud` ok · **39 migraciones** → personalidad nueva
> por `promover_personalidad.py` (md5 `a9aaafe61353`, coincide=True) → `promover_sabores.py`
> hechos=3 → **27/27 bancos** (`/root/bancos_promocion_7sep.log`) → panel `o1jo590exxeuco5s8j0arisy`
> en `60f8b4d` por la API de Coolify (login 200) → prompt vivo: 0 "confirma la dueña" / "según su
> ruta" / "ella es la dueña" / "por nuestra cuenta"; $14 + $3 en dólares = **$14.20** (PR #46).
>
> 🔓 **Lista blanca de producción:** `NUMEROS_PERMITIDOS=573005690062` (entorno) +
> `numeros_permitidos_extra` = los 3 primeros clientes de Whuilianny (upsert en `configuracion`,
> porque la clave no estaba en `CLAVES_CONFIG`; el PR `la-duena-en-silencio` la agrega para
> editarla por la API). **Decisión de Maired:** soltar gradual, 3-4 clientes al día, leyendo cada
> conversación; `todos` cuando haya evidencia. A los demás números el bot les guarda el mensaje y
> calla; Whuilianny los atiende desde su celular (coexistencia).
>
> ⏳ **Pendientes:** PR `la-duena-en-silencio` (45 menciones a "la dueña" en lo que lee el modelo →
> primera persona del negocio + red de código) → pruebas → prod · 3 fotos rotas en PRUEBAS por el
> balde R2 compartido (media 9, 33, 34: Galletas NY, Caldo de Huesos) → decidir separar el balde ·
> paso 7: Whuilianny revisa franjas en Horario y recibe su clave del panel · adelgazar el prompt.

## ⚠️ CÓMO SE DESPLIEGA HOY (cambió OTRA VEZ el 2026-08-22 — leer antes de tocar nada)

🟢 **UN PUSH A `master` DESPLIEGA EL TALLER SOLO, si la CI está VERDE.** Lo pidió Erwin el
22-ago y **reemplaza su decisión del 2-ago** ("ningún push despliega nada"). Va por **GitHub
Actions**, no por el webhook de Coolify: el job `desplegar` lleva `needs: verificar`, así que con
`ruff`/`compileall`/`pytest` en rojo el `curl` a Coolify **no llega a existir** (lección L41).

🔒 **PRODUCCIÓN SIGUE SIENDO SOLO A MANO.** En un `push` el destino se **fuerza** a `taller`
(`env.DESTINO` en `deploy.yml`); `produccion` solo sale de un `workflow_dispatch` que un humano
lanzó y eligió. Un push no puede tocar a las clientas reales ni por accidente.

⚠️ **`is_auto_deploy_enabled` sigue en `false` en las 3 apps, y así se queda.** No es un olvido:
el webhook de Coolify dispara al recibir el push **sin esperar a la CI**, así que encenderlo
dejaría dos despliegues compitiendo por el mismo push y uno de ellos sin puerta. El automatismo
vive en `deploy.yml`, no en Coolify.

✅ **COOLIFY RECONECTADO EL 2026-08-22** (lo pidió Erwin). Las 3 apps volvieron a `git_branch =
'master'`.

*(Estado anterior guardado en el VPS: `/root/COOLIFY_ANTES_2026-08-21.csv`.)*

⚠️ Coolify reconstruye desde GitHub: todo archivo editado a mano DENTRO del VPS se pierde en el
siguiente despliegue. **Nada se edita en el servidor** (regla dura de CLAUDE.md §3).

---

## 🧯 Si algo falla, revisa esto EN ORDEN (antes de preguntarle a nadie)

| Síntoma | Revisa, en orden |
|---|---|
| **El bot no contesta** | 1. Abre el `/salud` del entorno (pruebas: la URL del bloque 🏭 de arriba; producción: `api.masvidaconsciente.store/salud`) → ¿todo `ok`? ¿`saldo_usd` > 0? · 2. Panel → ¿`bot_activo` encendido? · 3. ¿Ese número está en la lista blanca? · 4. ¿Ese chat está pausado (bandeja / "atiendo yo")? *(La URL vieja `api-masvida.enovagroup.tech` murió con el taller.)* |
| **Se acabó el saldo de IA** | `/salud` → `saldo_ia`. Recargar en OpenRouter. Sin saldo el bot NO responde. |
| **Contesta raro o inventa** | 1. Panel → Configuración → ¿qué MODELO está activo? (hoy `anthropic/claude-sonnet-4.6` en los dos entornos; la fuente es la fila "Modelo IA activo" de las tablas de arriba) · 2. ¿Alguien editó la Personalidad? (vive en la BD) · 3. NO culpar al modelo primero: sospecha del código/datos. |
| **Cobro o precio mal** | Verificar **en la BD**, no en el chat: `SELECT items, total FROM pedidos`. |
| **Hice push y no pasó nada** | Es lo esperado: el deploy es a mano desde el 2-ago. Hay que lanzarlo desde Coolify/Actions. |
| **El panel no carga datos** | ¿El build tiene `NEXT_PUBLIC_API_URL`? (sin eso: "Failed to fetch"). |

---

## Cómo verificarlo tú misma (30 segundos)

```bash
gh run list --limit 10          # qué se desplegó y cuándo
git log origin/master -5         # últimos cambios en GitHub
```

---

## Historial de verificaciones

| Fecha | Producción | Taller | Nota |
|---|---|---|---|
| 2026-08-23 | `7e80b8a` (14-jul) | **`c5ba1c4`** | 🟢 **7 commits más, desplegados solos por el push** (`3d88cd1..c5ba1c4`). **645 tests** (eran 566) · **27/27 bancos** en el VPS y **24/27 en LOCAL** con el nuevo `banco_local.sh` —los bancos por fin corren ANTES de desplegar, y en su primera corrida cazaron 2 bugs que el VPS no había visto. La auditoría exhaustiva sacó **200 requisitos** del documento de Maired (una revisión a mano previa había sacado 51), y **3 de los 4 fallos graves eran de arreglos de ESE MISMO DÍA**: código nuevo, con tests en verde, que no hacía lo que decía (L65). El más grave: a quien pedía **"vegano"** el bot le ofrecía **manteca de cochino e hígado deshidratado** (L63). **DATOS cargados:** `dias_anticipacion` (16/12/4, estaba en 0 en los 32) y zona centro **$3 → $2**. 🔴 **`/salud` en `degradado`: el saldo de OpenRouter cayó a $1.70** (umbral $2.00, ~106 turnos). 🔴 **Pendiente #1:** pulir el prompt — **60.390 car / 44 reglas contradictorias sobre Haiku 4.5** es la causa raíz del "bot bruto" (L68). |
| 2026-08-22 (tarde) | `7e80b8a` (14-jul) | **`aef1042`** | 🟢 **Los 5 commits atascados: subidos y desplegados.** Push con el token de Erwin (`c0a2f71`, CI **verde**) → deploy por la API de Coolify (worker primero, bot después). **Checksum 5/5** en los DOS contenedores · **27/27 bancos verdes** corridos uno por uno · `/salud` `ok` con `fallos: []` · **cero regresión de datos reales** (32 productos / 37 variantes / 2 pedidos / 34 media / 10 conocimiento / 35 migraciones, idéntico antes y después). ⚠️ `clientes` pasó de 2 a 3, y **es por diseño**: los bancos crean el cliente de prueba `__simulador__` (excluido de la lista del panel) — no es una regresión, pero **por eso `clientes` no sirve como métrica de línea base después de correr bancos.** Y **el despliegue del taller pasó a ser AUTOMÁTICO en cada push** (`0426f3b`), con la CI como puerta y producción todavía solo a mano; validado dos veces seguidas (`0426f3b` y `aef1042`). |
| 2026-08-18 | `7e80b8a` (14-jul) | desconectado de GitHub | Descubierto: Coolify en rama `DESCONECTADO`, código de agosto solo en el servidor. |
| 2026-08-21 | `7e80b8a` (14-jul) | código de agosto (en GitHub) | Rescatados 32+6 commits a `master`. Deploy ahora es manual. Falta reconectar y promover a producción. |
| 2026-08-22 (2 y 3) | `7e80b8a` (14-jul) | `13a064f` (**master va 5 commits por delante, SIN desplegar**) | 🔴 **La CI llevaba 3 commits en ROJO y los 453 tests no corrían** (4 errores de `ruff`; `pytest` quedaba *skipped*) — arreglado. Cerrados los 2 huecos de la red del cierre (la HORA + la lista y la pregunta en frases distintas), el 3er sitio que empujaba a pedir el sabor (el schema de `opciones`), **la RED DEL TAMAÑO ADIVINADO** (P0.5, carril del dinero), y —cruzando los dos documentos de Whuilianny con el código— el **tercer caso del espejeo (cliente MOLESTO)**, las peticiones **sin signo de pregunta** y `asesorar`. **515 tests** (eran 453) · **18 reversiones → 18 rojas** · cero cambios en la BD. |
| 2026-08-22 | `7e80b8a` (14-jul) | **`13a064f`** | 🟢 **Coolify RECONECTADO** (rama `master`, auto-deploy OFF) y primer despliegue por Coolify desde julio. Cola de media (el texto sale antes que la foto) + red del cierre. **27/27 bancos verdes** corridos uno por uno · 453 tests · `/salud` 8/8 · checksum 5/5 · **cero regresión de datos** (32/37/2/34/10/2/35 idéntico antes y después). |
| 2026-08-21 (2) | `7e80b8a` (14-jul) | **`4a482c5`** | 9 bugs cerrados y desplegados ese día (memoria de 24h + su puerta de atrás, índice duplicado, 2 bancos del calendario, la banda ciega del 1%, args del LLM sin filtrar, saludo al volver, y 3 cegueras de las fotos). Modelo devuelto a **Haiku 4.5**. Nº de Maired añadido a la lista blanca. 421 tests · `/salud` 8/8. |

---

*Actualizar este archivo cada vez que se despliegue a producción. Es corto a propósito:
si crece, deja de leerse — y entonces vuelve a no servir para nada.*

---
> 🗃️ La historia (taller, verificaciones anteriores) está en `archivo/ESTADO-historia.md`.
