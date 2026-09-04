UV ?= uv
PYTHON ?= python3.12
UV_ENV = UV_PYTHON="$(PYTHON)" UV_PYTHON_DOWNLOADS=never

install:
	$(UV_ENV) $(UV) sync --frozen --all-groups
format-check:
	$(UV_ENV) $(UV) run --frozen ruff format --check .
lint:
	$(UV_ENV) $(UV) run --frozen ruff check .
typecheck:
	$(UV_ENV) $(UV) run --frozen mypy src
test:
	$(UV_ENV) $(UV) run --frozen pytest
verify: format-check lint typecheck test
build:
	$(UV_ENV) $(UV) build
clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov dist build
