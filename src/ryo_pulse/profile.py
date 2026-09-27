"""Profile hashing: two scans are comparable only if their normalised profiles hash the same."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .canonical import sha256_hex

if TYPE_CHECKING:
    from .models import ScanProfile

PROFILE_HASH_LEN = 16


def profile_hash(profile: "ScanProfile") -> str:
    return sha256_hex(profile.normalized())[:PROFILE_HASH_LEN]
