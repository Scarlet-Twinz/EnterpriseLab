#!/bin/sh
# Create the login roles selected by Agata Proxima's default tenant role prefix.
# Passwords are passed as psql variables, not interpolated by the shell.
set -eu
: "${LAB_TENANT_A_DB_PASSWORD:?Set LAB_TENANT_A_DB_PASSWORD}"
: "${LAB_TENANT_B_DB_PASSWORD:?Set LAB_TENANT_B_DB_PASSWORD}"
: "${LAB_TENANT_C_DB_PASSWORD:?Set LAB_TENANT_C_DB_PASSWORD}"

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 \
  --set=northstar_password="$LAB_TENANT_A_DB_PASSWORD" \
  --set=bluehaven_password="$LAB_TENANT_B_DB_PASSWORD" \
  --set=cedarstone_password="$LAB_TENANT_C_DB_PASSWORD" <<'SQL'
ALTER ROLE proxima_tenant_northstar LOGIN PASSWORD :'northstar_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
ALTER ROLE proxima_tenant_bluehaven LOGIN PASSWORD :'bluehaven_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
ALTER ROLE proxima_tenant_cedarstone LOGIN PASSWORD :'cedarstone_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
SQL
