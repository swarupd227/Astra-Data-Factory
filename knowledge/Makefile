# astra-knowledge (knowledge plane)
#
#   make install     editable install of core and this package with dev dependencies
#   make check       unit tests
#   make validate    validate ../specs the way CI does

PY ?= python

.PHONY: install check test validate

install:
	$(PY) -m pip install -e ../core -e ".[dev]"

check:
	$(PY) -m pytest -q

test: check

validate:
	PYTHONPATH=src:../core/src $(PY) -m astra_knowledge.cli --root .. --specs ../specs validate
