#!/bin/sh
# Docker Official Postgres image sources non-executable *.sh init files after schema.sql.
# The password is passed as a psql variable and quoted by psql, not interpolated by the shell.
set -eu
: "${PROXIMA_GATEWAY_PASSWORD:?Set PROXIMA_GATEWAY_PASSWORD for the local test database}"

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 \
  --set=gateway_password="$PROXIMA_GATEWAY_PASSWORD" <<'SQL'
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'proxima_gateway') THEN
        CREATE ROLE proxima_gateway NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
END
$$;

ALTER ROLE proxima_gateway LOGIN PASSWORD :'gateway_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
GRANT tenant_a, tenant_b, tenant_c TO proxima_gateway;
GRANT CONNECT ON DATABASE enterprise_lab TO proxima_gateway;
GRANT USAGE ON SCHEMA public TO proxima_gateway;
SQL
