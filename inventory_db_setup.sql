-- ======================================================================
-- One-time role setup for the Inventory module. Run as a superuser/owner on
-- the dgmts_static_db, BEFORE (or after) inventory_schema.sql.
--
-- Creates a dedicated role scoped to the `inventory` schema so the module can
-- NEVER touch the existing `public` (dgmts_static_db) tables.
-- Replace the password before running.
-- ======================================================================

DO $$ BEGIN
    CREATE ROLE dgmts_inventory_user LOGIN PASSWORD 'REPLACE_WITH_STRONG_PASSWORD';
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Allow connecting to the shared database.
GRANT CONNECT ON DATABASE dgmts_static_db TO dgmts_inventory_user;

-- Scope: only the inventory schema. Explicitly NO privileges on public.
GRANT USAGE ON SCHEMA inventory TO dgmts_inventory_user;
GRANT SELECT, INSERT, UPDATE, DELETE
    ON ALL TABLES IN SCHEMA inventory TO dgmts_inventory_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA inventory TO dgmts_inventory_user;

-- Future objects created in inventory also get these grants.
ALTER DEFAULT PRIVILEGES IN SCHEMA inventory
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO dgmts_inventory_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA inventory
    GRANT USAGE, SELECT ON SEQUENCES TO dgmts_inventory_user;

-- Belt-and-suspenders: make sure the role cannot use `public`.
REVOKE ALL ON SCHEMA public FROM dgmts_inventory_user;

-- Pin the role's default search_path to the inventory schema.
ALTER ROLE dgmts_inventory_user SET search_path TO inventory;
