# Repository-wide checks. Each component also has its own Makefile.
#
#   make check        everything CI runs on a pull request, without accounts
#   make configs      validate configs and release bundles only

PY ?= python

.PHONY: check configs foundation opencatalog generation verification

check: configs generation verification opencatalog foundation

configs:
	$(MAKE) -C generation validate

generation:
	$(MAKE) -C generation check

verification:
	$(MAKE) -C verification check

opencatalog:
	$(MAKE) -C tools/opencatalog check

foundation:
	$(MAKE) -C infra/terraform/foundation check
