# ONBOARDING.md — cómo montar un cliente nuevo (el método Enova, replicable)

> La receta para conectar el próximo negocio, sacada de lo que aprendimos con masvidaconsciente. Cada
> cliente es una "caja cerrada": su VPS, su bot, su panel, sus llaves, sus datos. Este documento es la
> lista de pasos; el detalle técnico de infraestructura vive en `ESTADO.md` y el porqué en `SESIONES.md`.
>
> El orden importa: **primero los datos del negocio, después soltar el bot poco a poco.** No se abre a
> clientes reales hasta que el catálogo, las zonas y la voz estén cargados y probados.

---

## 0. Antes de tocar nada: el levantamiento del negocio
No se construye sobre suposiciones. Se le pide a la dueña, o se lee de sus conversaciones reales:
- **Productos y precios:** qué vende, en qué presentaciones, precio en bolívares y en dólares, qué es
  por encargo y qué hay en stock, días de anticipación por producto.
- **Entrega:** retiro (¿dónde?) y delivery (¿qué zonas?, ¿cuánto cobra cada una?, ¿quién reparte?,
  ¿pide ubicación GPS o zona por nombre?), en qué momentos del día entrega.
- **Pago:** qué métodos acepta, si hay descuento pagando en dólares y de cuánto, si el delivery se
  cobra o se regala en algún caso, cómo confirma que recibió el dinero.
- **Voz:** cómo saluda, cómo trata a la clienta (apodos, bendiciones), cómo cierra una venta.
- **Reglas duras:** qué NUNCA debe decir el bot (promesas de salud, datos que no puede saber).

Regla: lo que las conversaciones de la dueña ya responden, no se le pregunta; se carga. Solo se le
pregunta lo que la evidencia no aclara.

## 1. Infraestructura (una caja cerrada por cliente)
- VPS con Coolify + Docker: postgres, redis, bot, worker, panel.
- Repositorio del bot y del panel; auto-deploy APAGADO, despliegue manual por workflow.
- **Llaves propias por cliente y por entorno:** OpenRouter (con tope de gasto), R2 (balde propio, no
  compartido entre pruebas y producción), token de Coolify. Doctrina: cada entorno con SUS llaves y SUS baldes.
- Dos entornos: **pruebas** (para probar por WhatsApp real sin tocar a la clienta) y **producción**.
- Respaldo diario cifrado desde el primer día; el vigía externo que avisa si producción se cae.

## 2. WhatsApp por coexistencia (Tech Provider de Meta)
- El número de la clienta se conecta por coexistencia: **ella lo sigue viendo en su propio celular.**
- Webhooks de Meta de dos niveles: el de la App (central de Enova, NO tocar) y el de la WABA (por
  cliente, en WhatsApp Manager o por Graph API). Cambiar el de una WABA no afecta a las otras.
- Regla dura: ningún envío proactivo automático sin aprobación humana; un envío mal calibrado quema la
  calidad del número y arriesga la cuenta de Meta de todos los clientes.

## 3. Cargar los datos en el panel (nunca por SQL directo)
En orden, por la puerta del panel o su API:
1. **Catálogo:** productos, presentaciones, precios, sabores/opciones, días de anticipación, fotos.
2. **Zonas de envío:** cada zona con su costo; el retiro con costo 0.
3. **Horario:** días de entrega, hora de corte, franjas o momentos del día.
4. **Métodos de pago:** con sus datos reales (titular, banco, teléfono, cédula, wallet).
5. **Conocimiento:** respuestas a las dudas frecuentes, con las palabras de la dueña.
6. **Personalidad (voz):** la voz de la asesora; la copia viva y canónica es la BD, no el archivo.
7. **Tasa:** margen y candado manual si hace falta.

## 4. La cuenta de la dueña
- Crear su cuenta propia en el panel (rol dueña) y entregarle la clave para que la cambie ella.
- La cuenta principal de administración (proveedora) no se cambia desde el panel a propósito.

## 5. Probar en pruebas antes de abrir
- Correr los bancos de prueba (deben quedar todos en verde) y una conversación completa por WhatsApp
  real al número de pruebas: pedido → entrega → pago → comprobante → cierre.
- Verificar en la base que el pedido quedó con el total correcto, no solo que el texto se vea bien.

## 6. Soltar el bot poco a poco (lista blanca)
- El bot arranca respondiendo SOLO a los números de la lista blanca; a los demás les guarda el mensaje.
- Empezar con 3 o 4 clientes al día. Leer cada día sus conversaciones (base + `llamadas_ia`), corregir
  lo que salga, y sumar más números.
- Abrir a "todos" solo cuando haya evidencia de que responde bien.
- Recordarle a la dueña: cada vez que ella conteste desde su celular, el bot se calla en ese chat; si
  quiere que el bot atienda a alguien, debe devolverle el chat.

## 7. Vigilar el costo
- Medir el costo por conversación en `llamadas_ia` desde el primer día.
- Elegir el modelo con el arnés de pruebas (`ensayo_closer.py`) sobre guiones reales, no por intuición;
  la línea base son las conversaciones que ya existen, no se paga por medir.
- Meta orientativa: costo por conversación bajo control y un tope mensual acordado con la clienta.

---

*Cada cliente nuevo empieza copiando este documento y `CLAUDE.md`, y hereda `FUNCIONES.md` recortado a
lo que ese cliente use. Lo que aprendamos montando el segundo cliente vuelve aquí.*
