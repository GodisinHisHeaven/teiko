"""Serve the interactive dashboard from the SQLite database, without write access."""

from functools import lru_cache
import argparse
import csv
import io
import json
from pathlib import Path

from flask import Flask, Response, jsonify, send_from_directory
from plotly.offline import get_plotlyjs

from analysis import build_payload, connect_database, frequency_table
from load_data import DB_PATH, ROOT


def create_app(db_path: Path = DB_PATH) -> Flask:
    app = Flask(__name__, static_folder=str(ROOT / "static"), static_url_path="/assets")
    db_path = Path(db_path)

    @lru_cache(maxsize=1)
    def cached_payload(_version):
        return build_payload(db_path)

    def payload():
        stat = db_path.stat()
        return cached_payload((stat.st_mtime_ns, stat.st_size, stat.st_ino))

    @app.get("/")
    def index():
        return send_from_directory(ROOT / "static", "index.html")

    @app.get("/plotly.min.js")
    def plotly():
        return Response(get_plotlyjs(), mimetype="application/javascript", headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/data.json")
    def data():
        return Response(json.dumps(payload(), allow_nan=False, separators=(",", ":")), mimetype="application/json")

    @app.get("/downloads/<name>.csv")
    def download(name):
        if name == "sample_frequencies":
            with connect_database(db_path) as connection:
                content = frequency_table(connection).to_csv(index=False)
        else:
            data = payload()
            tables = {"response_statistics": data["response"]["statistics"],
                      "baseline_statistics": data["response"]["baseline_statistics"],
                      "baseline_samples": data["baseline"]["samples"],
                      "baseline_by_project": data["baseline"]["by_project"],
                      "baseline_by_response": data["baseline"]["by_response"],
                      "baseline_by_sex": data["baseline"]["by_sex"],
                      "subject_frequencies": data["response"]["subjects"]}
            if name not in tables:
                return jsonify(error="Unknown export"), 404
            output = io.StringIO()
            rows = tables[name]
            if rows:
                writer = csv.DictWriter(output, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            content = output.getvalue()
        return Response(content, mimetype="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})

    @app.get("/healthz")
    def health():
        with connect_database(db_path) as connection:
            count = connection.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        return jsonify(status="ok", samples=count)

    @app.errorhandler(FileNotFoundError)
    def missing_database(_error):
        return jsonify(error="Database is missing. Run make pipeline, then restart the dashboard."), 503

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=8050, type=int)
    args = parser.parse_args()
    if not DB_PATH.is_file():
        parser.exit(1, "Database is missing. Run make pipeline first.\n")
    create_app().run(host=args.host, port=args.port, debug=False)
