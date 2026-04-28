"""Runtime BIAN translation primitives.

Loads `bian-alias-map.json` once at import time. Translates request bodies between
the BIAN-PascalCase wire shape and the camelCase Mongo storage shape.

This is the runtime alternative to plan-v2's PR-shared-1 generated-Pydantic
approach (Option B in the 2026-04-27 decision). The alias map IS the contract —
no `.py` model files to keep in sync.
"""

from .bian_registry import BianRegistry, registry
from .refs import derive_ref

__all__ = ["BianRegistry", "registry", "derive_ref"]
