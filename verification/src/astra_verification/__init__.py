"""Astra Data Factory verification plane.

Today: ephemeral sandboxes (S1.2.3). Dry-runs, golden replay, parity and the
DQ runner (E4) build on them.
"""

from astra_verification.sandbox import Sandbox, SandboxSpec, create, destroy, list_sandboxes, reap, sandbox

__all__ = ["Sandbox", "SandboxSpec", "create", "destroy", "list_sandboxes", "reap", "sandbox"]
