PRAGMA foreign_keys = ON;

CREATE TABLE projects (
    project TEXT PRIMARY KEY NOT NULL
);

-- The source has one project, disease, regimen and response per subject.
-- load_data.py rejects conflicting subject metadata rather than overwriting it.
CREATE TABLE subjects (
    subject TEXT PRIMARY KEY NOT NULL,
    project TEXT NOT NULL REFERENCES projects(project),
    condition TEXT NOT NULL,
    age INTEGER NOT NULL CHECK (age >= 0),
    sex TEXT NOT NULL CHECK (sex IN ('M', 'F')),
    treatment TEXT NOT NULL,
    response TEXT CHECK (response IN ('yes', 'no'))
);

CREATE TABLE samples (
    sample TEXT PRIMARY KEY NOT NULL,
    subject TEXT NOT NULL REFERENCES subjects(subject),
    sample_type TEXT NOT NULL,
    time_from_treatment_start INTEGER NOT NULL
);

CREATE TABLE populations (
    population TEXT PRIMARY KEY NOT NULL,
    display_order INTEGER UNIQUE NOT NULL
);

CREATE TABLE cell_counts (
    sample TEXT NOT NULL REFERENCES samples(sample),
    population TEXT NOT NULL REFERENCES populations(population),
    count INTEGER NOT NULL CHECK (typeof(count) = 'integer' AND count >= 0),
    PRIMARY KEY (sample, population)
);

CREATE TABLE load_metadata (
    key TEXT PRIMARY KEY NOT NULL,
    value TEXT NOT NULL
);

CREATE INDEX idx_subjects_cohort ON subjects(condition, treatment, response);
CREATE INDEX idx_subjects_project ON subjects(project);
CREATE INDEX idx_samples_subject ON samples(subject);
CREATE INDEX idx_samples_type_time ON samples(sample_type, time_from_treatment_start);

CREATE VIEW sample_metadata AS
SELECT s.sample, u.subject, u.project, u.condition, u.age, u.sex,
       u.treatment, u.response, s.sample_type, s.time_from_treatment_start
FROM samples AS s JOIN subjects AS u USING (subject);

CREATE VIEW sample_totals AS
SELECT sample, SUM(count) AS total_count FROM cell_counts GROUP BY sample;

-- A zero-total sample is retained, with an undefined (NULL) frequency.
CREATE VIEW sample_frequencies AS
SELECT c.sample, t.total_count, c.population, c.count,
       100.0 * c.count / NULLIF(t.total_count, 0) AS percentage
FROM cell_counts AS c JOIN sample_totals AS t USING (sample);

CREATE VIEW baseline_miraclib_melanoma_pbmc AS
SELECT * FROM sample_metadata
WHERE condition = 'melanoma' AND treatment = 'miraclib'
  AND sample_type = 'PBMC' AND time_from_treatment_start = 0;
