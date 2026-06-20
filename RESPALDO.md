# 🛟 RESPALDO de los datos (cómo activarlo)

> **Qué hace:** cada día respalda **los datos de la clienta** (base de datos: clientes, pedidos,
> pagos, catálogo, personalidad + las imágenes de los comprobantes), los **cifra** con una clave
> que SOLO tú controlas, y los sube **afuera del VPS** a Cloudflare R2. Si el servidor muere, esto
> es lo único que recupera el negocio. El código del bot ya está en GitHub, no hace falta respaldarlo.
>
> **Costo:** $0 (R2 da 10 GB gratis; la base pesa unos pocos MB).
>
> El servicio ya está montado en el `docker-compose`. Mientras no pongas los secretos, **se queda
> en pausa solito** y el bot funciona igual. Para activarlo, haz estos 2 pasos.

## Paso 1 — Crear el destino en Cloudflare R2 (gratis, 5 min)
1. Entra a **dash.cloudflare.com** → crea cuenta (gratis) → en el menú, **R2**.
2. **Create bucket** → nombre: `masvida-respaldos` (o el que quieras). Anótalo.
3. Arriba, copia tu **Account ID** (lo necesitas para la dirección).
4. **Manage R2 API Tokens** → **Create API Token** → permiso **Object Read & Write** sobre ese bucket.
   Te da dos valores que se ven UNA sola vez — cópialos:
   - **Access Key ID**
   - **Secret Access Key**

## Paso 2 — Poner los secretos en Coolify
En Coolify, en las **variables de entorno** del proyecto del bot, agrega estas 4 (y redespliega):

| Variable | Qué pones |
|---|---|
| `RESTIC_REPOSITORY` | `s3:https://TU_ACCOUNT_ID.r2.cloudflarestorage.com/masvida-respaldos` |
| `RESTIC_PASSWORD` | **Tu clave de cifrado** — invéntate una larga y guárdala bien (ver aviso ⚠️) |
| `R2_ACCESS_KEY_ID` | el *Access Key ID* del paso 1 |
| `R2_SECRET_ACCESS_KEY` | el *Secret Access Key* del paso 1 |

> Reemplaza `TU_ACCOUNT_ID` por tu Account ID, y `masvida-respaldos` por el nombre real del bucket.

Al redesplegar, el servicio `backup` hace el **primer respaldo de una vez** y luego uno cada 24h.

## ⚠️ AVISO CRÍTICO sobre `RESTIC_PASSWORD`
Esa clave **cifra el respaldo: sin ella, NADIE puede leerlo — ni Cloudflare, ni nosotros, ni tú.**
Es a propósito (así "solo tú lo controlas"). **Guárdala en tu gestor de contraseñas.** Si la pierdes,
el respaldo queda inservible. No la cambies sin entender que los respaldos viejos quedan atados a la vieja.

## Cómo saber que está funcionando
En Coolify, abre los **logs** del servicio `backup`. Debes ver `[backup] OK ...`.
O desde la terminal del VPS:
```sh
docker compose run --rm backup restic snapshots
```
Debe listar al menos un respaldo del día.

## Cómo RESTAURAR (si algún día hace falta)
Un respaldo solo sirve si se puede restaurar. **Hazlo conmigo la primera vez.** Estos comandos se
corren en la terminal del VPS, en la carpeta del proyecto (restic y psql viven DENTRO del contenedor,
por eso todo va con `docker compose run`):
```sh
# 1) Ver respaldos disponibles
docker compose run --rm backup restic snapshots

# 2) Restaurar el último a una carpeta REAL del VPS (para poder ver los archivos)
docker compose run --rm -v /root/restore:/restore backup restic restore latest --target /restore
#    Queda:  /root/restore/backup/db_*.sql.gz   y   /root/restore/data/comprobantes/...

# 3) Cargar la base de datos (con web y worker DETENIDOS, pero postgres ARRIBA):
docker compose run --rm -v /root/restore:/restore backup \
  sh -c 'gunzip -c /restore/backup/db_*.sql.gz | psql "postgresql://USUARIO:CLAVE@postgres:5432/masvidaconsciente"'

# 4) Copiar las imágenes restauradas de /root/restore/data/comprobantes de vuelta al volumen comprobantes.
```
> Reemplaza `USUARIO:CLAVE` por los de Postgres (los mismos de `DATABASE_URL`).
> Recomendado: **prueba una restauración UNA vez** después de activarlo, para dormir tranquila
> sabiendo que de verdad funciona (un respaldo sin probar es solo una esperanza).

## ⚙️ Nota técnica (para quien toque el deploy)
El servicio `backup` se **construye junto** con el bot en el mismo `docker compose up`. Si algún día
su build fallara (mirror de Alpine caído, paquete renombrado), **podría bloquear el deploy de `web`/`worker`**.
Hoy los paquetes están verificados. A futuro, para la fábrica, conviene publicar esta imagen
pre-construida (`image: ...`) y quitar el `build:` inline, para que el bot nunca dependa de ese build.

## Para la fábrica (clientes futuros)
Este mismo servicio se copia tal cual en cada cliente nuevo: solo cambian el bucket y la clave de
cifrado (cada cliente, su propio respaldo separado). Es parte del `ENOVA_BLUEPRINT`.
