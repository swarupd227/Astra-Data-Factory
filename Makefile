# Repository-wide checks. Each component also has its own Makefile.
#
#   make check        everything CI runs on a pull request, without accounts
#   make configs      validate configs and release bundles only

PY ?= python

.PHONY: check configs specs core knowledge foundation opencatalog generation verification agents control

check: specs configs core knowledge generation verification agents control opencatalog foundation

specs:
	$(MAKE) -C knowledge validate

configs:
	$(MAKE) -C generation validate

core:
	$(MAKE) -C core check

knowledge:
	$(MAKE) -C knowledge check

generation:
	$(MAKE) -C generation check

verification:
	$(MAKE) -C verification check

agents:
	$(MAKE) -C agents check

control:
	$(MAKE) -C control check

opencatalog:
	$(MAKE) -C tools/opencatalog check

foundation:
	$(MAKE) -C infra/terraform/foundation check
