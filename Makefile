.PHONY: test lint check

test:
	python -m pytest -q

lint:
	python -m ruff check src tests
	python -m ruff format --check src tests

check: lint test
