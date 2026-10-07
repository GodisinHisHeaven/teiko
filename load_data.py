"""Validate cell-count.csv and atomically build cell_counts.db. No dependencies."""

from __future__ import annotations

import csv
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import tempfile

ROOT = Path(__file__).resolve().parent
CSV_PATH = ROOT / "cell-count.csv"
DB_PATH = ROOT / "cell_counts.db"
POPULATIONS = ("b_cell", "cd8_t_cell", "cd4_t_cell", "nk_cell", "monocyte")
SUBJECT_FIELDS = ("project", "condition", "age", "sex", "treatment", "response")
REQUIRED_FIELDS = {
    "project", "subject", "condition", "age", "sex", "treatment", "response",
    "sample", "sample_type", "time_from_treatment_start", *POPULATIONS,
}
SQLITE_MAX = 2**63 - 1


def parse_integer(value: str, field: str, line: int, nonnegative: bool = True) -> int:
    if not re.fullmatch(r"-?\d+", value):
        raise ValueError(f"Line {line}: {field} must be an integer, got {value!r}")
    number = int(value)
    if abs(number) > SQLITE_MAX or (nonnegative and number < 0):
        raise ValueError(f"Line {line}: {field} is outside its allowed range")
    return number


def read_and_validate(csv_path: Path) -> tuple[list[dict], dict[str, tuple]]:
    rows, subjects, seen = [], {}, set()
    with csv_path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)):
            raise ValueError("CSV has duplicate column names")
        missing = REQUIRED_FIELDS - set(fields)
        if missing:
            raise ValueError(f"CSV is missing columns: {', '.join(sorted(missing))}")
        for line, raw in enumerate(reader, start=2):
            if None in raw or any(raw.get(k) is None for k in REQUIRED_FIELDS):
                raise ValueError(f"Line {line}: wrong number of columns")
            row = {key: raw[key].strip() for key in REQUIRED_FIELDS}
            for key in REQUIRED_FIELDS - {"response"}:
                if not row[key]:
                    raise ValueError(f"Line {line}: missing {key}")
            if row["sample"] in seen:
                raise ValueError(f"Line {line}: duplicate sample {row['sample']}")
            seen.add(row["sample"])
            if row["response"] not in ("yes", "no", ""):
                raise ValueError(f"Line {line}: response must be yes, no, or blank")
            if row["sex"] not in ("M", "F"):
                raise ValueError(f"Line {line}: sex must be M or F")
            row["response"] = row["response"] or None
            row["age"] = parse_integer(row["age"], "age", line)
            row["time_from_treatment_start"] = parse_integer(
                row["time_from_treatment_start"], "time_from_treatment_start", line, False
            )
            for population in POPULATIONS:
                row[population] = parse_integer(row[population], population, line)
            if sum(row[p] for p in POPULATIONS) > SQLITE_MAX:
                raise ValueError(f"Line {line}: total count exceeds SQLite integer range")
            metadata = tuple(row[k] for k in SUBJECT_FIELDS)
            subject = row["subject"]
            if subject in subjects and subjects[subject] != metadata:
                raise ValueError(f"Line {line}: conflicting metadata for subject {subject}")
            subjects[subject] = metadata
            rows.append(row)
    if not rows:
        raise ValueError("CSV contains no samples")
    return rows, subjects


def load_database(csv_path: Path = CSV_PATH, db_path: Path = DB_PATH) -> dict:
    csv_path, db_path = Path(csv_path), Path(db_path)
    rows, subjects = read_and_validate(csv_path)
    sha256 = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".cell-counts-", suffix=".db", dir=db_path.parent)
    os.close(descriptor)
    try:
        with sqlite3.connect(temporary) as connection:
            connection.executescript((ROOT / "schema.sql").read_text())
            connection.executemany("INSERT INTO projects VALUES (?)", [(p,) for p in sorted({r['project'] for r in rows})])
            connection.executemany("INSERT INTO populations VALUES (?, ?)", [(p, i) for i, p in enumerate(POPULATIONS)])
            connection.executemany("INSERT INTO subjects VALUES (?, ?, ?, ?, ?, ?, ?)", [(s, *meta) for s, meta in subjects.items()])
            connection.executemany("INSERT INTO samples VALUES (?, ?, ?, ?)", [
                (r["sample"], r["subject"], r["sample_type"], r["time_from_treatment_start"]) for r in rows
            ])
            connection.executemany("INSERT INTO cell_counts VALUES (?, ?, ?)", [
                (r["sample"], p, r[p]) for r in rows for p in POPULATIONS
            ])
            connection.executemany("INSERT INTO load_metadata VALUES (?, ?)", [
                ("source_file", csv_path.name), ("source_sha256", sha256),
                ("sample_count", str(len(rows))), ("schema_version", "1"),
            ])
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Foreign key validation failed")
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("SQLite integrity check failed")
        # Close before replace, including on Windows. A bad input leaves the old DB intact.
        connection.close()
        os.replace(temporary, db_path)
    finally:
        if 'connection' in locals():
            connection.close()
        Path(temporary).unlink(missing_ok=True)
    return {"samples": len(rows), "subjects": len(subjects), "counts": len(rows) * len(POPULATIONS), "source_sha256": sha256}


if __name__ == "__main__":
    result = load_database()
    print(f"Created {DB_PATH.name}: {result['samples']:,} samples, "
          f"{result['subjects']:,} subjects, {result['counts']:,} cell counts.")
