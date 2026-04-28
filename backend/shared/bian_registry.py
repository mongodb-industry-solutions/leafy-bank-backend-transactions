"""BIAN ↔ alias translation driven by `bian-alias-map.json`.

Forward direction: BIAN PascalCase request body → camelCase dict for the service layer.
Reverse direction: camelCase Mongo doc → BIAN PascalCase response body.

Both directions walk the dict recursively, looking up each path against the flat
alias map (keys are dotted alias paths; values are BIAN canonical names).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

logger = logging.getLogger(__name__)

DEFAULT_ALIAS_MAP_PATH = Path(__file__).parent / "bian-alias-map.json"


class BianRegistry:
    def __init__(self, alias_map_path: Path | str | None = None):
        path = Path(alias_map_path) if alias_map_path else DEFAULT_ALIAS_MAP_PATH
        if not path.exists():
            raise FileNotFoundError(f"BIAN alias map not found at {path}")
        data = json.loads(path.read_text())

        self.path = path
        self.bian_version = (data.get("$meta") or {}).get("bianVersion", "unknown")
        self.mongo_database = (data.get("$meta") or {}).get("mongoDatabase")

        # collections[name] : Dict[alias_path, bian_name]   (flat, dotted)
        self.collections: dict[str, dict[str, str]] = {
            k: v for k, v in data.items() if not k.startswith("$") and isinstance(v, dict)
        }
        # reverse[name] : Dict[bian_name, alias_path]   (flat, dotted)
        self.reverse: dict[str, dict[str, str]] = {
            coll: {bian: alias for alias, bian in fields.items()}
            for coll, fields in self.collections.items()
        }
        # bian_to_leaf[name] : Dict[bian_name, alias_leaf]  (just the last path segment)
        self.bian_to_leaf: dict[str, dict[str, str]] = {
            coll: {bian: alias.rsplit(".", 1)[-1] for alias, bian in fields.items()}
            for coll, fields in self.collections.items()
        }
        logger.info(
            "BianRegistry loaded BIAN %s from %s (collections=%s)",
            self.bian_version,
            path,
            list(self.collections.keys()),
        )

    # ---------- Single-field lookups ----------

    def alias_to_bian(self, collection: str, alias_path: str) -> Optional[str]:
        return self.collections.get(collection, {}).get(alias_path)

    def bian_to_alias(self, collection: str, bian_name: str) -> Optional[str]:
        return self.reverse.get(collection, {}).get(bian_name)

    def known_collections(self) -> list[str]:
        return list(self.collections.keys())

    def has_bian(self, collection: str, bian_name: str) -> bool:
        return bian_name in self.reverse.get(collection, {})

    # ---------- Whole-doc translations ----------

    def to_alias(self, collection: str, bian_doc: Mapping[str, Any]) -> dict[str, Any]:
        """Translate a BIAN-PascalCase request body to a camelCase dict."""
        if collection not in self.collections:
            raise KeyError(f"Unknown collection {collection!r}")
        return self._walk(bian_doc, collection, prefix="", direction="bian_to_alias")

    def to_bian(self, collection: str, mongo_doc: Mapping[str, Any]) -> dict[str, Any]:
        """Translate a camelCase Mongo doc to a BIAN-PascalCase response body."""
        if collection not in self.collections:
            raise KeyError(f"Unknown collection {collection!r}")
        return self._walk(mongo_doc, collection, prefix="", direction="alias_to_bian")

    # ---------- Required-field validation ----------

    def require(
        self,
        collection: str,
        bian_doc: Mapping[str, Any],
        required_bian_fields: Iterable[str],
    ) -> list[str]:
        """Return the list of required BIAN field names missing from `bian_doc`.

        Walks the alias map to resolve each required BIAN name to a dotted alias path,
        then checks the corresponding nested location in the (BIAN-keyed) input doc.
        """
        missing: list[str] = []
        for bian_name in required_bian_fields:
            alias_path = self.bian_to_alias(collection, bian_name)
            if alias_path is None:
                missing.append(bian_name)
                continue
            if not self._has_path_via_bian(bian_doc, collection, alias_path):
                missing.append(bian_name)
        return missing

    # ---------- Internals ----------

    def _walk(
        self,
        doc: Any,
        collection: str,
        prefix: str,
        direction: str,
    ) -> Any:
        if isinstance(doc, list):
            return [self._walk(item, collection, prefix, direction) for item in doc]
        if not isinstance(doc, dict):
            return doc

        forward = self.collections[collection]
        reverse = self.reverse[collection]

        out: dict[str, Any] = {}
        for key, value in doc.items():
            if direction == "bian_to_alias":
                # Find the alias path that maps from this BIAN name within prefix scope.
                bian_name = key
                alias_path = reverse.get(bian_name)
                if alias_path is None or not _under(alias_path, prefix):
                    # Pass-through unknown / out-of-scope keys with original name.
                    out[key] = self._walk(value, collection, prefix, direction)
                    continue
                leaf = alias_path.rsplit(".", 1)[-1]
                out[leaf] = self._walk(value, collection, alias_path, direction)
            else:  # alias_to_bian
                alias_path = f"{prefix}.{key}" if prefix else key
                bian_name = forward.get(alias_path)
                if bian_name is None:
                    # Pass-through fields not in the alias map (e.g. _id, audit fields).
                    out[key] = self._walk(value, collection, alias_path, direction)
                    continue
                out[bian_name] = self._walk(value, collection, alias_path, direction)
        return out

    def _has_path_via_bian(
        self,
        bian_doc: Mapping[str, Any],
        collection: str,
        alias_path: str,
    ) -> bool:
        """Check whether the BIAN-keyed doc carries a value at the alias path."""
        leaves = alias_path.split(".")
        cursor: Any = bian_doc
        for depth, leaf in enumerate(leaves):
            partial_alias = ".".join(leaves[: depth + 1])
            bian_name = self.collections[collection].get(partial_alias)
            if bian_name is None:
                return False
            if not isinstance(cursor, Mapping):
                return False
            if bian_name not in cursor:
                return False
            cursor = cursor[bian_name]
        return cursor is not None


def _under(alias_path: str, prefix: str) -> bool:
    if not prefix:
        return "." not in alias_path
    return alias_path.startswith(prefix + ".") and "." not in alias_path[len(prefix) + 1 :]


registry = BianRegistry(os.getenv("BIAN_ALIAS_MAP_PATH") or None)
