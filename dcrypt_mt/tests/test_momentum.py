from datetime import timedelta

from dcrypt_mt.models import TokenInfo, utcnow
from dcrypt_mt.momentum import txn_acceleration, volume_acceleration


def _info(**kw):
    base = dict(mint="m", symbol="T", pair_address="p", dex="d", price_native=1e-6, price_usd=1e-4,
                liquidity_usd=100_000, pair_created_at=utcnow() - timedelta(hours=3),
                buys_m5=40, sells_m5=20, buys_h1=160, sells_h1=80,
                volume_m5=125_000.0, volume_h1=500_000.0)
    base.update(kw)
    return TokenInfo(**base)


def test_txn_acceleration_pass():
    assert txn_acceleration(_info()) > 1.5


def test_volume_acceleration_pass():
    assert volume_acceleration(_info()) > 1.5


def test_zero_baseline_returns_none():
    i = _info(buys_h1=40, sells_h1=20, volume_h1=125_000.0)  # h1 == m5 -> baseline 0
    assert txn_acceleration(i) is None
    assert volume_acceleration(i) is None


def test_young_token_returns_none():
    i = _info(pair_created_at=utcnow() - timedelta(minutes=10))
    assert txn_acceleration(i, min_age_min=60.0) is None
    assert volume_acceleration(i, min_age_min=60.0) is None


def test_fail_values():
    i = _info(buys_m5=5, sells_m5=5, buys_h1=400, sells_h1=400, volume_m5=1000.0, volume_h1=1_000_000.0)
    assert txn_acceleration(i) < 1.5
    assert volume_acceleration(i) < 1.5
