"""Reference-derivation helpers shared across services and `transform.py`.

The v4 data model uses typed string refs (`CUST-xxxxxxxx`, `ACC-xxxxxxxx`,
`PAY-xxxxxxxx`, etc.) as the public identifier on every collection — never the raw
ObjectId. Keeping the formula in one place prevents service / migration drift.
"""

from __future__ import annotations

from bson import ObjectId


def derive_ref(prefix: str, oid: ObjectId | str, last_n: int = 8) -> str:
    """Build a typed string ref from an ObjectId.

    >>> derive_ref("ACC", ObjectId("661a4e0583b3a4567890abcd"))
    'ACC-7890abcd'
    """
    return f"{prefix}-{str(oid)[-last_n:]}"
