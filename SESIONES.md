# Bitácora de sesiones — masvidaconsciente

> **Dos prácticas adoptadas (inspiradas en el sistema del mentor Erwin), para no romper lo que funciona:**
>
> 1. **Registrar cada sesión** en este archivo: qué se cambió, por qué, y qué quedó pendiente.
> 2. **Cambios de base de datos con red de seguridad:** antes de tocar datos reales, probar el cambio dentro de una transacción y hacer **ROLLBACK** (deshacer) para verificar que está bien. Nunca alterar datos de producción sin ese ensayo previo.

---

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
