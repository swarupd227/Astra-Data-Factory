# Open Catalog tool
#
#   make install     editable install with dev dependencies into the active Python
#   make check       unit tests (no account needed)
#   make test        alias for check

PY ?= python

.PHONY: install install-verify check test

install:
	$(PY) -m pip install -e ".[dev]"

install-verify:
	$(PY) -m pip install -e ".[dev,verify]"

check:
	$(PY) -m pytest -q

test: check
