-- ======================================================================
-- One-time role setup for the Projects Directory module. Run as a
-- superuser on dgmts_static_db AFTER projects_schema.sql.
-- Replace the password first.
-- The role can use ONLY the `projects` schema (no access to public/inventory).
-- ======================================================================
DO $$ BEGIN
    CREATE ROLE dgmts_projects_user LOGIN PASSWORD 'REPLACE_WITH_STRONG_PASSWORD';
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

GRANT CONNECT ON DATABASE dgmts_static_db TO dgmts_projects_user;
GRANT USAGE ON SCHEMA projects TO dgmts_projects_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA projects TO dgmts_projects_user;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA projects TO dgmts_projects_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA projects
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO dgmts_projects_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA projects
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO dgmts_projects_user;

REVOKE ALL ON SCHEMA public FROM dgmts_projects_user;
REVOKE ALL ON SCHEMA inventory FROM dgmts_projects_user;
ALTER ROLE dgmts_projects_user SET search_path TO projects;
