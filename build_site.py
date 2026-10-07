"""Publish the database-derived report as the same interactive, static dashboard."""

from pathlib import Path
import shutil

from plotly.offline import get_plotlyjs

from analysis import REPORT_DIR
from load_data import ROOT


def build_site(destination: Path = ROOT / "site") -> None:
    if not (REPORT_DIR / "analysis.json").is_file():
        raise FileNotFoundError("Run analysis.py before building the static dashboard.")
    destination.mkdir(parents=True, exist_ok=True)
    assets = destination / "assets"
    shutil.copytree(ROOT / "static", assets, dirs_exist_ok=True)
    shutil.copyfile(ROOT / "static" / "index.html", destination / "index.html")
    shutil.copyfile(REPORT_DIR / "analysis.json", destination / "data.json")
    (destination / "plotly.min.js").write_text(get_plotlyjs())
    downloads = destination / "downloads"
    downloads.mkdir(exist_ok=True)
    for report in REPORT_DIR.glob("*.csv"):
        shutil.copyfile(report, downloads / report.name)
    (destination / ".nojekyll").touch()
    print("Built site/: interactive dashboard and downloadable database exports.")


if __name__ == "__main__":
    build_site()
