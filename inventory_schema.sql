-- ======================================================================
-- DGMTS Inventory & Purchase Management — authoritative schema.
-- Ported from the LIVE Supabase DB (project wihstqtluuiqzuvyarbc), NOT from
-- the stale frontend supabase-schema.sql. See §3.1 of INVENTORY_MIGRATION_PLAN.md.
--
-- Isolation: everything lives in the `inventory` schema inside the existing
-- dgmts_static_db. Run this as a superuser/owner once; the app connects with
-- a dedicated role (dgmts_inventory_user) scoped to this schema.
--
-- Differences vs Supabase original:
--   * users.id: dropped FK to auth.users; added gen_random_uuid() default.
--   * purchase_requests actor columns (requested_by/supervisor_id/order_placed_by/
--     received_by/approved_rejected_by) are TEXT (hold MS identifiers), as in prod.
--   * RLS + get_user_role() removed (authorization is enforced in Flask).
--   * Excluded (belong to a separate app / unused): inventory_role_tab_permissions,
--     consumable_items, inventory_stock, consumption_logs, suppliers.
-- ======================================================================

-- gen_random_uuid() is built into PostgreSQL core since v13 (this server is
-- PG18), so no pgcrypto extension is required. If you ever run this on PG<13,
-- uncomment: CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS inventory;
SET search_path TO inventory;

-- ---------- Enum types ----------
DO $$ BEGIN
    CREATE TYPE inventory.user_role AS ENUM ('admin', 'employee', 'supervisor', 'accounts');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE inventory.purchase_request_status AS ENUM
        ('Open', 'Rejected', 'Approved', 'Order Placed', 'Delivered');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ---------- Lookup tables ----------
CREATE TABLE IF NOT EXISTS inventory.branches (
    id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory.departments (
    id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL
);

-- ---------- Users (was linked to auth.users; now standalone) ----------
CREATE TABLE IF NOT EXISTS inventory.users (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email      TEXT NOT NULL,
    full_name  TEXT NOT NULL,
    role       inventory.user_role NOT NULL DEFAULT 'employee',
    branch_id  UUID REFERENCES inventory.branches(id) ON DELETE SET NULL,
    barcode_id TEXT NOT NULL UNIQUE
);

-- ---------- Inventory items + logs ----------
CREATE TABLE IF NOT EXISTS inventory.inventory_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    description     TEXT,
    barcode_id      TEXT NOT NULL UNIQUE,
    branch_id       UUID REFERENCES inventory.branches(id) ON DELETE CASCADE,
    quantity        INTEGER NOT NULL DEFAULT 0,
    threshold_value INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS inventory.inventory_logs (
    id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id   UUID REFERENCES inventory.inventory_items(id) ON DELETE CASCADE,
    user_id   UUID REFERENCES inventory.users(id) ON DELETE SET NULL,
    action    TEXT NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT NOW()
);

-- ---------- Purchase requests / orders ----------
-- NOTE: requested_by / supervisor_id / order_placed_by / received_by /
-- approved_rejected_by are TEXT in production (MS identifiers), NOT user FKs.
CREATE TABLE IF NOT EXISTS inventory.purchase_requests (
    po_number            TEXT PRIMARY KEY,           -- auto via trigger
    branch_id            UUID REFERENCES inventory.branches(id) ON DELETE CASCADE,
    request_date         TIMESTAMPTZ DEFAULT NOW(),
    requested_by         TEXT,
    supervisor_id        TEXT,
    department_id        UUID REFERENCES inventory.departments(id) ON DELETE SET NULL,
    quantity             INTEGER NOT NULL,
    item_description     TEXT NOT NULL,
    notes                TEXT,
    status               inventory.purchase_request_status NOT NULL DEFAULT 'Open',
    approval_date        TIMESTAMPTZ,
    order_placed_by      TEXT,
    order_placed_date    TIMESTAMPTZ,
    delivery_date        TIMESTAMPTZ,
    received_by          TEXT,
    approved_rejected_by TEXT,
    rejection_reason     TEXT,
    product_name         TEXT
);

-- ---------- Per-employee PO workflow action permissions (Roles tab) ----------
CREATE TABLE IF NOT EXISTS inventory.inventory_po_action_permissions (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_user_id       TEXT NOT NULL UNIQUE,
    employee_email         TEXT,
    employee_name          TEXT,
    job_title_display      TEXT,
    action_approve         BOOLEAN NOT NULL DEFAULT false,
    action_reject          BOOLEAN NOT NULL DEFAULT false,
    action_order_placed    BOOLEAN NOT NULL DEFAULT false,
    action_delivered       BOOLEAN NOT NULL DEFAULT false,
    action_edit            BOOLEAN NOT NULL DEFAULT false,
    is_purchase_supervisor BOOLEAN NOT NULL DEFAULT false,
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS inventory_po_action_permissions_user_idx
    ON inventory.inventory_po_action_permissions (employee_user_id);

-- ---------- Per-user dashboard tab permissions ----------
CREATE TABLE IF NOT EXISTS inventory.inventory_user_tab_permissions (
    employee_user_id      TEXT PRIMARY KEY,
    employee_email        TEXT,
    employee_name         TEXT,
    job_title_display     TEXT,
    tab_dashboard         BOOLEAN NOT NULL DEFAULT false,
    tab_purchase_requests BOOLEAN NOT NULL DEFAULT false,
    tab_purchase_orders   BOOLEAN NOT NULL DEFAULT false,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_inventory_user_tab_permissions_email
    ON inventory.inventory_user_tab_permissions (employee_email);

-- ---------- Purchase supervisor/approver eligible titles ----------
CREATE TABLE IF NOT EXISTS inventory.purchase_supervisor_job_titles (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_title_normalized TEXT NOT NULL UNIQUE,
    job_title_display    TEXT NOT NULL,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------- Microsoft directory cache ----------
CREATE TABLE IF NOT EXISTS inventory.ms_directory_departments (
    ms_dept_id INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS inventory.ms_directory_employees (
    ms_user_id       BIGINT PRIMARY KEY,
    employee_id      TEXT,
    name             TEXT NOT NULL DEFAULT '',
    email            TEXT,
    email_normalized TEXT,
    phone            TEXT,
    job_title_name   TEXT,
    department_name  TEXT,
    status           INTEGER,
    image            TEXT,
    raw              JSONB DEFAULT '{}'::jsonb,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    qr_code          TEXT,
    qr_uuid          TEXT
);
CREATE INDEX IF NOT EXISTS idx_ms_directory_employees_name_lower
    ON inventory.ms_directory_employees (lower(name));
CREATE INDEX IF NOT EXISTS idx_ms_directory_employees_email_normalized
    ON inventory.ms_directory_employees (email_normalized);
CREATE INDEX IF NOT EXISTS idx_ms_directory_employees_qr_uuid
    ON inventory.ms_directory_employees (qr_uuid);

CREATE TABLE IF NOT EXISTS inventory.ms_directory_meta (
    id                    SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    employees_synced_at   TIMESTAMPTZ,
    departments_synced_at TIMESTAMPTZ
);
INSERT INTO inventory.ms_directory_meta (id) VALUES (1)
    ON CONFLICT (id) DO NOTHING;

-- ---------- PO-number auto-generation trigger ----------
CREATE OR REPLACE FUNCTION inventory.generate_po_number()
RETURNS TRIGGER AS $$
DECLARE
    yr      TEXT;
    seq_val INTEGER;
BEGIN
    yr := to_char(COALESCE(NEW.request_date, NOW()), 'YYYY');
    -- Simplified sequence (matches production). For strict no-race, use a
    -- dedicated sequence table.
    SELECT COUNT(*) + 1 INTO seq_val
        FROM inventory.purchase_requests
        WHERE to_char(request_date, 'YYYY') = yr;
    IF NEW.po_number IS NULL OR NEW.po_number = '' THEN
        NEW.po_number := 'PO-' || yr || '-' || lpad(seq_val::text, 3, '0');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS set_po_number ON inventory.purchase_requests;
CREATE TRIGGER set_po_number
    BEFORE INSERT ON inventory.purchase_requests
    FOR EACH ROW EXECUTE FUNCTION inventory.generate_po_number();

-- ---------- Seed data (safe to keep; ON CONFLICT guards re-runs) ----------
INSERT INTO inventory.branches (name)
    SELECT v FROM (VALUES ('Chantilly'), ('Maryland'), ('Hampton'), ('DC')) AS t(v)
    WHERE NOT EXISTS (SELECT 1 FROM inventory.branches);

INSERT INTO inventory.departments (name)
    SELECT v FROM (VALUES ('IT'), ('HR'), ('Operations'), ('Sales')) AS t(v)
    WHERE NOT EXISTS (SELECT 1 FROM inventory.departments);

INSERT INTO inventory.purchase_supervisor_job_titles (job_title_normalized, job_title_display) VALUES
    ('branch manager', 'Branch Manager'),
    ('department manager', 'Department Manager'),
    ('director', 'Director'),
    ('drilling manager', 'Drilling Manager'),
    ('laboratory manager', 'Laboratory Manager'),
    ('president', 'President'),
    ('accounts officer', 'Accounts Officer'),
    ('admin officer', 'Admin Officer'),
    ('office/hr manager', 'Office/HR Manager'),
    ('office hr manager', 'Office HR Manager'),
    ('office / hr manager', 'Office / HR Manager')
ON CONFLICT (job_title_normalized) DO NOTHING;
