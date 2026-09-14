# astra-control (control plane)
#
#   make install     editable install of core and this package with dev dependencies
#   make check       unit tests

PY ?= python

.PHONY: install check test

install:
	$(PY) -m pip install -e ../core -e ".[dev]"

check:
	$(PY) -m pytest -q

test: check
