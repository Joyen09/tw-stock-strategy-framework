"""validate_take_profit 的統計工具：配對、勝率、停利觸發次數。

重點是「空過防護」——停利門檻設太高時完全不觸發，結果會跟基準一模一樣，
那是沒測到而不是通過。tp_exits 就是拿來擋這件事的，必須真的數得準。
"""
import importlib.util
import os

import pandas as pd
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "validate_take_profit",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "tools", "validate_take_profit.py"),
)
vtp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(vtp)


class _T:
    """最小的 Trade 替身（欄位與 engine.backtest.Trade 相同）。"""

    def __init__(self, date, symbol, side, shares, price, reason=""):
        self.date = pd.Timestamp(date)
        self.symbol = symbol
        self.side = side
        self.shares = shares
        self.price = price
        self.reason = reason


def test_round_trip_pairs_buy_and_sell():
    trades = [_T("2026-01-05", "2330", "BUY", 100, 100.0),
              _T("2026-02-05", "2330", "SELL", 100, 120.0, "停利出場：+20.0%")]
    rt = vtp._round_trips(trades)
    assert len(rt) == 1
    ret, days, is_tp = rt[0]
    assert ret == pytest.approx(0.2)
    assert days == 31
    assert is_tp is True


def test_fifo_order_across_two_lots():
    """先買的先賣（FIFO），兩批成本不同時報酬要各自算。"""
    trades = [_T("2026-01-05", "2330", "BUY", 100, 100.0),
              _T("2026-01-20", "2330", "BUY", 100, 200.0),
              _T("2026-03-05", "2330", "SELL", 200, 150.0)]
    rets = [round(r, 4) for r, _, _ in vtp._round_trips(trades)]
    assert rets == [0.5, -0.25]


def test_unclosed_position_is_not_counted():
    """還沒賣的不算報酬——未實現不能混進勝率裡。"""
    assert vtp._round_trips([_T("2026-01-05", "2330", "BUY", 100, 100.0)]) == []


def test_behaviour_counts_take_profit_exits():
    trades = [_T("2026-01-05", "2330", "BUY", 100, 100.0),
              _T("2026-02-05", "2330", "SELL", 100, 120.0, "停利出場：+20.0%"),
              _T("2026-03-05", "2454", "BUY", 100, 100.0),
              _T("2026-04-05", "2454", "SELL", 100, 90.0, "跌破季線，出場")]
    b = vtp._behaviour(trades)
    assert b["n"] == 2
    assert b["tp_exits"] == 1
    assert b["win_rate"] == 0.5
    assert b["best"] == pytest.approx(0.2)


def test_behaviour_on_no_trades_does_not_crash():
    """空頭期可能一筆都沒有；統計要回 NaN 而不是炸掉。"""
    b = vtp._behaviour([])
    assert b["n"] == 0 and b["tp_exits"] == 0
    assert b["win_rate"] != b["win_rate"]   # NaN


def test_criteria_are_pre_registered_and_guard_vacuous_pass():
    """標準必須事前寫死，而且一定要有『停利真的觸發過』這道防護。"""
    assert vtp.CRITERIA["min_tp_exits"] >= 1
    assert vtp.CRITERIA["bull_sharpe_min_ratio"] >= 1.0
    assert vtp.CRITERIA["bull_return_min_ratio"] >= 1.0
