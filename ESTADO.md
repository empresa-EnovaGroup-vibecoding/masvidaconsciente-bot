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
| **Bot: versión** | `7e80b8a` (14-jul) — **muy atrasada** | la de agosto (por confirmar el commit exacto tras reconectar) |
| **Modelo IA activo** | (el de julio) | 🔴 **`gpt-4o-mini`** (cambiado el 18-ago; antes Claude Haiku) |
| **Modo del agente** | UN agente | los 3 bloqueadores del modo DOS ya están cerrados (06-ago) |
| **Lista blanca** | ✅ activa | ✅ activa — 3 números (`NUMEROS_PERMITIDOS` + `numeros_permitidos_extra`) |
| **Bot en el mercado** | ❌ NO — apagado para clientas reales hasta la entrega | pruebas |

### 🔴 Lo que importa

**Producción sigue en la versión del 14-jul.** Todo el trabajo de agosto (y de fin de julio) está
en GitHub pero **aún no promovido a producción**. El bot NO atiende clientas reales todavía: es una
decisión deliberada hasta que esté listo para la entrega.

---

## ⚠️ CÓMO SE DESPLIEGA HOY (cambió — leer antes de tocar Coolify)

Erwin cambió el despliegue el 2-ago (commit `f0429db` del panel): **ningún push despliega solo.
El deploy es SIEMPRE a mano.** Antes un push a `master` reconstruía el taller; ya no.

⚠️ **Coolify del taller quedó apuntando a `DESCONECTADO-2026-08-02`.** Antes de reconectarlo a
`master`, confirmar con Erwin cómo quiere el despliegue ahora (manual del todo, o push→taller de
nuevo). **Reconectar sin acordarlo puede reactivar builds que él no quiere.**

⚠️ Coolify reconstruye desde GitHub: todo archivo editado a mano DENTRO del VPS se pierde en el
siguiente despliegue. **Nada se edita en el servidor** (regla dura de CLAUDE.md §3).

---

## 🧯 Si algo falla, revisa esto EN ORDEN (antes de preguntarle a nadie)

| Síntoma | Revisa, en orden |
|---|---|
| **El bot no contesta** | 1. Abre `api-masvida.enovagroup.tech/salud` → ¿todo `ok`? ¿`saldo_usd` > 0? · 2. Panel → ¿`bot_activo` encendido? · 3. ¿Ese número está en la lista blanca (son 3)? · 4. ¿Ese chat está pausado (bandeja / "atiendo yo")? |
| **Se acabó el saldo de IA** | `/salud` → `saldo_ia`. Recargar en OpenRouter. Sin saldo el bot NO responde. |
| **Contesta raro o inventa** | 1. Panel → Configuración → ¿qué MODELO está activo? (hoy `gpt-4o-mini`) · 2. ¿Alguien editó la Personalidad? (vive en la BD) · 3. NO culpar al modelo primero: sospecha del código/datos. |
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

---

*Actualizar este archivo cada vez que se despliegue a producción. Es corto a propósito:
si crece, deja de leerse — y entonces vuelve a no servir para nada.*
