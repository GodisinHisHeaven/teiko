"""Database queries, subject-level inference, and reproducible report exports."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd
from scipy import stats

from load_data import DB_PATH, POPULATIONS, ROOT

REPORT_DIR = ROOT / "reports"
ALPHA = 0.05
BOOTSTRAP_RESAMPLES = 2000
SEED = 20261007
COHORT_SQL = """
SELECT m.*, f.total_count, f.population, f.count, f.percentage
FROM sample_metadata AS m JOIN sample_frequencies AS f USING (sample)
WHERE m.condition = 'melanoma' AND m.treatment = 'miraclib'
  AND m.sample_type = 'PBMC' AND m.response IN ('yes', 'no')
"""


@contextmanager
def connect_database(path: Path = DB_PATH):
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Database not found: {path.name}. Run make pipeline first.")
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        yield connection
    finally:
        connection.close()


def frequency_table(connection) -> pd.DataFrame:
    return pd.read_sql_query("""
        SELECT f.sample, f.total_count, f.population, f.count, f.percentage
        FROM sample_frequencies AS f JOIN populations AS p USING (population)
        ORDER BY f.sample, p.display_order
    """, connection)


def subject_frequencies(cohort: pd.DataFrame, baseline: bool = False) -> pd.DataFrame:
    eligible = cohort[cohort["time_from_treatment_start"].eq(0)] if baseline else cohort
    # Each subject contributes one equally weighted value per population.
    # This is the average of sample percentages, not a pooled cell-count ratio.
    return (eligible.dropna(subset=["percentage"])
            .groupby(["subject", "project", "response", "population"], as_index=False)
            .agg(percentage=("percentage", "mean"), sample_count=("sample", "nunique")))


def holm_adjust(pvalues: list[float]) -> np.ndarray:
    """Holm step-down adjustment controls familywise error under dependence."""
    values = np.asarray(pvalues, dtype=float)
    order = np.argsort(values, kind="stable")
    adjusted = np.minimum(1.0, np.maximum.accumulate(values[order] * np.arange(len(values), 0, -1)))
    result = np.empty_like(adjusted)
    result[order] = adjusted
    return result


def compare_responses(subject_data: pd.DataFrame) -> pd.DataFrame:
    results = []
    for index, population in enumerate(POPULATIONS):
        subset = subject_data[subject_data["population"].eq(population)]
        yes = subset.loc[subset["response"].eq("yes"), "percentage"].to_numpy(dtype=float)
        no = subset.loc[subset["response"].eq("no"), "percentage"].to_numpy(dtype=float)
        row = {"population": population, "n_responders": len(yes), "n_nonresponders": len(no),
               "mean_responders": float(yes.mean()) if len(yes) else None,
               "mean_nonresponders": float(no.mean()) if len(no) else None,
               "median_responders": float(np.median(yes)) if len(yes) else None,
               "median_nonresponders": float(np.median(no)) if len(no) else None}
        if min(len(yes), len(no)) < 2:
            row.update({key: None for key in ("mean_difference_pp", "ci_low", "ci_high", "u_statistic", "p_value", "cliffs_delta")})
            row["status"] = "Insufficient subjects (at least 2 per group required)"
        else:
            test = stats.mannwhitneyu(yes, no, alternative="two-sided", method="asymptotic", use_continuity=True)
            rng = np.random.default_rng(SEED + index)
            differences = (rng.choice(yes, (BOOTSTRAP_RESAMPLES, len(yes)), replace=True).mean(axis=1)
                           - rng.choice(no, (BOOTSTRAP_RESAMPLES, len(no)), replace=True).mean(axis=1))
            low, high = np.quantile(differences, [0.025, 0.975])
            row.update(mean_difference_pp=float(yes.mean() - no.mean()), ci_low=float(low), ci_high=float(high),
                       u_statistic=float(test.statistic), p_value=float(test.pvalue),
                       cliffs_delta=float(2 * test.statistic / (len(yes) * len(no)) - 1), status="ok")
        results.append(row)
    # Untestable populations occupy their original place in the five-test family.
    adjusted = holm_adjust([r["p_value"] if r["p_value"] is not None else 1.0 for r in results])
    for row, value in zip(results, adjusted):
        row["p_holm"] = float(value) if row["p_value"] is not None else None
        row["significant"] = bool(row["p_holm"] is not None and row["p_holm"] < ALPHA)
    return pd.DataFrame(results)


def baseline_summary(connection) -> dict:
    samples = pd.read_sql_query("SELECT * FROM baseline_miraclib_melanoma_pbmc ORDER BY project, subject, sample", connection)
    projects = pd.read_sql_query("""
        SELECT project, COUNT(*) AS samples, COUNT(DISTINCT subject) AS subjects
        FROM baseline_miraclib_melanoma_pbmc GROUP BY project ORDER BY project
    """, connection)
    response = pd.read_sql_query("""
        SELECT COALESCE(response, 'unknown') AS response, COUNT(DISTINCT subject) AS subjects
        FROM baseline_miraclib_melanoma_pbmc GROUP BY response ORDER BY response
    """, connection)
    sex = pd.read_sql_query("""
        SELECT sex, COUNT(DISTINCT subject) AS subjects
        FROM baseline_miraclib_melanoma_pbmc GROUP BY sex ORDER BY sex
    """, connection)
    # Deliberately no sample_type or treatment predicate for this broader question.
    mean = connection.execute("""
        SELECT AVG(c.count), COUNT(*), COUNT(DISTINCT m.subject)
        FROM sample_metadata AS m JOIN cell_counts AS c USING (sample)
        WHERE m.condition = 'melanoma' AND m.sex = 'M' AND m.response = 'yes'
          AND m.time_from_treatment_start = 0 AND c.population = 'b_cell'
    """).fetchone()
    return {"samples": records(samples), "sample_count": len(samples), "subject_count": int(samples["subject"].nunique()),
            "by_project": records(projects), "by_response": records(response), "by_sex": records(sex),
            "male_responder_b_cells": {"mean": mean[0], "formatted": f"{mean[0]:.2f}" if mean[0] is not None else "n/a",
                                       "samples": mean[1], "subjects": mean[2]}}


def records(frame: pd.DataFrame) -> list[dict]:
    # pandas converts numpy scalars and missing values to standards-compliant JSON.
    return json.loads(frame.to_json(orient="records", double_precision=12))


def build_payload(db_path: Path = DB_PATH) -> dict:
    with connect_database(db_path) as connection:
        metadata = dict(connection.execute("SELECT key, value FROM load_metadata"))
        sample_data = pd.read_sql_query("""
            SELECT m.*, t.total_count FROM sample_metadata AS m
            JOIN sample_totals AS t USING (sample) ORDER BY m.sample
        """, connection)
        frequencies = frequency_table(connection)
        cohort = pd.read_sql_query(COHORT_SQL + " ORDER BY m.subject, m.sample, f.population", connection)
        baseline = baseline_summary(connection)
        primary_subjects = subject_frequencies(cohort)
        baseline_subjects = subject_frequencies(cohort, baseline=True)
        primary_stats = compare_responses(primary_subjects)
        baseline_stats = compare_responses(baseline_subjects)
        counts = frequencies.pivot(index="sample", columns="population", values="count")
        wide = sample_data.join(counts, on="sample")
        eligible_samples = cohort.drop_duplicates("sample")
        available = eligible_samples[eligible_samples["total_count"].gt(0)]
        return {
            "metadata": metadata,
            "populations": list(POPULATIONS),
            "overview": {"samples": len(sample_data), "subjects": int(sample_data["subject"].nunique()),
                         "projects": int(sample_data["project"].nunique()), "frequency_rows": len(frequencies),
                         "zero_total_samples": int(sample_data["total_count"].eq(0).sum()),
                         "missing_response_samples": int(sample_data["response"].isna().sum())},
            "samples": records(wide),
            "response": {
                "sample_count": len(eligible_samples), "subject_count": int(eligible_samples["subject"].nunique()),
                "analyzed_sample_count": len(available), "analyzed_subject_count": int(available["subject"].nunique()),
                "excluded_zero_total_samples": int(eligible_samples["total_count"].eq(0).sum()),
                "subjects": records(primary_subjects), "baseline_subjects": records(baseline_subjects),
                "statistics": records(primary_stats), "baseline_statistics": records(baseline_stats),
            },
            "baseline": baseline,
            "method": {"test": "Two-sided Mann–Whitney U", "correction": "Holm (five populations)",
                       "alpha": ALPHA, "unit": "subject", "bootstrap_resamples": BOOTSTRAP_RESAMPLES, "seed": SEED},
        }


def write_reports(db_path: Path = DB_PATH, output_dir: Path = REPORT_DIR) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = build_payload(db_path)
    with connect_database(db_path) as connection:
        frequency_table(connection).to_csv(output_dir / "sample_frequencies.csv", index=False)
        pd.read_sql_query(COHORT_SQL, connection).to_csv(output_dir / "response_cohort.csv", index=False)
    for name, data in (
        ("response_statistics", payload["response"]["statistics"]),
        ("baseline_statistics", payload["response"]["baseline_statistics"]),
        ("subject_frequencies", payload["response"]["subjects"]),
        ("baseline_samples", payload["baseline"]["samples"]),
        ("baseline_by_project", payload["baseline"]["by_project"]),
        ("baseline_by_response", payload["baseline"]["by_response"]),
        ("baseline_by_sex", payload["baseline"]["by_sex"]),
    ):
        pd.DataFrame(data).to_csv(output_dir / f"{name}.csv", index=False)
    (output_dir / "analysis.json").write_text(json.dumps(payload, allow_nan=False, separators=(",", ":")))
    significant = [r["population"] for r in payload["response"]["statistics"] if r["significant"]]
    baseline_significant = [r["population"] for r in payload["response"]["baseline_statistics"] if r["significant"]]
    summary = {
        "source_sha256": payload["metadata"]["source_sha256"], **payload["overview"],
        "response_cohort_samples": payload["response"]["sample_count"],
        "response_cohort_subjects": payload["response"]["subject_count"],
        "significant_populations": significant, "baseline_significant_populations": baseline_significant,
        "baseline": {k: v for k, v in payload["baseline"].items() if k != "samples"},
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    print(f"Exported {payload['overview']['frequency_rows']:,} frequency rows to reports/.")
    print(f"Significant populations (subject means, Holm p < 0.05): {', '.join(significant) or 'none'}")
    print(f"Male melanoma responders at baseline, all treatments / sample types: {payload['baseline']['male_responder_b_cells']['formatted']}")
    return payload


if __name__ == "__main__":
    write_reports()
