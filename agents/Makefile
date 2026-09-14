# astra-agents (agents plane)
#
#   make install     editable install of core, knowledge, generation and verification, plus this package, with dev dependencies
#   make check       unit tests

PY ?= python

.PHONY: install check test

install:
	$(PY) -m pip install -e ../core -e ../knowledge -e ../generation -e ../verification -e ".[dev]"

check:
	$(PY) -m pytest -q

test: check
