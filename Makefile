PYTHON ?= python3
VENV ?= .venv
RUN = $(VENV)/bin/python
HOST ?= 0.0.0.0
PORT ?= 8050

.PHONY: setup pipeline dashboard test

setup:
	$(PYTHON) -m venv $(VENV)
	$(RUN) -m pip install -r requirements.txt

pipeline:
	$(RUN) load_data.py
	$(RUN) analysis.py
	$(RUN) build_site.py

dashboard:
	$(RUN) dashboard.py --host $(HOST) --port $(PORT)

test:
	$(RUN) -m pytest -q
