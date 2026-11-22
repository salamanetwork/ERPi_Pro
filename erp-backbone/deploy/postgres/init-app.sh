#!/bin/sh
set -eu
[ "${#ERP_DB_PASSWORD}" -ge 32 ] && [ "${#POSTGRES_PASSWORD}" -ge 32 ] && [ "$ERP_DB_PASSWORD" != "$POSTGRES_PASSWORD" ] || { echo "Set two distinct database passwords of at least 32 characters" >&2; exit 1; }
case "$ERP_DB_PASSWORD:$POSTGRES_PASSWORD" in
    CHANGE_ME*|*:CHANGE_ME*) echo "Replace the database password placeholders" >&2; exit 1 ;;
esac
# The database service exposes its socket to its own processes only.
psql -X -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<'SQL'
\getenv db ERP_INITIAL_DB_NAME
\getenv password ERP_DB_PASSWORD
CREATE ROLE erp_app LOGIN PASSWORD :'password';
CREATE DATABASE :"db" OWNER erp_app;
SQL
