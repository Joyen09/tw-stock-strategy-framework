"""LiveTrader.scan 下單決策測試 (用假 provider/broker/strategy，dry_run 不真的送單)。

涵蓋：
- 零股 sizing 回歸：budget//price 直接算「股」，不可被放大成整張 (PR#4 那類 bug)
- max_positions 名額限制：只買訊號最強的前 N 檔
- regime 風向濾網：大盤空頭時禁止做多
- paused：暫停時只出場、不買進
"""
import pandas as pd
import pytest

from src.broker.base import Broker
from src.engine.trader import LiveTrader
from src.models import Action, Position, Signal
from src.strategies.base import Strategy


def _df(close: float, n: int = 5, end: str = "2026-06-30") -> pd.DataFrame:
    idx = pd.date_range(end=end, periods=n, freq="D")
    return pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close, "volume": 1000},
        index=idx,
    )


class FakeProvider:
    def __init__(self, price: float = 100.0, benchmark=None):
        self.price = price
        self._benchmark = benchmark

    def history(self, symbol, start, end):
        return _df(self.price, end=end)

    def fundamentals(self, symbol):
        return None

    def benchmark(self, start, end):
        return self._benchmark


class FakeStrategy(Strategy):
    """對指定 symbol 給定 action/strength 的假策略。"""

    name = "fake"

    def __init__(self, signals):
        super().__init__()
        self.signals = signals  # {symbol: (Action, strength)}

    def evaluate(self, ctx):
        action, strength = self.signals.get(ctx.symbol, (Action.HOLD, 0.0))
        return Signal(action=action, strength=strength, reason="test", symbol=ctx.symbol)


class FakeBroker(Broker):
    def __init__(self, positions=None):
        self._positions = positions or []

    def positions(self):
        return [p for p in self._positions if p.shares > 0]


def test_odd_lot_sizing_not_inflated():
    # budget 10000 / price 600 -> 16 股 (零股)，絕不能變成 16000 或 16 張
    provider = FakeProvider(price=600.0)
    strat = FakeStrategy({"2330": (Action.BUY, 1.0)})
    trader = LiveTrader(
        provider, FakeBroker(), strat,
        position_budget=10_000, dry_run=True, allow_odd_lot=True,
    )
    plans = trader.scan(["2330"], end="2026-06-30")
    assert len(plans) == 1
    assert plans[0].shares == 16  # int(10000 // 600)


def test_whole_lot_sizing_when_odd_disabled():
    # 停用零股：budget 大到能買整張時，股數為 1000 的倍數
    provider = FakeProvider(price=50.0)
    strat = FakeStrategy({"2330": (Action.BUY, 1.0)})
    trader = LiveTrader(
        provider, FakeBroker(), strat,
        position_budget=200_000, dry_run=True, allow_odd_lot=False,
    )
    plans = trader.scan(["2330"], end="2026-06-30")
    # 200000 // (50*1000) = 4 張 = 4000 股
    assert plans[0].shares == 4000


def test_max_positions_limits_to_strongest():
    provider = FakeProvider(price=100.0)
    strat = FakeStrategy({
        "AAA": (Action.BUY, 0.9),
        "BBB": (Action.BUY, 0.5),
        "CCC": (Action.BUY, 0.7),
    })
    trader = LiveTrader(
        provider, FakeBroker(), strat,
        position_budget=10_000, dry_run=True, max_positions=2,
    )
    plans = trader.scan(["AAA", "BBB", "CCC"], end="2026-06-30")
    bought = {p.symbol for p in plans}
    assert bought == {"AAA", "CCC"}  # 最強的兩檔，跳過 BBB


def test_regime_filter_blocks_buys_in_bear_market():
    # 大盤空頭 (最後值遠低於年線)：禁止做多
    bench = pd.Series([100.0] * 249 + [50.0])
    provider = FakeProvider(price=100.0, benchmark=bench)
    strat = FakeStrategy({"2330": (Action.BUY, 1.0)})
    trader = LiveTrader(
        provider, FakeBroker(), strat,
        position_budget=10_000, dry_run=True, regime_filter=True, regime_ma=200,
    )
    plans = trader.scan(["2330"], end="2026-06-30")
    assert plans == []


def test_regime_filter_allows_buys_in_bull_market():
    bench = pd.Series([100.0] * 249 + [200.0])
    provider = FakeProvider(price=100.0, benchmark=bench)
    strat = FakeStrategy({"2330": (Action.BUY, 1.0)})
    trader = LiveTrader(
        provider, FakeBroker(), strat,
        position_budget=10_000, dry_run=True, regime_filter=True, regime_ma=200,
    )
    plans = trader.scan(["2330"], end="2026-06-30")
    assert len(plans) == 1


def test_max_order_value_rejects_oversized_buy():
    # 保險絲：單筆買單金額超過上限就拒單 (防 sizing/報價/髒資料把金額放大)
    provider = FakeProvider(price=600.0)
    strat = FakeStrategy({"2330": (Action.BUY, 1.0)})
    trader = LiveTrader(
        provider, FakeBroker(), strat,
        position_budget=10_000, dry_run=True,
        max_order_value=5_000,  # 16 股 @600 = 9600 > 5000 -> 拒單
    )
    plans = trader.scan(["2330"], end="2026-06-30")
    assert plans == []


def test_max_order_value_default_allows_normal_buy():
    # 正常買單 (金額 <= budget) 不受保險絲影響；預設上限 = budget*1.5
    provider = FakeProvider(price=600.0)
    strat = FakeStrategy({"2330": (Action.BUY, 1.0)})
    trader = LiveTrader(
        provider, FakeBroker(), strat,
        position_budget=10_000, dry_run=True,  # max_order_value 預設 = 15000
    )
    plans = trader.scan(["2330"], end="2026-06-30")
    assert len(plans) == 1  # 16 股 @600 = 9600 < 15000，正常放行


def test_paused_blocks_buys_but_allows_sells():
    provider = FakeProvider(price=100.0)
    strat = FakeStrategy({
        "AAA": (Action.BUY, 1.0),
        "BBB": (Action.SELL, 1.0),
    })
    broker = FakeBroker(positions=[Position("BBB", shares=50, avg_price=90.0)])
    trader = LiveTrader(
        provider, broker, strat,
        position_budget=10_000, dry_run=True, paused=True,
    )
    plans = trader.scan(["AAA", "BBB"], end="2026-06-30")
    actions = {p.symbol: p.action for p in plans}
    assert actions == {"BBB": "SELL"}  # 只出場，不買 AAA


class _FakeNotifier:
    enabled = True

    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)
        return True


def test_heartbeat_sent_when_no_signals():
    """無訊號時要推心跳（證明有跑），且含現金/持倉/狀態。"""
    from src.broker.paper import PaperBroker

    notifier = _FakeNotifier()
    t = LiveTrader(FakeProvider(), PaperBroker(cash=50_000),
                   FakeStrategy({}),  # 全 HOLD -> 無訊號
                   position_budget=10_000, dry_run=True, notifier=notifier)
    t.scan(["2330"], "2026-07-06")
    assert len(notifier.sent) == 1
    assert "掃描完成" in notifier.sent[0]
    assert "50,000" in notifier.sent[0]
    assert "運作正常" in notifier.sent[0]


def test_heartbeat_shows_paused_state():
    """暫停中要在心跳裡標示 ⏸——避免 /pause 忘了解除卻無從察覺。"""
    from src.broker.paper import PaperBroker

    notifier = _FakeNotifier()
    t = LiveTrader(FakeProvider(), PaperBroker(cash=50_000),
                   FakeStrategy({}),
                   position_budget=10_000, dry_run=True, notifier=notifier, paused=True)
    t.scan(["2330"], "2026-07-06")
    assert len(notifier.sent) == 1
    assert "暫停買進中" in notifier.sent[0]
