# astra-data (generation plane)
#
#   make install     editable install with dev dependencies
#   make check       unit tests
#   make validate    validate ../configs and ../releases the way CI does

PY ?= python
export PYTHONPATH := src

.PHONY: install install-deploy check test validate

install:
	$(PY) -m pip install -e ".[dev]"

install-deploy:
	$(PY) -m pip install -e ".[dev,deploy]"

check:
	$(PY) -m pytest -q

test: check

validate:
	$(PY) -m astra_data.cli --root .. validate ../configs
	$(PY) -m astra_data.cli --root .. bundles check ../releases
