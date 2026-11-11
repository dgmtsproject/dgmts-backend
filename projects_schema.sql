-- ======================================================================
-- DGMTS Projects Directory — schema for the `projects` schema.
-- Ported from the Supabase `public.archive_*` tables (plus the access table
-- added for the standalone Projects app).
--
-- Isolation: everything lives in the `projects` schema of dgmts_static_db.
-- Run once as a superuser/owner, THEN projects_db_setup.sql (role + grants).
-- Then load the data with projects_data_migrate.py.
--
-- NOTE: the *_range_* columns hold year-month text such as '2024-03' (not full dates).
-- Column types were derived from the live data; projects_data_migrate.py
-- compares them with Supabase's information_schema and reports any mismatch
-- before copying.
-- ======================================================================

CREATE SCHEMA IF NOT EXISTS projects;
SET search_path TO projects;

-- ---------- Lookup ----------
CREATE TABLE IF NOT EXISTS projects.archive_service_types (
    id   SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

-- ---------- Projects ----------
CREATE TABLE IF NOT EXISTS projects.archive_projects (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_number        TEXT NOT NULL UNIQUE,
    project_name          TEXT,
    client                TEXT,
    is_verified           BOOLEAN DEFAULT FALSE,
    address               TEXT,
    city                  TEXT,
    state                 TEXT,
    county                TEXT,
    zip                   TEXT,
    project_summary       TEXT,
    service_type          TEXT,
    fee_type              TEXT,
    proposal_no           TEXT,
    proposal_date         DATE,
    agreement_date        DATE,
    dgmts_project_manager TEXT,
    dgmts_principal       TEXT,
    data_range_start      TEXT,
    data_range_end        TEXT,
    dfr_range_start       TEXT,
    dfr_range_end         TEXT,
    invoice_range_start   TEXT,
    invoice_range_end     TEXT,
    appears_in_zips       TEXT[] DEFAULT '{}',
    attachment_count      INTEGER DEFAULT 0,
    extra                 JSONB DEFAULT '{}'::jsonb,
    created_at            TIMESTAMPTZ DEFAULT now(),
    fee_amount            TEXT,
    client_aka            TEXT[] DEFAULT '{}',
    service_tags          TEXT[] DEFAULT '{}',
    enrichment_confidence TEXT,
    enrichment_notes      TEXT,
    evidence_files        TEXT[] DEFAULT '{}',
    enriched_from         TEXT[] DEFAULT '{}',
    year                  INTEGER,
    data_source           TEXT,
    service_sector        TEXT,
    sub_sector            TEXT,
    department            TEXT,
    branch_office         TEXT,
    generate_date         DATE,
    due_date              DATE,
    follow_up_date        DATE,
    poc_name              TEXT,
    poc_email             TEXT,
    poc_phone             TEXT,
    submitted_by          TEXT,
    project_manager_1     TEXT,
    project_manager_2     TEXT,
    archive_service_type  TEXT,
    archive_fee_type      TEXT,
    project_name_archive  TEXT,
    client_archive        TEXT,
    number_aliases        TEXT[] DEFAULT '{}',
    official              JSONB,
    official_alternates   JSONB DEFAULT '[]'::jsonb,
    enriched              JSONB,
    -- SharePoint extraction columns
    tasks_performed                 TEXT[],
    total_invoiced                  NUMERIC(14,2),
    payments_received               NUMERIC(14,2),
    balance_due                     NUMERIC(14,2),
    sharepoint_file_count           INTEGER,
    sharepoint_text_files_extracted INTEGER,
    sharepoint_extracted_at         TIMESTAMPTZ,
    sharepoint_source_year          TEXT,
    has_sharepoint_extraction       BOOLEAN NOT NULL DEFAULT FALSE,
    sharepoint_last_modified        TIMESTAMPTZ,
    -- Edited in the app
    notes                       TEXT,
    notes_updated_at            TIMESTAMPTZ,
    project_writeup             TEXT,
    project_writeup_updated_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_archive_projects_year   ON projects.archive_projects (year);
CREATE INDEX IF NOT EXISTS idx_archive_projects_client ON projects.archive_projects (client);

-- ---------- Child tables ----------
CREATE TABLE IF NOT EXISTS projects.archive_project_pocs (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects.archive_projects(id) ON DELETE CASCADE,
    name       TEXT, title TEXT, company TEXT, email TEXT, phone TEXT, address TEXT, role TEXT,
    source     TEXT
);
CREATE TABLE IF NOT EXISTS projects.archive_project_team (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects.archive_projects(id) ON DELETE CASCADE,
    name       TEXT, title TEXT, company TEXT, email TEXT, phone TEXT, role TEXT,
    source     TEXT
);
CREATE TABLE IF NOT EXISTS projects.archive_project_vendors (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects.archive_projects(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    source     TEXT
);
CREATE TABLE IF NOT EXISTS projects.archive_project_attachments (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id    UUID NOT NULL REFERENCES projects.archive_projects(id) ON DELETE CASCADE,
    relative_path TEXT NOT NULL,
    file_name     TEXT,
    file_ext      TEXT,
    source        TEXT
);
CREATE TABLE IF NOT EXISTS projects.archive_project_invoices (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id     UUID NOT NULL REFERENCES projects.archive_projects(id) ON DELETE CASCADE,
    invoice_number TEXT,
    invoice_date   TEXT,
    amount         NUMERIC(14,2),
    description    TEXT,
    source         TEXT NOT NULL DEFAULT 'sharepoint_2026',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS projects.archive_project_source_folders (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  UUID NOT NULL REFERENCES projects.archive_projects(id) ON DELETE CASCADE,
    year        TEXT,
    folder_path TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects.archive_project_service_types (
    project_id      UUID    NOT NULL REFERENCES projects.archive_projects(id) ON DELETE CASCADE,
    service_type_id INTEGER NOT NULL REFERENCES projects.archive_service_types(id) ON DELETE CASCADE,
    PRIMARY KEY (project_id, service_type_id)
);

CREATE INDEX IF NOT EXISTS idx_pocs_project         ON projects.archive_project_pocs (project_id, source);
CREATE INDEX IF NOT EXISTS idx_team_project         ON projects.archive_project_team (project_id, source);
CREATE INDEX IF NOT EXISTS idx_vendors_project      ON projects.archive_project_vendors (project_id, source);
CREATE INDEX IF NOT EXISTS idx_attachments_project  ON projects.archive_project_attachments (project_id, source);
CREATE INDEX IF NOT EXISTS idx_invoices_project     ON projects.archive_project_invoices (project_id);
CREATE INDEX IF NOT EXISTS idx_source_folders_proj  ON projects.archive_project_source_folders (project_id);

-- ---------- App access (per person, keyed by company-login user_id) ----------
CREATE TABLE IF NOT EXISTS projects.user_access (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_user_id TEXT NOT NULL UNIQUE,
    email            TEXT,
    name             TEXT,
    job_title        TEXT,
    can_dashboard    BOOLEAN NOT NULL DEFAULT FALSE,
    can_projects     BOOLEAN NOT NULL DEFAULT FALSE,
    can_chat         BOOLEAN NOT NULL DEFAULT FALSE,
    can_edit         BOOLEAN NOT NULL DEFAULT FALSE,
    is_admin         BOOLEAN NOT NULL DEFAULT FALSE,
    updated_by       TEXT,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
