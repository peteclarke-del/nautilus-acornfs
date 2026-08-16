.PHONY: benchmark check format lint messages test test-live typecheck

check: lint typecheck test

benchmark:
	python -m acornfs.benchmark --output build/performance/amd64.json --check-budgets

format:
	python -m ruff format .

lint:
	python -m ruff check .
	python -m ruff format --check .

messages:
	xgettext --language=Python --from-code=UTF-8 --sort-output --no-wrap \
		--keyword=_ --keyword=N_ --keyword=ngettext:1,2 --output=po/acornfs.pot \
		src/acornfs/core/repair.py src/acornfs/core/validation.py src/acornfs/desktop.py \
		src/acornfs/file_forge.py src/acornfs_nautilus/extension.py \
		src/acornfs_nautilus/logic.py

typecheck:
	python -m mypy src

test:
	python -m pytest

test-live:
	ACORNFS_RUN_LIVE_FUSE=1 python -m pytest tests/test_live_fuse.py
