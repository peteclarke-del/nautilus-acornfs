.PHONY: check format lint test test-live typecheck

check: lint typecheck test

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
