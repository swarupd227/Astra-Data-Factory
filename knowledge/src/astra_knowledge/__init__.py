"""Astra Data Factory knowledge plane.

Today: the spec registry (S2.1.1). Pattern library, domain packs and the
rule catalog (E2) join it here.
"""

from astra_knowledge.picture import Picture, PictureError, parse_picture
from astra_knowledge.registry import Citation, Field, Record, Registry, SourceSpec, load_spec_file

__all__ = ["Citation", "Field", "Picture", "PictureError", "Record", "Registry", "SourceSpec", "load_spec_file", "parse_picture"]
