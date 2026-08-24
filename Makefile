.PHONY: addon benchmark check debian-staging format fuzz-smoke lint messages package-smoke release test test-live typecheck

PYTHON ?= python3

check: lint typecheck test

addon:
	$(PYTHON) tools/build_addon.py --output dist

debian-staging:
	python tools/debian_staging.py --output build/debian-staging

benchmark:
	python -m acornfs.benchmark --output build/performance/amd64.json --check-budgets

format:
	python -m ruff format .

fuzz-smoke:
	mkdir -p build/fuzz/dsc build/fuzz/uri
	python fuzz/fuzz_dsc.py build/fuzz/dsc fuzz/corpus/dsc -atheris_runs=1000 -max_len=64
	python fuzz/fuzz_uri.py build/fuzz/uri fuzz/corpus/uri -atheris_runs=1000 -max_len=4096
	python fuzz/fuzz_adfs.py -atheris_runs=250 -max_len=8448

lint:
	python -m ruff check .
	python -m ruff format --check .

messages:
	xgettext --language=Python --from-code=UTF-8 --sort-output --no-wrap \
		--keyword=_ --keyword=N_ --keyword=ngettext:1,2 --output=po/acornfs.pot \
		src/acornfs/core/beebscsi.py src/acornfs/core/create.py src/acornfs/core/formats.py \
		src/acornfs/core/image.py \
		src/acornfs/core/properties.py src/acornfs/core/repair.py \
		src/acornfs/core/storage.py \
		src/acornfs/core/validation.py src/acornfs/desktop.py src/acornfs/file_forge.py \
		src/acornfs/greaseweazle.py \
		src/acornfs/fuse_adapter/operations.py src/acornfs/fuse_adapter/runner.py \
		src/acornfs/mounts.py src/acornfs/operations.py src/acornfs/preferences.py \
		src/acornfs/recovery.py src/acornfs/safe_paths.py src/acornfs_nautilus/extension.py \
		src/acornfs_nautilus/logic.py

package-smoke:
	python tools/package_smoke.py --build

release:
	python tools/release_artifacts.py --output build/release

typecheck:
	python -m mypy src

test:
	python -m pytest

test-live:
	ACORNFS_RUN_LIVE_FUSE=1 python -m pytest tests/test_live_fuse.py
