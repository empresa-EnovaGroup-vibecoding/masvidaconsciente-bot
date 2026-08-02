-- 026 · UN SOLO PAGO "REPORTADO" POR PEDIDO — lo garantiza Postgres, no el código.
--
-- 🔴 POR QUÉ (auditoría forense del 2026-08-02, EST-2). `registrar_comprobante` ya comprobaba
-- "¿hay otro pago reportado en este pedido?" antes de insertar… pero en PYTHON, con un SELECT
-- seguido de un INSERT. Entre esos dos pasos cabe otra tarea entera:
--
--   · El cliente manda DOS capturas seguidas (la del banco + la del SMS). Es lo normal.
--   · `_encolar_comprobante` las encola SIN countdown y el worker corre con --concurrency=2,
--     así que las dos tareas arrancan a la vez, en procesos distintos.
--   · Las dos hacen la visión (~45 s) y llegan al SELECT casi simultáneamente: ninguna ve a la
--     otra todavía. Las dos insertan.
--   · El UNIQUE de `comprobante_media_id` NO las frena: son dos media_id distintos.
--
-- Resultado: dos filas `reportado` sobre el mismo pedido, y el cliente recibe DOS mensajes de
-- "recibí tu pago". El daño mayor —contar la venta dos veces en /reporte— lo cierra aparte el
-- guard `_no_hay_otro_pago_confirmado` (router.py), pero la fila duplicada solo la puede impedir
-- la base de datos: es el único sitio donde la comprobación y la escritura son un mismo acto.
--
-- Es un índice PARCIAL a propósito: solo sobre 'reportado'. Un pago 'parcial' conviviendo con uno
-- 'reportado' es un flujo legítimo (pagó de menos y luego completa), y varios 'rechazado' o
-- 'confirmado' históricos tienen que poder coexistir.
--
-- ⚠️ Si la base ya arrastra duplicados de antes, el CREATE INDEX fallaría y el contenedor no
-- arrancaría (init_db.py aborta ruidosamente, y así debe ser). Por eso se limpian primero,
-- conservando el MÁS ANTIGUO de cada pedido: es el que el cliente vio referenciado y el que
-- tiene la cotización correcta. Los demás pasan a 'rechazado' con su motivo escrito, para que
-- quede rastro auditable — no se borra ni una fila de dinero.

UPDATE pagos SET estado = 'rechazado',
                 motivo_rechazo = COALESCE(motivo_rechazo, 'duplicado automatico (migracion 026)')
WHERE estado = 'reportado'
  AND id NOT IN (
      SELECT MIN(id) FROM pagos WHERE estado = 'reportado' GROUP BY pedido_id
  );

CREATE UNIQUE INDEX IF NOT EXISTS ux_pago_reportado_por_pedido
    ON pagos (pedido_id)
    WHERE estado = 'reportado';
