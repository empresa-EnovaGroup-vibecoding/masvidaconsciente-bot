# Continuidad de la atención entre Whuilianny y Alejandra

> Consultar ANTES de tocar cualquier cosa de esta funcionalidad (Claude, Codex, ChatGPT o una persona).
> Las decisiones de aquí son de Maired. No se vuelven a preguntar salvo que aparezca una contradicción concreta.

## El objetivo

Cuando Whuilianny atiende a una clienta (por texto o por audio) y después vuelve Alejandra, Alejandra tiene que
saber cuatro cosas: **qué vendió, a cuánto, si ya le cobró y qué falta**. Y tiene que seguir desde ahí como una
persona normal: que la clienta no tenga que repetir nada, que no le cobren dos veces y que nadie contradiga lo
que se acordó.

## El principio

**Entender es trabajo de la IA.** Alejandra lee la conversación completa entre las dos, con los audios ya
transcritos, y entiende a qué responde Whuilianny. Por ejemplo, si la clienta pregunta "¿me la dejas en 30?" y
Whuilianny contesta "ok nena", es un sí.

**El código solo impide dos cosas:**
- que Alejandra invente un número que no salió en esa conversación;
- que cobre dos veces lo que ya se pagó.

**Nunca** se ponen reglas de frases fijas para interpretar a Whuilianny.

## Decisiones vigentes

| Tema | Decisión |
|---|---|
| Precio o total que dio Whuilianny (o que aceptó) | Ese precio manda. Va sin el 20% de pagar en dólares. |
| Cuánto vale | Hasta que esa venta se pague o se entregue, con un tope de 14 días. Una compra nueva va a precio de catálogo. |
| Red de seguridad | Si Alejandra sigue con un precio distinto al del catálogo, a Whuilianny le llega una vez: "📝 Seguí con…". |
| "Ya le cobró" | Alejandra no cobra otra vez. Pide la dirección y el día. A Whuilianny se le pregunta por WhatsApp "¿te llegó el pago…?". |
| Pagos | Siempre los confirma una persona (Whuilianny por WhatsApp). |
| El lector (expediente) | Solo pregunta los pagos y avisa lo que no está en el catálogo. No crea tarjetas de pedidos ni de precios. |
| Abonos (14-oct, todavía NO construido) | Alejandra sigue la venta. Se cobra solo lo nuevo. El saldo se paga al entregar y se menciona solo cuando viene al caso. Whuilianny confirma cada abono por WhatsApp. Alejandra nunca ofrece fiar ni pagar en partes por su cuenta. |
| Producción | Fuera de estos cambios hasta validarlos en pruebas. |

## Cómo se trabaja

Antes de cada cambio se explican tres cosas:
1. qué parte del objetivo resuelve;
2. qué cambia para la clienta o para Whuilianny;
3. cómo se comprueba y qué queda pendiente.

La batería automática en verde no basta: se mira **en conversaciones** de pruebas. Las pruebas que gastan
dinero llevan un presupuesto aprobado antes.

## Estado (se actualiza al cerrar cada paso)

| Capacidad | Código | En pruebas | Visto funcionar |
|---|---|---|---|
| Precio de Whuilianny / "ok nena" (#76, #77) | ✅ | ✅ | ❌ |
| "Ya le cobró" | ✅ | ✅ | ❌ |
| Notita del punto de partida | ✅ | ✅ | ❌ |
| No arrastrar precios de una compra vieja | ✅ | ✅ | ❌ |
| Abonos | ❌ | — | ❌ |

⚠️ Del 9 al 14 de octubre pruebas corrió con GPT-4.1 por un error de configuración (SESIONES (50)). Lo que se
haya visto en esas fechas no cuenta.
