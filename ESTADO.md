# 📸 ESTADO — qué corre en cada servidor

> **Esta es la ÚNICA fuente de verdad sobre qué versión está viva en cada sitio.**
> El ROADMAP dice qué falta. SESIONES dice qué pasó. **Este archivo dice dónde estamos parados HOY.**
>
> ⚠️ Si este archivo y el ROADMAP se contradicen, **manda este archivo**.

---

## ✅ RESUELTO EL 2026-08-21 — el código de agosto está en GitHub

Del 2 al 21 de agosto, Erwin trabajó desplegando por `docker cp` con Coolify **desconectado a
propósito** (la rama `DESCONECTADO-2026-08-02`), porque sin acceso al repositorio el despliegue
automático le borraba sus cambios manuales. Ese código vivía SOLO en el servidor, sin respaldo.

**Se rescató:** se le dio acceso de colaborador (+ un token fine-grained a los 2 repos), y el
21-ago subió **32 commits del bot + 6 del panel** a `master`. Ya está todo en GitHub y respaldado.

Incluye: arreglos del dinero (DIN-2/4, la "banda ciega del 1%", args del LLM sin filtrar),
el modo DOS agentes con sus 3 bloqueadores cerrados, `/salud`, el `barredor`, redes de asesoría
y salud, memoria que no se olvida a las 24h, y 8 migraciones nuevas (hasta la 034).

---

## Última verificación: **2026-08-23 (17:40)**

| | 🏪 PRODUCCIÓN | 🧪 TALLER |
|---|---|---|
| **Servidor** | netcup `152.53.89.118` | Hostinger `2.25.139.106` |
| **Quién le escribe** | las clientas reales | número de la agencia: **+57 313 293 3806** |
| **Bot: versión** | `7e80b8a` (14-jul) — **muy atrasada** | ✅ **`c5ba1c4`** (23-ago), al día con `master`, desplegado solo por el push. **SHA de la imagen verificado en bot Y worker** — no el color del run (L59) |
| **Panel: versión** | (sin tocar desde julio) | ✅ **`b9a97c8`** — al día con `master` (sin commits nuevos desde el 22-ago). **Estaba 6 commits atrasado** (corría `d34ccd9`, de 13 días) y nadie lo había notado porque este archivo no listaba el panel |
| **Modelo IA activo** | (el de julio) | ✅ **`anthropic/claude-haiku-4.5`** (devuelto el 21-ago por decisión de Erwin; estuvo en `gpt-4o-mini` del 18 al 21-ago) |
| **Modo del agente** | UN agente | los 3 bloqueadores del modo DOS ya están cerrados (06-ago) |
| **Lista blanca** | ✅ activa | ✅ activa — **4 números** (verificado 23-ago): 2 en `NUMEROS_PERMITIDOS` (env: `584264399792` Maired · `573005690062` `dueno_telefono`) + 2 en `numeros_permitidos_extra` (BD) |
| **Bot en el mercado** | ❌ NO — apagado para clientas reales hasta la entrega | pruebas |

### 🔴 2026-08-23 — EL SALDO DE IA CRUZÓ EL UMBRAL: `/salud` está en `degradado`

**`/salud` no dice `ok`. Dice `degradado`, con `fallos: ["saldo_ia"]`.** El saldo de OpenRouter está
en **$1.70** y el umbral de la sonda es $2.00. A ~$0.016 por turno son **~106 turnos**, y cuando se
agote el bot **no da error: deja de responder**. Desde el chat es idéntico a "el bot está roto".
**Solo Erwin puede recargarlo** (openrouter.ai → Credits). Es lo único que puede arruinar una demo.

### 🟢 2026-08-23 — la auditoría exhaustiva, el entorno local y la causa raíz del "bot bruto"

7 commits más desplegados: `3d88cd1..c5ba1c4`. Lo verificado en vivo el 23-ago a las 17:40:

| | |
|---|---|
| Bot y worker | 🟢 **`c5ba1c4`** en el SHA de la imagen de los DOS contenedores |
| CI · paso `desplegar` · LOS BANCOS | 🟢 verdes en los **2 últimos push** (y producción `skipped`) |
| Tests | 🟢 **645** (eran 566) |
| Bancos | 🟢 **27/27** en el VPS · **24/27 en LOCAL** con `./banco_local.sh`, antes de desplegar |
| `/salud` | 🔴 **`degradado`** por el saldo · Postgres, Redis, Meta (**GREEN**), barredor, tasa y modelo en `ok` |
| Datos | 🟢 32 productos / 37 variantes / 34 media / 10 conocimiento / 35 migraciones |
| **`dias_anticipacion`** | 🟢 **CARGADO**: 16 en 0 · 12 en 1 · 4 en 2 *(estaba en 0 en los 32)* |
| **Zona centro** | 🟢 **$2.00** *(cobraba $3; la plantilla pide $2)* · oeste $5 · retiro $0 |

**Lo que cambió para la clienta:** a quien pide **"vegano"** ya no se le ofrece manteca de cochino
ni hígado deshidratado (freno de seguridad alimentaria) · la red de la salud caza el celíaco en sus
4 formas, no en 1 · el **recibo ya no sale duplicado** (lo insertaba el CÓDIGO, no el modelo:
comparaba texto literal y el modelo parafraseaba) · el cobro trae **desglose de 4 líneas** · y el
bot **ESPERA el clic de «Pago aprobado»** antes de coordinar la entrega, avisando a la dueña
(pasos 8-9 de la plantilla — ya reflejado en `CLAUDE.md` §3).

🔴 **EL TRABAJO #1 DE LA PRÓXIMA SESIÓN, y lo dejó pedido Erwin: PULIR EL PROMPT.** La causa de que
el bot "repita y suene robótico" no es un bug: son **44 reglas escritas por acumulación, varias
contradiciéndose**, sobre **Haiku 4.5** (el modelo más pequeño). Medido en vivo: **60.390 caracteres
(~15.100 tokens)** — y sigue creciendo (eran 59.381 ayer). Método y los 5 frenos que hay que
respetar: `prompt_proxima_sesion.md` §5 → P-PROMPT.

🟡 **Y un detalle que conviene decidir antes de la próxima prueba con Maired:** su chat **ya no está
en cero**. En la BD hay **58 mensajes, 4 pedidos y 6 clientes** (5 son `__simulador__`, 1 es ella)
de las pruebas del 22/23-ago. Si quiere volver a probar como clienta nueva, hay que limpiarlo otra
vez — con ensayo de ROLLBACK, y **acordándose de Redis** (`hist:`, `abuso:`, `cobro:`).

### 🟢 2026-08-22 (noche) — la plantilla de negocio de Maired, aplicada y viva

Los **9 commits** de la plantilla están desplegados y verificados: `a524822..15fb81f`.

| | |
|---|---|
| CI (`ruff` · `compileall` · `pytest`) | 🟢 **566 tests** verdes |
| Deploy del taller | 🟢 automático por el push · **producción `skipped`** (el candado funciona) |
| **LOS BANCOS** | 🟢 **27/27**, corridos por el vigilante tras el deploy |
| Checksum `master` vs los DOS contenedores | 🟢 **6/6 bit a bit** |
| `/salud` | 🟢 `ok`, `fallos: []` |
| Datos | 🟢 32 productos / 37 variantes / 34 media / 10 conocimiento / 35 migraciones — **sin cambios** |

**Qué cambió para la clienta:** el 20% + **delivery gratis** pagando en efectivo (y ya NO a Zelle
ni Binance) · el bot **consulta el calendario** en vez de inventarse las fechas · ya no dice que
revisó una cuenta que no tiene · saluda devolviendo la pregunta · no repite la ficha · las
alergias salen de la ficha del producto, nunca de una promesa general · el pie de foto ya no
lleva la lista de ingredientes. En la BD: **asesora** (no "asistente") y ya no devuelve el
"mi amor".

🔴 **Y DOS COSAS QUE SE DESCUBRIERON DESPLEGANDO, y conviene no olvidar:**

1. **El deploy de `a524822` (sesión de la tarde) había FALLADO en silencio.** El runner no pudo
   alcanzar el puerto 8000 de Coolify (`curl: (28) Timeout`, tres reintentos) porque Coolify
   estaba reiniciándose por el deploy anterior. **La CI salió verde, el paso TALLER en rojo, y
   nadie lo miró**: el taller se quedó con el código viejo mientras todo el mundo creía que
   estaba al día. Es el panel de los 13 días otra vez — *un automatismo que falla en silencio es
   peor que no tenerlo*. **Mirar el paso `desplegar`, no solo la CI.**
2. **Los bancos hacen su trabajo.** Salieron 2 rojos en el primer deploy, los dos míos: el conteo
   de tools (12 → 13) y —el bueno— `probar_carril_dinero`, que **fijaba la frase de la mentira**
   ("NO te aparece hecho a TU cuenta"). El banco defendía el bug. Reescrito para comprobar la
   intención, no el texto.

### 🔴 Lo que importa

**Producción sigue en la versión del 14-jul.** Todo el trabajo de agosto (y de fin de julio) está
en GitHub pero **aún no promovido a producción**. El bot NO atiende clientas reales todavía: es una
decisión deliberada hasta que esté listo para la entrega.

**🟢 RESUELTO EL 2026-08-22 (tarde): el taller ya está al día con `master`.** Los 5 commits que
estaban atascados en local (la CI arreglada · los dos huecos de la red del cierre · la RED DEL
TAMAÑO ADIVINADO del carril del dinero · el tercer caso del espejeo y los dos huecos que
destaparon las conversaciones reales) **se subieron y se desplegaron**: checksum 5/5, **27/27
bancos verdes**, `/salud` en `ok` y cero regresión de datos reales. Y desde `0426f3b` el
despliegue del taller **es automático en cada push** — el taller corre hoy **`aef1042`**.

**🔴 Lo que se descubrió el 2026-08-22 (2) y conviene no olvidar:** la **CI llevaba 3 commits en
ROJO** (`09f4253`, `13a064f`, `6c5d14c`) por 4 errores de `ruff`, y como `ruff` es el PRIMER paso
del job, `compileall` y `pytest` quedaban **skipped**: los 453 tests **no se ejecutaron ni una vez**
en esos tres commits. Ya está verde. Si el CI sale rojo, **no es cosmético: apaga la única puerta
que valida antes de desplegar.**

---

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
| **El bot no contesta** | 1. Abre `api-masvida.enovagroup.tech/salud` → ¿todo `ok`? ¿`saldo_usd` > 0? · 2. Panel → ¿`bot_activo` encendido? · 3. ¿Ese número está en la lista blanca (son 3)? · 4. ¿Ese chat está pausado (bandeja / "atiendo yo")? |
| **Se acabó el saldo de IA** | `/salud` → `saldo_ia`. Recargar en OpenRouter. Sin saldo el bot NO responde. |
| **Contesta raro o inventa** | 1. Panel → Configuración → ¿qué MODELO está activo? (hoy `anthropic/claude-haiku-4.5`) · 2. ¿Alguien editó la Personalidad? (vive en la BD) · 3. NO culpar al modelo primero: sospecha del código/datos. |
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
