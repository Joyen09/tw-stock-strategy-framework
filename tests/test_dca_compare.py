"""定期定額比較工具的算式：最低手續費、資金加權年化、濾網不吃掉本金。

這支工具要決定「要不要加濾網」，所以算式偏一點結論就偏一點。
三個最容易算錯的地方各自鎖住。
"""
import importlib.util
import os

import pandas as pd
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "dca_compare",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "tools", "dca_compare.py"),
)
dca = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dca)

from src.broker import fees  # noqa: E402


def _flat(n=400, price=100.0, start="2020-01-01"):
    idx = pd.bdate_range(start, periods=n)
    return pd.Series([price] * n, index=idx, dtype=float)


# ── 最低手續費：小額定期定額最常被漏掉的成本 ──

def test_min_fee_dominates_small_purchases():
    """每月 1 萬的手續費是 20 元（0.2%），不是費率算出來的 4 元。"""
    assert dca._buy_fee(10_000, 0.28) == pytest.approx(fees.MIN_FEE)
    assert dca._buy_fee(10_000, 0.28) > 10_000 * fees.BROKER_FEE_RATE * 0.28


def test_fee_rate_takes_over_for_large_purchases():
    big = 1_000_000
    assert dca._buy_fee(big, 0.28) == pytest.approx(big * fees.BROKER_FEE_RATE * 0.28)


# ── 扣款日 ──

def test_month_starts_picks_one_day_per_month():
    close = _flat(n=200, start="2020-01-01")
    days = dca._month_starts(close.index)
    assert len(days) == len({(d.year, d.month) for d in close.index})
    for d in days:                              # 每個都要是該月最早的交易日
        same = [x for x in close.index if (x.year, x.month) == (d.year, d.month)]
        assert d == min(same)


# ── 純定期定額 ──

def test_plain_dca_invests_every_month():
    close = _flat()
    curve, invested, bought, skipped = dca._simulate_dca(close, 10_000, 0.28, 0)
    assert bought == len(dca._month_starts(close.index))
    assert invested == pytest.approx(10_000 * bought)
    assert skipped == 0


def test_flat_market_loses_only_fees():
    """價格完全不動：期末市值應該剛好是投入減掉手續費，不能多也不能少。"""
    close = _flat()
    curve, invested, bought, _ = dca._simulate_dca(close, 10_000, 0.28, 0)
    assert float(curve.iloc[-1]) == pytest.approx(invested - bought * fees.MIN_FEE, rel=1e-9)


# ── 濾網版 ──

def test_filter_skips_purchases_below_the_average():
    """跌破均線的扣款日要跳過買進。"""
    idx = pd.bdate_range("2020-01-01", periods=400)
    vals = [100.0] * 200 + [50.0] * 200            # 後半段遠低於均線
    close = pd.Series(vals, index=idx, dtype=float)
    _, _, bought, skipped = dca._simulate_dca(close, 10_000, 0.28, ma_window=60)
    assert skipped > 0
    assert bought < len(dca._month_starts(idx))


def test_skipped_money_is_kept_as_cash_not_lost():
    """跳過的月份錢要留在帳上（是『晚投入』不是『少投入』）——
    算錯這點會讓濾網版看起來報酬很差，結論就反了。"""
    idx = pd.bdate_range("2020-01-01", periods=300)
    close = pd.Series([100.0] * 100 + [40.0] * 200, index=idx, dtype=float)
    curve, invested, _, skipped = dca._simulate_dca(close, 10_000, 0.28, ma_window=60)
    assert skipped > 0
    assert invested == pytest.approx(10_000 * len(dca._month_starts(idx)))
    # 市值不可能低於「被跳過而躺在現金裡的錢」
    assert float(curve.iloc[-1]) >= skipped * 10_000 * 0.99


def test_filter_never_blocks_before_the_average_exists():
    """均線還算不出來的初期不能憑空擋住買進（否則前幾個月白白不投入）。"""
    close = _flat(n=100)
    _, _, bought, _ = dca._simulate_dca(close, 10_000, 0.28, ma_window=250)
    assert bought == len(dca._month_starts(close.index))


# ── 年化（資金加權）──

def test_irr_is_zero_in_a_flat_market_before_fees():
    """不漲不跌時年化應該接近 0（只差手續費那一點點）。"""
    close = _flat()
    curve, _, bought, _ = dca._simulate_dca(close, 10_000, 0.28, 0)
    years = (close.index[-1] - close.index[0]).days / 365.25
    assert dca._irr(curve, 10_000, bought, years) == pytest.approx(0.0, abs=0.02)


def test_irr_is_not_the_naive_total_over_invested():
    """定期定額不能用「期末/總投入」直接開根號——後面投入的錢只放了幾個月，
    那樣算會嚴重低估年化。這裡確認 IRR 高於那個錯誤算法。"""
    idx = pd.bdate_range("2020-01-01", periods=600)
    close = pd.Series([100.0 * (1.0004 ** i) for i in range(600)], index=idx)
    curve, invested, bought, _ = dca._simulate_dca(close, 10_000, 0.28, 0)
    years = (idx[-1] - idx[0]).days / 365.25
    naive = (float(curve.iloc[-1]) / invested) ** (1 / years) - 1
    assert dca._irr(curve, 10_000, bought, years) > naive


def test_criteria_are_pre_registered():
    assert dca.CRITERIA["return_min_ratio"] <= 1.0
    assert dca.CRITERIA["dd_min_improve"] > 0
