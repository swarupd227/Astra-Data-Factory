"""Astra Data: the generation plane of Astra Data Factory.

Today: config validation against the versioned config schema, and deployment
and testing of release bundles against a target environment. The config
compiler and renderers (E3) build on these.
"""

from astra_data.validate import Problem, validate_config_file, validate_paths

__all__ = ["Problem", "validate_config_file", "validate_paths"]
