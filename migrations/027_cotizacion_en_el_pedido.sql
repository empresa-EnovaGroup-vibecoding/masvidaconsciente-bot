-- 027 · LA COTIZACIÓN DEJA DE SER VOLÁTIL.
--
-- 🔴 POR QUÉ (auditoría forense del 2026-08-02, DIN-5). Lo que se le cobró al cliente —el monto en
-- bolívares y la TASA con la que se calculó— vivía en UN SOLO SITIO: la clave `cobro:{telefono}`
-- de Redis, con TTL de 24 horas. `pedidos` no guardaba ni la tasa ni el monto cotizado.
--
-- Y quedarse sin esa clave es lo NORMAL en este negocio, no una rareza: los pedidos van con días
-- de anticipación, así que cotizar el viernes y pagar el domingo es el caso corriente. Cuando
-- expiraba:
--
--   · `registrar_comprobante` recalculaba el monto en Bs con la tasa de HOY. El cliente pagó los
--     Bs 16.591 que se le pidieron el viernes, el pago se grababa contra Bs 17.135 del domingo, y
--     "Monto distinto" le reclamaba Bs 544 que no debía.
--   · `_montos_cobrados` devolvía (None, None, None), así que no había NADA con que comparar el
--     comprobante. Eso, con el fail-open que también se corrigió hoy, hacía que un comprobante de
--     Bs 5.000 sobre una venta de Bs 16.591 disparara "recibí tu pago y coordino la entrega".
--
-- Redis sigue siendo la vía rápida; esto es el respaldo duradero. Un dato del que depende el
-- dinero no puede vivir solo en una caché con caducidad.
--
-- Se guarda la cotización COMPLETA (las tres monedas + la tasa + cuándo), no solo la tasa: así el
-- comprobante se compara contra lo que se le dijo al cliente, aunque el pedido se haya editado
-- después. Todas nullable: los pedidos viejos y los que aún no se cotizaron se quedan en NULL, y
-- el código cae al comportamiento de siempre.

ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS cotizado_bs          NUMERIC(14, 2);
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS cotizado_usd         NUMERIC(10, 2);
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS cotizado_usd_divisas NUMERIC(10, 2);
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS tasa_cotizada        NUMERIC(14, 4);
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS cotizado_at          TIMESTAMPTZ;
