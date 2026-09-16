# FUNCIONES.md — qué hace el sistema de másvida, en un solo lugar

> El mapa de lo que másvida hace hoy. Cada función tiene un número fijo (F-01, F-02…) para poder
> referirla y verificar el código contra ella. No es un plan (eso es `ROADMAP.md`) ni una bitácora
> (eso es `SESIONES.md`): es la foto de lo que ya funciona.
>
> **Cómo leer cada función:** *Para qué sirve* (en términos del negocio) · *Qué hace* · *Qué NUNCA
> puede pasar* (el límite duro) · *Cómo se ve que funciona* (qué mira una persona) · *Dónde vive*
> (qué parte es criterio del modelo y qué parte es código o dato — solo señala el lugar, para saber
> dónde tocar si algo cambia).
>
> **El sistema son tres piezas:** el **bot** (Alejandra, atiende WhatsApp y cobra), el **panel** (lo
> que ve la dueña) y la **infraestructura** (tasa, salud, respaldo). Alejandra se arma en 3 capas cada
> turno (`system_prompt.py` → `construir_partes_prompt`): la **personalidad** (voz, editable en el
> panel), las **reglas** (`_REGLAS`, blindadas en el código) y el **catálogo + herramientas**. El
> principio que gobierna todo: **el prompt sugiere, el código impide.**

---

## Lo que Alejandra hace con la clienta

### F-01 · Atender y saludar
- **Para qué:** que la clienta sienta que la atiende una persona cálida, no un robot.
- **Qué hace:** responde en la voz de Alejandra, saluda según la hora de Venezuela, recuerda a la
  clienta conocida por su nombre, y si le preguntan qué es, dice que es la asesora de masvidaconsciente.
- **Qué NUNCA:** decir que es humana, decir que es Whuilianny, o presentarse como "la dueña".
- **Cómo se ve:** un cliente nuevo recibe un saludo por la hora; uno conocido, su nombre; a "¿eres un
  bot?" contesta "soy Alejandra, la asesora" y sigue.
- **Dónde vive:** voz en la BD (clave `personalidad`); saludo y honestidad en `_REGLAS`; red que
  garantiza el saludo y frena la suplantación en `agent.py` (`_asegurar_saludo`, `frase_prohibida_siempre`).

### F-02 · Mostrar el catálogo y ayudar a elegir
- **Para qué:** que la clienta vea los productos y sus opciones sin que nadie abra un Excel.
- **Qué hace:** nombra los productos que calzan con lo que pide (`ver_catalogo`), da el detalle de uno
  (`info_producto`), manda el folleto en PDF (`enviar_catalogo`) y fotos o videos (`enviar_fotos_producto`).
- **Qué NUNCA:** inventar un producto, un precio o un ingrediente que no esté en el catálogo.
- **Cómo se ve:** pide "galletas" y recibe las que hay con sus sabores; pide "el catálogo" y le llega el PDF.
- **Dónde vive:** las 4 herramientas en `tools.py`; los datos, en la BD (panel → Catálogo).

### F-03 · Responder dudas del negocio
- **Para qué:** contestar "¿hacen envíos?", "¿dónde están?", "¿tiene descuento en dólares?".
- **Qué hace:** responde ubicación, pago y redes (`info_negocio`) y busca en lo que la dueña cargó en
  Conocimiento (`buscar_info`).
- **Qué NUNCA:** inventar una política; si no lo tiene cargado, lo dice y ofrece consultarlo.
- **Cómo se ve:** una duda general se responde con lo que hay en Conocimiento, no con algo inventado.
- **Dónde vive:** herramientas en `tools.py`; el contenido, en la BD (panel → Conocimiento).

### F-04 · Tomar el pedido
- **Para qué:** dejar registrado qué quiere la clienta, con cantidades y opciones.
- **Qué hace:** registra el pedido completo en una sola llamada (`registrar_pedido`), con los sabores o
  rellenos que eligió.
- **Qué NUNCA:** registrar dos veces el mismo pedido, ni cobrar un precio calculado por el modelo.
- **Cómo se ve:** el pedido aparece en el panel con sus productos y su total; no hay duplicados.
- **Dónde vive:** `registrar_pedido` en `tools.py`; los precios salen del catálogo (BD), nunca del modelo.

### F-05 · Acordar la entrega
- **Para qué:** que cada pedido tenga cómo, dónde y cuándo se entrega, antes de cobrar.
- **Qué hace:** valida la fecha contra el calendario y la anticipación de cada producto
  (`proxima_fecha_entrega`), y guarda la zona, la referencia y el momento del día (`anotar_entrega`).
- **Qué NUNCA:** prometer una hora exacta, prometer una fecha por su cuenta, ni cobrar un delivery sin
  la dirección de referencia.
- **Cómo se ve:** el pedido queda con fecha, zona y referencia; a "¿a las 10?" confirma el momento, no una hora.
- **Dónde vive:** las 2 herramientas y sus reglas en `tools.py`/`system_prompt.py`; las zonas y las
  franjas, en la BD (panel → Zonas de envío y Horario).

### F-06 · Cobrar
- **Para qué:** darle a la clienta el total correcto y los datos de pago del método que elija.
- **Qué hace:** calcula el total en bolívares a la tasa BCV del día; en dólares suma productos +
  delivery y después descuenta 20% a esa cuenta completa. Devuelve el cobro listo para copiar
  (`generar_datos_pago`).
- **Qué NUNCA:** dar datos de pago de memoria, inventar una cuenta, ni cobrar sin fecha de entrega acordada.
- **Cómo se ve:** el cobro muestra el desglose (productos, descuento, delivery, total) y los datos del
  método elegido, copiados tal cual.
- **Dónde vive:** `generar_datos_pago` en `tools.py`; la cuenta del dinero, en código (`monto_en_efectivo`);
  los métodos y sus datos, en la BD (panel → Configuración).

### F-07 · Recibir el comprobante
- **Para qué:** acusar el pago con calidez sin mentir sobre si el dinero llegó.
- **Qué hace:** reconoce por visión si la imagen es un comprobante, lo registra como reportado, dice que
  lo está revisando y avisa a la dueña (`registrar_comprobante`).
- **Qué NUNCA:** decir que verificó el dinero en el banco o que el pago quedó confirmado; eso solo lo
  sabe la dueña en su banco.
- **Cómo se ve:** la clienta recibe "ya lo recibí, lo estoy revisando"; en el panel aparece el pago como
  reportado y la dueña recibe el aviso.
- **Dónde vive:** `registrar_comprobante` en `tools.py`; la visión, con Gemini; las redes del dinero, en `agent.py`.

### F-08 · Cerrar tras la aprobación de la dueña
- **Para qué:** que la venta solo se cierre cuando la dueña confirmó el pago, no antes.
- **Qué hace:** cuando la dueña aprieta "Pago aprobado" en el panel, el bot le confirma a la clienta,
  coordina el momento de entrega en una línea y deja morir la conversación.
- **Qué NUNCA:** coordinar la entrega o cerrar antes del clic de la dueña; repetir el pedido o volver a cobrar.
- **Cómo se ve:** tras el clic, la clienta recibe un cierre corto y cálido; sin el clic, el bot espera.
- **Dónde vive:** el clic dispara `confirmar_pago` → `notificar_cliente_pago` (código); la regla del
  cierre, en `_REGLAS`.

### F-09 · Pasar a una persona
- **Para qué:** que el bot se aparte cuando algo no le toca, sin dejar a la clienta colgada.
- **Qué hace:** escala a una persona del negocio (`pedir_ayuda`) en 4 casos (precio del día, algo que no
  sabe, piden una persona, un reclamo), avisa a la dueña por WhatsApp y en el panel, y se calla en ese chat.
- **Qué NUNCA:** prometer que va a averiguar algo sin avisar de verdad; hablar de "la dueña" como un
  tercero ("le pregunto y te aviso").
- **Cómo se ve:** la clienta recibe "eso te lo confirmo enseguida"; la dueña recibe el aviso y el chat
  queda en sus manos.
- **Dónde vive:** `pedir_ayuda` en `tools.py`; el aviso, en `agent.py`/servicios.

### F-10 · Memoria del cliente
- **Para qué:** que Alejandra recuerde quién es cada clienta y qué pidió antes.
- **Qué hace:** guarda nombre y datos clave de salud o preferencia (`recordar_cliente`) y consulta
  pedidos anteriores para repetir uno (`ver_pedidos_cliente`).
- **Qué NUNCA:** inventar un dato de salud o un pedido que no existió.
- **Cómo se ve:** a una clienta conocida la saluda por su nombre y puede repetirle su pedido anterior.
- **Dónde vive:** las 2 herramientas en `tools.py`; la ficha, en la BD; se inyecta cada turno con `_ficha_cliente_texto`.

---

## Lo que protege el negocio (redes de seguridad, en el código)

### F-11 · Redes de seguridad
- **Para qué:** que el bot nunca mienta sobre el dinero, la identidad o la salud, aunque el modelo falle.
- **Qué hace:** frena en el código, antes de enviar, frases prohibidas: que el pago llegó o quedó
  confirmado, que revisó el banco, que es una persona o la dueña, promesas de salud ("no te sube el
  azúcar", "es seguro para tu diabetes"), un precio inventado, un pedido fantasma ("te lo anoto" sin registrar).
- **Qué NUNCA:** dejar salir una de esas frases al cliente; si el modelo insiste, se escala a la dueña.
- **Cómo se ve:** una respuesta con una de esas frases no se envía; en el log queda "FRASE PROHIBIDA".
- **Dónde vive:** `agent.py` (`_PROHIBIDO_SIEMPRE`, `_PROHIBIDO_EN_CHARLA`, `_dinero_inventado`,
  `_afirma_pedido_registrado`…). Regla de la casa: estas redes protegen contra cualquier modelo.

### F-12 · Coexistencia con la dueña
- **Para qué:** que el bot y Whuilianny compartan el mismo WhatsApp sin hablar encima.
- **Qué hace:** cuando la dueña contesta desde su celular, el bot se calla en ese chat (pausa); ella lo
  devuelve con "Devolver al bot"; al devolverlo, el bot retoma solo si quedó algo pendiente y no reabre
  una venta que ella ya cerró a mano.
- **Qué NUNCA:** hablarle encima a la dueña; reabrir y volver a cobrar una venta cerrada; enviar un
  mensaje proactivo sin que ella lo haya devuelto.
- **Cómo se ve:** si la dueña escribe, el chat dice "atiendes tú"; al devolverlo, el bot sigue donde quedó.
- **Dónde vive:** `tasks.py` (`_procesar_eco`, `_retomar`, `_hay_pendiente`) y el panel (Conversaciones).

### F-13 · Lista blanca y apertura gradual
- **Para qué:** soltar el bot cliente por cliente, sin exponer a todo el mundo de golpe.
- **Qué hace:** el bot solo responde a los números de la lista blanca; a los demás les guarda el mensaje
  y no contesta. La lista se edita en la configuración, y "todos" la abre a cualquiera.
- **Qué NUNCA:** responderle a un número fuera de la lista mientras la lista no esté vacía o en "todos".
- **Cómo se ve:** un número piloto recibe a Alejandra; el resto, solo queda registrado para la dueña.
- **Dónde vive:** `tasks.py` (`_numero_permitido`); la lista, en la BD (`numeros_permitidos_extra`) y el entorno.

---

## Lo que ve y maneja la dueña

### F-14 · El panel
- **Para qué:** que la dueña vea y controle todo su negocio desde una pantalla.
- **Qué hace:** 15 secciones — Conversaciones, Pedidos, Pagos, Entregas, Clientes, Catálogo, Zonas de
  envío, Horario, Conocimiento, Mensajes, Tasa, Reporte, Configuración, El bot te necesita (bandeja) y Mi Bot.
- **Qué NUNCA:** editar desde el panel las reglas del cobro (están blindadas en el código); borrar
  fotos del balde compartido; "Borrar" un chat sin querer perder sus mensajes.
- **Cómo se ve:** la dueña entra a `panel.masvidaconsciente.store`, aprueba pagos, edita el catálogo y la voz.
- **Dónde vive:** `masvidaconsciente-dashboard` (Next.js); los datos, en la BD por la API del bot.

### F-15 · Tasa, salud, respaldo y costo
- **Para qué:** que el negocio no se caiga en silencio y que cada gasto quede medido.
- **Qué hace:** trae la tasa BCV automática con margen y candado manual; el semáforo `/salud` vigila
  base, Redis, Meta, saldo y modelo; un vigía externo avisa si producción se cae; el respaldo diario
  cifrado; y `llamadas_ia` registra costo, modelo y latencia de cada respuesta.
- **Qué NUNCA:** cobrar con una tasa vieja sin avisar; quedarse sin saldo sin alarma (avisa bajo $5).
- **Cómo se ve:** `/salud` responde "ok"; la tasa del día sale en el cobro; el respaldo corre cada día.
- **Dónde vive:** `services/tasa.py`, `services/salud.py`, `scripts/backup.sh`, `services/telemetria.py`;
  la tasa y el candado, en el panel (Tasa).

---

*Documento vivo. Cada función tiene su id fijo: si se agrega una herramienta o una sección al panel,
se agrega aquí su F-xx. El detalle histórico de por qué cada una existe está en `SESIONES.md`.*
