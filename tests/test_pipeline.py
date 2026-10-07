from __future__ import annotations

import csv
import hashlib
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from analysis import baseline_summary, build_payload, compare_responses, connect_database, frequency_table, holm_adjust, subject_frequencies
from dashboard import create_app
from load_data import CSV_PATH, POPULATIONS, ROOT, load_database


def row(**overrides):
    return {"project": "p1", "subject": "s1", "condition": "melanoma", "age": "60", "sex": "M",
            "treatment": "miraclib", "response": "yes", "sample": "sample1", "sample_type": "PBMC",
            "time_from_treatment_start": "0", "b_cell": "10", "cd8_t_cell": "20", "cd4_t_cell": "30",
            "nk_cell": "15", "monocyte": "25", **overrides}


def write_csv(path, rows):
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(row()))
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.fixture(scope="module")
def full_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("full") / "test.db"
    load_database(CSV_PATH, path)
    return path


def test_standalone_loader_creates_db_beside_script(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for name in ("load_data.py", "schema.sql"):
        shutil.copyfile(ROOT / name, repo / name)
    write_csv(repo / "cell-count.csv", [row()])
    # No arguments and no dependency installation; also independent of working directory.
    subprocess.run([sys.executable, str(repo / "load_data.py")], cwd=tmp_path, check=True, capture_output=True)
    assert (repo / "cell_counts.db").exists()
    assert not (tmp_path / "cell_counts.db").exists()


def test_all_source_rows_and_counts_round_trip(full_db):
    source = pd.read_csv(CSV_PATH).set_index("sample")
    with connect_database(full_db) as db:
        restored = pd.read_sql_query("SELECT * FROM sample_metadata", db).set_index("sample")
        frequencies = frequency_table(db)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    counts = frequencies.pivot(index="sample", columns="population", values="count")
    for column in restored.columns:
        pd.testing.assert_series_equal(restored[column].fillna("").sort_index(), source[column].fillna("").sort_index(), check_names=False)
    for column in POPULATIONS:
        pd.testing.assert_series_equal(counts[column].sort_index(), source[column].sort_index(), check_names=False)
    assert len(restored) == 10500
    assert len(frequencies) == 52500
    assert restored["subject"].nunique() == 3500
    assert list(frequencies) == ["sample", "total_count", "population", "count", "percentage"]
    np.testing.assert_allclose(frequencies.groupby("sample")["percentage"].sum(), 100)
    totals = source[list(POPULATIONS)].sum(axis=1)
    np.testing.assert_allclose(frequencies["total_count"], frequencies["sample"].map(totals))


def test_loader_is_idempotent(tmp_path):
    csv_path = write_csv(tmp_path / "data.csv", [row(), row(sample="sample2", time_from_treatment_start="7")])
    db_path = tmp_path / "test.db"
    first = load_database(csv_path, db_path)
    second = load_database(csv_path, db_path)
    assert first == second
    with connect_database(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM subjects").fetchone()[0] == 1


@pytest.mark.parametrize("invalid", [
    [row(), row()],
    [row(), row(sample="sample2", age="61")],
    [row(b_cell="-1")], [row(b_cell="1.5")], [row(b_cell="")],
    [row(response="maybe")], [row(sex="unknown")], [row(subject="")],
    [row(time_from_treatment_start="NaN")], [row(b_cell=str(2**63 - 1))],
])
def test_invalid_input_preserves_existing_database(tmp_path, invalid):
    csv_path = write_csv(tmp_path / "data.csv", [row()])
    db_path = tmp_path / "test.db"
    load_database(csv_path, db_path)
    previous = hashlib.sha256(db_path.read_bytes()).hexdigest()
    write_csv(csv_path, invalid)
    with pytest.raises(ValueError):
        load_database(csv_path, db_path)
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == previous


def test_missing_columns_and_empty_file_are_rejected(tmp_path):
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text("sample,b_cell\na,1\n")
    with pytest.raises(ValueError, match="missing columns"):
        load_database(csv_path, tmp_path / "test.db")
    write_csv(csv_path, [])
    with pytest.raises(ValueError, match="no samples"):
        load_database(csv_path, tmp_path / "test.db")


def test_zero_totals_are_null_and_missing_response_is_preserved(tmp_path):
    csv_path = write_csv(tmp_path / "zero.csv", [row(response="", **{p: "0" for p in POPULATIONS})])
    db_path = tmp_path / "zero.db"
    load_database(csv_path, db_path)
    with connect_database(db_path) as db:
        rows = db.execute("SELECT total_count, percentage FROM sample_frequencies").fetchall()
        assert rows == [(0, None)] * 5
        assert db.execute("SELECT response FROM subjects").fetchone()[0] is None
    payload = build_payload(db_path)
    assert payload["overview"]["zero_total_samples"] == 1
    assert payload["overview"]["missing_response_samples"] == 1
    assert not any(s["significant"] for s in payload["response"]["statistics"])


def test_baseline_queries_and_broader_average(full_db):
    with connect_database(full_db) as db:
        baseline = baseline_summary(db)
    assert baseline["sample_count"] == baseline["subject_count"] == 656
    assert {r["project"]: r["samples"] for r in baseline["by_project"]} == {"prj1": 384, "prj3": 272}
    assert {r["response"]: r["subjects"] for r in baseline["by_response"]} == {"yes": 331, "no": 325}
    assert {r["sex"]: r["subjects"] for r in baseline["by_sex"]} == {"M": 344, "F": 312}
    answer = baseline["male_responder_b_cells"]
    assert answer["formatted"] == "10206.15"
    assert answer["samples"] == answer["subjects"] == 485
    # Independently check the broad scope, without applying PBMC/miraclib filters.
    source = pd.read_csv(CSV_PATH)
    expected = source.query("condition == 'melanoma' and sex == 'M' and response == 'yes' and time_from_treatment_start == 0")
    assert answer["mean"] == pytest.approx(expected.b_cell.mean())
    assert set(expected.treatment) == {"miraclib", "phauximab"}
    assert set(expected.sample_type) == {"PBMC", "WB"}


def test_subject_counts_do_not_count_samples_twice(tmp_path):
    source = write_csv(tmp_path / "two.csv", [row(), row(sample="sample2")])
    database = tmp_path / "test.db"
    load_database(source, database)
    with connect_database(database) as db:
        b = baseline_summary(db)
    assert b["sample_count"] == 2
    assert b["subject_count"] == 1
    assert b["by_response"] == [{"response": "yes", "subjects": 1}]
    assert b["by_sex"] == [{"sex": "M", "subjects": 1}]


def test_repeated_visits_collapse_to_one_subject_value():
    cohort = pd.DataFrame([
        {"subject": "a", "project": "p", "response": "yes", "population": "b_cell", "sample": "a0", "percentage": 10., "time_from_treatment_start": 0},
        {"subject": "a", "project": "p", "response": "yes", "population": "b_cell", "sample": "a7", "percentage": 30., "time_from_treatment_start": 7},
        {"subject": "b", "project": "p", "response": "no", "population": "b_cell", "sample": "b0", "percentage": 5., "time_from_treatment_start": 0},
    ])
    result = subject_frequencies(cohort)
    assert len(result) == 2
    assert result.loc[result.subject.eq("a"), "percentage"].item() == 20
    assert subject_frequencies(cohort, baseline=True).loc[lambda x: x.subject.eq("a"), "percentage"].item() == 10


def test_holm_known_values_and_ties():
    np.testing.assert_allclose(holm_adjust([.5, .02, .2, .01, .03]), [.5, .08, .4, .05, .09])
    np.testing.assert_allclose(holm_adjust([.01, .01, 1, 1, 1]), [.05, .05, 1, 1, 1])


def test_statistics_constant_data_and_effect_direction():
    frame = pd.DataFrame([{"population": p, "response": response, "percentage": value}
                          for p in POPULATIONS for response in ("yes", "no") for value in (20., 20., 20.)])
    result = compare_responses(frame)
    assert result["p_value"].eq(1).all()
    assert result["cliffs_delta"].eq(0).all()
    assert not result["significant"].any()
    frame.loc[frame.response.eq("yes"), "percentage"] = 30.
    higher = compare_responses(frame)
    assert higher["mean_difference_pp"].eq(10).all()
    assert higher["cliffs_delta"].eq(1).all()


def test_full_analysis_is_deterministic_and_uses_subjects(full_db):
    first, second = build_payload(full_db), build_payload(full_db)
    assert first == second
    assert first["response"]["sample_count"] == 1968
    assert first["response"]["subject_count"] == 656
    for result in first["response"]["statistics"]:
        assert result["n_responders"] == 331
        assert result["n_nonresponders"] == 325
        assert result["p_holm"] >= result["p_value"]
        assert result["ci_low"] <= result["mean_difference_pp"] <= result["ci_high"]


def test_dashboard_routes_and_exports(full_db):
    client = create_app(full_db).test_client()
    assert client.get("/").status_code == 200
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/assets/style.css").status_code == 200
    assert client.get("/healthz").json == {"status": "ok", "samples": 10500}
    response = client.get("/data.json")
    assert response.status_code == 200
    assert response.json["baseline"]["male_responder_b_cells"]["formatted"] == "10206.15"
    frequencies = client.get("/downloads/sample_frequencies.csv")
    assert len(frequencies.text.splitlines()) == 52501
    assert frequencies.text.splitlines()[0] == "sample,total_count,population,count,percentage"
    assert client.get("/downloads/baseline_samples.csv").status_code == 200
    assert client.get("/downloads/unknown.csv").status_code == 404


def test_missing_db_gives_actionable_error(tmp_path):
    client = create_app(tmp_path / "missing.db").test_client()
    assert client.get("/data.json").status_code == 503
    assert "make pipeline" in client.get("/data.json").json["error"]


def test_foreign_keys_and_negative_counts_are_constrained(full_db):
    with sqlite3.connect(full_db) as db:
        db.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO cell_counts VALUES ('not-a-sample', 'b_cell', 1)")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE cell_counts SET count = -1 WHERE sample = 'sample00000'")
