#!/bin/sh
set -e

# SentinelAI — Local PostgreSQL Database & Role Initialization
# Executed automatically by PostgreSQL container entrypoint on initial volume creation.

POSTGRES_DB="${POSTGRES_DB:-sentinel_ai}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_MIGRATOR_USER="${POSTGRES_MIGRATOR_USER:-sentinel_migrator}"
POSTGRES_MIGRATOR_PASSWORD="${POSTGRES_MIGRATOR_PASSWORD:-sentinel_migrator_password}"
POSTGRES_APP_USER="${POSTGRES_APP_USER:-sentinel_app}"
POSTGRES_APP_PASSWORD="${POSTGRES_APP_PASSWORD:-sentinel_local_password}"

echo "=========================================================="
echo "Initializing SentinelAI PostgreSQL Roles and Privileges..."
echo "Database:               ${POSTGRES_DB}"
echo "Migration Owner Role:   ${POSTGRES_MIGRATOR_USER}"
echo "Application Role:       ${POSTGRES_APP_USER}"
echo "=========================================================="

psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" <<-EOSQL
    -- 1. Create Migration Owner Role
    -- Has CREATEDB for test database isolation, but NOSUPERUSER and NOBYPASSRLS
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${POSTGRES_MIGRATOR_USER}') THEN
            CREATE ROLE ${POSTGRES_MIGRATOR_USER} WITH 
                LOGIN 
                PASSWORD '${POSTGRES_MIGRATOR_PASSWORD}' 
                CREATEDB 
                NOSUPERUSER 
                INHERIT 
                NOCREATEROLE 
                NOREPLICATION 
                NOBYPASSRLS;
        END IF;
    END
    \$\$;

    -- 2. Create Application Runtime Role
    -- Non-owner, least-privilege role: NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOBYPASSRLS
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${POSTGRES_APP_USER}') THEN
            CREATE ROLE ${POSTGRES_APP_USER} WITH 
                LOGIN 
                PASSWORD '${POSTGRES_APP_PASSWORD}' 
                NOSUPERUSER 
                NOCREATEDB 
                NOCREATEROLE 
                INHERIT 
                NOREPLICATION 
                NOBYPASSRLS;
        END IF;
    END
    \$\$;

    -- 3. Database Connection Privileges
    GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO ${POSTGRES_MIGRATOR_USER};
    GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO ${POSTGRES_APP_USER};

    -- 4. Schema Ownership & Permissions
    -- Migration role owns schema public so it can execute DDL (migrations)
    ALTER SCHEMA public OWNER TO ${POSTGRES_MIGRATOR_USER};
    GRANT ALL ON SCHEMA public TO ${POSTGRES_MIGRATOR_USER};
    GRANT USAGE ON SCHEMA public TO ${POSTGRES_APP_USER};

    -- Revoke general table creation from public and app roles
    REVOKE CREATE ON SCHEMA public FROM PUBLIC;
    REVOKE CREATE ON SCHEMA public FROM ${POSTGRES_APP_USER};

    -- 5. Default Privileges for sentinel_app
    -- DML only: SELECT, INSERT, UPDATE, DELETE on tables created by sentinel_migrator
    ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_MIGRATOR_USER} IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ${POSTGRES_APP_USER};

    ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_MIGRATOR_USER} IN SCHEMA public
        GRANT USAGE, SELECT ON SEQUENCES TO ${POSTGRES_APP_USER};

    -- Also configure default privileges if tables are created by superuser
    ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_USER} IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ${POSTGRES_APP_USER};

    ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_USER} IN SCHEMA public
        GRANT USAGE, SELECT ON SEQUENCES TO ${POSTGRES_APP_USER};
EOSQL

echo "SentinelAI database roles and privileges configured successfully."

