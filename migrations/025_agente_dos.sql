-- 025: LOS DOS AGENTES (Operador + Voz) — la bandera y los modelos por agente.
--
-- POR QUÉ. El bot corre con un system prompt de ~16.400 tokens, 42 reglas imperativas y 55
-- prohibiciones, y con DOS reglas que se declaran ambas "la MÁS importante" (ANTIINVENCIÓN y
-- BREVEDAD). Cuando todo es crítico, nada lo es. Por eso hay SIETE redes de regex en agent.py
-- que existen solo para atrapar al modelo incumpliendo — el propio código lo confiesa: *"el
-- prompt se lo prohibía DOS VECES y lo hizo igual"*.
--
-- La salida no es una regla más: es partir el agente en dos.
--   · OPERADOR — tiene las herramientas. Busca, registra, cobra. NO le escribe al cliente.
--   · VOZ      — escribe el mensaje. Sin herramientas, sin catálogo, sin datos bancarios.
--                No es que se le PROHÍBA inventar: es que NO TIENE DE DÓNDE.
--
-- 🔒 SE ESTRENA EN MODO 'uno' A PROPÓSITO: el comportamiento no cambia al desplegar. La proveedora
-- lo enciende cuando quiera desde el panel, y volver atrás es UN `UPDATE` — sin redeploy, efectivo
-- en el siguiente mensaje.
--
-- `modelo_operador` y `modelo_voz` NO se siembran: ausentes ⇒ caen a `modelo_ia` (compatibilidad).

INSERT INTO configuracion (clave, valor) VALUES ('agente_modo', 'uno')
  ON CONFLICT (clave) DO NOTHING;
