"""年線濾網穩健性測試的核心算式：無前視、成本有扣、統計正確。

這支工具要回答「那 +68.81% 是真的還是運氣」，所以它自己的算式不能有偏差——
少扣一次成本或偷看一天未來，結論就會往「有效」那邊偏。
"""
import importlib.util
import os

import pandas as pd
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "ma_filter_robustness",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "tools", "ma_filter_robustness.py"),
)
mfr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mfr)

from src.broker import fees  # noqa: E402


def _series(values):
    return pd.Series(values, index=pd.bdate_range("2020-01-01", periods=len(values)), dtype=float)


def test_no_lookahead_position_lags_the_signal():
    """部位必須是「昨天的訊號」——用今天的收盤決定今天的部位就是偷看未來。"""
    close = _series([100.0] * 5 + [200.0] * 5)      # 第 6 天才站上均線
    _, pos, _ = mfr._simulate(close, ma_window=3, fee_discount=0.28)
    first_signal = int((close >= close.rolling(3).mean()).values.argmax())
    assert bool(pos.iloc[first_signal]) is False    # 訊號當天還不能在場內
    assert bool(pos.iloc[first_signal + 1]) is True  # 隔天才進場


def test_costs_are_deducted_on_every_switch():
    """每次進出都要扣一次成本，否則這個對照組零成本、不公平地有利。"""
    close = _series([100.0] * 5 + [200.0] * 5)
    daily, pos, switch = mfr._simulate(close, ma_window=3, fee_discount=0.28)
    # 進場那天沒有價格變動時，當日報酬應剛好等於「負的買進手續費」
    entry = int(pos.values.argmax())
    assert daily.iloc[entry] == pytest.approx(-fees.BROKER_FEE_RATE * 0.28, abs=1e-9)
    assert int(switch.sum()) >= 1


def test_sell_cost_includes_transaction_tax():
    """賣出要扣手續費+證交稅（0.3%），只扣手續費會低估成本。"""
    close = _series([100.0] * 5 + [200.0] * 5 + [100.0] * 5)
    daily, pos, _ = mfr._simulate(close, ma_window=3, fee_discount=0.28)
    exits = [i for i in range(1, len(pos)) if pos.iloc[i - 1] and not pos.iloc[i]]
    assert exits, "測試資料應該要有一次出場"
    # 出場當天不持有 → 沒有價格報酬，扣的就是賣出成本
    assert daily.iloc[exits[0]] == pytest.approx(
        -(fees.BROKER_FEE_RATE * 0.28 + fees.TAX_RATE), abs=1e-9)


def test_flat_market_filter_only_loses_costs():
    """完全不動的市場：濾網不該憑空生出報酬，最多只會賠掉成本。"""
    daily, _, _ = mfr._simulate(_series([100.0] * 60), ma_window=10, fee_discount=0.28)
    assert mfr._stats(daily)["ret"] <= 0


def test_stats_on_known_series():
    r = pd.Series([0.1, -0.1, 0.1], index=pd.bdate_range("2020-01-01", periods=3))
    st = mfr._stats(r)
    assert st["ret"] == pytest.approx(1.1 * 0.9 * 1.1 - 1)
    assert st["dd"] < 0            # 中間跌過，回撤必為負


def test_stats_handles_too_short_input():
    assert mfr._stats(pd.Series([], dtype=float)) is None


def test_criteria_are_pre_registered():
    """標準必須事前寫死，且是「愈嚴愈好」的方向，不能看到結果再放寬。"""
    assert mfr.CRITERIA["ma_lengths_beating_bh_dd"] >= 3
    assert mfr.CRITERIA["window_dd_improve_ratio"] >= 0.7
    assert mfr.MA_LENGTHS == [100, 150, 200, 250]   # 200 不可以是唯一被測的
