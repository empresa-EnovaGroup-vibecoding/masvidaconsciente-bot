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

## Última verificación: **2026-08-21**

| | 🏪 PRODUCCIÓN | 🧪 TALLER |
|---|---|---|
| **Servidor** | netcup `152.53.89.118` | Hostinger `2.25.139.106` |
| **Quién le escribe** | las clientas reales | número de la agencia: **+57 313 293 3806** |
| **Bot: versión** | `7e80b8a` (14-jul) — **muy atrasada** | ✅ **`13a064f`** (22-ago), desplegado **por Coolify desde GitHub** (ya no por `docker cp`) y con checksum 5/5 contra `master` |
| **Modelo IA activo** | (el de julio) | ✅ **`anthropic/claude-haiku-4.5`** (devuelto el 21-ago por decisión de Erwin; estuvo en `gpt-4o-mini` del 18 al 21-ago) |
| **Modo del agente** | UN agente | los 3 bloqueadores del modo DOS ya están cerrados (06-ago) |
| **Lista blanca** | ✅ activa | ✅ activa — 3 números (`NUMEROS_PERMITIDOS` + `numeros_permitidos_extra`) |
| **Bot en el mercado** | ❌ NO — apagado para clientas reales hasta la entrega | pruebas |

### 🔴 Lo que importa

**Producción sigue en la versión del 14-jul.** Todo el trabajo de agosto (y de fin de julio) está
en GitHub pero **aún no promovido a producción**. El bot NO atiende clientas reales todavía: es una
decisión deliberada hasta que esté listo para la entrega.

**🟡 Y el TALLER también va por detrás de `master` desde el 2026-08-22 (2):** el taller corre
`13a064f`; encima hay **5 commits sin desplegar** (la CI arreglada · los dos huecos de la red del
cierre · la RED DEL TAMAÑO ADIVINADO del carril del dinero · el tercer caso del espejeo y los dos
huecos que destaparon las conversaciones reales). **Nada de eso está vivo todavía** — hace falta
`./subir_a_enova.sh <TOKEN>` (el push necesita el token de Erwin) y después un deploy a mano
(§ "cómo se despliega hoy" y `SESIONES.md` 2026-08-22).

**🔴 Lo que se descubrió el 2026-08-22 (2) y conviene no olvidar:** la **CI llevaba 3 commits en
ROJO** (`09f4253`, `13a064f`, `6c5d14c`) por 4 errores de `ruff`, y como `ruff` es el PRIMER paso
del job, `compileall` y `pytest` quedaban **skipped**: los 453 tests **no se ejecutaron ni una vez**
en esos tres commits. Ya está verde. Si el CI sale rojo, **no es cosmético: apaga la única puerta
que valida antes de desplegar.**

---

## ⚠️ CÓMO SE DESPLIEGA HOY (cambió — leer antes de tocar Coolify)

Erwin cambió el despliegue el 2-ago (commit `f0429db` del panel): **ningún push despliega solo.
El deploy es SIEMPRE a mano.** Antes un push a `master` reconstruía el taller; ya no.

✅ **COOLIFY RECONECTADO EL 2026-08-22** (lo pidió Erwin). Las 3 apps volvieron a `git_branch =
'master'`, y **`is_auto_deploy_enabled` se puso en `false` en las tres** para NO revertir la
decisión del 2-ago: el deploy sigue siendo SIEMPRE a mano. Si alguna vez se quiere push→taller
otra vez, es un `UPDATE application_settings SET is_auto_deploy_enabled=true`.

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
| 2026-08-18 | `7e80b8a` (14-jul) | desconectado de GitHub | Descubierto: Coolify en rama `DESCONECTADO`, código de agosto solo en el servidor. |
| 2026-08-21 | `7e80b8a` (14-jul) | código de agosto (en GitHub) | Rescatados 32+6 commits a `master`. Deploy ahora es manual. Falta reconectar y promover a producción. |
| 2026-08-22 (2 y 3) | `7e80b8a` (14-jul) | `13a064f` (**master va 5 commits por delante, SIN desplegar**) | 🔴 **La CI llevaba 3 commits en ROJO y los 453 tests no corrían** (4 errores de `ruff`; `pytest` quedaba *skipped*) — arreglado. Cerrados los 2 huecos de la red del cierre (la HORA + la lista y la pregunta en frases distintas), el 3er sitio que empujaba a pedir el sabor (el schema de `opciones`), **la RED DEL TAMAÑO ADIVINADO** (P0.5, carril del dinero), y —cruzando los dos documentos de Whuilianny con el código— el **tercer caso del espejeo (cliente MOLESTO)**, las peticiones **sin signo de pregunta** y `asesorar`. **515 tests** (eran 453) · **18 reversiones → 18 rojas** · cero cambios en la BD. |
| 2026-08-22 | `7e80b8a` (14-jul) | **`13a064f`** | 🟢 **Coolify RECONECTADO** (rama `master`, auto-deploy OFF) y primer despliegue por Coolify desde julio. Cola de media (el texto sale antes que la foto) + red del cierre. **27/27 bancos verdes** corridos uno por uno · 453 tests · `/salud` 8/8 · checksum 5/5 · **cero regresión de datos** (32/37/2/34/10/2/35 idéntico antes y después). |
| 2026-08-21 (2) | `7e80b8a` (14-jul) | **`4a482c5`** | 9 bugs cerrados y desplegados ese día (memoria de 24h + su puerta de atrás, índice duplicado, 2 bancos del calendario, la banda ciega del 1%, args del LLM sin filtrar, saludo al volver, y 3 cegueras de las fotos). Modelo devuelto a **Haiku 4.5**. Nº de Maired añadido a la lista blanca. 421 tests · `/salud` 8/8. |

---

*Actualizar este archivo cada vez que se despliegue a producción. Es corto a propósito:
si crece, deja de leerse — y entonces vuelve a no servir para nada.*
