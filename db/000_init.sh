#!/bin/sh
# PostgreSQL ilk açılışta çalışır: n8n ve factory için ayrı kullanıcı + veritabanı oluşturur.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE ROLE n8n LOGIN PASSWORD '${N8N_DB_PASSWORD}';
CREATE DATABASE n8n OWNER n8n;
CREATE ROLE factory LOGIN PASSWORD '${FACTORY_DB_PASSWORD}';
CREATE DATABASE factory OWNER factory;
SQL

psql -v ON_ERROR_STOP=1 --username factory --dbname factory -f /docker-entrypoint-initdb.d/sql/001_schema.sql
