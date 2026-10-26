.PHONY: install test lint fmt check demo-fetch

SERIES ?= evals/corpora/sherlock-holmes/series.json

install:
	pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check src tests
	ruff format --check src tests
	mypy

fmt:
	ruff check --fix src tests
	ruff format src tests

check: lint test

demo-fetch:
	canonkeeper fetch $(SERIES)
	canonkeeper estimate $(SERIES)
