-- Campos usados para montar ocorrências sem redigitação no aplicativo.
-- Aplicar depois de 001_schema.sql em bancos de desenvolvimento já existentes.

BEGIN;

ALTER TABLE app.notes ADD COLUMN IF NOT EXISTS city varchar(100) NOT NULL DEFAULT '';
ALTER TABLE app.notes ADD COLUMN IF NOT EXISTS seller varchar(100) NOT NULL DEFAULT '';
ALTER TABLE app.notes ADD COLUMN IF NOT EXISTS items jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE app.delivery_events ADD COLUMN IF NOT EXISTS arrival_time varchar(5) NOT NULL DEFAULT '';
ALTER TABLE app.delivery_events ADD COLUMN IF NOT EXISTS affected_item varchar(500) NOT NULL DEFAULT '';
ALTER TABLE app.delivery_events ADD COLUMN IF NOT EXISTS seller_name varchar(100) NOT NULL DEFAULT '';

COMMIT;
