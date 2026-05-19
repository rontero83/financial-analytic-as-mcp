"""Task identifier generation per D-03.

Task ID format: ``YYYYMMDDTHHMMSS-<8 hex>`` — sortable lexicographically;
collision-safe via 8-hex-char random suffix.

`_new_task_id()` and `TASK_ID_RE` live HERE (DRY). `task_store.py` and
`task_manager.py` import from this module — they MUST NOT redefine these.
"""
from __future__ import annotations

import random
import re
from datetime import datetime, timezone

TASK_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}-[0-9a-f]{8}$")


def _new_task_id() -> str:
    """Generate a fresh task id in the D-03 format.

    Example: ``20260519T143052-a3b7c9d1``

    Uses UTC ``datetime`` for the timestamp and ``random.randrange(2**32)``
    for the 8-hex suffix (≈4 billion variants per second — collision-safe
    for any realistic single-server workload).
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    suf = f"{random.randrange(2**32):08x}"
    return f"{ts}-{suf}"
