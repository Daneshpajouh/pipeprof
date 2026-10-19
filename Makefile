PY ?= python3
VENV ?= .venv
BIN = $(VENV)/bin

.PHONY: dev check lint type test shell build docs docker clean

dev:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -e ".[dev,docs]" build twine

lint:
	$(BIN)/ruff check src tests
	$(BIN)/ruff format --check src tests

type:
	$(BIN)/mypy

test:
	$(BIN)/pytest --cov=hpc_job_profiler --cov-report=term --cov-fail-under=90

shell:
	shellcheck scripts/*.sh
	bats tests/shell

check: lint type test

build:
	rm -rf dist
	$(BIN)/python -m build
	$(BIN)/twine check --strict dist/*

docs:
	$(BIN)/mkdocs build --strict

docker:
	docker build -t hjp .
	docker build --target test -t hjp-test . && docker run --rm hjp-test

clean:
	rm -rf dist site .coverage .pytest_cache .mypy_cache .ruff_cache
