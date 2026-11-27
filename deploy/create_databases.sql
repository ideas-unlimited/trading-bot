-- Create the three databases once, as the postgres superuser:
--   psql -U postgres -f deploy/create_databases.sql
-- Idempotent: each CREATE is emitted only when the database is missing.
\set ON_ERROR_STOP on
SELECT 'CREATE DATABASE trading_common OWNER tradingbot'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'trading_common')\gexec
SELECT 'CREATE DATABASE trading_paper OWNER tradingbot'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'trading_paper')\gexec
SELECT 'CREATE DATABASE trading_prod OWNER tradingbot'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'trading_prod')\gexec

-- The app user keeps away from prod: revoke connect on prod for it only if
-- you later add a dedicated read-only role for the paper dashboard.
