.PHONY: setup run run-api init-sqlite init-graph test eval build

PYTHON := .venv/bin/python

setup:
	python -m venv .venv
	$(PYTHON) -m pip install -U pip
	$(PYTHON) -m pip install -e './backend[dev]'
	npm --prefix frontend ci

run:
	$(PYTHON) backend/scripts/run_local.py

run-api:
	$(PYTHON) backend/scripts/run_local.py --no-frontend

init-sqlite:
	cd backend && ../$(PYTHON) -m scripts.init_sqlite --seed-demo

init-graph:
	cd backend && ../$(PYTHON) -m scripts.init_data

test:
	cd backend && ../$(PYTHON) -m pytest -q

eval:
	cd backend && ../$(PYTHON) -m scripts.evaluate

build:
	npm --prefix frontend run build
