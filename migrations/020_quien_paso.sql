-- 020 — QUIÉN APRETÓ EL FRENO (aditiva e idempotente)
--
-- 🔴 BUG REAL, cazado en vivo el 2026-07-12 (lo introduje ese mismo día):
--
-- La red anti-atropello (`_enviar_en_partes` vuelve a mirar la pausa justo antes de enviar)
-- sabía QUE el chat estaba pausado, pero no QUIÉN lo pausó. Y hay dos casos OPUESTOS:
--
--   a) LA DUEÑA tomó el chat  → el bot DEBE callarse (si no, le habla encima al cliente).
--   b) EL BOT se pausó SOLO   → el bot DEBE mandar igual su último mensaje.
--      (`pedir_ayuda` / la red de la promesa pausan el chat para escalar a la humana; pero
--      antes el bot tiene que decirle al cliente "dame un momentito, te confirmo". Si no,
--      el cliente se queda con SILENCIO TOTAL — que es justo lo que pasó.)
--
-- Sin esta columna, el caso (b) se confundía con el (a) y el mensaje del bot se tiraba a la
-- basura: el cliente escribía "Hola" y NO recibía absolutamente nada.
--
-- 'dueña' = lo pausó una PERSONA (escribió desde el panel, o apretó "Yo atiendo").
-- 'bot'   = lo pausó el propio bot al escalar (pedir_ayuda / promesa / frase bloqueada).
-- NULL    = no está pausado (o es un chat viejo, de antes de esta migración).
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS pausado_por TEXT;
ALTER TABLE clientes DROP CONSTRAINT IF EXISTS ck_cliente_pausado_por;
ALTER TABLE clientes ADD CONSTRAINT ck_cliente_pausado_por
    CHECK (pausado_por IS NULL OR pausado_por IN ('dueña', 'bot'));

-- Backfill CONSERVADOR: a los chats que YA están pausados y no sabemos por quién, se les pone
-- 'dueña'. Es el lado seguro: ante la duda, el bot se calla (nunca hablarle encima a una
-- persona que está atendiendo). Lo contrario —asumir 'bot'— haría que el bot se metiera en
-- una conversación que la dueña tiene tomada.
UPDATE clientes SET pausado_por = 'dueña' WHERE bot_pausado IS TRUE AND pausado_por IS NULL;
