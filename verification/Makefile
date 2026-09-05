# astra-verification (verification plane)
#
#   make install     editable install of generation and this package with dev dependencies
#   make check       unit tests

# pytest finds src/ and ../generation/src through pyproject.toml, so no
# PYTHONPATH is needed here.
PY ?= python

.PHONY: install install-deploy check test

install:
	$(PY) -m pip install -e ../generation -e ".[dev]"

install-deploy:
	$(PY) -m pip install -e "../generation[deploy]" -e ".[dev,deploy]"

check:
	$(PY) -m pytest -q

test: check
