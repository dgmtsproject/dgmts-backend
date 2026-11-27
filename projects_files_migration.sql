-- Run ONCE on an existing server (as the postgres superuser) to add file attachments
-- on project notes. Already included in projects_schema.sql for fresh installs.
-- ---------- Files attached to a project's notes ----------
CREATE TABLE IF NOT EXISTS projects.project_note_files (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id   UUID NOT NULL REFERENCES projects.archive_projects(id) ON DELETE CASCADE,
    file_name    TEXT NOT NULL,
    content_type TEXT,
    size_bytes   BIGINT NOT NULL,
    data         BYTEA NOT NULL,
    uploaded_by  TEXT,
    uploaded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_project_note_files_project ON projects.project_note_files (project_id, uploaded_at DESC);

GRANT SELECT, INSERT, UPDATE, DELETE ON projects.project_note_files TO dgmts_projects_user;
