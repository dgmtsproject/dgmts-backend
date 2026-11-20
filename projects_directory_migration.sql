-- Run ONCE on an existing server (as the postgres superuser) to add the employee
-- directory cache used by the super user's Access page. Already included in
-- projects_schema.sql for fresh installs.
CREATE TABLE IF NOT EXISTS projects.employee_directory (
    user_id     TEXT PRIMARY KEY,
    name        TEXT,
    email       TEXT,
    job_title   TEXT,
    department  TEXT,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_employee_directory_email ON projects.employee_directory (lower(email));
GRANT SELECT, INSERT, UPDATE, DELETE ON projects.employee_directory TO dgmts_projects_user;
