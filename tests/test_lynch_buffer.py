"""lynch 季線緩衝：遲滞區間的行為（不打 API，純邏輯）。"""
import pandas as pd
import pytest

from src.models import Action, Fundamentals, Position
from src.strategies.base import StrategyContext
from src.strategies.lynch import LynchStrategy


def _prices(last_close: float, base: float = 100.0, n: int = 80):
    """前 n-1 天都是 base（季線≈base），最後一天收在 last_close。"""
    idx = pd.bdate_range("2026-01-01", periods=n)
    closes = [base] * (n - 1) + [last_close]
    return pd.DataFrame({"open": closes, "high": closes, "low": closes,
                         "close": closes, "volume": [1000] * n}, index=idx)


def _good_fundamentals():
    """四項條件全過，讓測試只聚焦在季線那一項。

    peg 是 pe/eps_growth 算出來的唯讀屬性：20/25 = 0.8，在 max_peg=1.2 之內。
    """
    return Fundamentals(symbol="2330", name="測試", pe=20.0, eps_growth=25.0,
                        revenue_growth=10.0, debt_ratio=30.0)


def _ctx(last_close, held=True, base=100.0):
    pos = Position(symbol="2330", shares=100, avg_price=base) if held else None
    return StrategyContext(symbol="2330", prices=_prices(last_close, base),
                           fundamentals=_good_fundamentals(), benchmark=None,
                           position=pos, chips=None)


def test_default_is_unchanged_behaviour():
    """預設 exit_buffer=0 必須與原本完全一樣：跌破季線就賣。"""
    s = LynchStrategy()
    assert s.params["exit_buffer"] == 0.0
    assert s.evaluate(_ctx(99.9)).action == Action.SELL   # 差一點點也賣
    assert s.evaluate(_ctx(100.0)).action != Action.SELL  # 正好在季線上不賣


def test_buffer_holds_through_shallow_dip():
    """3% 緩衝：跌破季線但還在緩衝內 → 續抱（這就是要修的洗盤情境）。"""
    s = LynchStrategy(exit_buffer=0.03)
    sig = s.evaluate(_ctx(98.0))  # 季線下 2%，在緩衝內
    assert sig.action != Action.SELL


def test_buffer_still_exits_on_real_break():
    """跌破緩衝線就該出場——緩衝不是拿掉停損。"""
    s = LynchStrategy(exit_buffer=0.03)
    sig = s.evaluate(_ctx(96.0))  # 季線下 4%，超出緩衝
    assert sig.action == Action.SELL
    assert "季線-3%" in sig.reason


def test_the_actual_tsmc_case_would_be_held():
    """實盤案例：2330 買在 2425、兩天後跌到 2320（-4.3%）被砍。

    以買進時貼著季線估算，5% 緩衝會讓它續抱而非兩天洗掉。
    （這只說明機制有效，不代表該採用——採用與否看 validate_lynch_buffer.py）
    """
    base = 2425.0
    assert LynchStrategy().evaluate(_ctx(2320.0, base=base)).action == Action.SELL
    held = LynchStrategy(exit_buffer=0.05).evaluate(_ctx(2320.0, base=base))
    assert held.action != Action.SELL


def test_weak_fundamentals_still_exit_regardless_of_buffer():
    """緩衝只放寬「技術面」那一路；基本面轉弱該走還是要走。"""
    weak = Fundamentals(symbol="2330", name="測試", pe=None, eps_growth=None,
                        revenue_growth=None, debt_ratio=None)
    ctx = StrategyContext(symbol="2330", prices=_prices(105.0), fundamentals=weak,
                          benchmark=None, position=Position("2330", 100, 100.0), chips=None)
    assert LynchStrategy(exit_buffer=0.05).evaluate(ctx).action == Action.SELL


def test_buffer_does_not_loosen_entry():
    """緩衝只放寬出場；進場門檻仍是「站上季線」，否則變成追跌。"""
    s = LynchStrategy(exit_buffer=0.05)
    sig = s.evaluate(_ctx(98.0, held=False))  # 季線之下、但在緩衝內
    assert sig.action != Action.BUY


@pytest.mark.parametrize("buf", [0.0, 0.02, 0.03, 0.05])
def test_exit_line_is_monotonic_in_buffer(buf):
    """緩衝越大，觸發賣出所需的跌幅越大（單調性，防公式寫反）。

    注意：季線本身會被「當天」那根 K 拉動（最後一根佔 60 分之 1），
    所以臨界價要從 price < ma*(1-buf) 且 ma = (59*base + price)/60 解出來，
    不能直接寫 base*(1-buf)。
    """
    base = 100.0
    boundary = 59 * base * (1 - buf) / (59 + buf)
    s = LynchStrategy(exit_buffer=buf)
    assert s.evaluate(_ctx(boundary + 0.05)).action != Action.SELL  # 剛好還在緩衝內
    assert s.evaluate(_ctx(boundary - 0.05)).action == Action.SELL  # 剛好跌破


def test_larger_buffer_tolerates_larger_dip():
    """跨緩衝值比較：同一個跌幅，緩衝大的續抱、緩衝小的出場。"""
    dip = 97.0  # 季線下約 3%
    assert LynchStrategy(exit_buffer=0.0).evaluate(_ctx(dip)).action == Action.SELL
    assert LynchStrategy(exit_buffer=0.02).evaluate(_ctx(dip)).action == Action.SELL
    assert LynchStrategy(exit_buffer=0.05).evaluate(_ctx(dip)).action != Action.SELL
