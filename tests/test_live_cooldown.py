"""實盤掃描的冷却期：賣出後 N 個交易日內不重買（回測本來就有，實盤過去漏了）。"""
import pandas as pd
import pytest

from src.broker.base import Order, OrderSide
from src.broker.persistent_paper import PersistentPaperBroker
from src.engine.trader import LiveTrader
from src.models import Action, Signal


class _AlwaysBuy:
    """永遠喊買，用來檢驗冷却期有沒有真的擋住。"""
    name = "always_buy"
    requires_fundamentals = False
    min_bars = 1

    def evaluate(self, ctx):
        return Signal(symbol=ctx.symbol, action=Action.BUY, strength=1.0, reason="測試買進")


class _Provider:
    def __init__(self, df):
        self.df = df

    def history(self, symbol, start, end):
        return self.df

    def fundamentals(self, symbol):
        return None

    def benchmark(self, start, end):
        return None

    def universe(self):
        return []


@pytest.fixture
def prices():
    idx = pd.bdate_range("2026-08-03", periods=12)
    return pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0,
                         "close": 100.0, "volume": 1000}, index=idx)


def _trader(broker, provider, cooldown=5):
    return LiveTrader(provider, broker, _AlwaysBuy(), position_budget=10_000,
                      dry_run=False, cooldown_days=cooldown)


def test_no_rebuy_within_cooldown(tmp_path, prices):
    """剛賣掉就再喊買時，冷却期內要擋下來（不然就是一直來回洗）。"""
    b = PersistentPaperBroker(path=str(tmp_path / "p.json"), cash=100_000)
    b.place_order(Order("2330", OrderSide.BUY, 10, 100.0, "先建倉"))
    b.place_order(Order("2330", OrderSide.SELL, 10, 100.0, "停損"))
    b.trades[-1]["date"] = "2026-08-12"  # 賣在倒數第 2 個交易日

    plans = _trader(b, _Provider(prices)).scan(["2330"], "2026-08-18")
    assert plans == []  # 冷却期內，不該有買單


def test_rebuy_allowed_after_cooldown(tmp_path, prices):
    b = PersistentPaperBroker(path=str(tmp_path / "p.json"), cash=100_000)
    b.place_order(Order("2330", OrderSide.BUY, 10, 100.0, "先建倉"))
    b.place_order(Order("2330", OrderSide.SELL, 10, 100.0, "停損"))
    b.trades[-1]["date"] = "2026-08-03"  # 賣在最早那天，之後已過 5 個交易日以上

    plans = _trader(b, _Provider(prices)).scan(["2330"], "2026-08-18")
    assert [p.action for p in plans] == ["BUY"]


def test_cooldown_zero_disables_the_guard(tmp_path, prices):
    b = PersistentPaperBroker(path=str(tmp_path / "p.json"), cash=100_000)
    b.place_order(Order("2330", OrderSide.BUY, 10, 100.0, "先建倉"))
    b.place_order(Order("2330", OrderSide.SELL, 10, 100.0, "停損"))
    b.trades[-1]["date"] = "2026-08-12"

    plans = _trader(b, _Provider(prices), cooldown=0).scan(["2330"], "2026-08-18")
    assert [p.action for p in plans] == ["BUY"]


def test_cooldown_only_blocks_the_sold_symbol(tmp_path, prices):
    """冷却是針對「剛賣掉的那一檔」，不該波及其他股票。"""
    b = PersistentPaperBroker(path=str(tmp_path / "p.json"), cash=100_000)
    b.place_order(Order("2330", OrderSide.BUY, 10, 100.0, "先建倉"))
    b.place_order(Order("2330", OrderSide.SELL, 10, 100.0, "停損"))
    b.trades[-1]["date"] = "2026-08-12"

    plans = _trader(b, _Provider(prices)).scan(["2330", "2317"], "2026-08-18")
    assert [p.symbol for p in plans] == ["2317"]


def test_broker_without_trade_log_is_not_blocked(prices):
    """券商沒有成交紀錄 (如 Shioaji) 時只是失去這層保護，不能因此擋掉所有買單。"""
    class _NoLog:
        trades = None

        def positions(self):
            return []

        def cash(self):
            return 100_000.0

        def place_order(self, order):
            order.filled = True
            return order

    plans = _trader(_NoLog(), _Provider(prices)).scan(["2330"], "2026-08-18")
    assert [p.action for p in plans] == ["BUY"]
