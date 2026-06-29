"""策略與回測引擎的基本測試 (用離線樣本資料，不需網路)。"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import strategies
from src.data.sample import SampleDataProvider
from src.engine.backtest import Backtester
from src.models import Action, Fundamentals
from src.strategies.base import StrategyContext


def test_registry_builds_all():
    for name in strategies.REGISTRY:
        strat = strategies.build(name)
        assert strat.name == name


def test_graham_buys_cheap_stock():
    strat = strategies.build("graham")
    cheap = Fundamentals("9999", pe=8, pb=1.1, current_ratio=180, debt_ratio=40, dividend_yield=4)
    prices = pd.DataFrame({"open": [10], "high": [10], "low": [10], "close": [10], "volume": [1000]})
    sig = strat.evaluate(StrategyContext("9999", prices, fundamentals=cheap))
    assert sig.action == Action.BUY
    assert sig.strength > 0


def test_graham_rejects_expensive_stock():
    strat = strategies.build("graham")
    pricey = Fundamentals("9998", pe=40, pb=8, current_ratio=80, debt_ratio=70, dividend_yield=0)
    prices = pd.DataFrame({"open": [10], "high": [10], "low": [10], "close": [10], "volume": [1000]})
    sig = strat.evaluate(StrategyContext("9998", prices, fundamentals=pricey))
    assert sig.action == Action.HOLD


def test_strategy_requires_min_bars():
    strat = strategies.build("livermore")
    prices = pd.DataFrame({"open": [10], "high": [10], "low": [10], "close": [10], "volume": [1000]})
    sig = strat.evaluate(StrategyContext("1234", prices))
    assert sig.action == Action.HOLD  # 資料不足


def test_peg_computation():
    f = Fundamentals("1111", pe=15, eps_growth=15)
    assert abs(f.peg - 1.0) < 1e-9


def test_backtest_runs_and_reports():
    provider = SampleDataProvider()
    bt = Backtester(provider, initial_cash=1_000_000, warmup=200)
    result = bt.run(strategies.build("livermore"), provider.universe(), "2024-01-01", "2025-12-31")
    assert not result.equity_curve.empty
    assert result.equity_curve.iloc[0] > 0
    # 績效指標可計算且不爆錯
    _ = (result.total_return, result.cagr, result.max_drawdown, result.sharpe)


def test_us_overnight_buys_on_strong_overnight():
    """注入合成隔夜資料：大漲日應出 BUY、大跌日持倉應 SELL。"""
    from src.data.us_lead import USLeadProvider
    from src.strategies.us_overnight import USOvernightStrategy
    from src.models import Position

    dates = pd.bdate_range(end="2025-12-31", periods=40)
    # 前 39 天緩漲建立均線之上趨勢，最後一天美股隔夜大漲
    overnight = pd.Series([0.0] * 39 + [0.03], index=dates)
    lead = USLeadProvider(series_map={"^SOX": overnight, "TSM": overnight})
    strat = USOvernightStrategy(lead_provider=lead, up_threshold=0.015, trend_ma=20, exit_ma=10)

    close = pd.Series(range(100, 140), index=dates, dtype=float)  # 穩定上升趨勢
    prices = pd.DataFrame({"open": close, "high": close, "low": close, "close": close,
                           "volume": [1000] * 40})
    sig = strat.evaluate(StrategyContext("2330", prices))
    assert sig.action == Action.BUY

    # 大跌隔夜 + 持倉 → SELL
    overnight2 = pd.Series([0.0] * 39 + [-0.03], index=dates)
    lead2 = USLeadProvider(series_map={"^SOX": overnight2, "TSM": overnight2})
    strat2 = USOvernightStrategy(lead_provider=lead2, down_threshold=0.015)
    sig2 = strat2.evaluate(StrategyContext("2330", prices, position=Position("2330", 1000, 100)))
    assert sig2.action == Action.SELL


def test_all_strategies_backtest_without_error():
    provider = SampleDataProvider()
    bt = Backtester(provider, warmup=250)
    for name in strategies.REGISTRY:
        result = bt.run(strategies.build(name), provider.universe(), "2024-01-01", "2025-12-31")
        assert not result.equity_curve.empty, name


if __name__ == "__main__":
    import traceback

    failed = 0
    for fn_name, fn in sorted(globals().items()):
        if fn_name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {fn_name}")
            except Exception:
                failed += 1
                print(f"FAIL {fn_name}")
                traceback.print_exc()
    sys.exit(1 if failed else 0)
