-- Run once against STATIC_DB_* (dgmts_static_db) on the VPS.
-- Careers page job openings, managed from /admin/careers.
-- Then add 'careers' to ALLOWED_TABLES in dgmts-backend routes/dgmts_static_routes.py and restart gunicorn.

CREATE TABLE IF NOT EXISTS careers (
    id              SERIAL PRIMARY KEY,
    title           TEXT NOT NULL,
    job_id          TEXT,
    worksite        TEXT,
    description     TEXT,
    responsibilities TEXT,
    requirements    TEXT,
    apply_method    TEXT NOT NULL DEFAULT 'email'
                    CHECK (apply_method IN ('email', 'other')),
    apply_details   TEXT NOT NULL,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    is_published    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_careers_published_sort
    ON careers (is_published, sort_order);

-- Seed the two openings that were previously hardcoded in src/pages/CareerPage/jobData.js.
-- Only runs while the table is empty, so re-running this file never duplicates them.
INSERT INTO careers (title, job_id, worksite, description, responsibilities, requirements, apply_method, apply_details, sort_order)
SELECT * FROM (VALUES
    ($q$Staff Geotechnical Engineer and Construction Inspectors$q$, NULL, NULL, $q$Currently we are seeking a Staff Geotechnical Engineer and Construction Inspectors:

Perform construction material testing in the field (Soil, and Concrete). Perform general lab testing of soil and concrete such as hydrometer test, moisture density test, sulfate testing and soils strength test per the applicable ASTM procedures. Perform soil density tests and DCP tests on field, evaluated sub-grade for pavement and drainage structures, prepared pile driving logs using Saximeter, perform reinforcement steel inspection for different substructures including pile plug, pile cap and pedestals. Reviews test results and submit strength data to project managers.$q$, NULL, $q$Requires a bachelor's degree in civil engineering. Must have a minimum of total 9 undergraduate level credit hours in Design of refined concreate, Strength of Materials and Soil Mechanics.$q$, $q$email$q$, $q$info@dullesgeotechnical.com$q$, 0),
    ($q$Geotechnical Instrumentation Engineer$q$, $q$GE0319$q$, $q$2024 Exploration Way, Hampton, VA, 23666$q$, NULL, $q$Develop and implement geotechnical instrumentation systems. Oversee the installation, operation, maintenance, and repair of mechanical components in geotechnical instruments and equipment, including pile dynamic analyzers (PDA), vibration monitoring devices, inclinometers, piezometers, and groundwater measurement devices. Review technical documents to plan and execute work effectively. Maintain and repair laboratory equipment, performing necessary laboratory tests. Obtain certification for PDA testing. Research, install, operate, and maintain geotechnical instruments, drilling equipment, and laboratory tools to meet project requirements. Conduct mechanical testing on construction materials to determine properties such as strength, density, composition, and moisture content. Analyse test data to ensure materials meet required specifications and quality standards. Prepare detailed reports summarising test results and findings. Support the engineering and quality teams by preparing engineering reports, using CAD models, and detailing the performance of various instruments and equipment.$q$, $q$Bachelor’s Degree in Mechanical Engineering or related field. Proficient in CAD software, including AutoCAD and MicroStation.$q$, $q$other$q$, $q$Mail your CV with Job ID#: GE0319 to 14155 Sullyfield Circle, Ste H, Chantilly, VA 20151.$q$, 1)
) AS v(title, job_id, worksite, description, responsibilities, requirements, apply_method, apply_details, sort_order)
WHERE NOT EXISTS (SELECT 1 FROM careers);
