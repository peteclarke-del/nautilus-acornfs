.PHONY: benchmark check format lint test test-live typecheck

check: lint typecheck test

benchmark:
	python -m acornfs.benchmark --output build/performance/amd64.json --check-budgets

format:
	python -m ruff format .

lint:
	python -m ruff check .
	python -m ruff format --check .

typecheck:
	python -m mypy src

test:
	python -m pytest

test-live:
	ACORNFS_RUN_LIVE_FUSE=1 python -m pytest tests/test_live_fuse.py
