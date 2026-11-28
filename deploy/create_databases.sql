-- Create the three databases once, as the postgres superuser:
--   psql -U postgres -f deploy/create_databases.sql
-- Idempotent: each CREATE is emitted only when the database is missing.
\set ON_ERROR_STOP on
SELECT 'CREATE DATABASE trading_common OWNER tradingbot'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'trading_common')\gexec
SELECT 'CREATE DATABASE trading_demo OWNER tradingbot'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'trading_demo')\gexec
SELECT 'CREATE DATABASE trading_live OWNER tradingbot'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'trading_live')\gexec

-- The demo role keeps away from live: add a dedicated read-only role if the
-- demo dashboard should never see live rows.
