-- ======================================================================
-- One-time role setup for the Task Manager module.
-- Order:  1) tasks_schema.sql   2) this file   (both as the database owner/superuser,
--         on dgmts_static_db).
--
-- Creates a dedicated role scoped to the `tasks` schema so the module can NEVER touch
-- the existing `public` (dgmts_static_db) tables. Replace the password before running.
-- ======================================================================

DO $$ BEGIN
    CREATE ROLE dgmts_tasks_user LOGIN PASSWORD 'admin';
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

GRANT CONNECT ON DATABASE dgmts_static_db TO dgmts_tasks_user;

GRANT USAGE ON SCHEMA tasks TO dgmts_tasks_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA tasks TO dgmts_tasks_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA tasks TO dgmts_tasks_user;
-- The workflow functions are SECURITY DEFINER; the schema script revokes them from
-- PUBLIC, so the module's role needs EXECUTE explicitly.
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA tasks TO dgmts_tasks_user;

ALTER DEFAULT PRIVILEGES IN SCHEMA tasks GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO dgmts_tasks_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA tasks GRANT USAGE, SELECT ON SEQUENCES TO dgmts_tasks_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA tasks GRANT EXECUTE ON FUNCTIONS TO dgmts_tasks_user;

-- Belt-and-suspenders: the role cannot use `public`.
REVOKE ALL ON SCHEMA public FROM dgmts_tasks_user;

ALTER ROLE dgmts_tasks_user SET search_path TO tasks;
