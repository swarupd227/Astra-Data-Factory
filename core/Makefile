# astra-core (shared building blocks)
#
#   make install     editable install with dev dependencies
#   make check       unit tests

PY ?= python

.PHONY: install check test

install:
	$(PY) -m pip install -e ".[dev]"

check:
	$(PY) -m pytest -q

test: check
