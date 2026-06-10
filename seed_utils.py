from __future__ import annotations

import hashlib
import random
from typing import Any, Optional


RANDOM_SEED_MODULUS = 2**32


def derive_seed(master_seed: Optional[int], *parts: Any) -> Optional[int]:
    if master_seed is None:
        return None

    payload = "|".join([str(master_seed)] + [str(part) for part in parts])
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return int(digest[:16], 16) % RANDOM_SEED_MODULUS


def set_global_seed(seed: Optional[int]) -> None:
    if seed is None:
        return

    random.seed(seed)

    try:
        import numpy as np
    except ImportError:
        return

    np.random.seed(seed % RANDOM_SEED_MODULUS)
