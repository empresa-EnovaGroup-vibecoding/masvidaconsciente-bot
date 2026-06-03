# Enova Blueprint — Guía del Tech Provider

> Este documento explica cómo funciona el modelo de negocio de Enova y cómo replicar
> el sistema para cada cliente nuevo. Escrito para que Maired pueda entenderlo
> y explicárselo a otros.

---

## ¿Qué es un Tech Provider de Meta?

Enova tiene un permiso especial de Meta que permite conectar los números de WhatsApp
de sus clientes a la API oficial. Esto significa que el cliente puede:
- Seguir usando WhatsApp Business en su teléfono (coexistencia)
- Y al mismo tiempo tener un bot que responde automáticamente

Sin ser Tech Provider, esto no es posible.

---

## El modelo de negocio

```
Maired (Enova) 
    ↓ conecta el número del cliente vía Embedded Signup
Meta (portafolio Enova)
    ↓ el número queda en el portafolio de Enova
Bot del cliente (en un VPS propio)
    ↓ recibe mensajes, responde con IA, registra pedidos
Dashboard del cliente
    ↓ el dueño del negocio ve sus pedidos y conversaciones
```

**Cada cliente tiene:**
- Su propio VPS (servidor independiente)
- Su propio bot configurado con su catálogo y personalidad
- Su propio dashboard
- Costo separado

---

## Arquitectura de WhatsApp (lo más importante)

Meta tiene dos niveles de webhook:

| Nivel | Dónde se configura | Para qué sirve |
|-------|-------------------|----------------|
| App (Enova API) | Meta Developers | Para la verificación de Meta. **No tocar.** |
| WABA (cada cliente) | WhatsApp Manager → Preferencias | Para el bot de cada cliente |

**Regla de oro:** Para cada cliente nuevo, configuras el webhook en **WhatsApp Manager**,
en la WABA específica del cliente. NUNCA en Meta Developers (eso es de Enova, no del cliente).

---

## Cómo onboardear un cliente nuevo (paso a paso)

### Paso 1: Conectar el número
1. El cliente accede a `sistema-recepcion-digital.vercel.app/conectar`
2. Hace el Embedded Signup → su WABA queda en el portafolio de Enova
3. Enova habilita coexistencia (el cliente sigue usando su teléfono)

### Paso 2: Crear el bot
1. Copiar el repo `masvidaconsciente-bot` como plantilla
2. Cambiar el catálogo de productos (`migrations/002_seed_catalogo.sql`)
3. Cambiar el system prompt del agente (`app/agent/system_prompt.py`)
4. Crear nuevo VPS en Hostinger (o usar el existente con otro proyecto en Coolify)
5. Crear nuevo proyecto en Coolify con los servicios: bot + worker + PostgreSQL + Redis

### Paso 3: Configurar el webhook
1. Ir a `business.facebook.com` → Ajustes → Cuentas de WhatsApp
2. Clic en la WABA del cliente
3. Pestaña "Preferencias"
4. Configurar URL del webhook y Verify Token
5. Meta verifica la URL → el bot empieza a recibir mensajes

### Paso 4: Completar las variables de entorno
En Coolify, actualizar en el bot:
- `META_PHONE_NUMBER_ID`: el ID del número del cliente
- `META_ACCESS_TOKEN`: token permanente del número
- `META_APP_SECRET`: el App Secret de la app Enova API
- `META_VERIFY_TOKEN`: el token que pusiste en el webhook

**Variables del cobro (Pago Móvil):**
- `JWT_SECRET`: llave larga y aleatoria para el dashboard (genérala con `openssl rand -hex 32`). Sin ella el bot NO arranca, a propósito.
- `ADMIN_EMAIL` y `ADMIN_PASSWORD`: credenciales para entrar al dashboard; la contraseña debe ser fuerte (no se aceptan valores por defecto).
- `DASHBOARD_ORIGIN`: dominio del dashboard del cliente (para CORS).
- `DUENO_TELEFONO`: número que recibe el aviso cuando entra un pago (en pruebas, el tuyo; en producción, el del cliente).
- `TASA_API_URL` (Cotizave) y/o `TASA_MANUAL_DEFAULT`: con al menos una, el bot cobra en bolívares (si la API falla, usa la tasa manual).
- `COMPROBANTES_DIR`: carpeta de los comprobantes (por defecto `/data/comprobantes`).

**Volumen de comprobantes:** en Coolify, marca como **persistente** el volumen `comprobantes` (montado en web y worker), para que las imágenes de los pagos no se borren al redesplegar.

---

## Dónde encontrar los datos de Meta

| Dato | Dónde está |
|------|-----------|
| Phone Number ID | WhatsApp Manager → Números de teléfono → clic en el número |
| Access Token | Hay que generar uno permanente en el Graph API Explorer |
| App Secret | Meta Developers → Enova API → Configuración → Información básica |
| WABA ID | WhatsApp Manager → Cuentas de WhatsApp → clic en la WABA |

---

## Preguntas frecuentes

**¿Si cambio el webhook de una WABA afecta a otras?**
No. Cada WABA tiene su webhook independiente.

**¿Puedo tener múltiples clientes en el mismo VPS?**
Sí, con Coolify puedes tener varios proyectos en el mismo servidor.

**¿El cliente puede ver el código?**
Solo si tú le das acceso al GitHub. El dashboard es lo que él ve.

**¿Qué pasa si el bot falla?**
Coolify reinicia el contenedor automáticamente. Para problemas graves, se accede al VPS.
