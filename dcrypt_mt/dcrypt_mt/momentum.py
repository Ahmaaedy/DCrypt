"""Baseline-relative acceleration gate. Stateless: compares m5 rate against the
token's own h1 baseline (minus the current m5 bucket), not against recent polls."""
from __future__ import annotations

from .models import TokenInfo

_BUCKETS_PER_H1 = 11  # h1 span minus the current m5 bucket


def _too_young(info: TokenInfo, min_age_min: float) -> bool:
    return info.age_min is not None and info.age_min < min_age_min


def txn_acceleration(info: TokenInfo, min_age_min: float = 60.0) -> float | None:
    m5 = info.buys_m5 + info.sells_m5
    h1 = info.buys_h1 + info.sells_h1
    baseline = (h1 - m5) / _BUCKETS_PER_H1
    if baseline <= 0 or _too_young(info, min_age_min):
        return None
    return m5 / baseline


def volume_acceleration(info: TokenInfo, min_age_min: float = 60.0) -> float | None:
    baseline = (info.volume_h1 - info.volume_m5) / _BUCKETS_PER_H1
    if baseline <= 0 or _too_young(info, min_age_min):
        return None
    return info.volume_m5 / baseline
