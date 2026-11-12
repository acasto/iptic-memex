UV ?= uv
PYTEST ?= $(UV) run --locked python -m pytest

.PHONY: test test-web export-requirements

test:
	$(PYTEST) -q

test-web:
	$(PYTEST) -q tests/web/test_webapp.py

# Keep the pip fallback generated from the same lock as uv installations.
export-requirements:
	$(UV) export --locked --no-dev --no-emit-project --output-file requirements.txt
