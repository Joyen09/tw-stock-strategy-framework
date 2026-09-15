"""lynch 固定停利 take_profit 的行為（不打 API，純邏輯）。

停利是「感覺很對但可能有害」的那類參數：它砍掉大贏家的上檔，下檔卻不受限。
所以這裡只鎖定**機制正確**（該觸發時觸發、預設關閉），要不要真的開起來
由 tools/validate_take_profit.py 的三關驗證決定，不由這些測試背書。
"""
import pandas as pd

from src.models import Action, Fundamentals, Position
from src.strategies.base import StrategyContext
from src.strategies.lynch import LynchStrategy

MA_BASE = 100.0  # 前 n-1 天都收在這個價位，季線 ≈ 100


def _prices(last_close: float, n: int = 80):
    idx = pd.bdate_range("2026-01-01", periods=n)
    closes = [MA_BASE] * (n - 1) + [last_close]
    return pd.DataFrame({"open": closes, "high": closes, "low": closes,
                         "close": closes, "volume": [1000] * n}, index=idx)


def _good_fundamentals():
    """四項條件全過（peg = pe/eps_growth = 0.8），確保只有停利那條在起作用。"""
    return Fundamentals(symbol="2330", name="測試", pe=20.0, eps_growth=25.0,
                        revenue_growth=10.0, debt_ratio=30.0)


def _ctx(last_close: float, avg_price: float = MA_BASE, held: bool = True):
    pos = Position(symbol="2330", shares=100, avg_price=avg_price) if held else None
    return StrategyContext(symbol="2330", prices=_prices(last_close),
                           fundamentals=_good_fundamentals(), benchmark=None,
                           position=pos, chips=None)


def test_default_is_off():
    """預設必須關閉——漲一倍也不能因為停利而賣。"""
    s = LynchStrategy()
    assert s.params["take_profit"] == 0.0
    sig = s.evaluate(_ctx(200.0, avg_price=100.0))
    assert sig.action != Action.SELL


def test_triggers_exactly_at_threshold():
    """門檻用 >=：正好到 +20% 就該走，不能差一塊錢就不算。"""
    s = LynchStrategy(take_profit=0.20)
    assert s.evaluate(_ctx(120.0, avg_price=100.0)).action == Action.SELL


def test_does_not_trigger_below_threshold():
    s = LynchStrategy(take_profit=0.20)
    assert s.evaluate(_ctx(119.9, avg_price=100.0)).action != Action.SELL


def test_take_profit_overrides_a_perfectly_healthy_holding():
    """這就是停利的本質：基本面全過、站在季線之上，照樣被賣掉。

    測試存在的目的不是背書這個行為，而是把代價寫清楚——被賣掉的正是
    「一切都好」的那種持股，也就是最可能繼續漲的那種。
    """
    s = LynchStrategy(take_profit=0.15)
    sig = s.evaluate(_ctx(130.0, avg_price=100.0))
    assert sig.action == Action.SELL
    assert "停利" in sig.reason
    assert "+30.0%" in sig.reason   # 理由要講出實際獲利幅度，事後查得到


def test_no_position_is_never_a_take_profit_sell():
    s = LynchStrategy(take_profit=0.10)
    assert s.evaluate(_ctx(200.0, held=False)).action != Action.SELL


def test_missing_cost_basis_does_not_trigger():
    """成本基礎是 0（帳戶資料異常）時不能把它當成「漲無限多 %」而亂賣。"""
    s = LynchStrategy(take_profit=0.10)
    assert s.evaluate(_ctx(120.0, avg_price=0.0)).action != Action.SELL


def test_take_profit_does_not_disable_the_normal_stop():
    """開了停利之後，原本的跌破季線出場仍要有效（停利不是拿來取代停損的）。"""
    s = LynchStrategy(take_profit=0.20)
    sig = s.evaluate(_ctx(95.0, avg_price=100.0))
    assert sig.action == Action.SELL
    assert "停利" not in sig.reason   # 走的是季線那條路徑


def test_take_profit_combines_with_exit_buffer():
    """兩個參數各管一邊：上檔停利、下檔緩衝，互不干擾。"""
    s = LynchStrategy(take_profit=0.20, exit_buffer=0.05)
    assert s.evaluate(_ctx(125.0, avg_price=100.0)).action == Action.SELL   # 上檔停利
    assert s.evaluate(_ctx(97.0, avg_price=100.0)).action != Action.SELL    # 下檔在緩衝內
    assert s.evaluate(_ctx(94.0, avg_price=100.0)).action == Action.SELL    # 跌破緩衝線
