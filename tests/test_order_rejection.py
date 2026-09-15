"""未成交的單必須看得見（2026-09 lynch-mid100 的回歸測試）。

事故：帳戶現金剩 3,072、budget 設 10,000，34 檔候選一檔都買不起。
買單是全有全無，沒成交就不進 plans，心跳於是每天回報「無交易訊號」——
跟「策略真的沒看上任何股票」長得一模一樣，帳戶卡了很久沒人發現。

所以這裡鎖三件事：
1. 未成交要被記下來（trader.rejected）
2. 資金不足要講出「需要多少 / 帳上有多少」
3. 心跳不可以在有單被拒時還說「無交易訊號」
"""
import pandas as pd

from src.broker.base import Broker, Order
from src.engine.trader import LiveTrader
from src.models import Action, Position, Signal
from src.strategies.base import Strategy


def _df(close: float, n: int = 5, end: str = "2026-09-15") -> pd.DataFrame:
    idx = pd.date_range(end=end, periods=n, freq="D")
    return pd.DataFrame({"open": close, "high": close, "low": close,
                         "close": close, "volume": 1000}, index=idx)


class FakeProvider:
    def __init__(self, price=100.0):
        self.price = price

    def history(self, symbol, start, end):
        return _df(self.price, end=end)

    def fundamentals(self, symbol):
        return None

    def benchmark(self, start, end):
        return None


class FakeStrategy(Strategy):
    name = "fake"

    def __init__(self, signals):
        super().__init__()
        self.signals = signals

    def evaluate(self, ctx):
        action, strength = self.signals.get(ctx.symbol, (Action.HOLD, 0.0))
        return Signal(action=action, strength=strength, reason="test", symbol=ctx.symbol)


class BrokerOutOfCash(Broker):
    """模擬 PaperBroker 資金不足的回報：訂單原封退回、note 接上原因。"""

    def __init__(self, cash_=3_072.0, positions=None):
        self._cash = cash_
        self._positions = positions or []

    def positions(self):
        return [p for p in self._positions if p.shares > 0]

    def cash(self):
        return self._cash

    def place_order(self, order: Order) -> Order:
        order.note += " | 資金不足，未成交"
        return order  # filled 維持 False


class RecordingNotifier:
    enabled = True

    def __init__(self):
        self.messages = []

    def send(self, msg):
        self.messages.append(msg)
        return True


def _trader(broker, notifier=None, **kw):
    return LiveTrader(FakeProvider(price=100.0), broker,
                      FakeStrategy({"2330": (Action.BUY, 1.0)}),
                      position_budget=10_000, dry_run=False,
                      notifier=notifier, **kw)


def test_insufficient_cash_is_recorded_not_silent():
    t = _trader(BrokerOutOfCash())
    plans = t.scan(["2330"], end="2026-09-15")
    assert plans == []                 # 沒成交，本來就不該出現在計畫裡
    assert len(t.rejected) == 1        # 但必須留下痕跡
    assert "資金不足" in t.rejected[0]


def test_rejection_says_how_much_was_needed_and_available():
    """只說「資金不足」沒用，要看得出差多少才知道該加多少錢。"""
    t = _trader(BrokerOutOfCash(cash_=3_072.0))
    t.scan(["2330"], end="2026-09-15")
    msg = t.rejected[0]
    assert "10,000" in msg   # 需要的金額 (budget 10000 / 100 元 = 100 股)
    assert "3,072" in msg    # 帳上現金


def test_heartbeat_must_not_claim_no_signal_when_orders_were_rejected():
    """這就是事故的核心：心跳說謊，帳戶看起來一切正常。"""
    n = RecordingNotifier()
    t = _trader(BrokerOutOfCash(), notifier=n)
    t.scan(["2330"], end="2026-09-15")
    msg = n.messages[-1]
    assert "無交易訊號" not in msg
    assert "未成交" in msg
    assert "資金不足" in msg


def test_heartbeat_still_says_no_signal_when_there_really_is_none():
    """反向確認：真的沒訊號時，訊息要維持原樣（別製造假警報）。"""
    n = RecordingNotifier()
    t = LiveTrader(FakeProvider(), BrokerOutOfCash(), FakeStrategy({}),
                   position_budget=10_000, dry_run=False, notifier=n)
    t.scan(["2330"], end="2026-09-15")
    assert "無交易訊號" in n.messages[-1]
    assert t.rejected == []


def test_safety_fuse_rejection_is_also_visible():
    """保險絲拒單以前只 print，現在也要進 rejected（同樣是靜默失敗）。"""
    t = _trader(BrokerOutOfCash(cash_=10_000_000.0), max_order_value=1_000.0)
    t.scan(["2330"], end="2026-09-15")
    assert len(t.rejected) == 1
    assert "保險絲" in t.rejected[0]


def test_rejected_resets_each_scan():
    """每輪重算，不可把昨天的未成交累積下去。"""
    t = _trader(BrokerOutOfCash())
    t.scan(["2330"], end="2026-09-15")
    t.scan(["2330"], end="2026-09-15")
    assert len(t.rejected) == 1


def test_successful_sell_is_not_reported_as_rejected():
    """賣出正常成交時不能誤報未成交。"""

    class OkBroker(BrokerOutOfCash):
        def place_order(self, order):
            order.filled = True
            order.fill_price = order.price
            return order

    broker = OkBroker(positions=[Position(symbol="2330", shares=100, avg_price=90.0)])
    t = LiveTrader(FakeProvider(), broker, FakeStrategy({"2330": (Action.SELL, 1.0)}),
                   position_budget=10_000, dry_run=False)
    plans = t.scan(["2330"], end="2026-09-15")
    assert len(plans) == 1 and t.rejected == []
